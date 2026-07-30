"""장애 중증도와 소득 구간(기준중위소득 %) — 표준값·사전·분류 규칙.

이 모듈은 두 곳이 함께 쓴다. 그래서 별도 파일로 뺐다.
  - 배치 백필: 제도 원문 → disability_severity / income_pct_max
                (scripts/backfill_grading.py)
  - 대화 중  : 사용자가 밝힌 카테고리 → 사용자 소득 %  (app/cb/nodes.py)

역할 분담 원칙 (구현 스펙 6장):
    사용자의 자유 발화를 카테고리로 '분류'하는 것만 LLM이 한다.
    카테고리 → 법정 %/중증도 '변환'과 856건과의 '판정'은 전부 여기 코드가 한다.
    LLM에게 "몇 %인지 계산해" 같은 요청은 하지 않는다. 법정 고정값이라
    재현성이 있어야 하고, 비결정적 출력이 자격 판정을 흔들면 안 된다.

왜 '유형'이 아니라 '중증도'인가 (856건 실측):
    장애 언급 263건 중 구체적 유형(지체·시각·청각…) 명시는 26건(10%)뿐이다.
    유형을 물어봐야 걸러지는 게 거의 없다. 반면 중증도는 41건(16%)에 명시되어
    있고, 그 41건은 '심한 장애인만' 같은 식으로 자격이 실제로 갈린다.
"""
import re
from typing import Dict, List, Optional, Tuple

# --- 장애 중증도 표준값 -------------------------------------------------------
SEVERE = "severe"          # 구 1~3급 / '장애의 정도가 심한 장애인' / 중증
MILD = "mild"              # 구 4~6급 / '장애의 정도가 심하지 않은 장애인' / 경증
UNKNOWN = "unknown"        # 장애는 언급하지만 정도는 명시하지 않음 (가장 흔하다)

SEVERITY_VALUES: List[str] = [SEVERE, MILD, UNKNOWN]

# 제도가 장애를 언급하기는 하는가. 이 말이 하나도 없으면 컬럼은 NULL로 둔다.
# ('unknown'은 "장애를 언급하지만 정도를 모름"이라는 뜻이라 NULL과 다르다.)
_DISABILITY_MENTION = re.compile(r"장애")

# 순서가 중요하다. '심하지 않은'이 '심한'을 포함하므로 mild를 먼저 본다.
_RE_MILD_PHRASE = re.compile(r"장애\s*의?\s*정도가\s*심하지\s*않은")
_RE_SEVERE_PHRASE = re.compile(r"장애\s*의?\s*정도가\s*심한")
_RE_SEVERE_WORD = re.compile(r"중증\s*장애")
_RE_MILD_WORD = re.compile(r"경증\s*장애")

# '1~3급', '1급∼3급', '1-3급' 같은 범위 표기.
_RE_GRADE_RANGE = re.compile(r"([1-6])\s*급?\s*[~∼〜\-–—]\s*([1-6])\s*급")
# 단독 등급. 범위를 먼저 걷어낸 뒤에 찾는다.
_RE_GRADE_SINGLE = re.compile(r"([1-6])\s*급")

_SEVERE_GRADES = {1, 2, 3}
_MILD_GRADES = {4, 5, 6}

# 백필 결과에 붙는 규칙 이름. CSV 표본과 리포트에서 어느 규칙이 잡았는지 본다.
RULE_PHRASE = "phrase"          # '장애의 정도가 심한/심하지 않은'
RULE_WORD = "word"              # '중증장애' / '경증장애'
RULE_GRADE = "grade"            # 등급 숫자
RULE_AMBIGUOUS = "grade_mixed"  # 1~3급과 4~6급이 함께 → 사람 확인 필요
RULE_NONE = "none"              # 장애는 언급하나 정도 근거 없음


def classify_severity(text: str) -> Tuple[Optional[str], str, str]:
    """제도 원문 → (중증도, 적용된 규칙, 근거 구절).

    장애를 아예 언급하지 않으면 (None, RULE_NONE, "")을 돌려준다.
    NULL과 'unknown'을 구분하기 위해서다 — NULL은 장애와 무관한 제도이고,
    'unknown'은 장애인 대상이지만 정도를 안 가리는 제도다. 랭킹에서 전자는
    아예 보지 않고, 후자는 기존 장애등록 여부 로직으로만 판단한다.

    규칙 순서는 구현 스펙 2-2 그대로다. 앞 규칙이 걸리면 뒤는 보지 않는다.
    """
    body = text or ""
    if not _DISABILITY_MENTION.search(body):
        return None, RULE_NONE, ""

    # 1) '장애의 정도가 심한 / 심하지 않은' — 현행 법령 표기라 가장 믿을 만하다.
    match = _RE_MILD_PHRASE.search(body)
    if match:
        return MILD, RULE_PHRASE, _excerpt(body, match)
    match = _RE_SEVERE_PHRASE.search(body)
    if match:
        return SEVERE, RULE_PHRASE, _excerpt(body, match)

    # 2) 중증/경증. 둘 다 나오면 범위가 걸친 것이므로 정도로 가르지 않는다.
    severe_word = _RE_SEVERE_WORD.search(body)
    mild_word = _RE_MILD_WORD.search(body)
    if severe_word and mild_word:
        return UNKNOWN, RULE_AMBIGUOUS, _excerpt(body, severe_word)
    if severe_word:
        return SEVERE, RULE_WORD, _excerpt(body, severe_word)
    if mild_word:
        return MILD, RULE_WORD, _excerpt(body, mild_word)

    # 3) 등급 숫자. 범위 표기를 먼저 펼치고 나서 단독 등급을 줍는다.
    grades, evidence = _collect_grades(body)
    if grades:
        if grades <= _SEVERE_GRADES:
            return SEVERE, RULE_GRADE, evidence
        if grades <= _MILD_GRADES:
            return MILD, RULE_GRADE, evidence
        # 1~3급과 4~6급이 함께 언급됐다. '1~6급 모두 해당'이면 정도를 안 가리는
        # 것이고, '3급 이상은 A, 4급 이하는 B'면 정도로 갈리는 것이다.
        # 규칙으로 구별할 수 없으므로 unknown으로 두고 사람이 표본 확인한다.
        return UNKNOWN, RULE_AMBIGUOUS, evidence

    # 4) 장애인 대상이지만 정도 근거가 없다. 198건(장애 언급 건의 75%)이 여기다.
    return UNKNOWN, RULE_NONE, ""


# 'N급'이 장애 등급인지 판정하는 창. 이 범위 안에 '장애'가 없으면 다른 등급이다.
#
# 실측 오탐: 「보상금」(보훈)의 "6급 유족은 상이원인사망 여부에 따라 차등 지급"에서
# 국가유공자 상이등급 6급이 장애 4~6급으로 잡혀 mild가 됐다. 문서 전체에는
# '장애'가 있지만(상이/장애 관련 제도라서) 그 'N급'은 장애 등급이 아니다.
_GRADE_CONTEXT_PAD = 20


def _grade_in_disability_context(body: str, match: "re.Match") -> bool:
    start = max(0, match.start() - _GRADE_CONTEXT_PAD)
    end = min(len(body), match.end() + _GRADE_CONTEXT_PAD)
    return "장애" in body[start:end]


def _collect_grades(body: str) -> Tuple[set, str]:
    """원문에 등장하는 장애 등급 숫자 집합과 근거 구절.

    범위 표기('1~3급')를 먼저 펼친다. 펼치지 않고 단독 등급만 주우면
    '1~3급'에서 1과 3만 잡혀 2가 빠지는데, 집합 판정에는 영향이 없지만
    근거 구절이 엉뚱해진다.

    'N급' 주변에 '장애'가 없으면 버린다. 보훈 상이등급·자동차손배법 후유장애
    등급처럼 숫자 등급을 쓰는 다른 제도가 섞여 들어오기 때문이다.
    """
    grades = set()
    evidence = ""
    for match in _RE_GRADE_RANGE.finditer(body):
        if not _grade_in_disability_context(body, match):
            continue
        low, high = int(match.group(1)), int(match.group(2))
        if low > high:
            low, high = high, low
        grades.update(range(low, high + 1))
        evidence = evidence or _excerpt(body, match)
    # 범위로 이미 잡은 구간을 지운 뒤 단독 등급을 찾는다.
    remainder = _RE_GRADE_RANGE.sub(" ", body)
    for match in _RE_GRADE_SINGLE.finditer(remainder):
        if not _grade_in_disability_context(remainder, match):
            continue
        grades.add(int(match.group(1)))
        evidence = evidence or _excerpt(remainder, match)
    return grades, evidence


# --- 소득 구간 ----------------------------------------------------------------
# 법정 카테고리 → 기준중위소득 %. 2024~2026 동일하다.
# 원문에 %가 직접 적혀 있으면(regex) 그쪽이 항상 우선이고, 이 사전은
# 카테고리명만 적힌 226건을 위한 대체 경로다.
INCOME_CATEGORY_PCT: Dict[str, int] = {
    "생계급여": 32,
    "의료급여": 40,
    "주거급여": 48,
    "교육급여": 50,
    "차상위계층": 50,
    # 급여 종류가 명시되지 않은 '기초생활수급자'는 생계급여 기준을 쓴다.
    "기초생활수급자": 32,
}

# 원문에서 카테고리를 찾을 때 쓰는 표기 변형.
# '차상위'는 '차상위계층' 없이 단독으로도 흔하게 쓰인다.
_INCOME_CATEGORY_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("생계급여", re.compile(r"생계\s*급여")),
    ("의료급여", re.compile(r"의료\s*급여")),
    ("주거급여", re.compile(r"주거\s*급여")),
    ("교육급여", re.compile(r"교육\s*급여")),
    ("차상위계층", re.compile(r"차상위")),
    ("기초생활수급자", re.compile(r"기초\s*생활\s*(보장)?\s*수급|국민기초생활")),
]

# 구현 스펙 3-2의 1번. 원문에 %가 직접 적힌 경우가 가장 정확하다.
_RE_INCOME_PCT = re.compile(r"중위\s*소득\s*(?:의\s*)?(\d{1,3})\s*%")

# 말이 되는 상한. 실제 제도는 대개 30~180% 구간이고, 그 밖의 값은 오탐이다
# ('중위소득 대비 300%' 같은 표현은 자격 상한이 아니라 통계 설명인 경우가 많다).
INCOME_PCT_MAX_SANE = 200

RULE_PCT_REGEX = "pct_regex"        # 원문에 "중위소득 O%"가 직접 있음
RULE_CATEGORY = "category_dict"     # 카테고리명 → 사전값
RULE_NO_INCOME = "none"             # 근거 없음 → NULL


def classify_income(text: str) -> Tuple[Optional[int], str, str]:
    """제도 원문 → (income_pct_max, 적용된 규칙, 근거 구절).

    근거가 없으면 (None, RULE_NO_INCOME, "")이다. 억지로 값을 만들지 않는다 —
    잘못된 %는 자격 판정을 조용히 왜곡시키고, 그게 값이 없는 것보다 위험하다.

    %가 여러 개 나오면 가장 큰 값을 쓴다. income_pct_max는 '이하' 상한이라
    가장 큰 값이 그 제도가 받아주는 가장 넓은 문이다. 작은 값을 쓰면
    실제로는 자격이 되는 사용자를 떨어뜨린다.
    """
    body = text or ""

    matches = [m for m in _RE_INCOME_PCT.finditer(body)]
    values = [int(m.group(1)) for m in matches]
    values = [v for v in values if 0 < v <= INCOME_PCT_MAX_SANE]
    if values:
        best = max(values)
        evidence = _excerpt(body, next(
            m for m in matches if int(m.group(1)) == best))
        return best, RULE_PCT_REGEX, evidence

    # 카테고리명으로 대체. 여러 개면 역시 가장 넓은 상한을 쓴다
    # ('기초생활수급자 및 차상위계층' → 32와 50 중 50).
    found: List[Tuple[int, str]] = []
    for name, pattern in _INCOME_CATEGORY_PATTERNS:
        for match in pattern.finditer(body):
            if _is_negated(body, match):
                continue
            found.append((INCOME_CATEGORY_PCT[name], _excerpt(body, match)))
            break
    if found:
        pct, evidence = max(found, key=lambda pair: pair[0])
        return pct, RULE_CATEGORY, evidence

    return None, RULE_NO_INCOME, ""


# 카테고리 바로 뒤에 오면 뜻이 뒤집히는 말들. 실측 오탐:
# 「독립유공자 의료비 지원」의 "선정기준 : ... (의료급여수급권자 제외)"가
# income_pct_max=40으로 잡혔다. 자격 상한이 아니라 배제 조항인데 정반대로 읽은 것이다.
_RE_NEGATION = re.compile(r"제외|除外|아닌|미대상|비대상|불가|해당하지\s*않")
_NEGATION_PAD = 16


def _is_negated(body: str, match: "re.Match") -> bool:
    """카테고리 언급이 배제 조항인가. 뒤쪽만 본다 (한국어는 부정어가 뒤에 온다)."""
    tail = body[match.end():match.end() + _NEGATION_PAD]
    return bool(_RE_NEGATION.search(tail))


# --- 기준중위소득 표 (1~4인 가구, 원/월) ----------------------------------------
# 사용자가 소득 숫자를 밝혔을 때만 쓰는 보조 경로다.
# 기본 경로는 우회 질문 + 앵커 제도 역추론(app/cb/prompts/narrow.md)이고,
# 그걸로 구간이 안 잡혔을 때 converse가 2단계로 대략의 액수를 묻는다
# (app/cb/prompts/converse.md의 '소득 파악은 단계적으로').
MEDIAN_INCOME: Dict[str, Dict[int, int]] = {
    "2026": {1: 2564238, 2: 4199292, 3: 5359036, 4: 6494738},
    "2025": {1: 2392013, 2: 3932658, 3: 5025353, 4: 6097773},
    "2024": {1: 2228445, 2: 3682609, 3: 4714657, 4: 5729913},
}

DEFAULT_MEDIAN_YEAR = "2026"


def pct_from_income(
    monthly_income: Optional[int],
    household_size: Optional[int],
    year: str = DEFAULT_MEDIAN_YEAR,
) -> Optional[int]:
    """월소득 + 가구원수 → 기준중위소득 대비 %.

    표에 없는 가구원수(5인 이상)는 계산하지 않는다. 5인 이상 기준액을 표에
    넣지 않은 상태에서 4인 값으로 나누면 실제보다 높은 %가 나와서, 자격이
    되는 사용자를 떨어뜨린다. 모르는 채로 두는 편이 안전하다.
    """
    if not monthly_income or monthly_income <= 0:
        return None
    table = MEDIAN_INCOME.get(year) or MEDIAN_INCOME[DEFAULT_MEDIAN_YEAR]
    base = table.get(household_size or 0)
    if not base:
        return None
    return round(monthly_income / base * 100)


# --- 사용자 쪽 값 (LLM이 분류한 것을 코드가 변환) --------------------------------
# intent_extract가 고를 수 있는 닫힌 선택지. 지어낸 값을 막기 위해 enum으로 준다.
INCOME_CATEGORY_NONE = "해당없음"
INCOME_CATEGORY_UNSURE = "모름"
INCOME_CATEGORIES: List[str] = list(INCOME_CATEGORY_PCT.keys()) + [
    INCOME_CATEGORY_NONE, INCOME_CATEGORY_UNSURE,
]

SEVERITY_HINT_SEVERE = "심한"
SEVERITY_HINT_MILD = "심하지않은"
SEVERITY_HINT_UNSURE = "모름"
SEVERITY_HINTS: List[str] = [
    SEVERITY_HINT_SEVERE, SEVERITY_HINT_MILD, SEVERITY_HINT_UNSURE,
]

_HINT_TO_SEVERITY = {
    SEVERITY_HINT_SEVERE: SEVERE,
    SEVERITY_HINT_MILD: MILD,
}

# '해당없음'이라고 답한 사용자가 넘어선 선. 차상위(50%)까지가 이 선이다.
# 정확한 %를 모르므로 '50 초과'라는 사실만 들고 간다.
INCOME_NONE_FLOOR = 50


def severity_from_hint(hint: Optional[str]) -> Optional[str]:
    """LLM이 분류한 힌트 → 표준 중증도. '모름'과 미응답은 None이다."""
    return _HINT_TO_SEVERITY.get(hint or "")


def user_income_pct(category: Optional[str]) -> Optional[int]:
    """사용자가 밝힌 급여 카테고리 → 기준중위소득 %.

    '해당없음'과 '모름'은 %로 바꾸지 않는다. 해당없음은 '50% 초과'라는 하한만
    알려줄 뿐 정확한 값이 아니고, 그 구분은 user_income_floor가 맡는다.
    """
    return INCOME_CATEGORY_PCT.get(category or "")


def user_income_floor(category: Optional[str]) -> Optional[int]:
    """사용자가 '해당없음'이라 답했을 때의 소득 하한(초과 기준).

    수급·차상위 어디에도 해당하지 않는다면 기준중위소득 50%는 넘는다는 뜻이다.
    정확한 %가 아니므로 제외가 아니라 감점에만 쓴다.
    """
    return INCOME_NONE_FLOOR if category == INCOME_CATEGORY_NONE else None


# --- 공통 -----------------------------------------------------------------------
_EXCERPT_PAD = 30


def _excerpt(body: str, match: "re.Match") -> str:
    """근거 구절. 사람이 CSV로 확인할 때 앞뒤 맥락이 있어야 판단이 된다."""
    start = max(0, match.start() - _EXCERPT_PAD)
    end = min(len(body), match.end() + _EXCERPT_PAD)
    return " ".join(body[start:end].split())
