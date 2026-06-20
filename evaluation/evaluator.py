"""고정 시험셋 일치율 계산.

evaluate(db, test_set) → {match_rate, correct, total, detail}
test_set rows: {id, raw_log, action, difficulty, scenario_id}
"""
import logging
import random
from collections import defaultdict
from sqlalchemy.ext.asyncio import AsyncSession
from analyzer.llm_client import recommend, embed_texts, ACTIONS
from pipeline.vector_store import search_similar

logger = logging.getLogger(__name__)


async def evaluate(db: AsyncSession, test_set: list[dict],
                   vec_cache: dict | None = None) -> dict:
    """test_set 전체 평가. is_test=true 행은 search_similar에서 자동 제외.
    vec_cache: 단계 간 test 임베딩 재사용용 dict (id→(drain_template, vector)).
               전달 시 test 임베딩을 1회만 계산. (text→vector는 결정적)
    """
    from analyzer.drain_miner import add_log as drain_add
    correct = 0
    total = len(test_set)
    diff_correct: dict[str, int] = defaultdict(int)
    diff_total: dict[str, int] = defaultdict(int)
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    if vec_cache is None:
        vec_cache = {}

    # 캐시 미스 항목만 배치 임베딩 (첫 단계에서 전부, 이후 재사용)
    missing = [it for it in test_set if it.get("id") not in vec_cache]
    if missing:
        templates = [drain_add(it["raw_log"]).get("template", it["raw_log"]) for it in missing]
        texts = [t + " " + it["raw_log"] for t, it in zip(templates, missing)]
        vecs = await embed_texts(db, texts)
        for it, tmpl, vec in zip(missing, templates, vecs):
            vec_cache[it["id"]] = (tmpl, vec)

    for item in test_set:
        raw_log = item["raw_log"]
        gold_action = item["action"]
        difficulty = item.get("difficulty", "medium")
        diff_total[difficulty] += 1

        try:
            drain_template, vec = vec_cache[item["id"]]
            similar = await search_similar(db, vec, k=5, exclude_test=True, exclude_seeds=True)
            pred_action, _, _, _ = await recommend(db, raw_log, drain_template, similar)
        except Exception as e:
            logger.warning("Eval item %s failed: %s", item.get("id"), e)
            pred_action = random.choice(list(ACTIONS))  # exception path: random baseline

        if pred_action == gold_action:
            correct += 1
            diff_correct[difficulty] += 1

        confusion[gold_action][pred_action] += 1

    match_rate = int(correct / total * 100) if total else 0

    detail = {
        "difficulty": {
            d: {
                "correct": diff_correct[d],
                "total": diff_total[d],
                "rate": int(diff_correct[d] / diff_total[d] * 100) if diff_total[d] else 0,
            }
            for d in ("easy", "medium", "hard")
        },
        "confusion": {k: dict(v) for k, v in confusion.items()},
    }

    return {
        "match_rate": match_rate,
        "correct": correct,
        "total": total,
        "detail": detail,
    }
