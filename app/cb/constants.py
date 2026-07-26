"""복지로 3종 필터 어휘 + 서울 자치구 목록 + 태그 정규화.

3종 필터 값은 복지로 체크박스 값 그대로다. 임의로 지어내지 않는다.
cb.cb_institutions의 CHECK 제약과 반드시 일치해야 한다
(scripts/migrations/001_cb_schema.sql 참고).
"""
from typing import Dict, List, Optional, Sequence

# --- 3종 필터 정규 어휘 -------------------------------------------------------
LIFE_CYCLE_TAGS: List[str] = [
    "임신·출산", "영유아", "아동", "청소년", "청년", "중장년", "노년",
]

HOUSEHOLD_TAGS: List[str] = [
    "저소득", "장애인", "한부모·조손", "다자녀", "다문화·탈북민", "보훈대상자",
]

THEME_TAGS: List[str] = [
    "신체건강", "정신건강", "생활지원", "주거", "일자리", "문화·여가", "안전·위기",
    "임신·출산", "보육", "교육", "입양·위탁", "보호·돌봄", "서민금융", "법률", "에너지",
]

TAG_VOCABULARY: Dict[str, List[str]] = {
    "life_cycle": LIFE_CYCLE_TAGS,
    "household": HOUSEHOLD_TAGS,
    "theme": THEME_TAGS,
}

# --- 상태·등급 ----------------------------------------------------------------
# 복지제도가 실제로 신청 자격의 기준으로 쓰는 것들이다. 3종 필터 어휘로는
# 표현할 수 없어서(복지로에 '장기요양등급'이라는 값이 없다) 따로 둔다.
#
# 진단명은 넣지 않는다. "무슨 병이세요"는 이 서비스가 물을 자리가 아니고,
# 사용자가 먼저 말하면 그때 검색어에 실린다.
CONDITION_TAGS: List[str] = [
    "장기요양등급", "장애등록", "기초생활수급", "차상위",
]

# --- 도움의 대상 --------------------------------------------------------------
# 영케어러는 본인 것(청년 주거·일자리)과 돌보는 분 것(노년 의료·돌봄)이 섞인다.
# 어느 쪽을 찾는지 모르면 검색이 엉뚱한 생애주기로 흐른다.
TARGET_SELF = "self"
TARGET_CAREE = "caree"
TARGET_FOR_VALUES: List[str] = [TARGET_SELF, TARGET_CAREE]

# --- 나이 → 생애주기 ----------------------------------------------------------
# 복지로 생애주기 구간에 맞춘 경계다. 제도마다 실제 연령 기준은 제각각이라
# (청년월세는 19~34세, 청년 정책 일부는 39세까지) 이 값은 검색 태그를 고르는
# 용도로만 쓴다. 자격 판정에 쓰면 안 된다.
_AGE_BANDS = ((5, "영유아"), (12, "아동"), (18, "청소년"), (34, "청년"), (64, "중장년"))


def life_cycle_for_age(age: Optional[int]) -> Optional[str]:
    """나이 → 생애주기 태그 1개. 모르거나 값이 이상하면 None."""
    if age is None or not 0 <= age <= 120:
        return None
    for upper, label in _AGE_BANDS:
        if age <= upper:
            return label
    return "노년"

# --- 서울 25개 자치구 ---------------------------------------------------------
# region_scope 판정 기준. 이 목록에 정확히 일치할 때만 district다.
SEOUL_GU: frozenset = frozenset([
    "종로구", "중구", "용산구", "성동구", "광진구", "동대문구", "중랑구", "성북구",
    "강북구", "도봉구", "노원구", "은평구", "서대문구", "마포구", "양천구", "강서구",
    "구로구", "금천구", "영등포구", "동작구", "관악구", "서초구", "강남구", "송파구",
    "강동구",
])

SEOUL_CTPV = "서울특별시"

REGION_NATIONAL = "national"
REGION_METRO = "metro"
REGION_DISTRICT = "district"


def region_scope(source: str, sgg_nm: Optional[str]) -> str:
    """제도의 지역 범위를 판정한다.

    자치구 화이트리스트에 정확히 일치할 때만 district로 본다.
    그 외(빈값, '-', '서울특별시교육청', 향후 신규 값)는 전부 metro로 흡수한다(fail-open).

    빈값 여부로 갈랐다면 sggNm='-'인 「학교 밖 청소년 교육참여수당」(서울시교육청)처럼
    어느 그룹에도 안 잡혀 검색에서 통째로 누락되는 건이 생긴다.
    """
    if source == "central":
        return REGION_NATIONAL
    return REGION_DISTRICT if (sgg_nm or "").strip() in SEOUL_GU else REGION_METRO


def region_keys_for_user(user_sgg: Optional[str]) -> List[str]:
    """유저 자치구 기준 검색 대상 region_key 목록 (3그룹 합집합).

    복지로 자체 UI는 구 단위 검색에서 시 본청 제도를 빼고 보여주지만,
    그게 바로 우리가 해결하려는 파편화 문제라 따라가지 않는다.
    """
    keys = [REGION_NATIONAL, REGION_METRO]
    sgg = (user_sgg or "").strip()
    if sgg in SEOUL_GU:
        keys.append(sgg)
    return keys


# --- 태그 정규화 --------------------------------------------------------------
# API 원문 표기가 흔들린다. 같은 API 안에서도 생애주기는 '임신 · 출산'(공백 있음),
# 관심주제는 '임신·출산'(공백 없음)으로 다르게 온다.
# 중앙부처는 구분자가 ","이고 지자체는 ", "다.
_MIDDOT_CHARS = "·ㆍ‧∙•・"


def _normalize_label(raw: str) -> str:
    """공백/가운뎃점 변형을 정규 표기로 되돌린다."""
    label = raw.strip()
    for ch in _MIDDOT_CHARS:
        label = label.replace(ch, "·")
    # '임신 · 출산' -> '임신·출산', '한부모 · 조손' -> '한부모·조손'
    while " ·" in label or "· " in label:
        label = label.replace(" ·", "·").replace("· ", "·")
    return label.strip()


def parse_tags(raw_value: Optional[str], kind: str) -> List[str]:
    """API의 콤마 구분 문자열을 정규 태그 리스트로 바꾼다.

    어휘에 없는 값은 조용히 버린다 (DB CHECK 제약에 걸려 INSERT 전체가
    실패하는 것보다, 알 수 없는 태그 하나를 버리는 쪽이 안전하다).
    버려진 값은 호출부에서 로깅한다.
    """
    if not raw_value:
        return []
    allowed = TAG_VOCABULARY[kind]
    seen: List[str] = []
    for chunk in raw_value.split(","):
        label = _normalize_label(chunk)
        if label in allowed and label not in seen:
            seen.append(label)
    return seen


def unknown_tags(raw_value: Optional[str], kind: str) -> List[str]:
    """어휘에 없어서 버려진 값들 (로깅/모니터링용)."""
    if not raw_value:
        return []
    allowed = TAG_VOCABULARY[kind]
    return [
        _normalize_label(c)
        for c in raw_value.split(",")
        if _normalize_label(c) and _normalize_label(c) not in allowed
    ]


def filter_to_vocabulary(labels: Sequence[str], kind: str) -> List[str]:
    """LLM이 돌려준 태그를 어휘로 제한한다 (지어낸 값 차단)."""
    allowed = TAG_VOCABULARY[kind]
    out: List[str] = []
    for label in labels or []:
        norm = _normalize_label(str(label))
        if norm in allowed and norm not in out:
            out.append(norm)
    return out
