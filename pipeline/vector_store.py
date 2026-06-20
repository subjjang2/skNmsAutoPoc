"""pgvector 검색 + 시나리오 upsert."""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, select
from db.models import Scenario

logger = logging.getLogger(__name__)


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


async def search_similar(
    db: AsyncSession,
    vector: list[float],
    k: int = 5,
    exclude_test: bool = True,
    exclude_seeds: bool = False,
) -> list[dict]:
    """코사인 유사도 top-k 반환.
    exclude_test=True  → is_test=false 행만
    exclude_seeds=True → SCN-* 시드 행 제외 (실험 평가 전용)
    """
    vec_str = _vec_literal(vector)
    rows = await db.execute(
        text("""
            SELECT scenario_id, action, difficulty, log_text, drain_template,
                   is_gold, 1 - (embedding <=> CAST(:vec AS vector)) AS score
            FROM scenarios
            WHERE embedding IS NOT NULL
              AND (:include_test OR is_test = false)
              AND (NOT :exclude_seeds OR scenario_id NOT LIKE 'SCN-%')
            ORDER BY embedding <=> CAST(:vec AS vector)
            LIMIT :k
        """),
        {"vec": vec_str, "k": k,
         "include_test": not exclude_test,
         "exclude_seeds": exclude_seeds},
    )
    return [dict(r._mapping) for r in rows.fetchall()]


async def upsert_scenario(
    db: AsyncSession,
    scenario_id: str,
    vector: list[float],
    action: str,
    log_text: str = "",
    drain_template: str = "",
    difficulty: str = "medium",
    is_gold: bool = False,
    is_test: bool = False,
    source: str = "normal",
) -> None:
    """scenarios 행 upsert (embedding 포함). scenario_id unique 충돌 시 UPDATE."""
    vec_str = _vec_literal(vector)
    existing = await db.execute(
        select(Scenario).where(Scenario.scenario_id == scenario_id)
    )
    row = existing.scalar_one_or_none()
    if row:
        await db.execute(
            text("""
                UPDATE scenarios
                SET embedding = CAST(:vec AS vector),
                    action = :action,
                    log_text = :log_text,
                    drain_template = :drain_template,
                    difficulty = :difficulty,
                    is_gold = :is_gold,
                    is_test = :is_test,
                    source = :source
                WHERE scenario_id = :sid
            """),
            {
                "vec": vec_str, "sid": scenario_id,
                "action": action, "log_text": log_text,
                "drain_template": drain_template, "difficulty": difficulty,
                "is_gold": is_gold, "is_test": is_test, "source": source,
            },
        )
    else:
        db.add(Scenario(
            scenario_id=scenario_id,
            action=action,
            log_text=log_text,
            drain_template=drain_template,
            difficulty=difficulty,
            is_gold=is_gold,
            is_test=is_test,
            source=source,
        ))
        await db.flush()
        await db.execute(
            text("UPDATE scenarios SET embedding = CAST(:vec AS vector) WHERE scenario_id = :sid"),
            {"vec": vec_str, "sid": scenario_id},
        )
    await db.commit()


async def upsert_scenario_batch(
    db: AsyncSession,
    rows: list[dict],
    vectors: list[list[float]],
) -> None:
    """배치 upsert — experiment 용."""
    for row, vec in zip(rows, vectors):
        await upsert_scenario(db, vector=vec, **row)


async def reset_training_embeddings(db: AsyncSession) -> None:
    """실험용 임베딩 초기화. 시드 시나리오(SCN-*)는 보존."""
    await db.execute(
        text(
            "UPDATE scenarios SET embedding = NULL "
            "WHERE is_test = false AND scenario_id NOT LIKE 'SCN-%'"
        )
    )
    await db.commit()
    logger.info("Training embeddings reset (experiment rows only, SCN-* preserved)")


async def create_ivfflat_index(db: AsyncSession) -> None:
    """rows > 1000 시 성능 인덱스. 이미 존재하면 무시."""
    await db.execute(
        text(
            "CREATE INDEX IF NOT EXISTS scenarios_embedding_idx "
            "ON scenarios USING ivfflat (embedding vector_cosine_ops)"
        )
    )
    await db.commit()
