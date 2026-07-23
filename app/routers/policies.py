"""제도 조회 API (API_SPEC 5. 제도 상세 조회).

매칭 응답은 payload를 가볍게 유지하려고 policy_id + 판단 근거만 싣는다.
카드/상세 화면에 필요한 나머지 필드는 프론트가 여기로 따로 가져간다.
"""
import logging
from typing import List

from fastapi import APIRouter, Depends, Query

from app import db
from app.auth import get_current_carer_id
from app.errors import ApiError, DatabaseUnavailable, PolicyNotFound
from app.schemas import PolicyDetail, PolicyListResponse, PolicyTranslateResponse
from app.services import policies, policy_translate

logger = logging.getLogger(__name__)
# 제도 조회는 carer별로 결과가 달라지지 않아서 carer_id 값 자체는 쓰지 않는다.
# 다만 명세상 /api/v1/*는 전부 인증이 필요하므로 라우터 단위로 토큰만 검증한다.
router = APIRouter(
    prefix="/api/v1/policies",
    tags=["policies"],
    dependencies=[Depends(get_current_carer_id)],
)


def _parse_ids(raw: str) -> List[int]:
    """"2,7,16" → [2, 7, 16]. 중복은 제거하고 요청 순서는 유지한다."""
    ids: List[int] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            value = int(token)
        except ValueError:
            raise ApiError("ids는 쉼표로 구분한 정수 목록이어야 합니다: {!r}".format(token))
        if value not in ids:
            ids.append(value)
    if not ids:
        raise ApiError("ids가 비어 있습니다. 예: /api/v1/policies?ids=2,7,16")
    return ids


@router.get("", response_model=PolicyListResponse)
async def list_policies(
    ids: str = Query(..., description="쉼표로 구분한 policy_id 목록 (예: 2,7,16)")
) -> dict:
    """API_SPEC 5. 배치 조회 — 매칭 결과 카드 여러 개를 한 번에 그릴 때 (N+1 방지)."""
    if not db.available():
        raise DatabaseUnavailable()

    policy_ids = _parse_ids(ids)
    rows = await policies.fetch_policies(policy_ids)
    found = {row["policy_id"]: row for row in rows}

    missing = [pid for pid in policy_ids if pid not in found]
    if missing and not found:
        # 전부 없으면 요청 자체가 잘못된 것으로 보고 에러를 낸다
        raise PolicyNotFound(
            "존재하지 않는 policy_id입니다: {}".format(
                ", ".join(str(pid) for pid in missing)
            )
        )
    if missing:
        # 일부만 없으면 나머지 카드까지 못 그리게 되므로 경고만 남기고 진행한다
        logger.warning("배치 조회에 없는 policy_id %s (응답에서 제외)", missing)

    # 요청 순서대로 돌려준다 (프론트가 매칭 결과 순서를 그대로 쓸 수 있게)
    return {
        "policies": [policies.to_detail(found[pid]) for pid in policy_ids if pid in found]
    }


@router.get("/{policy_id}", response_model=PolicyDetail)
async def get_policy(policy_id: int) -> dict:
    """API_SPEC 5. 단건 상세 조회 — 매칭 결과 카드 클릭 / 저장한 제도 목록."""
    if not db.available():
        raise DatabaseUnavailable()

    rows = await policies.fetch_policies([policy_id])
    if not rows:
        logger.info("존재하지 않는 policy_id=%s", policy_id)
        raise PolicyNotFound()  # 메시지는 화면에 그대로 뜨므로 policy_id를 넣지 않는다
    return policies.to_detail(rows[0])


@router.post("/{policy_id}/translate", response_model=PolicyTranslateResponse)
async def translate_policy(policy_id: int) -> dict:
    """제도 번역기 — 제도 조건을 이해하기 쉬운 말로 풀어준다.

    1차 버전은 파라미터 없이 policy_id만 받는다.
    (질문/서류이력 등은 body를 추가하는 형태로 나중에 붙인다.)
    """
    if not db.available():
        raise DatabaseUnavailable()

    row = await policy_translate.fetch_policy(policy_id)
    if row is None:
        logger.info("존재하지 않는 policy_id=%s", policy_id)
        raise PolicyNotFound()  # 메시지는 화면에 그대로 뜨므로 policy_id를 넣지 않는다

    return {
        "policy_id": policy_id,
        "explanation": await policy_translate.translate(row),
    }
