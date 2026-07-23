"""누적 프로필 정의 + phase 완료 판정.

필드명은 phase*.md의 추출 규칙에 나온 이름을 그대로 쓴다.
DB 컬럼과 이름이 다른 것들은 app/db_mapping.py에서 변환한다.
"""
from typing import Any, Dict, List, Optional, Set

# --- 4유형 (policy_types 고정값) ---------------------------------------------
TYPE_CARE = "돌봄가사"
TYPE_MEDICAL = "의료건강"
TYPE_YOUTH = "심리청년특화"
TYPE_LIVING = "생계주거"

# phase1: 문서 '목표' 5개 필드
PHASE1_FIELDS = [
    "household_members",
    "housing_type",
    "living_with_relation",
    "has_income_activity",
    "military_service_status",
]

# phase2
PHASE2_REQUIRED = ["priority_burden_type"]

# phase3: selected_types에 따라 조건부
# dependents_count(생계주거)는 빠져 있다. "부양가족 수"는 대화 원칙상 직접 물을 수 없는
# 행정 용어라, 묻지 못하면서 필수로 두면 phase3에서 교착이 생긴다.
# 대신 household_members에서 파생한다(_derive 참고).
PHASE3_FIELDS_BY_TYPE = {
    TYPE_CARE: ["daily_care_hours_self", "has_backup_caregiver"],
    TYPE_MEDICAL: ["medical_burden_level"],
    TYPE_YOUTH: ["employment_status", "isolation_level"],
    TYPE_LIVING: [],
}

# phase4: 우회 질문으로 모으는 신호 최소 개수 (문서 '다음 단계 판단')
PHASE4_MIN_SIGNALS = 2

# 한 phase에서 이 턴수를 넘기면 더 캐묻지 않고 넘어간다 (무한 루프 방지).
# 문서 원칙상 한 번에 최대 2개씩 물으므로 5턴이면 해당 phase 질문은 다 나간다.
MAX_TURNS_PER_PHASE = 5


class Profile:
    """세션에 누적되는 사용자 프로필."""

    def __init__(
        self,
        carer_id: int,
        age: Optional[int] = None,
        region_sigungu: Optional[str] = None,
        selected_types: Optional[List[str]] = None,
        case_number: Optional[int] = None,
        name: Optional[str] = None,
    ):
        self.carer_id = carer_id
        self.name = name
        self.age = age
        self.region_sigungu = region_sigungu
        self.selected_types: List[str] = selected_types or []
        self.case_number = case_number

        # phase1~6에서 채워지는 값들
        self.fields: Dict[str, Any] = {}
        # 사용자가 "모른다"고 답한 필드 (다시 캐묻지 않기 위해 기록)
        self.unknown_fields: Set[str] = set()
        # phase4 우회 질문으로 모은 소득 신호 (carer_income_signal 행이 된다)
        self.income_signals: List[Dict[str, Any]] = []
        # phase3 돌봄대상자 (cared 행이 된다)
        self.care_recipients: List[Dict[str, Any]] = []
        # phase5 요약 확인 결과
        self.confirmed: bool = False
        self.missing_info: List[str] = []

    # --- 조회 -----------------------------------------------------------------
    def get(self, key: str) -> Any:
        return self.fields.get(key)

    def is_resolved(self, key: str) -> bool:
        """값이 있거나, 사용자가 모른다고 답했으면 '해결됨'으로 본다."""
        return self.fields.get(key) is not None or key in self.unknown_fields

    def phase3_required_fields(self) -> List[str]:
        required: List[str] = []
        for t in self.selected_types:
            required.extend(PHASE3_FIELDS_BY_TYPE.get(t, []))
        return required

    def pending_fields(self, phase: int) -> List[str]:
        """해당 phase에서 아직 확인 못 한 필드 목록."""
        if phase == 1:
            candidates = PHASE1_FIELDS
        elif phase == 2:
            candidates = PHASE2_REQUIRED
        elif phase == 3:
            candidates = self.phase3_required_fields()
        elif phase == 4:
            candidates = ["housing_deposit", "housing_monthly_rent"]
            if TYPE_LIVING not in self.selected_types:
                candidates = []
        else:
            candidates = []
        pending = [f for f in candidates if not self.is_resolved(f)]
        if phase == 3 and not self.care_recipients:
            pending.append("care_recipients")
        return pending

    # --- 갱신 -----------------------------------------------------------------
    def merge(self, extracted: Dict[str, Any]) -> Dict[str, Any]:
        """추출 결과를 프로필에 반영하고, 실제로 반영된 것만 돌려준다."""
        applied: Dict[str, Any] = {}
        if not extracted:
            return applied

        for key, value in extracted.items():
            if value is None:
                continue
            if key == "unknown_fields":
                for f in value:
                    self.unknown_fields.add(f)
                applied["unknown_fields"] = list(value)
            elif key == "care_recipients":
                if self._merge_care_recipients(value):
                    applied["care_recipients"] = self.care_recipients
            elif key == "signal":
                self.income_signals.append(value)
                applied["signal"] = value
            elif key == "signals":
                self.income_signals.extend(value)
                applied["signals"] = value
            elif key == "corrections":
                self.fields.update(value)
                applied.update(value)
            elif key == "missing_info":
                self.missing_info = value
                applied["missing_info"] = value
            elif key == "confirmed":
                self.confirmed = bool(value)
                applied["confirmed"] = bool(value)
            else:
                if self.fields.get(key) == value:
                    continue  # 이미 같은 값 — 재추출된 것이므로 변경으로 치지 않는다
                self.fields[key] = value
                self.unknown_fields.discard(key)
                applied[key] = value

        self._derive()
        return applied

    def _merge_care_recipients(self, incoming: List[Dict[str, Any]]) -> bool:
        """relation_to_user 기준으로 갱신. 실제로 바뀐 게 있으면 True."""
        changed = False
        for item in incoming:
            relation = item.get("relation_to_user")
            existing = next(
                (c for c in self.care_recipients if c.get("relation_to_user") == relation),
                None,
            )
            if existing is None:
                self.care_recipients.append(item)
                changed = True
                continue
            for k, v in item.items():
                if v is not None and existing.get(k) != v:
                    existing[k] = v
                    changed = True
        return changed

    def _derive(self) -> None:
        """진술값에서 파생되는 값 계산."""
        # 매칭 규칙 1: 제대(연장가능)이면 age_max에 연장연수를 더해 비교
        status = self.fields.get("military_service_status") or ""
        if "제대" in str(status):
            self.fields.setdefault("military_service_extension_years", 3)

        # 부양가족 수는 직접 묻지 않고 가구원 수에서 추정한다 (본인 제외)
        members = self.fields.get("household_members")
        if self.fields.get("dependents_count") is None and isinstance(members, int):
            self.fields["dependents_count"] = max(members - 1, 0)

        # 매칭 규칙 3: 재산/주거 숫자 조건은 financial_detail_status='known'일 때만 비교
        financial_keys = [
            "housing_deposit",
            "housing_monthly_rent",
            "household_asset_value",
            "vehicle_value",
        ]
        if any(self.fields.get(k) is not None for k in financial_keys):
            self.fields["financial_detail_status"] = "known"
        elif any(k in self.unknown_fields for k in financial_keys):
            self.fields["financial_detail_status"] = "unknown_needs_probe"
        else:
            self.fields.setdefault("financial_detail_status", "not_asked")

        # 매칭 규칙 2: 소득 상태를 known / estimated_from_signals / unknown_needs_probe로 구분
        if self.fields.get("income_value") is not None:
            self.fields["income_value_status"] = "known"
        elif self.income_signals:
            self.fields["income_value_status"] = "estimated_from_signals"
        elif self._income_probed():
            self.fields["income_value_status"] = "unknown_needs_probe"
        else:
            self.fields.setdefault("income_value_status", "not_asked")

    def _income_probed(self) -> bool:
        """소득 관련 질문을 했는데 '모른다'로 끝난 흔적이 있는가."""
        keywords = ("income", "생활비", "소득", "수입")
        return any(any(k in f for k in keywords) for f in self.unknown_fields)

    # --- phase 전이 판정 (각 문서의 '다음 단계 판단' 섹션) ----------------------
    def phase_complete(self, phase: int, turns_in_phase: int = 0) -> bool:
        if turns_in_phase >= MAX_TURNS_PER_PHASE:
            return True

        if phase == 1:
            # "위 5개 필드가 모두 채워지면 phase 2로"
            return all(self.is_resolved(f) for f in PHASE1_FIELDS)

        if phase == 2:
            # "priority_burden_type이 확인되면 phase 3으로"
            return self.is_resolved("priority_burden_type")

        if phase == 3:
            # "해당하는 세부 필드가 모두 채워지고, care_recipients 최소 1건 이상"
            if not self.care_recipients:
                return False
            return all(self.is_resolved(f) for f in self.phase3_required_fields())

        if phase == 4:
            # "신호가 최소 2개 이상 쌓이거나, 사용자가 명확한 값을 진술하면"
            if len(self.income_signals) >= PHASE4_MIN_SIGNALS:
                return True
            if self.fields.get("income_value") is not None:
                return True
            # "계속 모른다만 나오면 unknown_needs_probe로 남긴 채 넘어가도 된다"
            return self.fields.get("income_value_status") == "unknown_needs_probe"

        if phase == 5:
            return self.confirmed

        return False

    # --- 직렬화 ---------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "carer_id": self.carer_id,
            "name": self.name,
            "age": self.age,
            "region_sigungu": self.region_sigungu,
            "selected_types": self.selected_types,
            "case_number": self.case_number,
        }
        data.update(self.fields)
        if self.care_recipients:
            data["care_recipients"] = self.care_recipients
        if self.income_signals:
            data["income_signals"] = self.income_signals
        if self.unknown_fields:
            data["unknown_fields"] = sorted(self.unknown_fields)
        if self.missing_info:
            data["missing_info"] = self.missing_info
        return data
