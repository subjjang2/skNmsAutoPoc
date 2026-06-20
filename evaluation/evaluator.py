"""고정 시험셋 일치율 계산.

evaluate(db, test_set) → {match_rate, correct, total, detail}
test_set rows: {id, raw_log, action, difficulty, scenario_id}
"""
import logging
from collections import defaultdict
from sqlalchemy.ext.asyncio import AsyncSession
from analyzer.llm_client import recommend, embed_texts
from pipeline.vector_store import search_similar

logger = logging.getLogger(__name__)


async def evaluate(db: AsyncSession, test_set: list[dict]) -> dict:
    """test_set 전체 평가. is_test=true 행은 search_similar에서 자동 제외."""
    correct = 0
    total = len(test_set)
    diff_correct: dict[str, int] = defaultdict(int)
    diff_total: dict[str, int] = defaultdict(int)
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for item in test_set:
        raw_log = item["raw_log"]
        gold_action = item["action"]
        difficulty = item.get("difficulty", "medium")
        diff_total[difficulty] += 1

        try:
            from analyzer.drain_miner import add_log as drain_add
            drain_result = drain_add(raw_log)
            drain_template = drain_result.get("template", raw_log)
            vecs = await embed_texts(db, [drain_template + " " + raw_log])
            similar = await search_similar(db, vecs[0], k=5, exclude_test=True)
            pred_action, _, _, _ = await recommend(db, raw_log, drain_template, similar)
        except Exception as e:
            logger.warning("Eval item %s failed: %s", item.get("id"), e)
            pred_action = "MONITOR"  # safe default

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
