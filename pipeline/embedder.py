"""임베딩 제공자 — 런타임 중 전환 금지 (차원 불일치 방지).
EMBED_BACKEND=local  → sentence-transformers (all-MiniLM-L6-v2, dim=384)
EMBED_BACKEND=dgx    → DGX /v1/embeddings (dim은 probe로 확정)
"""
import os
import logging

logger = logging.getLogger(__name__)

_local_model = None
EMBED_BACKEND = os.environ.get("EMBED_BACKEND", "local")


def get_local_model():
    global _local_model
    if _local_model is None:
        from sentence_transformers import SentenceTransformer
        _local_model = SentenceTransformer("all-MiniLM-L6-v2")
        logger.info("SentenceTransformer loaded (all-MiniLM-L6-v2, dim=384)")
    return _local_model


async def embed_texts_direct(texts: list[str]) -> list[list[float]]:
    """DB 세션 없는 직접 임베딩 (build_dataset, experiment 용)."""
    if EMBED_BACKEND == "dgx":
        from openai import AsyncOpenAI
        import os as _os
        client = AsyncOpenAI(
            base_url=f"{_os.environ.get('DGX_URL','').rstrip('/')}/v1",
            api_key=_os.environ.get("DGX_API_KEY", "dummy"),
            timeout=30.0,
        )
        try:
            resp = await client.embeddings.create(
                model=_os.environ.get("DGX_EMBED_MODEL", "Qwen2.5-14B"),
                input=texts,
            )
            return [d.embedding for d in resp.data]
        except Exception as e:
            logger.warning("DGX embed 직접 호출 실패, local fallback: %s", e)

    # local fallback
    model = get_local_model()
    vecs = model.encode(texts, normalize_embeddings=True)
    return [v.tolist() for v in vecs]


async def probe_dim() -> int:
    """서버 시작 시 1회 probe → EMBED_DIM 확정."""
    vecs = await embed_texts_direct(["probe"])
    dim = len(vecs[0])
    logger.info("EMBED_DIM confirmed: %d (backend=%s)", dim, EMBED_BACKEND)
    return dim
