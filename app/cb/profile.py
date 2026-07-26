"""cb_user_profile — 유저 지역 조회.

지역은 대화로 묻지 않는다. 회원가입 때 받은 값을 쓴다.

  회원가입 → public.carers.region
                ↓ (cb_user_profile 행이 없을 때만 1회 복사)
          cb.cb_user_profile.region_sgg
                ↓
     constants.region_keys_for_user() → ['national', 'metro', '은평구']

public.carers를 읽는 것은 cb 격리 원칙의 **유일한 예외**다 (2026-07-26 승인,
001_cb_schema.sql의 region_sgg 컬럼 주석에 기록되어 있다). 행이 한 번 생기면
다시 읽지 않는다. FK도 JOIN도 걸지 않고, 이 함수 안에서만 명시적으로 한정해서
조회한다.
"""
import logging
from typing import Optional

from app.cb import constants, db

logger = logging.getLogger(__name__)


def normalize_sgg(raw: Optional[str]) -> Optional[str]:
    """자치구 이름만 남긴다. 25개 화이트리스트에 없으면 None.

    운영 데이터는 '강동구'처럼 자치구 단문으로 들어 있지만, 프론트가
    '서울특별시 은평구' 형태로 저장하기 시작해도 깨지지 않게 토큰으로도 훑는다.
    모르는 값은 None으로 둔다 — 틀린 자치구로 검색하면 남의 동네 제도가 나온다.
    """
    text = (raw or "").strip()
    if not text:
        return None
    if text in constants.SEOUL_GU:
        return text
    for token in text.split():
        if token in constants.SEOUL_GU:
            return token
    logger.info("[profile] 알 수 없는 지역 표기라 지역 없이 진행한다: %r", text)
    return None


async def region_sgg(user_id: int) -> Optional[str]:
    """유저의 거주 자치구. 없으면 None (전국+서울시 제도만 검색된다).

    cb_user_profile에 행이 없을 때만 carers를 읽고, 읽은 값을 복사해 둔다.
    복사에 실패해도 이번 대화는 계속되어야 하므로 값은 그대로 돌려준다.
    """
    pool = await db.connect()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT region_sgg FROM cb.cb_user_profile WHERE user_id = $1", user_id
        )
        if row is not None:
            return normalize_sgg(row["region_sgg"])

        # 여기서만 public을 읽는다.
        carer = await conn.fetchrow(
            "SELECT region FROM public.carers WHERE carer_id = $1", user_id
        )
        sgg = normalize_sgg(carer["region"] if carer else None)

        # 자치구를 못 알아내도 행은 만든다. 안 만들면 대화할 때마다
        # carers를 다시 읽게 되어 '1회 복사'가 아니게 된다.
        await conn.execute(
            "INSERT INTO cb.cb_user_profile (user_id, region_sgg, region_copied_at) "
            "VALUES ($1, $2, now()) ON CONFLICT (user_id) DO NOTHING",
            user_id, sgg,
        )
        logger.info("[profile] 지역 1회 복사 user=%s → %s", user_id, sgg or "(없음)")
        return sgg


async def mark_ready(user_id: int, thread_id: str) -> None:
    """결과까지 마친 대화를 기록한다.

    새로고침이나 재로그인 뒤에 결과 화면으로 돌아갈 수 있게 하는 유일한 단서다.
    기록에 실패해도 이번 대화는 정상이므로 호출부에서 삼킨다.
    """
    pool = await db.connect()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO cb.cb_user_profile (user_id, last_ready_thread_id, last_ready_at) "
            "VALUES ($1, $2, now()) "
            "ON CONFLICT (user_id) DO UPDATE "
            "SET last_ready_thread_id = EXCLUDED.last_ready_thread_id, "
            "    last_ready_at = EXCLUDED.last_ready_at",
            user_id, thread_id,
        )


async def last_ready_thread(user_id: int) -> Optional[str]:
    """마지막으로 결과까지 마친 대화의 thread_id. 없으면 None."""
    pool = await db.connect()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT last_ready_thread_id FROM cb.cb_user_profile WHERE user_id = $1",
            user_id,
        )
    return (row["last_ready_thread_id"] if row else None) or None
