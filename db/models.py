import os
from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, ForeignKey, JSON
from pgvector.sqlalchemy import Vector
from db.database import Base

_EMBED_DIM = int(os.environ.get("EMBED_DIM", "384"))


class LLMSettings(Base):
    __tablename__ = "llm_settings"

    id = Column(Integer, primary_key=True)
    active_backend = Column(String(10), default="dgx")
    dgx_url = Column(Text, default="")
    dgx_api_key = Column(Text, default="")
    dgx_model = Column(String(100), default="Qwen2.5-14B")
    dgx_embed_model = Column(String(100), default="Qwen2.5-14B")
    claude_api_key = Column(Text, default="")
    claude_model = Column(String(100), default="claude-sonnet-4-6")
    system_prompt = Column(Text, default="")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Scenario(Base):
    """지식베이스 — 학습/시험 행. pgvector embedding 포함."""
    __tablename__ = "scenarios"

    id = Column(Integer, primary_key=True)
    scenario_id = Column(String(50), unique=True, index=True, nullable=False)
    pattern_regex = Column(Text, default="")
    log_text = Column(Text, default="")
    drain_template = Column(Text, default="")
    action = Column(String(20), nullable=False)   # MONITOR|RESOURCE_CHECK|AGENT_RESTART|RESCHEDULE|ESCALATE
    difficulty = Column(String(10), default="medium")   # easy|medium|hard
    description = Column(Text, default="")
    is_gold = Column(Boolean, default=False)
    source = Column(String(10), default="normal")   # normal|gold
    is_test = Column(Boolean, default=False)        # True → pgvector 검색 제외
    embedding = Column(Vector(_EMBED_DIM), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Incident(Base):
    """라이브 로그 이벤트 — UI 전용."""
    __tablename__ = "incidents"

    id = Column(Integer, primary_key=True)
    incident_id = Column(String(50), unique=True, index=True, nullable=False)
    raw_log = Column(Text, default="")
    drain_template = Column(Text, default="")
    recommended_action = Column(String(20), default="")
    confidence = Column(Integer, default=0)
    llm_reason = Column(Text, default="")
    similar_cases = Column(JSON, default=list)
    backend_used = Column(String(20), default="")
    operator_action = Column(String(20), default="")
    status = Column(String(20), default="WAIT_APPROVAL")  # NEW|ANALYZING|WAIT_APPROVAL|APPROVED|REJECTED|AUTO
    is_correct = Column(Boolean, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class GoldData(Base):
    """운영자가 거절+수정한 케이스 — 골드 데이터."""
    __tablename__ = "gold_data"

    id = Column(Integer, primary_key=True)
    incident_id = Column(String(50), ForeignKey("incidents.incident_id"), index=True)
    operator_action = Column(String(20), nullable=False)
    recommended_action = Column(String(20), default="")
    correction_note = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class EvalResult(Base):
    """학습 곡선 포인트 — 1 row = 1 step."""
    __tablename__ = "eval_results"

    id = Column(Integer, primary_key=True)
    run_id = Column(String(50), index=True, nullable=False)
    step_index = Column(Integer, default=0)
    training_count = Column(Integer, default=0)
    gold_count = Column(Integer, default=0)
    match_rate = Column(Integer, default=0)   # 0-100 int percent
    correct = Column(Integer, default=0)
    total = Column(Integer, default=0)
    detail = Column(JSON, default=dict)       # {easy, medium, hard, confusion}
    created_at = Column(DateTime, default=datetime.utcnow)


class Counter(Base):
    """시퀀스 ID 발급용 카운터."""
    __tablename__ = "counters"

    name = Column(String(40), primary_key=True)
    value = Column(Integer, default=0)
