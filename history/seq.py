from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

KST = ZoneInfo("Asia/Seoul")


async def next_seq_id(db: AsyncSession, prefix: str) -> str:
    """KST 기준 일자별 리셋 시퀀스 ID. 예: INC-20260619-000001"""
    today_kst = datetime.now(KST).strftime("%Y%m%d")
    counter_key = f"{prefix}-{today_kst}"
    row = await db.execute(
        text(
            "INSERT INTO counters(name, value) VALUES (:n, 1) "
            "ON CONFLICT(name) DO UPDATE SET value = counters.value + 1 "
            "RETURNING value"
        ),
        {"n": counter_key},
    )
    seq_val = row.scalar_one()
    await db.commit()
    return f"{prefix}-{today_kst}-{seq_val:06d}"
