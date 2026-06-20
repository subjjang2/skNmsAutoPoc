"""합성 골드 데이터 생성.

hard/medium 시나리오를 LLM paraphrase로 증강 → gold_pool.json.
라벨(action)은 시나리오에서 차용, LLM은 로그 문장만 생성.

실행: python -m pipeline.synth_gold [--per-scenario 30]
"""
import argparse
import asyncio
import json
import logging
import pathlib
import re
import yaml

DATA_DIR = pathlib.Path(__file__).parent.parent / "data" / "openstack"
SCENARIOS_YAML = pathlib.Path(__file__).parent.parent / "config" / "scenarios.yaml"
GOLD_POOL_JSON = DATA_DIR / "gold_pool.json"

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def _load_scenarios(difficulties=("hard", "medium")) -> list[dict]:
    data = yaml.safe_load(SCENARIOS_YAML.read_text(encoding="utf-8"))
    return [s for s in data.get("scenarios", []) if s.get("difficulty") in difficulties]


def _extract_json_array(raw: str) -> list:
    """<think> 블록 제거 후 JSON 배열 추출. 마지막 [...] 우선."""
    # Qwen3 thinking 태그 제거
    cleaned = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()
    # 마지막 [...] 블록 찾기 (greedy → 가장 넓은 범위)
    m = re.search(r'\[.*\]', cleaned, re.DOTALL)
    if not m:
        raise ValueError(f"JSON 배열 없음. 원문: {cleaned[:200]}")
    return json.loads(m.group())


async def _generate_batch(db, scenario: dict, n: int) -> tuple[list[str], str | None]:
    """n건 요청 단일 LLM 호출. (결과, 에러) 반환."""
    from analyzer.llm_client import analyze
    system = "/no_think You are an OpenStack log generator. Output JSON array only, no explanation, no thinking."
    prompt = (
        f"Scenario: {scenario.get('description', '')}\n"
        f"Action needed: {scenario['action']}\n"
        f"Reference log: {scenario.get('log_text', '')}\n\n"
        f"Generate exactly {n} OpenStack error log lines for this failure.\n"
        f"Rules: different vocabulary from reference, include hostnames/UUIDs/IPs.\n"
        f'Output ONLY valid JSON array: ["log1","log2",...]'
    )
    try:
        raw, _ = await analyze(db, system, prompt, max_tokens=1024)
        items = _extract_json_array(raw)
        return [str(x).strip() for x in items if str(x).strip()][:n], None
    except Exception as e:
        return [], f"{type(e).__name__}: {str(e)[:120]}"


async def _generate_variants(db, scenario: dict, n: int) -> tuple[list[str], str | None]:
    """n건을 BATCH_SIZE씩 나눠서 호출."""
    BATCH_SIZE = 10
    results: list[str] = []
    last_err: str | None = None

    remaining = n
    while remaining > 0:
        batch_n = min(BATCH_SIZE, remaining)
        batch, err = await _generate_batch(db, scenario, batch_n)
        if batch:
            results.extend(batch)
        if err:
            last_err = err
        remaining -= batch_n

    if not results:
        return [], last_err or "모든 배치 실패"
    return results, None


async def generate(n_per: int = 30, on_progress=None) -> list[dict]:
    from dotenv import load_dotenv
    load_dotenv()
    from db.database import AsyncSessionLocal

    scenarios = _load_scenarios(difficulties=("hard", "medium"))
    total = len(scenarios)
    logger.info("대상 시나리오: %d개 (hard/medium), 시나리오당 %d건", total, n_per)
    if on_progress:
        on_progress(0, total, "", 0, None)

    rows: list[dict] = []
    errors: list[str] = []
    async with AsyncSessionLocal() as db:
        for i, s in enumerate(scenarios, 1):
            if on_progress:
                on_progress(i - 1, total, s["id"], len(rows), None)
            variants, err = await _generate_variants(db, s, n_per)
            if err:
                errors.append(f"{s['id']}: {err}")
                logger.warning("[%d/%d] %s LLM 실패: %s", i, total, s["id"], err)
            for j, log in enumerate(variants):
                rows.append({
                    "id": f"GLD-{s['id']}-{j+1:03d}",
                    "raw_log": log,
                    "action": s["action"],
                    "difficulty": s["difficulty"],
                    "scenario_id": s["id"],
                    "source": "gold",
                })
            logger.info("[%d/%d] %s → %d건 생성", i, total, s["id"], len(variants))
            first_err = errors[0] if errors else None
            if on_progress:
                on_progress(i, total, s["id"], len(rows), first_err)

    if not rows:
        raise RuntimeError(f"모든 시나리오 LLM 실패 ({len(errors)}개). 첫 에러: {errors[0] if errors else '알 수 없음'}")

    import random
    random.seed(42)
    random.shuffle(rows)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    GOLD_POOL_JSON.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("gold_pool.json 저장: %d건 (셔플 완료)", len(rows))

    # DB에도 저장 (Railway 재시작 대비)
    await _save_to_db(rows)
    logger.info("DB 저장 완료: %d건", len(rows))
    return rows


async def _save_to_db(rows: list[dict]) -> None:
    """gold 데이터를 scenarios 테이블에 저장 (embedding 없이 텍스트만)."""
    from db.database import AsyncSessionLocal
    from db.models import Scenario
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        # 기존 synth_gold 행 삭제 후 재삽입
        from sqlalchemy import text as sa_text
        await db.execute(sa_text(
            "DELETE FROM scenarios WHERE source = 'synth_gold'"
        ))
        await db.commit()

        for r in rows:
            db.add(Scenario(
                scenario_id=r["id"],
                action=r["action"],
                log_text=r["raw_log"],
                difficulty=r["difficulty"],
                is_gold=True,
                is_test=False,
                source="synth_gold",
            ))
        await db.commit()


async def load_from_db() -> list[dict]:
    """DB에서 synth_gold 행 로드 → experiment용 dict 리스트."""
    from db.database import AsyncSessionLocal
    from db.models import Scenario
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(Scenario)
            .where(Scenario.source == "synth_gold")
            .order_by(Scenario.scenario_id)
        )).scalars().all()

    return [
        {
            "id": r.scenario_id,
            "raw_log": r.log_text,
            "action": r.action,
            "difficulty": r.difficulty or "medium",
            "scenario_id": r.scenario_id,
            "source": "synth_gold",
        }
        for r in rows
    ]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-scenario", type=int, default=30,
                        help="시나리오당 생성 건수 (기본 30 → 약 600건)")
    args = parser.parse_args()
    asyncio.run(generate(n_per=args.per_scenario))
