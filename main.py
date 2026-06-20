"""AI NMS FastAPI 진입점 — port 8001."""
import asyncio
import json
import logging
import os
import pathlib
from contextlib import asynccontextmanager
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db, init_db, AsyncSessionLocal
from db.models import Incident, Scenario, GoldData, EvalResult, LLMSettings
from history.seq import next_seq_id
from analyzer.drain_miner import add_log as drain_add
from analyzer.llm_client import recommend, embed_texts
from pipeline.vector_store import search_similar, upsert_scenario
from pipeline.embedder import probe_dim

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

TEMPLATES_DIR = pathlib.Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_bg_tasks: set[asyncio.Task] = set()
_drain_call_count = 0
_gold_status: dict = {"running": False, "error": None, "count": 0}


async def _save_drain_state() -> None:
    from analyzer.drain_miner import get_state_json
    state = get_state_json()
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("INSERT INTO key_value (key, value, updated_at) VALUES ('drain_state', :v, now()) "
                 "ON CONFLICT (key) DO UPDATE SET value = :v, updated_at = now()"),
            {"v": state},
        )
        await db.commit()


async def _load_drain_state() -> None:
    from analyzer.drain_miner import restore_state_json
    async with AsyncSessionLocal() as db:
        row = await db.execute(text("SELECT value FROM key_value WHERE key = 'drain_state'"))
        state = row.scalar_one_or_none()
    if state:
        restore_state_json(state)
        logger.info("Drain3 상태 복원 완료")


async def _auto_build_dataset():
    """data 파일 없으면 자동 다운로드+빌드 (Railway 등 ephemeral FS 대응)."""
    import pathlib
    data_dir = pathlib.Path("data/openstack")
    if not (data_dir / "train_pool.json").exists():
        logger.info("train_pool.json 없음 — 자동 빌드 시작")
        from pipeline.log_loader import load_raw_lines
        from pipeline.build_dataset import build
        load_raw_lines()
        build(test_size=int(os.environ.get("TEST_SIZE", "200")))
        logger.info("데이터셋 자동 빌드 완료")


async def _embed_seeded_scenarios():
    """log_text 있지만 embedding 없는 시드 시나리오를 시작 시 임베딩."""
    from db.database import AsyncSessionLocal
    from pipeline.vector_store import upsert_scenario
    from pipeline.embedder import embed_texts_direct

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            text("SELECT scenario_id, log_text, action, drain_template, difficulty, is_gold, is_test, source "
                 "FROM scenarios WHERE log_text != '' AND embedding IS NULL")
        )).fetchall()

    if not rows:
        logger.info("임베딩 필요한 시드 시나리오 없음")
        return

    logger.info("시드 시나리오 %d개 임베딩 시작", len(rows))
    texts = [r.log_text for r in rows]
    vecs = await embed_texts_direct(texts)

    async with AsyncSessionLocal() as db:
        for row, vec in zip(rows, vecs):
            await upsert_scenario(
                db,
                scenario_id=row.scenario_id,
                vector=vec,
                action=row.action,
                log_text=row.log_text,
                drain_template=row.drain_template or "",
                difficulty=row.difficulty,
                is_gold=row.is_gold,
                is_test=row.is_test,
                source=row.source,
            )
    logger.info("시드 시나리오 임베딩 완료 (%d개)", len(rows))


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Templates dir: %s (exists=%s)", TEMPLATES_DIR, TEMPLATES_DIR.exists())
    dim = await probe_dim()
    os.environ["EMBED_DIM"] = str(dim)
    await init_db()
    await _load_drain_state()
    await _embed_seeded_scenarios()
    await _auto_build_dataset()
    yield
    for t in list(_bg_tasks):
        t.cancel()


app = FastAPI(title="AI NMS POC", version="0.1.0", lifespan=lifespan)


# ── HTML pages ──────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    try:
        return templates.TemplateResponse(request, "dashboard.html")
    except Exception as e:
        logger.error("dashboard 렌더링 실패: %s", e, exc_info=True)
        raise HTTPException(500, f"템플릿 오류: {e}")


@app.get("/accuracy", response_class=HTMLResponse)
async def accuracy(request: Request):
    return templates.TemplateResponse(request, "accuracy.html")


@app.get("/admin", response_class=HTMLResponse)
async def admin(request: Request):
    return templates.TemplateResponse(request, "admin.html")


@app.get("/health")
async def health():
    return {"status": "ok"}


# ── Pydantic models ──────────────────────────────────────────────────

class LogRequest(BaseModel):
    log: str


class RejectRequest(BaseModel):
    action: str
    note: str = ""


class AdminSettingsRequest(BaseModel):
    dgx_url: str = ""
    dgx_api_key: str = ""
    dgx_model: str = "Qwen2.5-14B"
    dgx_embed_model: str = "Qwen2.5-14B"
    claude_api_key: str = ""
    claude_model: str = "claude-sonnet-4-6"
    active_backend: str = "dgx"
    system_prompt: str = ""


class EvalRunRequest(BaseModel):
    train_steps: list[int] = [200, 400, 800]
    gold_steps: list[int] = [0, 100, 300, 600]


class AutoDemoRequest(BaseModel):
    threshold: int = 85


# ── POST /api/logs ───────────────────────────────────────────────────

@app.post("/api/logs")
async def analyze_log(req: LogRequest, db: AsyncSession = Depends(get_db)):
    if not req.log.strip():
        raise HTTPException(400, "log 필드가 비어있습니다.")

    global _drain_call_count
    drain_result = drain_add(req.log)
    drain_template = drain_result["template"]
    _drain_call_count += 1
    if _drain_call_count % 5 == 0:
        asyncio.create_task(_save_drain_state())

    vecs = await embed_texts(db, [drain_template + " " + req.log])
    similar = await search_similar(db, vecs[0], k=5, exclude_test=True)

    action, confidence, reason, backend = await recommend(db, req.log, drain_template, similar)

    incident_id = await next_seq_id(db, "INC")

    inc = Incident(
        incident_id=incident_id,
        raw_log=req.log,
        drain_template=drain_template,
        recommended_action=action,
        confidence=confidence,
        llm_reason=reason,
        similar_cases=[
            {"scenario_id": s.get("scenario_id"), "action": s.get("action"),
             "score": float(s.get("score", 0)), "log_text": s.get("log_text", "")}
            for s in similar
        ],
        backend_used=backend,
        status="WAIT_APPROVAL",
    )
    db.add(inc)
    await db.commit()

    return {
        "incident_id": incident_id,
        "drain_template": drain_template,
        "recommended_action": action,
        "confidence": confidence,
        "reason": reason,
        "similar_cases": inc.similar_cases,
        "backend_used": backend,
    }


# ── Incidents ────────────────────────────────────────────────────────

@app.get("/api/incidents/recent")
async def recent_incidents(limit: int = 50, db: AsyncSession = Depends(get_db)):
    rows = await db.execute(
        select(Incident).order_by(Incident.created_at.desc()).limit(limit)
    )
    incs = rows.scalars().all()
    return [
        {
            "incident_id": i.incident_id,
            "recommended_action": i.recommended_action,
            "confidence": i.confidence,
            "status": i.status,
            "is_correct": i.is_correct,
            "created_at": i.created_at.isoformat() if i.created_at else None,
            "raw_log": i.raw_log[:120],
        }
        for i in incs
    ]


@app.get("/api/incidents/{incident_id}")
async def get_incident(incident_id: str, db: AsyncSession = Depends(get_db)):
    row = await db.execute(
        select(Incident).where(Incident.incident_id == incident_id)
    )
    inc = row.scalar_one_or_none()
    if not inc:
        raise HTTPException(404, "incident not found")
    return {
        "incident_id": inc.incident_id,
        "raw_log": inc.raw_log,
        "drain_template": inc.drain_template,
        "recommended_action": inc.recommended_action,
        "confidence": inc.confidence,
        "llm_reason": inc.llm_reason,
        "similar_cases": inc.similar_cases,
        "backend_used": inc.backend_used,
        "status": inc.status,
        "operator_action": inc.operator_action,
        "is_correct": inc.is_correct,
        "created_at": inc.created_at.isoformat() if inc.created_at else None,
    }


@app.post("/api/incidents/{incident_id}/approve")
async def approve_incident(incident_id: str, db: AsyncSession = Depends(get_db)):
    row = await db.execute(
        select(Incident).where(Incident.incident_id == incident_id)
    )
    inc = row.scalar_one_or_none()
    if not inc:
        raise HTTPException(404, "incident not found")

    inc.status = "APPROVED"
    inc.operator_action = inc.recommended_action

    # normal training data로 추가 (upsert_scenario 내부에서 commit 포함)
    vecs = await embed_texts(db, [inc.drain_template + " " + inc.raw_log])
    await upsert_scenario(
        db,
        scenario_id=f"GLD-{inc.incident_id}",
        vector=vecs[0],
        action=inc.recommended_action,
        log_text=inc.raw_log,
        drain_template=inc.drain_template,
        is_gold=False,
        is_test=False,
        source="normal",
    )
    return {"status": "approved", "incident_id": incident_id}


@app.post("/api/incidents/{incident_id}/reject")
async def reject_incident(incident_id: str, req: RejectRequest, db: AsyncSession = Depends(get_db)):
    if req.action not in {"MONITOR", "RESOURCE_CHECK", "AGENT_RESTART", "RESCHEDULE", "ESCALATE"}:
        raise HTTPException(400, f"invalid action: {req.action}")

    row = await db.execute(
        select(Incident).where(Incident.incident_id == incident_id)
    )
    inc = row.scalar_one_or_none()
    if not inc:
        raise HTTPException(404, "incident not found")

    inc.status = "REJECTED"
    inc.operator_action = req.action
    inc.is_correct = False

    # gold_data 저장
    db.add(GoldData(
        incident_id=incident_id,
        operator_action=req.action,
        recommended_action=inc.recommended_action,
        correction_note=req.note,
    ))

    # gold scenario 추가 (upsert_scenario 내부에서 commit 포함)
    vecs = await embed_texts(db, [inc.drain_template + " " + inc.raw_log])
    await upsert_scenario(
        db,
        scenario_id=f"GOLD-{inc.incident_id}",
        vector=vecs[0],
        action=req.action,
        log_text=inc.raw_log,
        drain_template=inc.drain_template,
        is_gold=True,
        is_test=False,
        source="gold",
    )
    return {"status": "rejected", "gold_action": req.action, "incident_id": incident_id}


# ── Drain3 clusters ──────────────────────────────────────────────────

@app.get("/api/drain/clusters")
async def drain_clusters():
    from analyzer.drain_miner import _get_miner
    miner = _get_miner()
    clusters = []
    for cid, cluster in miner.drain.id_to_cluster.items():
        clusters.append({
            "cluster_id": cid,
            "template": " ".join(cluster.log_template_tokens),
            "size": cluster.size,
        })
    clusters.sort(key=lambda c: c["size"], reverse=True)
    return {"count": len(clusters), "clusters": clusters}


# ── Stats ────────────────────────────────────────────────────────────

@app.get("/api/stats")
async def stats(db: AsyncSession = Depends(get_db)):
    total_inc = (await db.execute(text("SELECT COUNT(*) FROM incidents"))).scalar() or 0
    total_scn = (await db.execute(text("SELECT COUNT(*) FROM scenarios"))).scalar() or 0
    gold_count = (await db.execute(
        text("SELECT COUNT(*) FROM scenarios WHERE is_gold = true")
    )).scalar() or 0
    normal_count = total_scn - gold_count

    latest_eval = (await db.execute(
        select(EvalResult).order_by(EvalResult.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    latest_rate = latest_eval.match_rate if latest_eval else None

    return {
        "total_incidents": total_inc,
        "total_scenarios": total_scn,
        "gold_count": gold_count,
        "normal_count": normal_count,
        "gold_ratio": round(gold_count / total_scn * 100, 1) if total_scn else 0,
        "latest_match_rate": latest_rate,
    }


# ── Eval ─────────────────────────────────────────────────────────────

@app.post("/api/gold/generate", status_code=202)
async def generate_gold(per_scenario: int = 30):
    """합성 골드 데이터 생성 — hard/medium 시나리오 LLM paraphrase."""
    from pipeline.synth_gold import generate as _gen_gold

    _gold_status["running"] = True
    _gold_status["error"] = None
    _gold_status["count"] = 0

    async def _run():
        try:
            rows = await _gen_gold(n_per=per_scenario)
            _gold_status["count"] = len(rows)
            logger.info("Gold 데이터 생성 완료: %d건", len(rows))
        except Exception as e:
            _gold_status["error"] = str(e)
            logger.error("Gold 생성 실패: %s", e, exc_info=True)
        finally:
            _gold_status["running"] = False

    task = asyncio.create_task(_run())
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return {"status": "started", "per_scenario": per_scenario,
            "message": f"백그라운드 생성 중 — 완료 후 gold_pool.json 저장"}


@app.get("/api/gold/status")
async def gold_status(db: AsyncSession = Depends(get_db)):
    import pathlib, json as _json
    p = pathlib.Path("data/openstack/gold_pool.json")
    file_count = len(_json.loads(p.read_text(encoding="utf-8"))) if p.exists() else 0

    db_count = (await db.execute(
        text("SELECT COUNT(*) FROM scenarios WHERE source = 'synth_gold'")
    )).scalar() or 0

    total = file_count or db_count
    return {
        "exists": total > 0,
        "count": total,
        "file_count": file_count,
        "db_count": int(db_count),
        "running": _gold_status["running"],
        "error": _gold_status["error"],
    }


@app.post("/api/eval/run", status_code=202)
async def start_eval(req: EvalRunRequest):
    from evaluation.experiment import run_experiment
    import uuid

    steps = []
    for tc in req.train_steps:
        steps.append((tc, 0))
    for gc in req.gold_steps:
        if gc > 0:
            steps.append((req.train_steps[-1], gc))

    run_id = f"RUN-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"

    async def _run():
        try:
            await run_experiment(steps=steps, run_id=run_id)
        except Exception as e:
            logger.error("Experiment %s failed: %s", run_id, e, exc_info=True)

    task = asyncio.create_task(_run())
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)

    return {"run_id": run_id, "status": "started", "steps": steps}


@app.get("/api/eval/curve")
async def eval_curve(run_id: str | None = None, db: AsyncSession = Depends(get_db)):
    if not run_id:
        # run_id 미지정 시 최신 run 자동 선택
        latest = (await db.execute(
            select(EvalResult).order_by(EvalResult.created_at.desc()).limit(1)
        )).scalar_one_or_none()
        if latest:
            run_id = latest.run_id

    q = select(EvalResult).order_by(EvalResult.step_index)
    if run_id:
        q = q.where(EvalResult.run_id == run_id)
    rows = (await db.execute(q)).scalars().all()

    majority_baseline = 35  # MONITOR 다수 클래스 추정 기준선

    return {
        "run_id": rows[0].run_id if rows else run_id,
        "majority_baseline": majority_baseline,
        "points": [
            {
                "step": r.step_index,
                "label": f"{r.training_count}/{r.gold_count}",
                "training_count": r.training_count,
                "gold_count": r.gold_count,
                "match_rate": r.match_rate,
                "correct": r.correct,
                "total": r.total,
                "detail": r.detail,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


@app.get("/api/eval/runs")
async def eval_runs(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        text("""
            SELECT run_id,
                   COUNT(*) AS steps_done,
                   MIN(match_rate) AS min_rate,
                   MAX(match_rate) AS max_rate,
                   MAX(match_rate) - MIN(match_rate) AS lift,
                   MAX(created_at) AS last_updated
            FROM eval_results
            GROUP BY run_id
            ORDER BY MAX(created_at) DESC
            LIMIT 20
        """)
    )).fetchall()
    return {"runs": [
        {
            "run_id": r.run_id,
            "steps_done": r.steps_done,
            "min_rate": r.min_rate,
            "max_rate": r.max_rate,
            "lift": r.lift,
            "last_updated": r.last_updated.isoformat() if r.last_updated else None,
        }
        for r in rows
    ]}


# ── Demo auto ────────────────────────────────────────────────────────

@app.post("/api/demo/auto")
async def demo_auto(req: AutoDemoRequest, db: AsyncSession = Depends(get_db)):
    """대기 중 인시던트를 신뢰도 임계값 기준으로 자동 처리 시뮬레이션."""
    rows = (await db.execute(
        select(Incident)
        .where(Incident.status == "WAIT_APPROVAL")
        .order_by(Incident.created_at.desc())
        .limit(100)
    )).scalars().all()

    auto_count = 0
    hold_count = 0
    for inc in rows:
        if inc.confidence >= req.threshold:
            inc.status = "AUTO"
            auto_count += 1
        else:
            hold_count += 1

    await db.commit()
    return {
        "threshold": req.threshold,
        "auto_processed": auto_count,
        "held_for_review": hold_count,
        "total_checked": len(rows),
    }


# ── Scenarios ────────────────────────────────────────────────────────

@app.get("/api/scenarios/list")
async def list_scenarios(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(Scenario)
        .where(Scenario.source == "normal", Scenario.is_gold == False, Scenario.is_test == False)
        .where(Scenario.scenario_id.like("SCN-%"))
        .order_by(Scenario.scenario_id)
    )).scalars().all()
    return {"scenarios": [
        {
            "scenario_id": s.scenario_id,
            "action": s.action,
            "difficulty": s.difficulty,
            "description": s.description,
            "log_text": s.log_text,
            "alt_logs": s.alt_logs or [],
        }
        for s in rows
    ]}


class AltLogsRequest(BaseModel):
    alt_logs: list[str]


@app.put("/api/scenarios/{scenario_id}/alt_logs")
async def update_alt_logs(scenario_id: str, req: AltLogsRequest, db: AsyncSession = Depends(get_db)):
    row = (await db.execute(
        select(Scenario).where(Scenario.scenario_id == scenario_id)
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(404, f"scenario {scenario_id} not found")
    cleaned = [l.strip() for l in req.alt_logs if l.strip()]
    row.alt_logs = cleaned
    await db.commit()
    return {"scenario_id": scenario_id, "alt_logs": cleaned}


@app.post("/api/scenarios/{scenario_id}/generate_alt_logs")
async def generate_alt_logs(scenario_id: str, db: AsyncSession = Depends(get_db)):
    row = (await db.execute(
        select(Scenario).where(Scenario.scenario_id == scenario_id)
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(404, f"scenario {scenario_id} not found")

    from analyzer.llm_client import analyze
    system = "You are an OpenStack log generator. Output JSON array only, no explanation."
    prompt = (
        f"OpenStack NMS log scenario: {row.description}\n"
        f"Action required: {row.action}\n"
        f"Reference log (DO NOT reuse any words from this): {row.log_text}\n\n"
        f"Generate 4 English OpenStack log messages for the same failure scenario.\n"
        f"STRICT RULES:\n"
        f"- Use completely different vocabulary from the reference log\n"
        f"- Use different log format (syslog, journald, python logging, etc.)\n"
        f"- Include realistic details: timestamps, UUIDs, IP addresses, port numbers\n"
        f"- Each message must look like it came from a different OpenStack component or node\n"
        f"Output JSON array only. Example: [\"log1\", \"log2\", \"log3\", \"log4\"]"
    )
    raw, _ = await analyze(db, system, prompt)
    import re as _re
    m = _re.search(r'\[.*\]', raw, _re.DOTALL)
    if not m:
        raise HTTPException(500, f"LLM 파싱 실패: {raw[:200]}")
    try:
        alt = json.loads(m.group())
        alt = [str(x).strip() for x in alt if str(x).strip()][:5]
    except Exception:
        raise HTTPException(500, f"JSON 파싱 실패: {raw[:200]}")

    row.alt_logs = alt
    await db.commit()
    return {"scenario_id": scenario_id, "alt_logs": alt}


# ── Admin settings ───────────────────────────────────────────────────

@app.get("/api/admin/settings")
async def get_settings(db: AsyncSession = Depends(get_db)):
    s = (await db.execute(select(LLMSettings).limit(1))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "설정 없음")
    return {
        "active_backend": s.active_backend,
        "dgx_url": s.dgx_url,
        "dgx_model": s.dgx_model,
        "dgx_embed_model": s.dgx_embed_model,
        "claude_model": s.claude_model,
        "system_prompt": s.system_prompt,
        "has_claude_key": bool(s.claude_api_key),
        "has_dgx_key": bool(s.dgx_api_key),
    }


@app.post("/api/admin/settings")
async def update_settings(req: AdminSettingsRequest, db: AsyncSession = Depends(get_db)):
    s = (await db.execute(select(LLMSettings).limit(1))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "설정 없음")
    key_fields = {"dgx_api_key", "claude_api_key"}
    for field, val in req.model_dump().items():
        if not hasattr(s, field):
            continue
        if field in key_fields and not val:
            continue  # 빈 문자열로 기존 키 덮어쓰기 방지
        setattr(s, field, val)
    await db.commit()
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
