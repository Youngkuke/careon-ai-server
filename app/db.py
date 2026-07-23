"""PostgreSQL 접근 (asyncpg, 원시 SQL).

DATABASE_URL이 비어 있으면 DB 없이 동작한다 (조회는 None/빈 리스트 반환).
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import asyncpg

from app.config import settings

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None

# DB의 timestamp 컬럼이 전부 'without time zone'이라 오프셋 없는 문자열이 나간다
# ("2026-07-21T14:35:53"). 프론트가 브라우저 로컬로 파싱하면 어긋나므로 여기서 붙여준다.
# 컬럼 타입(timestamptz) 변경은 Spring 엔티티까지 걸려서 응답 직렬화로만 해결한다.
#
# ⚠️ naive 값을 'KST 벽시계'로 해석한다 (UTC가 아니다).
#    policies.application_deadline이 공고 원문의 한국시간을 그대로 담고 있기 때문이다.
#    예: deadline_date_raw "2026.5.26. 18:00" → application_deadline 2026-05-26 18:00:00
#    이걸 UTC로 보면 마감이 9시간 밀린다.
# 그래서 우리가 쓰는 시각도 NOW_KST로 KST 벽시계를 저장해 규칙을 하나로 맞춘다.
# (한국은 서머타임이 없어서 고정 +09:00으로 충분하다.)
KST = timezone(timedelta(hours=9))

# INSERT/UPDATE에서 now() 대신 쓴다. UTC가 아니라 KST 벽시계를 저장한다.
NOW_KST = "(now() AT TIME ZONE 'Asia/Seoul')"


def _with_timezone(value: Any) -> Any:
    """naive datetime에 KST 오프셋을 붙인다 (시각은 그대로). 그 외 값은 건드리지 않는다."""
    if not isinstance(value, datetime):
        return value
    if value.tzinfo is None:
        return value.replace(tzinfo=KST)
    return value.astimezone(KST)


async def connect() -> None:
    global _pool
    if not settings.database_url:
        logger.warning("DATABASE_URL이 없습니다. DB 조회 없이 동작합니다.")
        return
    _pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=5)
    logger.info("PostgreSQL 연결 완료")


async def disconnect() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def available() -> bool:
    return _pool is not None


async def fetch(query: str, *args: Any) -> List[Dict[str, Any]]:
    if _pool is None:
        return []
    async with _pool.acquire() as conn:
        rows = await conn.fetch(query, *args)
    return [{k: _with_timezone(v) for k, v in r.items()} for r in rows]


async def fetchrow(query: str, *args: Any) -> Optional[Dict[str, Any]]:
    rows = await fetch(query, *args)
    return rows[0] if rows else None


async def execute(query: str, *args: Any) -> None:
    if _pool is None:
        return
    async with _pool.acquire() as conn:
        await conn.execute(query, *args)


# --- 조회 헬퍼 ---------------------------------------------------------------
async def get_carer(carer_id: int) -> Optional[Dict[str, Any]]:
    return await fetchrow("SELECT * FROM carers WHERE carer_id = $1", carer_id)


async def get_cared(carer_id: int) -> List[Dict[str, Any]]:
    return await fetch(
        "SELECT cared_id, cared_relation, age, condition_summary, severity_level "
        "FROM cared WHERE carer_id = $1 ORDER BY cared_id",
        carer_id,
    )
