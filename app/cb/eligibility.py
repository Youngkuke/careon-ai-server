"""자격이 한정된 제도를 목록에서 걷어내는 필터.

문제: 태그 필터는 '겹치면 통과(&&)'라서, 사용자가 가구상황을 말하지 않으면
필터가 아예 작동하지 않는다. 그 결과 지원대상이 완전히 다른 제도가 같은
주제 태그 하나로 섞여 올라온다. 실제로 "치매 어머니 병원비"에
'가정폭력피해자 치료회복 프로그램 및 의료비지원'이 맞춤 3위로 나왔다.

그 제도의 태그는 이랬다:
    생애=[영유아, 아동, 중장년, 노년]  가구=[보훈대상자]  주제=[신체건강, ...]
    지원대상="가정폭력 등 피해자 및 동반가족(아동)을 대상으로 합니다."
'노년'으로 필터를 통과했고, 가구태그는 아예 틀렸다(tags_source=llm).

그래서 두 가지를 본다:
  A. 지원대상 원문에 적힌 자격 한정어 (신원·사건 기반 + 질환 기반)
  B. 배타적인 가구상황 태그 (304건)

A는 목록에서 뺀다. B는 순위만 낮춘다.

처음에는 A도 감점만 했다. 말하지 않았을 뿐 해당자일 수 있으니 '혹시 관심
있으실 수도'에 남겨두자는 판단이었다. 실측(2026-07-27)에서 그게 틀렸다.
아버지 돌봄 상담 결과 20건에 '가정폭력피해자 의료비', '외국인근로자 등
의료지원', '(산재근로자)케어센터지원', '영농도우미 지원'이 그대로 남았다.
20건을 훑어야 하면 사용자가 직접 검색하는 것과 다르지 않다.

되살릴 길은 열려 있다. 사용자가 대화에서 해당한다고 말하면(claimed)
그 그룹은 감점도 제외도 하지 않는다.
"""
import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from app.cb import grading

logger = logging.getLogger(__name__)

# --- A. 지원대상 원문의 자격 한정어 -------------------------------------------
# 그룹: (제도에서 찾을 표현, 사용자가 해당자임을 밝힐 때 쓸 표현)
# 제도 쪽 표현은 좁게 잡는다. '외국인'처럼 흔한 낱말을 넣으면 일반 제도의
# 본문에도 걸려서 멀쩡한 제도가 내려간다.
EXCLUSIVE_GROUPS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "폭력피해": (
        ("가정폭력", "성폭력", "친밀관계폭력", "데이트폭력", "스토킹", "성매매 피해"),
        ("가정폭력", "성폭력", "폭력 피해", "스토킹", "데이트폭력"),
    ),
    "범죄피해": (("범죄피해",), ("범죄피해", "범죄 피해")),
    "북한이탈주민": (
        ("북한이탈", "탈북", "새터민", "귀순"),
        ("북한이탈", "탈북", "새터민"),
    ),
    "외국인·이주민": (
        ("외국인근로자", "결혼이민자", "난민신청자", "국적 취득 전"),
        ("외국인", "이주민", "결혼이민", "난민", "귀화"),
    ),
    "노숙인": (("노숙인",), ("노숙", "거리 생활")),
    "출소자": (
        ("출소", "출소예정", "보호관찰", "수형자"),
        ("출소", "보호관찰", "교도소", "수감"),
    ),
    "산재근로자": (
        ("산재근로자", "산업재해", "산재보험급여", "진폐"),
        ("산재", "산업재해", "진폐"),
    ),
    "보훈대상": (
        ("국가유공", "보훈대상", "고엽제", "5·18", "참전유공", "독립유공",
         "의사상자", "위안부", "특수임무유공"),
        ("국가유공", "보훈", "고엽제", "참전", "독립유공", "유공자"),
    ),
    "환경오염피해": (("환경오염피해",), ("환경오염",)),
    "농어업인": (
        ("농업인", "어업인", "영농", "농어업인", "농가"),
        ("농사", "농업", "어업", "농가", "영농"),
    ),
}

# --- A-2. 특정 질환에 한정된 제도 ------------------------------------------------
# "아프다"는 말 하나로 856건 중 의료 제도가 전부 후보가 된다. 그중 상당수는
# 진단명이 박혀 있어서, 그 병이 아니면 신청 자체가 안 된다. 실측에서
# '암환자의료비지원'이 암 이야기가 없는 상담의 맞춤 6위로 올라왔다.
#
# 장애등록과 장기요양등급은 **일부러 넣지 않았다.** 거동이 불편한 어르신은
# 실제로 해당할 가능성이 높은데 사용자가 등급을 먼저 말하지는 않는다.
# 이 둘은 대화에서 직접 물어본 뒤에 판단한다.
CONDITION_GROUPS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "암": (("암환자", "암 환자", "암 진단", "항암"), ("암", "항암", "종양", "백혈병")),
    "희귀·난치질환": (
        ("희귀질환", "난치질환", "중증난치", "희귀·중증난치"),
        ("희귀질환", "난치", "희귀병"),
    ),
    "치매": (("치매",), ("치매", "인지증")),
    "정신질환": (
        ("정신질환자", "중증정신질환", "조현병", "정신재활"),
        ("정신질환", "조현병", "우울", "정신과"),
    ),
    "감염병": (("결핵", "한센", "에이즈", "HIV"), ("결핵", "한센", "에이즈")),
}

# 자격 한정어가 걸렸을 때의 감점. 이쪽은 원문 근거라 태그보다 신뢰도가 높다.
# 목록에서 빠지므로 실제로는 순서에 영향을 주지 않지만, 제외를 끄고
# 실측할 때를 위해 남겨둔다.
EXCLUSIVE_TERM_FACTOR = 0.5

# --- A-3. 등급·수급 자격 ---------------------------------------------------------
# 위 두 그룹과 판정 방향이 반대다. 위쪽은 '사용자가 해당한다고 말하지 않았으면
# 제외'인데, 이쪽은 **명시적으로 아니라고 답했을 때만** 제외한다.
#
# 거동이 불편한 어르신은 장기요양등급이 있을 가능성이 높은데 사용자가 먼저
# 등급 얘기를 꺼내지는 않는다. 말 안 했다고 빼면 가장 필요한 제도가 사라진다.
# 그래서 검색 직전에 직접 물어보고(app/cb/nodes.py의 ask_narrow),
# "아니요"라는 답을 받은 것만 걷어낸다.
DENIABLE_GROUPS: Dict[str, Tuple[str, ...]] = {
    "장기요양등급": ("장기요양", "요양등급", "장기요양보험"),
    "장애등록": ("등록장애인", "장애인등록", "장애정도", "장애인복지법"),
    "기초생활수급": ("기초생활수급", "생계급여", "의료급여 수급", "기초생활보장"),
    "차상위": ("차상위",),
}

# --- A-4. 좁은 질환에 게이트가 걸린 제도 ------------------------------------------
# A-2(CONDITION_GROUPS)는 암·희귀난치·치매처럼 '넓은 카테고리'만 다룬다. 그보다
# 좁은 단일 질환(백내장·요실금·탈모증·제1형 당뇨병…)은 사전에 없어서 그대로
# 통과했다. 실측(2026-07-28): "치매+뇌졸중 후유증" 상담의 맞춤 구간에
# 「노인 개안수술비 지원」과 「요실금 치료지원 사업」이 올라왔다.
#
# 왜 고정 목록을 만들지 않았는가
#   질환명 목록은 끝이 없고 데이터가 늘면 계속 손봐야 한다. 대신 '이 제도가
#   특정 진단을 자격으로 걸고 있는가'를 먼저 규칙으로 판정하고(게이트),
#   질환어는 그 제도의 원문에서 그때그때 뽑는다. 목록을 들고 있지 않으므로
#   새 제도가 들어와도 코드를 고칠 필요가 없다.
#
#   게이트를 먼저 거는 것이 핵심이다. 856건 전체에 '○○ 환자' 같은 문형을
#   돌리면 「햇살론youth」의 "채무조정 성실상환자"가 '채무조정 성실상' 질환으로
#   잡히는 식의 오탐이 쏟아진다. 게이트를 통과하는 19건 안에서는 그런 문장이
#   나오지 않는다.
#
# 규모(2026-07-28 실측): 856건 중 게이트 통과 19건
#   (진단요건 16 / 수술대상 4 / 상병코드 3, 중복 포함)
_RE_DISEASE_GATE = re.compile(
    r"상병\s*코드|질병\s*코드"          # ICD 코드를 나열하는 제도
    r"|진단\s*(?:을|를)?\s*받|진단\s*기준|확진"   # 진단이 자격 요건인 제도
    r"|수술\s*대상|대상\s*질환"          # 수술/대상질환을 명시하는 제도
)

# 게이트 표지 주변 이 범위에서만 질환어를 찾는다. 넓히면 자격과 무관한
# 문장(지원 내용·문의처)까지 들어온다.
_DISEASE_WINDOW = 70

# 창 안에서 질환어를 집어내는 문형.
#
# 앞의 둘은 '대상 질환을 대놓고 열거한' 자리라 그대로 믿는다.
# 뒤의 둘은 평범한 문장에서 뽑는 것이라 제도명 대조를 한 번 더 통과해야 한다
# (_names_the_disease). 이 대조가 없으면 "출생 후 2년 **이내**에 ...으로
# 진단받고"의 '이내', "**최종** 진단을 받은 병원에서"의 '최종'이 질환어로
# 잡혀서 엉뚱한 제도가 내려간다.
#
# 괄호 열거만 무조건 믿는다. '수술대상(백내장, 망막질환, 녹내장)'처럼 대상 질환을
# 대놓고 나열한 자리라 제도명에 그 병이 없어도(「노인 개안수술비 지원」) 근거가 된다.
_RE_DISEASE_LISTS = [
    re.compile(r"수술\s*대상\s*\(([^)]{2,80})\)"),
]
_RE_DISEASE_SENTENCES = [
    # '(대상질환) ...' / '대상질환: ...'. 구분자를 요구한다 — 안 그러면
    # 「희귀질환자 의료비 지원사업」의 "진단서를 통해 대상질환 확인- 관할부서에서
    # 소득/재산조사"가 '재산조사'라는 질환으로 잡힌다.
    re.compile(r"대상\s*질환\s*(?:\)|[:：])\s*([^)\n]{2,60})"),
    re.compile(r"([가-힣A-Za-z0-9제·]{2,14})\s*(?:으로|로)?\s*진단"),
    re.compile(r"([가-힣A-Za-z0-9제·]{2,14})\s*(?:환자|질환자|의심자|유병자)"),
]

_RE_TERM_SPLIT = re.compile(r"[,、·/]|\s등\s|\s및\s|\s또는\s")
_RE_JOSA_TAIL = re.compile(r"(으로|로|을|를|이|가|은|는|의|에|와|과|및)$")

# 포괄 표현. **이 목록만 하드코딩한다.** 질환명 목록과 달리 닫혀 있고 늘어나지
# 않는다 — 복지 문서가 쓰는 상위 개념어는 사실상 고정이기 때문이다.
# 여기 있는 말은 '좁은 질환'이 아니므로 감점 근거가 되지 못한다.
_BROAD_TERMS: Set[str] = {
    "질환", "질병", "상병", "중증", "중증질환", "만성", "만성질환", "희귀질환",
    "희귀난치성질환", "희귀난치질환", "난치질환", "중증난치질환", "노인성질환",
    "장애", "장애인", "등록장애인", "중증장애인", "경증장애인", "정신질환",
    "노인", "어르신", "아동", "청소년", "영아", "신생아", "임산부", "산모",
    "환자", "질환자", "대상자", "신청자", "본인", "가구원", "국민", "구민",
    "저소득", "저소득층", "수급자", "차상위", "기타", "해당", "감염병",
    "상병코드", "질병코드", "선별검사", "확진검사", "종합심리검사",
    # 행정 용어. 자격 문장에 섞여 들어오는데 질환이 아니다.
    "수술대상", "대상질환", "건강보험급여", "소득", "재산", "재산조사", "진단서",
}


def narrow_disease_terms(row: Dict[str, Any]) -> List[str]:
    """제도가 자격으로 거는 '좁은 질환어'. 게이트를 통과하지 못하면 빈 목록.

    선정기준까지 본다. 「성동구 청년 등 탈모 치료 지원」처럼 지원대상에는
    거주·연령만 적고 진단 요건은 선정기준에 적는 제도가 있다.
    선정기준이 없는 호출(컬럼을 안 읽은 경우)에도 그냥 동작한다.
    """
    text = " ".join(filter(None, [
        row.get("serv_nm"), row.get("target_detail"), row.get("select_criteria"),
    ]))
    if not _RE_DISEASE_GATE.search(text):
        return []

    name = row.get("serv_nm") or ""
    terms: List[str] = []
    for marker in _RE_DISEASE_GATE.finditer(text):
        start = max(0, marker.start() - _DISEASE_WINDOW)
        window = text[start:marker.end() + _DISEASE_WINDOW]
        for frames, trusted in ((_RE_DISEASE_LISTS, True),
                                (_RE_DISEASE_SENTENCES, False)):
            for frame in frames:
                for match in frame.finditer(window):
                    for chunk in _RE_TERM_SPLIT.split(match.group(1)):
                        term = _clean_term(chunk)
                        if not term or term in terms:
                            continue
                        if not trusted and not _names_the_disease(name, term):
                            continue
                        terms.append(term)
    return terms


def _names_the_disease(serv_nm: str, term: str) -> bool:
    """제도명이 이 질환을 내걸고 있는가.

    문장에서 뽑은 후보를 거르는 관문이다. 특정 질환에 게이트가 걸린 제도는
    대개 이름에 그 병을 달고 있다(「요실금 치료지원 사업」, 「소아·청소년
    제1형 당뇨병 환자 지원사업」). 이름에 없으면 감점 근거로 쓰지 않는다 —
    놓치는 쪽이 멀쩡한 제도를 내리는 쪽보다 안전하다.

    접두 부분문자열까지 인정한다. '탈모증'은 「성동구 청년 등 탈모 치료 지원」의
    이름에 그대로는 없지만 '탈모'로 들어 있다 (search.py의 접두어 규칙과 같은 발상).
    """
    if term in serv_nm:
        return True
    return any(term[:size] in serv_nm for size in range(len(term) - 1, 1, -1))


def _clean_term(raw: str) -> Optional[str]:
    term = " ".join(raw.split()).strip(" ()[]<>·,.'\"")
    term = _RE_JOSA_TAIL.sub("", term).strip()
    if not (2 <= len(term) <= 14):
        return None
    if not re.search(r"[가-힣]", term):
        return None
    if term in _BROAD_TERMS:
        return None
    # 조사만 떼고 남은 동사·부사 꼬리를 걸러낸다 ('필요로 하', '가능하므').
    if term.endswith(("하", "되", "므", "며", "고", "서", "니")):
        return None
    return term


# 감점 배율. 기존 '배타적 가구상황 태그'와 같은 값에서 출발한다 — 둘 다
# "원문 근거는 있지만 사용자가 해당자가 아니라고 단정할 수는 없다"는 성격이다.
# 조정하려면 이 상수만 바꾸면 된다.
NARROW_DISEASE_FACTOR = 0.7


def disease_penalty(
    row: Dict[str, Any],
    user_text: str,
    conditions: Sequence[str],
    claimed: Set[str],
) -> Tuple[float, List[str]]:
    """대화에 한 번도 안 나온 좁은 질환이 자격으로 걸려 있으면 감점한다.

    제외하지 않는다. 사용자가 말하지 않았을 뿐 실제로 그 병일 수 있고,
    "모르면 배제하지 않는다"는 원칙이 여기에도 그대로 적용된다.

    감점하지 않는 경우:
      - 질환어가 사용자 발화에 나온다 (그 병을 말한 사람이다)
      - 이미 확인된 자격과 이어지는 질환이다 (conditions / claimed)
        예: 장기요양등급을 받았다고 답한 사용자에게 '치매' 게이트 제도를
            내리면 안 된다. 등급이 곧 그 상태의 확인이다.
    """
    terms = narrow_disease_terms(row)
    if not terms:
        return 1.0, []

    haystack = user_text or ""
    unmentioned = [t for t in terms if t not in haystack]
    if len(unmentioned) < len(terms):
        # 하나라도 사용자가 말했으면 이 제도는 대화 안에 있는 것이다.
        return 1.0, []

    # 이미 확인된 자격과 겹치면 손대지 않는다.
    for group in claimed:
        _, user_terms = _all_groups().get(group, ((), ()))
        if any(any(u in t for u in user_terms) for t in terms):
            return 1.0, []
    for condition in conditions or ():
        for keyword in DENIABLE_GROUPS.get(condition, ()):
            if any(keyword in t for t in terms):
                return 1.0, []

    return NARROW_DISEASE_FACTOR, ["질환 한정: %s" % ", ".join(unmentioned[:3])]


# --- B. 배타적인 가구상황 태그 -------------------------------------------------
# '저소득'과 '장애인'은 넣지 않는다.
#   저소득: 대부분 우선지원 성격이라 누구나 해당될 수 있다.
#   장애인: 돌보는 분이 장애인인 경우가 이 서비스의 핵심 사용자다. 사용자가
#           태그로 말하지 않았다고 내리면 정작 필요한 제도가 밀린다.
EXCLUSIVE_HOUSEHOLD: Set[str] = {"보훈대상자", "다문화·탈북민", "다자녀", "한부모·조손"}

# 태그는 72%가 LLM이 붙인 것이라 오태깅이 섞여 있다(가정폭력 제도에 '보훈대상자'가
# 달려 있었다). 원문 근거보다 약하게 매긴다.
EXCLUSIVE_HOUSEHOLD_FACTOR = 0.7


def _contains_any(text: str, needles: Iterable[str]) -> bool:
    return any(needle in text for needle in needles)


def _all_groups() -> Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]]:
    """신원 기반 + 질환 기반. 판정 방식이 같아서 한 묶음으로 본다."""
    merged = dict(EXCLUSIVE_GROUPS)
    merged.update(CONDITION_GROUPS)
    return merged


def user_claimed_groups(user_text: str) -> Set[str]:
    """사용자가 스스로 해당한다고 말한 자격 그룹.

    대화 원문에서 찾는다. 태그(household)만 보면 놓친다 — 복지로 어휘에
    '가정폭력피해자'나 '산재'가 아예 없어서 태그로는 표현할 방법이 없다.
    """
    claimed = set()
    for group, (_, user_terms) in _all_groups().items():
        if _contains_any(user_text, user_terms):
            claimed.add(group)
    return claimed


def penalty_for(
    row: Dict[str, Any],
    user_text: str,
    user_household: Sequence[str],
    claimed: Set[str],
    denied: Sequence[str] = (),
    conditions: Sequence[str] = (),
) -> Tuple[float, List[str], bool]:
    """감점 배율, 이유, 그리고 '목록에서 빼야 하는가'.

    세 번째 값은 지원대상 원문에 자격이 적혀 있을 때만 True다. 태그는
    72%가 LLM이 붙인 것이라 오태깅이 섞여 있어서(노인 의료 제도에
    '한부모·조손'이 달려 있다) 목록을 좌우하게 두면 정작 필요한 제도가
    사라진다. 태그는 순위만 낮춘다.
    """
    factor = 1.0
    reasons: List[str] = []
    from_source = False

    haystack = "%s %s" % (row.get("serv_nm") or "", row.get("target_detail") or "")
    for group, (inst_terms, _) in _all_groups().items():
        if group in claimed:
            continue
        if _contains_any(haystack, inst_terms):
            factor *= EXCLUSIVE_TERM_FACTOR
            reasons.append(group)
            from_source = True

    for group in denied or ():
        terms = DENIABLE_GROUPS.get(group)
        if terms and _contains_any(haystack, terms):
            # 사용자가 해당하지 않는다고 직접 답했다. 추정이 아니라 답변이므로
            # 위 그룹들과 같은 무게로 걷어낸다.
            factor *= EXCLUSIVE_TERM_FACTOR
            reasons.append("%s 아님" % group)
            from_source = True

    mine = set(user_household or [])
    narrow = (set(row.get("household_tags") or []) & EXCLUSIVE_HOUSEHOLD) - mine
    if narrow:
        factor *= EXCLUSIVE_HOUSEHOLD_FACTOR
        reasons.extend(sorted(narrow))

    # 좁은 질환 게이트. from_source를 켜지 않는다 — 원문 근거가 있어도 이건
    # 제외가 아니라 감점까지만이다 (사용자가 그 병을 말하지 않았을 뿐일 수 있다).
    disease_factor, disease_reasons = disease_penalty(
        row, user_text, conditions, claimed)
    if disease_factor < 1.0:
        factor *= disease_factor
        reasons.extend(disease_reasons)

    return factor, reasons, from_source


def apply(
    rows: List[Dict[str, Any]],
    user_text: str,
    user_household: Sequence[str],
    denied: Sequence[str] = (),
    conditions: Sequence[str] = (),
) -> List[Dict[str, Any]]:
    """자격이 어긋나는 제도를 걷어낸 목록을 돌려준다.

    원문에 자격이 박힌 건은 빼고, 태그로만 어긋나는 건은 순위만 낮춘다.
    denied는 사용자가 '해당하지 않는다'고 직접 답한 등급·수급 자격이다.
    conditions는 반대로 '해당한다'고 답한 자격이며, 이미 확인된 상태와
    이어지는 질환 게이트 제도를 내리지 않기 위해 쓴다.
    """
    claimed = user_claimed_groups(user_text or "")
    if claimed:
        logger.info("[eligibility] 사용자가 밝힌 자격: %s", ", ".join(sorted(claimed)))
    if denied:
        logger.info("[eligibility] 해당 없다고 답한 자격: %s", ", ".join(denied))

    kept: List[Dict[str, Any]] = []
    demoted = 0
    dropped: List[str] = []
    for row in rows:
        factor, reasons, from_source = penalty_for(
            row, user_text or "", user_household, claimed, denied, conditions)
        if factor >= 1.0:
            kept.append(row)
            continue
        if from_source:
            # 지원대상 원문에 자격이 적혀 있고 사용자는 해당한다고 말한 적이
            # 없다. 순위만 낮추면 경계에서 흔들린다 — 실제로 '가정폭력피해자
            # 의료비'가 감점 뒤에도 8위/9위를 오갔다.
            dropped.append("%s(%s)" % (row.get("serv_nm") or row.get("serv_id"),
                                       ",".join(reasons)))
            continue
        row["rrf"] = float(row.get("rrf") or 0.0) * factor
        row["eligibility_notes"] = reasons
        demoted += 1
        kept.append(row)

    if dropped or demoted:
        logger.info("[eligibility] 제외 %d건 / 감점 %d건", len(dropped), demoted)
    for note in dropped:
        logger.debug("[eligibility] 제외: %s", note)
    return kept


# --- C. 장애 중증도 / 소득 구간 (003 마이그레이션으로 구조화한 축) ----------------
# 위 A·B와 다른 점: 여기서는 **가산도 한다**. A·B는 "어긋나는 것을 걷어내는"
# 필터라 감점·제외뿐이었는데, 중증도와 소득은 사용자가 직접 밝힌 값과 제도
# 원문의 값이 맞아떨어지는 순간이 있고, 그건 유사도보다 강한 자격 신호다.
#
# 값의 출처는 cb.cb_institutions.disability_severity / income_pct_max이고
# scripts/backfill_grading.py가 원문 regex + 법정 사전으로 채운다. LLM이
# 개입하지 않으므로 같은 입력에는 항상 같은 순위가 나온다.
SEVERITY_MATCH_BOOST = 1.3
# 중증도가 명시적으로 어긋날 때. 제외하지 않는다 — '심하지 않은 장애인' 전용
# 제도라도 신청 시점에 재판정을 받는 경우가 있고, 무엇보다 사용자가 자기
# 등급을 잘못 알고 있을 수 있다.
SEVERITY_MISMATCH_FACTOR = 0.7

INCOME_MATCH_BOOST = 1.3
# 사용자 소득 구간이 제도 상한을 넘을 때. 이것도 제외하지 않는다.
#
# income_pct_max의 61%(181/298건)는 원문의 %가 아니라 카테고리명 사전 매핑에서
# 왔고, 그중 일부는 자격 상한이 아니라 '대상 예시'다. 실측:
#   「찾아가는 복지 방문 강화 사업」의 "기초생활수급자, 독거어르신, 장애인,
#    한부모가족 등 사회취약계층" → 32%로 잡혔지만 32% 상한 제도가 아니다.
# 이 값으로 제외까지 하면 정작 그 사업이 필요한 차상위 사용자에게서 사라진다.
# 그래서 A그룹(원문에 자격이 박힌 건)과 달리 감점에서 멈춘다.
# 대신 배율은 A와 같은 0.5로 둬서 맞춤 구간 밖으로 확실히 밀어낸다.
INCOME_OVER_FACTOR = 0.5
# 사용자가 "수급·차상위 어디에도 해당 없다"고만 답한 경우. 정확한 %를 모르고
# 50% 초과라는 하한만 아는 상태라 더 약하게 매긴다.
INCOME_LIKELY_OVER_FACTOR = 0.7


def grading_adjust(
    rows: List[Dict[str, Any]],
    *,
    severity: Optional[str] = None,
    income_pct: Optional[int] = None,
    income_floor: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """확인된 중증도·소득 구간으로 순위를 올리고 내린다. 목록에서 빼지는 않는다.

    세 인자 모두 None이면 아무것도 하지 않는다. 사용자가 말하지 않은 대화에서는
    기존 동작 그대로여야 한다(회귀 없음).

    제도 쪽 값이 없거나(NULL) 'unknown'이면 손대지 않는다. NULL은 '제한 없음'이
    아니라 '원문에 근거 없음'이고, unknown은 '장애인 대상이지만 정도를 안 가림'
    이다. 둘 다 기존 장애등록 여부 로직(DENIABLE_GROUPS)이 계속 담당한다.

    정렬은 하지 않는다. 호출부(nodes._rerank)가 모든 신호를 얹은 뒤 한 번만 한다.
    """
    if severity is None and income_pct is None and income_floor is None:
        return rows

    boosted = 0
    demoted = 0
    for row in rows:
        factor = 1.0
        notes: List[str] = []

        row_severity = row.get("disability_severity")
        if severity and row_severity in (grading.SEVERE, grading.MILD):
            if row_severity == severity:
                factor *= SEVERITY_MATCH_BOOST
                notes.append("중증도 일치")
            else:
                factor *= SEVERITY_MISMATCH_FACTOR
                notes.append("중증도 불일치")

        row_income = row.get("income_pct_max")
        if row_income is not None:
            if income_pct is not None:
                if income_pct <= row_income:
                    factor *= INCOME_MATCH_BOOST
                    notes.append("소득 기준 충족(%d%%≤%d%%)" % (income_pct, row_income))
                else:
                    factor *= INCOME_OVER_FACTOR
                    notes.append("소득 기준 초과(%d%%>%d%%)" % (income_pct, row_income))
            elif income_floor is not None and row_income <= income_floor:
                factor *= INCOME_LIKELY_OVER_FACTOR
                notes.append("소득 기준 초과 추정(>%d%%)" % income_floor)

        if factor == 1.0:
            continue
        row["rrf"] = float(row.get("rrf") or 0.0) * factor
        row["grading_notes"] = notes
        # 자격이 확인된 건은 유사도가 조금 멀어도 맞춤에 남겨야 한다.
        # cards.split_sections가 이 표시를 보고 거리 컷을 건너뛴다.
        row["grading_confirmed"] = factor > 1.0
        if factor > 1.0:
            boosted += 1
        else:
            demoted += 1

    if boosted or demoted:
        logger.info(
            "[grading] 가산 %d건 / 감점 %d건 (사용자 중증도=%s 소득=%s%s)",
            boosted, demoted, severity or "미상",
            f"{income_pct}%" if income_pct is not None else "미상",
            f" 하한>{income_floor}%" if income_floor is not None else "",
        )
    return rows
