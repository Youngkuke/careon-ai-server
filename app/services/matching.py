"""매칭 프롬프트 실행 (matching_prompt.md).

제도 57건 전체를 컨텍스트에 넣고 LLM이 프로필과 하나씩 대조한다
(문서 '왜 이런 구조로 매칭하는가': 이 규모에서는 벡터검색/SQL필터보다 정확).
카탈로그는 모든 사용자에게 동일하므로 prompt caching을 건다.
"""
import json
import logging
from datetime import date
from typing import Any, Dict, List, Optional, Set

from app import db, llm, prompts
from app.config import settings
from app.profile import Profile
from app.services import policies

logger = logging.getLogger(__name__)

# 응답(명세)의 한글 그룹명
GROUP_FIT = "적합"
GROUP_UNKNOWN = "확인_불가"
GROUP_UNFIT = "부적합"

# tool input_schema의 property key는 ^[a-zA-Z0-9_.-]{1,64}$ 만 허용돼서
# 한글 키를 쓸 수 없다. 내부는 영문 키로 받고 응답에서 한글로 바꾼다.
TOOL_FIT = "fit"
TOOL_UNKNOWN = "needs_confirmation"
TOOL_UNFIT = "not_eligible"

TOOL_TO_GROUP = {
    TOOL_FIT: GROUP_FIT,
    TOOL_UNKNOWN: GROUP_UNKNOWN,
    TOOL_UNFIT: GROUP_UNFIT,
}

# missing_field는 phase6가 그대로 받아 되물을 질문을 만든다.
# 서술문("소득 확인 필요")이 오면 질문을 만들 수 없으므로 프로필 필드명으로 제약한다.
MISSING_FIELDS = [
    "income_value",
    "housing_deposit",
    "housing_monthly_rent",
    "household_asset_value",
    "vehicle_value",
    "has_basic_livelihood_support",
    "has_cha_sang_wi",
    "employment_status",
    "isolation_level",
    "medical_burden_level",
    "housing_type",
    "military_service_status",
    "care_recipients",
    "other",
]

MATCHING_TOOL = {
    "name": "return_matches",
    "description": (
        "제도 목록 전체를 세 그룹으로 분류해서 반환한다. 모든 제도가 정확히 한 그룹에 들어가야 한다. "
        "fit=적합, needs_confirmation=확인_불가, not_eligible=부적합."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            TOOL_FIT: {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "policy_id": {"type": "integer"},
                        "name": {"type": "string"},
                        "reason": {
                            "type": "string",
                            "description": "왜 맞는지 — 충족한 조건들을 구체적으로 (나이, 소득, 지역, 돌봄대상 등)",
                        },
                        "caution": {
                            "type": ["string", "null"],
                            "description": (
                                "출처 불일치, 추정치 사용, 자부담금, 마감 임박, "
                                "다른 적합 제도와의 중복수혜불가 안내 등. 없으면 null"
                            ),
                        },
                    },
                    "required": ["policy_id", "name", "reason"],
                },
            },
            TOOL_UNKNOWN: {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "policy_id": {"type": "integer"},
                        "name": {"type": "string"},
                        "missing_field": {
                            "type": "string",
                            "enum": MISSING_FIELDS,
                            "description": (
                                "판단을 막고 있는 프로필 필드를 이 목록에서 정확히 하나 고른다. "
                                "이미 프로필에 값이 있는 필드는 고르면 안 된다 "
                                "(그 경우는 확인_불가가 아니라 적합/부적합으로 판단해야 한다). "
                                "목록에 없는 정보가 필요하면 other를 쓴다."
                            ),
                        },
                        "reason": {"type": "string"},
                    },
                    "required": ["policy_id", "name", "missing_field", "reason"],
                },
            },
            TOOL_UNFIT: {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "policy_id": {"type": "integer"},
                        "name": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["policy_id", "name", "reason"],
                },
            },
        },
        "required": [TOOL_FIT, TOOL_UNKNOWN, TOOL_UNFIT],
    },
}


# 실측에서 반복적으로 나온 규칙 위반을 막기 위한 판정 기준.
# (규칙 7 위반 4건 / 규칙 2 위반 4건 / "확실하지 않으면 추천 안 함" 위반 1건)
GROUP_DECISION_RULES = """## 그룹 판정 기준 (위 규칙을 적용할 때 반드시 지킬 것)

### 부적합에 넣어도 되는 유일한 경우
제도가 요구하는 조건 중 **하나라도 명확히 불충족**임이 프로필로 확인될 때만이다.
아래 사유로 부적합에 넣는 것은 규칙 7 위반이며 절대 금지한다.
- "사용자가 고른 관심 유형(selected_types)과 분야가 안 맞아서"
- "우선순위가 낮아 보여서" / "매칭성이 낮아서" / "실질적 대상이 아닌 것 같아서"
- "돌봄/의료와 무관한 제도라서"
자격증 응시료 지원, 청년수당, 문화패스처럼 돌봄과 무관해 보이는 제도도
나이·소득·지역 조건만 맞으면 반드시 적합 또는 확인_불가에 넣는다.
reason에 "조건은 충족하나 ~라서 제외"라고 쓰고 있다면 그건 부적합이 아니다.

### 확인_불가에 넣어야 하는 경우
판단에 필요한 값이 **프로필에 아예 없거나** 상태가 not_asked / unknown_needs_probe일 때만이다.
- income_value_status가 known 또는 estimated_from_signals이면 **반드시 비교해서**
  적합/부적합으로 판정한다. estimated_from_signals는 확인_불가 사유가 아니며,
  이 경우 caution에 "추정 소득 기준"이라고 명시하면 된다.
- financial_detail_status가 known이면 보증금·월세·재산 조건을 비교해서 판정한다.
- 프로필에 이미 값이 있는 필드(예: household_members가 2로 확인됨)를
  missing_field로 지목하면 안 된다.

### 적합에 넣어도 되는 경우
조건 충족이 확인된 경우만이다. reason이나 caution에
"해당하지 않을 수 있어", "자격 여부 확인 필요" 같은 표현을 쓰게 된다면
그건 적합이 아니라 확인_불가다. (마감 임박·자부담·출처 불일치 caution은 예외 — 규칙 8~10)

### reason 길이
적합·확인_불가의 reason은 충족/미확인 근거를 구체적으로 쓴다.
부적합의 reason은 불충족 조건 하나만 짧게 쓴다 (예: "대상 연령 21~23세 초과").
"""


# 프론트 화면에 노출하는 그룹 (부적합은 안 보여주므로 제외)
VISIBLE_GROUPS = (GROUP_FIT, GROUP_UNKNOWN)


def to_match_items(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """내부 매칭 결과 → 프론트 응답용 목록.

    내부 결과에는 reason/caution/부적합이 다 들어 있지만, 화면에는 제도 목록만
    뿌리므로 policy_id + match_group으로 줄인다. 적합을 먼저 정렬한다.
    """
    return [
        {"policy_id": item["policy_id"], "match_group": group}
        for group in VISIBLE_GROUPS
        for item in result.get(group) or []
    ]


async def save_match_result(
    carer_id: int, items: List[Dict[str, Any]]
) -> Dict[int, int]:
    """매칭 결과를 matched_policy에 저장하고 {policy_id: matched_policy_id}를 돌려준다.

    ⚠️ match_group 컬럼이 DB에 생기기 전에는 호출하면 안 된다.
    호출 여부는 settings.match_persist_enabled가 통제한다 (app/config.py 참고).

    이미 있는 행은 UPDATE, 없는 행만 INSERT한다. was_benefited는 사용자가 직접 입력한
    값이라(PATCH matched-policies), 재매칭 때 DELETE로 날리면 안 되기 때문이다.
    """
    if not items:
        return {}

    existing = {
        row["policy_id"]: row["matched_policy_id"]
        for row in await db.fetch(
            "SELECT matched_policy_id, policy_id FROM matched_policy WHERE carer_id = $1",
            carer_id,
        )
    }

    saved: Dict[int, int] = {}
    for item in items:
        policy_id = item["policy_id"]
        group = item["match_group"]
        matched_policy_id = existing.get(policy_id)
        if matched_policy_id is not None:
            await db.execute(
                "UPDATE matched_policy SET match_group = $1, updated_at = now() "
                "WHERE matched_policy_id = $2",
                group,
                matched_policy_id,
            )
        else:
            row = await db.fetchrow(
                "INSERT INTO matched_policy (carer_id, policy_id, match_group) "
                "VALUES ($1, $2, $3) RETURNING matched_policy_id",
                carer_id,
                policy_id,
                group,
            )
            matched_policy_id = (row or {}).get("matched_policy_id")
        saved[policy_id] = matched_policy_id

    # 매칭까지 끝나면 2단계 진단 완료 (api.md '매칭 실행')
    await db.execute(
        "UPDATE carers SET diagnosis_completed = TRUE, updated_at = now() "
        "WHERE carer_id = $1",
        carer_id,
    )
    logger.info("매칭 결과 저장: carer_id=%s %d건", carer_id, len(saved))
    return saved


async def current_benefit_policy_ids(carer_id: int) -> List[int]:
    """매칭 규칙 5: 이미 받고 있는 제도 (matched_policy.was_benefited)."""
    rows = await db.fetch(
        "SELECT policy_id FROM matched_policy WHERE carer_id = $1 AND was_benefited IS TRUE",
        carer_id,
    )
    return [r["policy_id"] for r in rows]


def build_system(catalog_text: str) -> List[Dict[str, Any]]:
    """system 블록. 카탈로그는 전 사용자 공통이라 캐시 대상으로 표시한다."""
    rules = prompts.matching_doc().render(exclude_prefixes=("출력 형식",))
    return [
        {
            "type": "text",
            "text": (
                "당신은 가족돌봄청년 복지제도 매칭 엔진입니다.\n"
                "아래 매칭 규칙을 그대로 적용해서, 주어진 제도 목록의 모든 제도를 "
                "적합 / 확인_불가 / 부적합 세 그룹으로 빠짐없이 분류하세요.\n"
                "결과는 return_matches 도구로 반환하며, 그룹명은 다음과 같이 대응합니다: "
                "적합=fit, 확인_불가=needs_confirmation, 부적합=not_eligible.\n"
                "reason과 caution은 한국어로 작성하세요.\n\n"
                + rules
                + "\n\n"
                + GROUP_DECISION_RULES
            ),
        },
        {
            "type": "text",
            "text": "[제도 목록]\n\n" + catalog_text,
            "cache_control": {"type": "ephemeral"},
        },
    ]


def build_user_message(
    profile: Profile,
    current_benefits: List[int],
    target_ids: List[int],
    today: Optional[str] = None,
) -> str:
    parts = [
        "[유저 프로필]",
        json.dumps(profile.to_dict(), ensure_ascii=False, indent=2),
        "",
        "[이미 수혜 중인 제도 policy_id — 매칭 규칙 5]",
        json.dumps(current_benefits, ensure_ascii=False),
    ]
    if today:
        parts += ["", "[오늘 날짜 — 마감일 판단 기준]", today]
    parts += [
        "",
        "[이번에 판단할 policy_id — 총 {}건]".format(len(target_ids)),
        json.dumps(target_ids, ensure_ascii=False),
        "",
        "제도 목록에는 다른 자치구 전용 사업도 들어 있지만, 거주지 불일치는 이미 "
        "시스템이 걸러냈습니다. 위 목록에 있는 policy_id만 판단하고, 목록에 없는 "
        "제도는 결과에 포함하지 마세요.",
        "위 policy_id를 하나도 빠뜨리지 말고, 각각 정확히 한 그룹에만 넣으세요.",
    ]
    return "\n".join(parts)


def _normalize(
    raw: Dict[str, Any], catalog: List[Dict[str, Any]]
) -> Dict[str, List[Dict[str, Any]]]:
    """LLM 결과를 검증/보정한다.

    - policy_name은 LLM이 준 name 대신 DB 값을 쓴다 (제도명 할루시네이션 방지)
    - 카탈로그에 없는 policy_id는 버린다
    - 중복 배정은 첫 그룹만 남긴다
    - 누락된 제도는 확인_불가로 회수한다 (조용히 사라지면 기회 손실)
    """
    by_id = {row["policy_id"]: row for row in catalog}
    result: Dict[str, List[Dict[str, Any]]] = {
        GROUP_FIT: [],
        GROUP_UNKNOWN: [],
        GROUP_UNFIT: [],
    }
    seen: Set[int] = set()

    for tool_key in (TOOL_FIT, TOOL_UNKNOWN, TOOL_UNFIT):
        group = TOOL_TO_GROUP[tool_key]
        for item in raw.get(tool_key) or []:
            if not isinstance(item, dict):
                logger.warning("%s 항목이 dict가 아님: %r (무시)", tool_key, item)
                continue
            pid = item.get("policy_id")
            if pid not in by_id:
                logger.warning("매칭 결과에 없는 policy_id=%s (무시)", pid)
                continue
            if pid in seen:
                logger.warning("policy_id=%s 중복 배정 (무시)", pid)
                continue
            seen.add(pid)
            entry: Dict[str, Any] = {
                "policy_id": pid,
                "policy_name": by_id[pid]["policy_name"],
                "reason": item.get("reason") or "",
            }
            if group == GROUP_FIT:
                entry["caution"] = item.get("caution") or None
            elif group == GROUP_UNKNOWN:
                entry["missing_field"] = item.get("missing_field") or "unknown"
            result[group].append(entry)

    missing = [pid for pid in by_id if pid not in seen]
    for pid in missing:
        logger.warning("policy_id=%s 미분류 → 확인_불가로 회수", pid)
        result[GROUP_UNKNOWN].append(
            {
                "policy_id": pid,
                "policy_name": by_id[pid]["policy_name"],
                "missing_field": "unclassified",
                "reason": "매칭 과정에서 판단되지 않아 확인이 필요합니다.",
            }
        )
    return result


async def run_matching(
    profile: Profile, today: Optional[str] = None
) -> Dict[str, Any]:
    """매칭 프롬프트를 1회 실행하고 결과를 돌려준다."""
    catalog = await policies.get_catalog()
    if not catalog:
        raise RuntimeError("제도 목록을 불러오지 못했습니다 (DATABASE_URL 확인)")

    # 지역은 결정론적 조건이라 코드로 먼저 거른다 (LLM 출력 토큰/지연 절감)
    candidates, region_excluded = policies.split_by_region(catalog, profile.region_sigungu)
    logger.info(
        "매칭 후보 %d건 (지역 불일치로 제외 %d건)", len(candidates), len(region_excluded)
    )

    benefits = await current_benefit_policy_ids(profile.carer_id)
    # 매칭 규칙 9(마감 임박 caution) 판단에 오늘 날짜가 필요하다
    today = today or date.today().isoformat()
    target_ids = [row["policy_id"] for row in candidates]
    raw = await llm.complete_tool_cached(
        # 카탈로그는 전 사용자 공통이라 그대로 둬야 prompt cache가 항상 히트한다.
        # 지역 필터는 "판단할 policy_id 목록"으로 전달해서 출력 토큰만 줄인다.
        system=build_system(policies.catalog_to_text(catalog)),
        messages=[
            {
                "role": "user",
                "content": build_user_message(profile, benefits, target_ids, today),
            }
        ],
        tool=MATCHING_TOOL,
        max_tokens=settings.matching_max_tokens,
        effort="high",
        thinking=True,
    )
    result = _normalize(raw, candidates)

    # 지역 불일치분은 명세대로 부적합 목록에 그대로 실어준다
    for row in region_excluded:
        result[GROUP_UNFIT].append(
            {
                "policy_id": row["policy_id"],
                "policy_name": row["policy_name"],
                "reason": "{} 거주자 대상 사업 (현재 거주지: {})".format(
                    row["application_region"], profile.region_sigungu
                ),
            }
        )

    result["has_followup_available"] = bool(result[GROUP_UNKNOWN])
    logger.info(
        "매칭 완료 carer_id=%s 적합=%d 확인_불가=%d 부적합=%d",
        profile.carer_id,
        len(result[GROUP_FIT]),
        len(result[GROUP_UNKNOWN]),
        len(result[GROUP_UNFIT]),
    )
    return result
