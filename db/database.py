import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase


def _build_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


class Base(DeclarativeBase):
    pass


_engine = None
_session_factory = None


def _get_engine():
    global _engine
    if _engine is None:
        url = _build_url()
        if not url:
            raise RuntimeError("DATABASE_URL 환경변수가 설정되지 않았습니다.")
        _engine = create_async_engine(url, pool_size=5, pool_pre_ping=True)
    return _engine


def _get_session_factory():
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(_get_engine(), expire_on_commit=False)
    return _session_factory


class _LazySessionLocal:
    def __call__(self):
        return _get_session_factory()()

    def __getattr__(self, name):
        return getattr(_get_session_factory(), name)


AsyncSessionLocal = _LazySessionLocal()


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    from db.models import Base as ModelBase, LLMSettings, Scenario
    from sqlalchemy import select, text
    import yaml, pathlib, os

    engine = _get_engine()

    async with engine.begin() as conn:
        # pgvector extension
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(ModelBase.metadata.create_all)

        # idempotent 컬럼 추가
        for stmt in [
            "ALTER TABLE llm_settings ADD COLUMN IF NOT EXISTS dgx_embed_model VARCHAR(100) DEFAULT 'Qwen2.5-14B'",
            "ALTER TABLE scenarios ADD COLUMN IF NOT EXISTS is_test BOOLEAN DEFAULT FALSE",
            "ALTER TABLE scenarios ADD COLUMN IF NOT EXISTS alt_logs JSONB DEFAULT '[]'",
            "ALTER TABLE scenarios ADD COLUMN IF NOT EXISTS hold_out BOOLEAN DEFAULT FALSE",
            # hold_out 시나리오 임베딩 초기화 (시드에서 제거)
            "UPDATE scenarios SET embedding = NULL, hold_out = TRUE WHERE scenario_id IN "
            "('SCN-033','SCN-034','SCN-035','SCN-036','SCN-037','SCN-038','SCN-039','SCN-040')",
            # 액션 10개 재설계 후 구버전 synth_gold 삭제 (action 라벨 불일치)
            "DELETE FROM scenarios WHERE source = 'synth_gold'",
            # 지난 실험 임시 행 정리 (RUN-*-TRN/GLD). 실험마다 재생성되므로 보존 불필요.
            # eval_results(실험 결과/곡선)는 별도 테이블이라 영향 없음.
            "DELETE FROM scenarios WHERE scenario_id LIKE 'RUN-%'",
        ]:
            await conn.execute(text(stmt))

    async with AsyncSessionLocal() as session:
        # llm_settings 시드
        result = await session.execute(select(LLMSettings).limit(1))
        if not result.scalar_one_or_none():
            session.add(LLMSettings(
                dgx_url=os.environ.get("DGX_URL", ""),
                dgx_api_key=os.environ.get("DGX_API_KEY", "dummy"),
                dgx_model=os.environ.get("DGX_MODEL", "Qwen2.5-14B"),
                dgx_embed_model=os.environ.get("DGX_EMBED_MODEL", "Qwen2.5-14B"),
                claude_api_key=os.environ.get("CLAUDE_API_KEY", ""),
                claude_model=os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6"),
                active_backend="local" if os.environ.get("EMBED_BACKEND", "local") == "local" else "dgx",
            ))
            await session.commit()

        # scenarios.yaml → DB 시드 (idempotent)
        yaml_path = pathlib.Path(__file__).parent.parent / "config" / "scenarios.yaml"
        if yaml_path.exists():
            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            for s in data.get("scenarios", []):
                result = await session.execute(
                    select(Scenario).where(Scenario.scenario_id == s["id"])
                )
                row = result.scalar_one_or_none()
                if not row:
                    session.add(Scenario(
                        scenario_id=s["id"],
                        pattern_regex=s.get("pattern_regex", ""),
                        log_text=s.get("log_text", ""),
                        action=s["action"],
                        difficulty=s.get("difficulty", "medium"),
                        description=s.get("description", ""),
                        source="normal",
                        is_gold=False,
                        is_test=False,
                        hold_out=s.get("hold_out", False),
                    ))
                else:
                    # action 라벨 변경 반영 (시나리오 재설계 시 업데이트)
                    row.action = s["action"]
                    row.difficulty = s.get("difficulty", row.difficulty)
                    row.hold_out = s.get("hold_out", False)
            await session.commit()

    print("DB 초기화 완료 (pgvector enabled)")
