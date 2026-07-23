"""phase별 추출 tool 스키마.

각 phase md의 '추출 규칙' JSON 예시를 그대로 JSON Schema로 옮긴 것.
공통으로 `unknown_fields`를 하나 추가했다 — 사용자가 "모른다"고 답한 항목을
기록해서 같은 걸 다시 캐묻지 않기 위한 것(phase4 원칙 3, phase6 규칙 4).
"""
from typing import Any, Dict

_UNKNOWN_FIELDS = {
    "type": "array",
    "items": {"type": "string"},
    "description": (
        "사용자가 '모른다/기억 안 난다'고 답했거나 답을 피한 필드명 목록. "
        "값을 못 얻었지만 다시 물어보면 안 되는 항목만 넣는다."
    ),
}

_CARE_RECIPIENT = {
    "type": "object",
    "properties": {
        "relation_to_user": {
            "type": "string",
            "description": "사용자와의 관계 (예: 모, 부, 조모, 형제)",
        },
        "condition_summary": {
            "type": "string",
            "description": "사용자가 말한 그대로의 상태 서술 (예: 뇌병변장애 1급, 거동 불편, 3년째 악화 중)",
        },
        "severity_level": {
            "type": "string",
            "enum": ["높음", "중간", "낮음"],
            "description": "서술형 답변에서 시스템이 추정한 심각도. 사용자에게 등급을 묻지 말 것.",
        },
        "status": {
            "type": "string",
            "enum": ["active", "hospitalized", "improved", "deceased"],
            "description": "현재 돌봄 상태",
        },
    },
    "required": ["relation_to_user"],
}

_SIGNAL = {
    "type": "object",
    "description": "우회 질문으로 얻은 소득/생활비 신호 한 건. income_value를 직접 채우지 않는다.",
    "properties": {
        "signal_type": {
            "type": "string",
            "description": "예: 월생활비, 아르바이트수입, 통신비, 교통비, 가족지원금",
        },
        "raw_value": {"type": "string", "description": "사용자 발화 원문 (예: 한 80만원 정도?)"},
        "parsed_value": {"type": "integer", "description": "원 단위 숫자로 파싱한 값"},
        "source": {
            "type": "string",
            "enum": ["indirect_question", "self_declared", "system_inferred"],
        },
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["signal_type", "raw_value"],
}

PHASE_SCHEMAS: Dict[int, Dict[str, Any]] = {
    1: {
        "type": "object",
        "properties": {
            "household_members": {"type": "integer", "description": "같이 사는 가족 수 (본인 포함)"},
            "housing_type": {
                "type": "string",
                "enum": ["자가", "전세", "월세", "무상거주"],
            },
            "housing_type_status": {
                "type": "string",
                "enum": ["known", "unknown"],
                "description": "주거형태를 사용자가 모른다고 하면 unknown",
            },
            "living_with_relation": {
                "type": "string",
                "description": "돌봄대상자와의 관계 + 동거 여부 (예: 모(동거), 조모(비동거))",
            },
            "has_income_activity": {"type": "boolean", "description": "일/알바 여부만. 금액은 phase4."},
            "military_service_status": {
                "type": "string",
                "enum": ["해당없음", "복무예정", "복무중", "제대", "제대(연장가능)"],
            },
            "unknown_fields": _UNKNOWN_FIELDS,
        },
    },
    2: {
        "type": "object",
        "properties": {
            "priority_burden_type": {
                "type": "string",
                "enum": ["시간부담", "경제부담", "건강악화", "고립감", "진로단절"],
            },
            "priority_types": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["돌봄가사", "의료건강", "심리청년특화", "생계주거"],
                },
                "description": "selected_types를 우선순위 순으로 재정렬한 것",
            },
            "priority_reason_summary": {
                "type": "string",
                "description": "왜 그게 1순위인지 한 문장 요약",
            },
            "unknown_fields": _UNKNOWN_FIELDS,
        },
    },
    3: {
        "type": "object",
        "properties": {
            "daily_care_hours_self": {"type": "number", "description": "본인이 하루에 돌보는 시간"},
            "daily_care_hours_household": {"type": "number", "description": "가구 전체 돌봄시간"},
            "has_backup_caregiver": {"type": "boolean"},
            "backup_caregiver_relation": {"type": "string"},
            "medical_burden_level": {"type": "string", "enum": ["높음", "중간", "낮음"]},
            "employment_status": {
                "type": "string",
                "enum": ["재학", "휴학", "재직", "구직", "무직", "자퇴"],
            },
            "isolation_level": {"type": "string", "enum": ["높음", "중간", "낮음"]},
            "dependents_count": {"type": "integer", "description": "부양가족 수"},
            "care_recipients": {"type": "array", "items": _CARE_RECIPIENT},
            "unknown_fields": _UNKNOWN_FIELDS,
        },
    },
    4: {
        "type": "object",
        "properties": {
            "signal": _SIGNAL,
            "housing_deposit": {
                "type": "integer",
                "description": (
                    "임차보증금, 원 단위 정수. 한국어 금액 축약을 주의해서 변환한다: "
                    "보증금 맥락에서 '8천'은 8천만원(80000000), '1억'은 100000000, "
                    "'500'은 500만원(5000000)을 뜻한다. 단위가 애매하면 추출하지 말 것."
                ),
            },
            "housing_monthly_rent": {
                "type": "integer",
                "description": (
                    "월세, 원 단위 정수. 월세 맥락에서 '50'은 50만원(500000), "
                    "'없다/무월세'는 0. 단위가 애매하면 추출하지 말 것."
                ),
            },
            "household_asset_value": {"type": "integer", "description": "재산 총액, 원 단위 정수"},
            "vehicle_value": {"type": "integer", "description": "차량가액, 원 단위 정수"},
            "has_basic_livelihood_support": {"type": "boolean", "description": "기초생활수급 여부"},
            "has_cha_sang_wi": {"type": "boolean", "description": "차상위계층 여부"},
            "income_value": {
                "type": "integer",
                "description": "사용자가 월 소득을 명확한 숫자로 진술했을 때만 채운다. 추정하지 말 것.",
            },
            "income_related_utterance": {"type": "string", "description": "소득 관련 발화 원문"},
            "unknown_fields": _UNKNOWN_FIELDS,
        },
    },
    5: {
        "type": "object",
        "properties": {
            "confirmed": {
                "type": "boolean",
                "description": "사용자가 요약 내용이 맞다고 확인했으면 true",
            },
            "corrections": {
                "type": "object",
                "description": "사용자가 정정한 필드들 (필드명: 새 값)",
                "additionalProperties": True,
            },
            "missing_info": {
                "type": "array",
                "items": {"type": "string"},
                "description": "아직 확인 못 한 필드명 목록",
            },
        },
    },
    6: {
        "type": "object",
        "properties": {
            "updates": {
                "type": "object",
                "description": (
                    "보완질문으로 새로 얻은 값들. 원래 필드명 그대로 쓴다 "
                    "(예: housing_deposit, housing_monthly_rent, employment_status)."
                ),
                "additionalProperties": True,
            },
            "financial_detail_status": {
                "type": "string",
                "enum": ["not_asked", "unknown_needs_probe", "known"],
            },
            "income_value_status": {
                "type": "string",
                "enum": ["not_asked", "unknown_needs_probe", "estimated_from_signals", "known"],
            },
            "unknown_fields": _UNKNOWN_FIELDS,
        },
    },
}


# phase1~4는 '수집' 단계라, 사용자가 뒤 phase 내용을 먼저 말해도 받아적어야 한다
# (phase1 원칙 4: "이후 phase 정보까지 먼저 말하면 그 정보도 놓치지 말고 반영한다").
# 그래서 추출 스키마는 1~4를 합쳐서 쓰고, '어느 phase를 지금 묻고 있는지'는
# 프롬프트의 추출 규칙 텍스트로 구분한다.
COLLECTION_PHASES = (1, 2, 3, 4)


def _merged_collection_schema() -> Dict[str, Any]:
    properties: Dict[str, Any] = {}
    for phase in COLLECTION_PHASES:
        properties.update(PHASE_SCHEMAS[phase]["properties"])
    return {"type": "object", "properties": properties}


_COLLECTION_SCHEMA = _merged_collection_schema()


def extraction_tool(phase: int) -> Dict[str, Any]:
    schema = _COLLECTION_SCHEMA if phase in COLLECTION_PHASES else PHASE_SCHEMAS[phase]
    return {
        "name": "extract_fields",
        "description": (
            "사용자 발화에서 새로 확인된 필드만 추출한다. "
            "언급되지 않았거나 확실하지 않은 필드는 절대 포함하지 않는다. "
            "필드는 이 스키마의 최상위 키로 바로 넣는다 (parameters 같은 걸로 한 번 더 감싸지 않는다)."
        ),
        "input_schema": schema,
    }
