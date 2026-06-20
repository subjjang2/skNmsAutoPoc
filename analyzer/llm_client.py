"""DGX(OpenAI-compatible) / Claude API 분기 + 자동 폴백.
NMS 추가: recommend() → (action, confidence, reason, backend)
          embed_texts() → list[list[float]]
"""
import json
import logging
from openai import AsyncOpenAI
import anthropic
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from db.models import LLMSettings

logger = logging.getLogger(__name__)

ACTIONS = {
    "MONITOR", "INVESTIGATE",
    "AGENT_RESTART", "DEPENDENCY_RESTART",
    "RESOURCE_CHECK", "QUOTA_ADJUST",
    "RESCHEDULE",
    "STORAGE_ESCALATE", "SECURITY_ESCALATE", "ESCALATE",
}

_SYSTEM_RECOMMEND = """You are an OpenStack infrastructure operations expert.
Given a log line and similar past cases, choose exactly ONE action from the 10 below.

- MONITOR: transient blip, auto-recovers (retries, will-retry). Watch only.
- INVESTIGATE: cause unclear, no auto-recovery. Active log/metric analysis needed (latency, flapping, degraded).
- AGENT_RESTART: OpenStack service agent unresponsive/crashed (nova-compute, neutron/cinder/glance-agent, keystone, dhcp, designate).
- DEPENDENCY_RESTART: underlying dependency down (libvirt, rabbitmq/oslo.messaging, hypervisor, vnc/console proxy).
- RESOURCE_CHECK: physical resource shortage on host (RAM, vcpu, disk, OOM, CPU throttling).
- QUOTA_ADJUST: tenant/project quota or limit exceeded (not physical shortage).
- RESCHEDULE: instance/node placement failed; move to another host (affinity, live-migration, ironic, magnum).
- STORAGE_ESCALATE: storage-layer integrity/availability failure → storage team (Ceph, Swift, Glance corruption, data loss).
- SECURITY_ESCALATE: security/auth incident → security team (repeated auth failure, intrusion, SSL/TLS, unauthorized root).
- ESCALATE: severe general outage → on-call (DB corrupt, kernel panic, multiple hosts down, SLA/P1 breach).

Use similar past cases as strong hints. Prefer the most specific action.

Reply with a single JSON line only:
{"action":"<one of 10>","confidence":<0-100>,"reason":"<one line>"}"""


async def _get_settings(db: AsyncSession) -> LLMSettings:
    result = await db.execute(select(LLMSettings).limit(1))
    s = result.scalar_one_or_none()
    if not s:
        raise RuntimeError("LLM 설정 없음. /admin 에서 먼저 설정하세요.")
    return s


async def _call_dgx(s: LLMSettings, system_prompt: str, user_prompt: str,
                    max_tokens: int = 512) -> str:
    client = AsyncOpenAI(
        base_url=f"{s.dgx_url.rstrip('/')}/v1",
        api_key=s.dgx_api_key or "dummy",
        timeout=60.0,
    )
    resp = await client.chat.completions.create(
        model=s.dgx_model or "Qwen2.5-14B",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=max_tokens,
        temperature=0,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    content = resp.choices[0].message.content or ""
    if not content.strip():
        raise RuntimeError("DGX 응답 비어있음")
    return content.strip()


async def _call_claude(s: LLMSettings, system_prompt: str, user_prompt: str,
                       max_tokens: int = 512) -> str:
    client = anthropic.AsyncAnthropic(api_key=s.claude_api_key)
    resp = await client.messages.create(
        model=s.claude_model or "claude-sonnet-4-6",
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return resp.content[0].text.strip()


async def analyze(db: AsyncSession, system_prompt: str, user_prompt: str,
                  max_tokens: int = 512) -> tuple[str, str]:
    """범용 LLM 호출. (result_text, used_backend) 반환."""
    s = await _get_settings(db)
    if s.active_backend == "claude":
        result = await _call_claude(s, system_prompt, user_prompt, max_tokens=max_tokens)
        return result, "claude"

    logger.info("DGX 호출 (url=%s model=%s)", s.dgx_url, s.dgx_model)
    try:
        result = await _call_dgx(s, system_prompt, user_prompt, max_tokens=max_tokens)
        return result, "dgx"
    except Exception as dgx_err:
        logger.error("DGX 오류: %s", dgx_err, exc_info=True)
        if s.claude_api_key:
            logger.info("Claude API로 자동 폴백")
            result = await _call_claude(s, system_prompt, user_prompt, max_tokens=max_tokens)
            return result, "claude(fallback)"
        raise RuntimeError(f"DGX 오류 + Claude 키 미설정: {dgx_err}") from dgx_err


def _build_user_prompt(raw_log: str, drain_template: str, similar: list[dict]) -> str:
    lines = [f"[Current Log]\n{raw_log}", f"\n[Drain3 Pattern]\n{drain_template}"]
    if similar:
        lines.append("\n[Similar Past Cases (cosine, ranked)]")
        for i, c in enumerate(similar[:5], 1):
            lines.append(
                f"{i}. ({c.get('score', 0):.2f}) action={c.get('action','')} :: {c.get('log_text','')}"
            )
    return "\n".join(lines)


async def recommend(
    db: AsyncSession,
    raw_log: str,
    drain_template: str,
    similar_cases: list[dict],
) -> tuple[str, int, str, str]:
    """추천 반환. (action, confidence, reason, backend_used)."""
    user_prompt = _build_user_prompt(raw_log, drain_template, similar_cases)
    raw, backend = await analyze(db, _SYSTEM_RECOMMEND, user_prompt)

    # JSON 파싱 — 실패 시 top-1 유사 케이스 fallback
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        parsed = json.loads(raw[start:end])
        action = parsed.get("action", "").upper()
        if action not in ACTIONS:
            raise ValueError(f"invalid action: {action}")
        confidence = int(parsed.get("confidence", 50))
        reason = str(parsed.get("reason", ""))
        return action, confidence, reason, backend
    except Exception as e:
        logger.warning("JSON 파싱 실패 (%s). fallback → top-1 유사 사례", e)
        fallback_action = similar_cases[0]["action"] if similar_cases else "MONITOR"
        return fallback_action, 40, f"LLM parse error — fallback from similar case. raw={raw[:100]}", backend


async def embed_texts(db: AsyncSession, texts: list[str]) -> list[list[float]]:
    """텍스트 임베딩. EMBED_BACKEND=dgx → DGX, local → sentence-transformers."""
    from pipeline.embedder import EMBED_BACKEND
    s = await _get_settings(db)
    if EMBED_BACKEND == "dgx" and s.dgx_url:
        try:
            client = AsyncOpenAI(
                base_url=f"{s.dgx_url.rstrip('/')}/v1",
                api_key=s.dgx_api_key or "dummy",
                timeout=30.0,
            )
            resp = await client.embeddings.create(
                model=s.dgx_embed_model or "Qwen2.5-14B",
                input=texts,
            )
            return [d.embedding for d in resp.data]
        except Exception as e:
            logger.warning("DGX embed 실패, sentence-transformers fallback: %s", e)

    return _local_embed(texts)


def _local_embed(texts: list[str]) -> list[list[float]]:
    from pipeline.embedder import get_local_model
    model = get_local_model()
    vecs = model.encode(texts, normalize_embeddings=True)
    return [v.tolist() for v in vecs]
