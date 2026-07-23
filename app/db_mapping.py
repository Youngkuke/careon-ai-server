"""문서 필드명 ↔ careon_full_v2.sql 컬럼명 매핑.

phase*.md의 필드명과 DB 컬럼명이 다른 것들이 있어서 여기서 한 번에 변환한다.
매핑에 없는 필드는 대응 컬럼이 없는 것이므로 세션 프로필에만 남는다.
"""
from typing import Any, Dict, List, Tuple

# 문서 필드명 -> carers 컬럼명
FIELD_TO_CARER_COLUMN: Dict[str, str] = {
    # phase1
    "household_members": "household_members_count",
    "housing_type": "housing_type",
    "housing_type_status": "know_housing_type",
    "has_income_activity": "has_income_activity",
    "military_service_status": "military_service_status",
    "military_service_extension_years": "military_service_extension_years",
    # phase2
    "priority_burden_type": "biggest_burden_type",
    "priority_reason_summary": "burden_type_reason_summary",
    # phase3
    "daily_care_hours_self": "daily_care_hours_self",
    "daily_care_hours_household": "daily_care_hours_household",
    "has_backup_caregiver": "has_backup_caregiver",
    "backup_caregiver_relation": "backup_caregiver_relation",
    "medical_burden_level": "medical_burden_level",
    # phase4
    "income_value": "income_value",
    "income_value_status": "income_value_status",
    "income_assessment_criteria": "income_assessment_criteria",
    "median_income_ratio": "median_income_ratio",
    "income_variability_type": "income_variability_type",
    "income_related_utterance": "income_related_utterance",
    "has_basic_livelihood_support": "has_basic_livelihood_support",
    "has_cha_sang_wi": "has_cha_sang_wi",
    "housing_deposit": "housing_deposit",
    "housing_monthly_rent": "housing_monthly_rent",
    "household_asset_value": "household_asset_value",
    "vehicle_value": "vehicle_value",
    "financial_detail_status": "financial_detail_status",
}

CARER_COLUMN_TO_FIELD: Dict[str, str] = {v: k for k, v in FIELD_TO_CARER_COLUMN.items()}

# carers에 대응 컬럼이 없어 세션 프로필에만 남는 필드
# (스키마 확장 시 여기서 빼고 위 매핑에 추가하면 됨)
SESSION_ONLY_FIELDS: Tuple[str, ...] = (
    "living_with_relation",
    "priority_types",
    "employment_status",
    "isolation_level",
    "dependents_count",
)

# VARCHAR 컬럼인데 문서 추출 규칙은 숫자로 뽑는 필드 → 문자열로 캐스팅
_VARCHAR_COLUMNS = ("daily_care_hours_self", "daily_care_hours_household")

# BOOLEAN 컬럼인데 문서는 'self_declared'/'system_inferred' 문자열을 쓰는 필드.
# DB 타입에 맞춰 self_declared=True 로 저장한다.
SOURCE_COLUMNS = {
    "has_basic_livelihood_support": "has_basic_livelihood_support_source",
    "has_cha_sang_wi": "has_cha_sang_wi_source",
}


def to_carer_columns(fields: Dict[str, Any]) -> Dict[str, Any]:
    """프로필 필드 dict를 carers UPDATE용 {컬럼: 값}으로 변환."""
    out: Dict[str, Any] = {}
    for field, value in fields.items():
        column = FIELD_TO_CARER_COLUMN.get(field)
        if column is None or value is None:
            continue
        if column in _VARCHAR_COLUMNS:
            value = str(value)
        out[column] = value
    # is_student는 employment_status에서 파생
    employment = fields.get("employment_status")
    if employment is not None:
        out["is_student"] = employment in ("재학", "휴학")
    return out


def build_carer_update(fields: Dict[str, Any], carer_id: int) -> Tuple[str, List[Any]]:
    """UPDATE carers SET ... WHERE carer_id = $n 쿼리를 만든다."""
    columns = to_carer_columns(fields)
    if not columns:
        return "", []
    assignments = []
    args: List[Any] = []
    for i, (column, value) in enumerate(sorted(columns.items()), start=1):
        assignments.append("{} = ${}".format(column, i))
        args.append(value)
    args.append(carer_id)
    query = "UPDATE carers SET {}, updated_at = now() WHERE carer_id = ${}".format(
        ", ".join(assignments), len(args)
    )
    return query, args
