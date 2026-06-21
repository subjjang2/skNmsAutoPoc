"""학습 곡선 드라이버.

steps: (train_count, gold_count) → reset → upsert → evaluate → eval_results
run_id 반환. DB에 EvalResult 저장.
"""
import json
import logging
import pathlib
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from db.models import EvalResult
from db.database import AsyncSessionLocal
from analyzer.llm_client import embed_texts
from pipeline.vector_store import (
    upsert_scenario,
    reset_training_embeddings,
    create_ivfflat_index,
)
from evaluation.evaluator import evaluate

logger = logging.getLogger(__name__)

DATA_DIR = pathlib.Path(__file__).parent.parent / "data" / "openstack"

DEFAULT_STEPS = [
    (0, 0),      # 순수 baseline: 사례 0 → 랜덤 ~10%
    (800, 0),    # train 한계: 흔한 장애 800개로도 희귀 장애 못 잡음
    (800, 50),   # gold 소량 → 상승 시작
    (800, 100),
    (800, 200),
    (800, 400),  # gold 충분 → 포화
]


def _load_json(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


async def _build_alt_test_set() -> list[dict]:
    """DB에서 alt_logs 있는 시나리오 → test set 구성."""
    from sqlalchemy import text as sa_text
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(sa_text(
            "SELECT scenario_id, action, difficulty, alt_logs "
            "FROM scenarios WHERE scenario_id LIKE 'SCN-%' AND alt_logs IS NOT NULL"
        ))).fetchall()
    result = []
    for r in rows:
        alt = r.alt_logs if isinstance(r.alt_logs, list) else json.loads(r.alt_logs or "[]")
        for log in alt:
            if log and log.strip():
                result.append({
                    "id": f"ALT-{r.scenario_id}-{len(result):03d}",
                    "raw_log": log.strip(),
                    "action": r.action,
                    "difficulty": r.difficulty or "medium",
                    "scenario_id": r.scenario_id,
                })
    return result


async def run_experiment(
    steps: list[tuple[int, int]] | None = None,
    run_id: str | None = None,
) -> str:
    """학습 곡선 실험 실행. run_id 반환."""
    if steps is None:
        steps = DEFAULT_STEPS
    if run_id is None:
        run_id = f"RUN-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"

    train_pool = _load_json(DATA_DIR / "train_pool.json")

    # test_set = hold_out 시나리오 (build_dataset가 생성). train_pool과 모집단 분리됨.
    # hold_out 패턴은 gold_pool로만 커버 → gold 추가 시 정확도 상승.
    test_set = _load_json(DATA_DIR / "test_set.json")
    logger.info("test_set (hold_out) 사용: %d건", len(test_set))

    if not train_pool or not test_set:
        raise RuntimeError(
            "train_pool.json 또는 test_set.json 없음. "
            "python -m pipeline.build_dataset 먼저 실행하세요."
        )

    # gold pool: 파일 우선, 없으면 DB에서 로드 (Railway 재시작 대비)
    gold_pool = _load_json(DATA_DIR / "gold_pool.json")[:600]
    if gold_pool:
        logger.info("gold_pool.json 사용: %d건", len(gold_pool))
    else:
        from pipeline.synth_gold import load_from_db as _load_gold_db
        gold_pool = (await _load_gold_db())[:600]
        if gold_pool:
            logger.info("DB synth_gold 사용: %d건", len(gold_pool))
        else:
            logger.warning("gold_pool 없음 — gold step 스킵 (관리자 페이지에서 Gold 생성 필요)")

    logger.info("Experiment %s: %d steps, train_pool=%d, test_set=%d, gold_pool=%d",
                run_id, len(steps), len(train_pool), len(test_set), len(gold_pool))

    # 임베딩 캐시: 텍스트→벡터는 결정적이므로 단계마다 재계산하지 않고 1회만.
    # 최대 train/gold 건수만큼 미리 전부 임베딩 → 단계 루프는 조회만 (5~6배 빠름).
    from analyzer.drain_miner import add_log as _drain_add

    def _row_text(r: dict) -> str:
        return _drain_add(r["raw_log"]).get("template", r["raw_log"]) + " " + r["raw_log"]

    max_train = max((s[0] for s in steps), default=0)
    max_gold = max((s[1] for s in steps), default=0)

    test_vec_cache: dict = {}  # test 임베딩 단계 간 재사용

    async with AsyncSessionLocal() as db:
        train_vec_cache: list[list[float]] = []
        if max_train:
            logger.info("train 임베딩 캐시 생성: %d건 (1회)", min(max_train, len(train_pool)))
            train_vec_cache = await embed_texts(db, [_row_text(r) for r in train_pool[:max_train]])
        gold_vec_cache: list[list[float]] = []
        if max_gold and gold_pool:
            logger.info("gold 임베딩 캐시 생성: %d건 (1회)", min(max_gold, len(gold_pool)))
            gold_vec_cache = await embed_texts(db, [_row_text(r) for r in gold_pool[:max_gold]])

        for step_idx, (train_count, gold_count) in enumerate(steps):
            if gold_count > 0 and not gold_pool:
                logger.warning("Step %d: gold_count=%d 요청이나 gold_pool 없음 — 스킵", step_idx, gold_count)
                continue
            logger.info("Step %d: train=%d gold=%d", step_idx, train_count, gold_count)

            # 1) 학습 임베딩 초기화
            await reset_training_embeddings(db)

            # 2) train_pool 적재 (캐시된 벡터 재사용)
            train_rows = train_pool[:train_count]
            if train_rows:
                for row, vec in zip(train_rows, train_vec_cache[:train_count]):
                    await upsert_scenario(
                        db,
                        scenario_id=f"{run_id}-TRN-{row['id']}",
                        vector=vec,
                        action=row["action"],
                        log_text=row["raw_log"],
                        difficulty=row.get("difficulty", "medium"),
                        is_gold=False,
                        is_test=False,
                        source="normal",
                        commit=False,
                    )
                await db.commit()

            # 3) gold 적재 (캐시된 벡터 재사용)
            gold_rows = gold_pool[:gold_count]
            if gold_rows:
                for row, vec in zip(gold_rows, gold_vec_cache[:gold_count]):
                    await upsert_scenario(
                        db,
                        scenario_id=f"{run_id}-GLD-{row['id']}",
                        vector=vec,
                        action=row["action"],
                        log_text=row["raw_log"],
                        difficulty=row.get("difficulty", "medium"),
                        is_gold=True,
                        is_test=False,
                        source="gold",
                        commit=False,
                    )
                await db.commit()

            # 4) ivfflat 인덱스 (rows > 1000 시 효과)
            if train_count + gold_count > 1000:
                await create_ivfflat_index(db)

            # 5) 평가 (test 임베딩은 캐시 재사용)
            result = await evaluate(db, test_set, vec_cache=test_vec_cache)

            # 6) EvalResult 저장
            db.add(EvalResult(
                run_id=run_id,
                step_index=step_idx,
                training_count=train_count,
                gold_count=gold_count,
                match_rate=result["match_rate"],
                correct=result["correct"],
                total=result["total"],
                detail=result["detail"],
            ))
            await db.commit()
            logger.info(
                "Step %d done: match_rate=%d%% (%d/%d)",
                step_idx, result["match_rate"], result["correct"], result["total"]
            )

    logger.info("Experiment %s completed", run_id)
    return run_id
