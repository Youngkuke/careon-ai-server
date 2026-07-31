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


# --- A-5. 언급된 질환·상황과 원문이 일치할 때의 가산 --------------------------------
# disease_penalty의 반대 방향이다. 저쪽은 "대화에 없는 좁은 질환이 자격으로
# 걸린 제도"를 내리고, 이쪽은 "대화에 나온 질환이 원문에 실제로 적힌 제도"를
# 올린다. 지금까지는 후자가 중립(1.0)이라 아무 일도 하지 않았다.
#
# 왜 필요한가 (search.py 첫머리의 실측과 같은 문제):
#   "치매 걸린 부모님 돌봄 지원" 질의에서 임베딩은 '부모님·돌봄·지원'의 어휘
#   겹침을 '치매'라는 핵심어보다 강하게 본다. 키워드 채널이 그걸 건져 올리지만
#   구간을 나누는 것은 RRF 순위이고 맞춤 섹션 안 정렬은 distance라, 정작
#   치매 제도가 맞춤 아래쪽이나 혹시관심으로 밀리는 일이 남는다.
#
# **CONDITION_GROUPS와 일부러 분리했다.** 그쪽은 _all_groups()를 거쳐 감점·제외에도
# 쓰인다. 거기에 항목을 늘리면 그 병을 말하지 않은 사용자에게서 제도가 통째로
# 사라진다. 이 표는 가산에만 쓰이므로 늘려도 무엇 하나 사라지지 않는다.
#
# (사용자가 쓸 표현, 제도 원문에 적힌 표현)으로 나눈 이유는 둘이 다르기 때문이다.
# 856건 실측(2026-07-31): 사용자가 흔히 쓰는 '뇌졸중·중풍·편마비'는 원문에
# 0건이고, 같은 상태를 제도는 전부 '뇌병변'(9건)으로 적는다. '알츠하이머'도
# 0건이고 원문은 '치매'(9건)다. 사용자 말을 그대로 찾으면 한 건도 안 걸린다.
DISEASE_BOOST_GROUPS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    # 오른쪽 괄호 안 숫자는 856건 중 원문(제도명/지원대상/서비스내용) 출현 건수.
    "치매": (("치매", "인지증", "알츠하이머"), ("치매",)),                      # 9
    "뇌병변": (("뇌병변", "뇌졸중", "뇌경색", "뇌출혈", "중풍", "편마비", "반신마비"),
               ("뇌병변",)),                                                # 9
    "파킨슨": (("파킨슨",), ("파킨슨",)),                                     # 3
    "암": (("암", "항암", "종양", "백혈병"),
           ("암환자", "암 환자", "항암", "종양", "백혈병")),                    # 5
    "희귀·난치": (("희귀질환", "희귀병", "난치"), ("희귀질환", "난치")),          # 17
    "정신질환": (("정신질환", "조현병", "우울", "조울", "공황", "정신과"),
                 ("정신질환", "우울")),                                       # 4
    "발달장애": (("발달장애", "자폐", "지적장애", "경계선지능"),
                 ("발달장애", "자폐", "지적장애")),                            # 29
    "당뇨": (("당뇨",), ("당뇨",)),                                          # 7
    "감염병": (("결핵", "한센", "에이즈", "HIV"), ("결핵", "한센")),            # 6
    "척수·와상": (("척수", "와상", "거동이 불편", "거동 불편", "사지마비", "전신마비"),
                  ("척수", "와상", "거동")),                                  # 6
    "신장": (("투석", "신부전", "콩팥"), ("투석",)),                           # 1
    # 아래 셋은 증상 확인 노드(nodes.needs_condition_probe)를 넣으면서 함께 열었다.
    # 그 질문의 답으로 가장 많이 돌아올 표현인데, 받아놓고 쓸 데가 없으면
    # 한 턴을 더 쓴 값이 순위에 아무것도 싣지 못한다.
    #
    # 856건 실측(2026-07-31): 원문은 '지체장애'(4) '휠체어'(12) '보행'(4) '목발'(1)로
    # 적는다. '보행'이 「저소득 노인 보행보조차 지원」「어르신 보행기 지원」을
    # 끌어오는데, 낙상·거동 어르신 상담에서 정확히 필요한 제도들이다.
    "지체·보행": (("지체장애", "다리가 불편", "다리를 다치", "걷기 힘드", "걷지 못",
                  "휠체어", "목발", "골절", "낙상", "넘어지"),
                  ("지체장애", "휠체어", "보행", "목발")),                     # 18
    "시각": (("시각장애", "눈이 안 보", "앞이 안 보", "실명", "저시력"),
             ("시각장애",)),                                                 # 8
    "청각": (("청각장애", "귀가 안 들", "잘 안 들리", "난청", "보청기"),
             ("청각장애", "난청", "보청기")),                                 # 11
}

# 가산 배율. 기존 가산(SEVERITY_MATCH_BOOST / INCOME_MATCH_BOOST)과 같은 값이다.
# 셋 다 "사용자가 밝힌 사실과 제도 원문이 맞아떨어졌다"는 같은 성격이라
# 한쪽만 세게 줄 이유가 없다.
DISEASE_MATCH_BOOST = 1.3

# 제도명이 '사용자가 말하지 않은' 질환을 내걸고 있을 때의 감점.
#
# 실측(2026-07-31, 페르소나1 — 치매·뇌졸중 돌봄): 「발달장애인 긴급돌봄사업」과
# 「발달장애인 가족휴식지원사업」이 맞춤 구간에 올라왔다. 돌봄 주제를 넓히면서
# (intent_extract.md의 보호·돌봄·생활지원 규칙) 후보가 늘었는데, 이것들을
# 내릴 근거가 아무 데도 없었다 — disease_penalty는 진단 게이트(_RE_DISEASE_GATE)를
# 통과하는 제도만 보고, 이 둘은 통과하지 않는다.
#
# **제도명만 본다.** 본문까지 보면 대상 질환을 여러 개 나열한 제도가 통째로
# 깎여서, 정작 사용자에게 맞는 제도가 사라진다. 제도명에 병이 박혀 있다는 것은
# 그 병이 이 제도의 정체라는 뜻이라 근거가 단단하다.
#
# 제외하지 않고 감점만 한다. 사용자가 말하지 않았을 뿐 해당할 수도 있다는
# 이 파일 전체의 원칙이 여기에도 그대로 적용된다.
DISEASE_MISMATCH_FACTOR = 0.7


def mentioned_disease_groups(user_text: str) -> Dict[str, Tuple[str, ...]]:
    """대화에 나온 질환 그룹 → 제도 원문에서 찾을 표현."""
    return {
        group: inst_terms
        for group, (user_terms, inst_terms) in DISEASE_BOOST_GROUPS.items()
        if _contains_any(user_text or "", user_terms)
    }


def disease_boost(rows: List[Dict[str, Any]], user_text: str) -> List[Dict[str, Any]]:
    """대화에 나온 질환·상황이 원문에 실제로 적힌 제도를 끌어올린다.

    제도명·지원대상·서비스내용 세 곳을 본다. 요청대로 service_content까지
    보려면 search.py의 SELECT에 그 컬럼이 있어야 한다(없으면 조용히 건너뛴다).

    disease_match 표시를 남긴다. cards.split_sections가 이걸 보고
      - 거리 컷을 면제하고 (grading_confirmed와 같은 취급)
      - 맞춤 섹션 안에서 앞자리에 세운다.
    배율만으로는 부족하다 — 구간을 나누는 것은 RRF 순위인데 맞춤 섹션의
    정렬은 distance라, 배율로 순위를 올려도 섹션 안에서 다시 뒤로 밀린다.

    정렬은 하지 않는다. 호출부(nodes._rerank)가 모든 신호를 얹은 뒤 한 번만 한다.
    """
    mentioned = mentioned_disease_groups(user_text)
    if not mentioned:
        # 질환 이야기가 없는 대화에서는 가산도 감점도 하지 않는다.
        # 안 그러면 제도명에 병이 든 제도가 모든 대화에서 깎인다.
        return rows

    boosted = 0
    demoted = 0
    for row in rows:
        serv_nm = row.get("serv_nm") or ""
        body = " ".join(filter(None, [
            serv_nm, row.get("target_detail"), row.get("service_content"),
        ]))
        hits = [group for group, inst_terms in mentioned.items()
                if _contains_any(body, inst_terms)]
        if hits:
            row["rrf"] = float(row.get("rrf") or 0.0) * DISEASE_MATCH_BOOST
            row["disease_match"] = hits
            boosted += 1
            continue

        # 원문 어디에도 사용자의 병이 없는데 제도명은 다른 병을 내걸고 있다.
        others = [group for group, (_, inst_terms) in DISEASE_BOOST_GROUPS.items()
                  if group not in mentioned and _contains_any(serv_nm, inst_terms)]
        if others:
            row["rrf"] = float(row.get("rrf") or 0.0) * DISEASE_MISMATCH_FACTOR
            row["disease_mismatch"] = others
            demoted += 1

    logger.info("[disease] 대화에 나온 질환=%s → 일치 %d건 가산 / 다른 질환 전용 %d건 감점",
                ", ".join(sorted(mentioned)), boosted, demoted)
    return rows


# --- A-6. 확인된 자격(conditions)이 원문에 걸릴 때의 가산 ---------------------------
# DENIABLE_GROUPS는 지금까지 '아니라고 답한 것'을 걷어내는 데만 쓰였다. 반대쪽,
# 즉 사용자가 **맞다고 답한 것**은 순위에 아무 영향도 주지 않았다 —
# disease_penalty를 억제하는 데만 쓰이고 끝이었다.
#
# 이건 가장 확실한 신호를 버리는 것이다. ask_narrow가 대화의 마지막 한 턴을
# 써서 받아낸 답이고, 추정이 아니라 사용자의 명시적 답변이다.
#
# 실측(2026-07-31, 페르소나1 — 치매·뇌졸중, 장기요양등급 있음):
#   장기요양을 원문에 언급하는 제도는 856건 중 25건뿐이라 신호가 선명하다.
#   후보 30건에서 「재가급여」「시설급여」「특별현금급여(가족요양비)」
#   「가사·간병 방문 지원사업」은 전부 걸리고, 「발달장애인 긴급돌봄사업」
#   「장애인가족지원센터 긴급돌봄서비스」는 전부 안 걸린다. 이 신호를 안 쓰니
#   등급을 확인해 놓고도 발달장애 제도가 맞춤 구간에 올라와 있었다.
#
# 배율은 다른 가산과 같은 1.3이다. 제외하거나 거리 컷을 면제하지는 않는다 —
# '장기요양'이라는 말이 원문에 있다는 것이 곧 '이 제도가 당신 것'이라는 뜻은
# 아니어서(자격 조항일 수도, 배제 조항일 수도 있다) 질환명 일치만큼 강하게 보지 않는다.
CONDITION_MATCH_BOOST = 1.3

# 자격 조항인가, **배제** 조항인가.
#
# 실측(2026-07-31, 페르소나1): 「가사·간병 방문 지원사업」 원문의
# '노인장기요양보험급여'는 자격이 아니라 배제 조항이다 —
#   "만 65세 미만의 ... 아래에 해당하는 경우 지원대상에서 제외합니다.
#    - 만 65세 이상의 어르신
#    - ... 유사 돌봄서비스를 받고 있는자 * ... 노인장기요양보험급여"
# 이걸 자격 신호로 읽고 가산하면 정반대로 동작한다. 장기요양등급이 있어서
# **오히려 못 받는** 제도를 맨 위로 올린 것이다.
#
# grading._is_negated와 같은 발상인데 창을 양쪽으로 잡는다. 저쪽은 뒤만 보면
# 됐지만(카테고리 바로 뒤에 '제외'가 붙는 형태), 배제 목록은 '제외합니다'가
# 목록보다 앞에 나오고 항목이 여러 줄에 걸친다.
#
# '제외'만 찾으면 부족하다. 실측: 「노인맞춤돌봄서비스」는 같은 뜻을
#   "유사중복사업을 노인맞춤돌봄서비스보다 우선적으로 제공 ① 노인장기요양보험 등급자"
# 라고 쓴다. 장기요양 등급자는 이 서비스가 아니라 그쪽을 받으라는 말이라
# 사실상 후순위 배제인데, '제외'라는 낱말은 어디에도 없다.
_RE_CONDITION_NEGATION = re.compile(
    r"제외|받고\s*있는\s*자|유사\s*중복|중복\s*(수급|지원|사업|불가)"
    r"|우선\s*(적으로)?\s*제공|불가|해당하지\s*않|아닌\s*자")
_CONDITION_NEGATION_WINDOW = 80


def _is_disqualifier(body: str, terms: Sequence[str]) -> bool:
    """이 자격어가 원문에서 '배제' 쪽에만 등장하는가.

    배제 문맥이 아닌 등장이 하나라도 있으면 자격 조항으로 본다. 놓치는 쪽이
    (가산을 안 하는 쪽이) 정반대로 올리는 쪽보다 안전하다.
    """
    seen = False
    for term in terms:
        for match in re.finditer(re.escape(term), body):
            seen = True
            window = body[max(0, match.start() - _CONDITION_NEGATION_WINDOW):
                          match.end() + _CONDITION_NEGATION_WINDOW]
            if not _RE_CONDITION_NEGATION.search(window):
                return False
    return seen


def condition_boost(rows: List[Dict[str, Any]],
                    conditions: Sequence[str]) -> List[Dict[str, Any]]:
    """사용자가 해당한다고 답한 자격이 원문에 걸린 제도를 끌어올린다.

    정렬은 하지 않는다. 호출부(nodes._rerank)가 모든 신호를 얹은 뒤 한 번만 한다.
    """
    groups = {c: DENIABLE_GROUPS[c] for c in (conditions or ()) if c in DENIABLE_GROUPS}
    if not groups:
        return rows

    boosted = 0
    skipped = 0
    for row in rows:
        # 제도명이 사용자가 말하지 않은 질환 전용이면 끌어올리지 않는다.
        # 「발달장애인 긴급돌봄사업」이 '장애인복지법' 한 낱말로 가산을 받아
        # 감점(0.7)을 상쇄해 버렸다. 등록장애인인 것은 맞지만 이 제도는
        # 지적·자폐성 장애 전용이다.
        if row.get("disease_mismatch"):
            skipped += 1
            continue
        body = " ".join(filter(None, [
            row.get("serv_nm"), row.get("target_detail"), row.get("service_content"),
        ]))
        hits = [name for name, terms in groups.items()
                if _contains_any(body, terms) and not _is_disqualifier(body, terms)]
        if not hits:
            continue
        row["rrf"] = float(row.get("rrf") or 0.0) * CONDITION_MATCH_BOOST
        row["condition_match"] = hits
        boosted += 1

    logger.info("[condition] 확인된 자격=%s → 가산 %d건 (다른 질환 전용 %d건 건너뜀)",
                ", ".join(sorted(groups)), boosted, skipped)
    return rows


# --- A-7. 본인 축과 돌봄 대상 축이 뒤섞인 제도 --------------------------------------
# 실측(2026-07-31, 26세 사용자 / 81세 할아버지 상담): "저도 일자리가 빠듯하다"는
# **본인** 발화에 「장애인일자리지원」이 결과에 섞여 나왔다. 이 제도의 지원대상은
# "18세 이상 「장애인복지법」상 등록된 미취업 장애인"이다.
#
# 어디에도 막을 자리가 없었다:
#   - 3종 필터는 겹치면 통과(&&)라 theme=[일자리]만으로 들어온다.
#   - 생애주기는 [중장년, 청년, 노년]이라 _apply_target_signal이 안 건드린다.
#   - 가구상황 '장애인'은 EXCLUSIVE_HOUSEHOLD에서 **일부러 뺀 값**이다
#     (돌보는 분이 장애인인 경우가 이 서비스의 핵심 사용자라서).
#   - 지원대상의 '장애인복지법'은 DENIABLE_GROUPS라 사용자가 명시적으로
#     "아니다"라고 답했을 때만 걸린다. 이 대화에선 물어본 적이 없다.
#   - 제도명에 '일자리'가 박혀 있어서 키워드 채널이 오히려 강하게 밀어올린다.
#
# 그래서 축을 본다. 사용자가 **본인 몫으로만** 꺼낸 주제로 걸렸는데, 그 제도가
# 등록장애인을 자격으로 걸고 있으면 본인 축과 돌봄 대상 축을 곱한 것이다.
#
# **EXCLUSIVE_HOUSEHOLD에서 '장애인'을 뺀 판단을 뒤집지 않는다.** 그 판단의 근거는
# "돌보는 분이 장애인인 경우가 핵심 사용자"인데, 그런 제도는 돌봄 축 주제
# (보호·돌봄, 신체건강 등)로 걸리므로 여기서 손대지 않는다. 이 감점은 돌봄 축
# 주제가 하나도 안 걸린 제도에만 닿는다.
AXIS_MISMATCH_FACTOR = 0.7


def _is_disability_gated(row: Dict[str, Any]) -> bool:
    """제도명이 장애인을 내걸고, 지원대상 원문도 등록을 자격으로 거는가.

    **가구상황 태그로 판정하지 않는다.** 태그는 72%가 LLM이 붙인 것인 데다,
    '장애인' 태그는 취약계층을 두루 열거하는 제도에도 붙는다 — 856건 실측에서
    「국민임대주택공급」「에너지바우처」「TV수신료 면제」가 그렇다. 태그로 걸면
    청년 주거 상담에서 국민임대주택이 사라진다.

    제도명 조건을 함께 요구하는 것이 핵심이다. 제도명에 '장애'가 박혀 있다는
    것은 그게 이 제도의 정체라는 뜻이지 곁다리 대상이 아니다
    (_names_the_disease가 쓰는 것과 같은 판단이다).
    """
    serv_nm = row.get("serv_nm") or ""
    if "장애" not in serv_nm:
        return False
    body = "%s %s" % (serv_nm, row.get("target_detail") or "")
    return _contains_any(body, DENIABLE_GROUPS["장애등록"])


def axis_adjust(rows: List[Dict[str, Any]],
                self_themes: Sequence[str],
                caree_themes: Sequence[str]) -> List[Dict[str, Any]]:
    """본인 몫 주제로만 걸린 등록장애인 전용 제도를 뒤로 민다.

    돌보는 분을 위해 찾는 대화에서만 호출한다(nodes._rerank). 본인이 곧
    등록장애인인 대화에서는 이 제도들이 정확히 사용자 것이다.

    양쪽에 다 나온 주제는 어느 쪽으로도 세지 않는다. "일자리"가 본인 이야기로도
    돌보는 분 이야기로도 나왔다면 그건 가려낼 수 있는 신호가 아니다.

    정렬은 하지 않는다. 호출부가 모든 신호를 얹은 뒤 한 번만 한다.
    """
    mine = set(self_themes or ()) - set(caree_themes or ())
    theirs = set(caree_themes or ()) - set(self_themes or ())
    if not mine:
        return rows

    demoted: List[str] = []
    for row in rows:
        themes = set(row.get("theme_tags") or [])
        # 본인 몫 주제로 걸리지 않았거나, 돌봄 축 주제에도 함께 걸렸으면 손대지 않는다.
        if not (themes & mine) or (themes & theirs):
            continue
        if not _is_disability_gated(row):
            continue
        row["rrf"] = float(row.get("rrf") or 0.0) * AXIS_MISMATCH_FACTOR
        row["axis_mismatch"] = sorted(themes & mine)
        demoted.append(row.get("serv_nm") or row.get("serv_id") or "")

    logger.info("[axis] 본인 몫 주제=%s → 등록장애인 전용 %d건 감점 %s",
                ", ".join(sorted(mine)), len(demoted), demoted[:5])
    return rows


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

    # 사용자가 말한 병을 이 제도가 실제로 다루는가 (disease_boost가 남긴 표시).
    #
    # 이게 켜져 있으면 **질환 그룹(CONDITION_GROUPS)으로는 빼지 않는다.**
    # 의료 제도는 대상 질환을 여러 개 나열하는 일이 흔한데, 그중 하나가
    # 사용자 것이면 나머지를 말하지 않았다는 이유로 뺄 근거가 없다.
    # 실측(2026-07-31): "어머니가 치매신데 병원비" 상담에서 「건강보험 산정특례」가
    # 치매 가산을 받고도 '중증난치'라는 다른 대상 때문에 목록에서 빠졌다.
    # (disease_penalty의 "하나라도 사용자가 말했으면 이 제도는 대화 안에 있다"와 같은 규칙이다.)
    #
    # 신원 기반 그룹(EXCLUSIVE_GROUPS)에는 적용하지 않는다. 「(산재근로자)케어센터지원」이
    # 서비스내용에 치매를 적었다고 해서 산재근로자가 아닌 사용자가 받을 수 있게 되지는 않는다.
    #
    # **제도명이 그 질환을 내걸고 있으면 우회하지 않는다.** 이 단서가 없으면 반대로
    # 과하다. 실측(2026-07-31, 페르소나1): 「희귀질환자 의료비 지원사업」이 서비스내용
    # 부수 조항의 "지체 또는 뇌병변 장애의 정도가 심한 장애인"에 걸려 뇌병변 가산을
    # 받았고, 그 바람에 희귀질환 제외가 통째로 건너뛰어져 맞춤 1위가 됐다.
    # 제도명에 '희귀질환'이 박혀 있으면 그게 이 제도의 정체이지 곁다리가 아니다.
    # (eligibility._names_the_disease가 쓰는 것과 같은 판단이다.)
    covers_mentioned = bool(row.get("disease_match"))
    serv_nm = row.get("serv_nm") or ""

    haystack = "%s %s" % (serv_nm, row.get("target_detail") or "")
    for group, (inst_terms, _) in _all_groups().items():
        if group in claimed:
            continue
        if (covers_mentioned and group in CONDITION_GROUPS
                and not _contains_any(serv_nm, inst_terms)):
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
    # 확인된 자격(장기요양등급 등)이 이 제도 원문에 걸려 있으면 태그 감점을 하지 않는다.
    # 이 감점의 근거는 태그인데 72%가 LLM이 붙인 것이라 오태깅이 섞여 있고
    # (바로 위 주석), 반대편에는 사용자가 ask_narrow에서 직접 답한 사실이 있다.
    # 어느 쪽을 믿을지는 분명하다.
    #
    # 실측(2026-07-31, 페르소나1): 「가사·간병 방문 지원사업」이 장기요양·치매에
    # 모두 걸리고도 '한부모·조손' 태그 감점 때문에 맞춤 구간 밖으로 밀렸다.
    # 이 제도의 실제 지원대상은 한부모 전용이 아니다.
    if narrow and not row.get("condition_match"):
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
