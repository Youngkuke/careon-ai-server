"""자격이 한정된 제도를 뒤로 미는 신호.

문제: 태그 필터는 '겹치면 통과(&&)'라서, 사용자가 가구상황을 말하지 않으면
필터가 아예 작동하지 않는다. 그 결과 지원대상이 완전히 다른 제도가 같은
주제 태그 하나로 섞여 올라온다. 실제로 "치매 어머니 병원비"에
'가정폭력피해자 치료회복 프로그램 및 의료비지원'이 맞춤 3위로 나왔다.

그 제도의 태그는 이랬다:
    생애=[영유아, 아동, 중장년, 노년]  가구=[보훈대상자]  주제=[신체건강, ...]
    지원대상="가정폭력 등 피해자 및 동반가족(아동)을 대상으로 합니다."
'노년'으로 필터를 통과했고, 가구태그는 아예 틀렸다(tags_source=llm).

그래서 두 가지를 본다:
  A. 지원대상 원문에 적힌 자격 한정어 (전체 856건 중 123건)
  B. 배타적인 가구상황 태그 (304건)

둘 다 '제외'가 아니라 '감점'이다. 사용자가 실제로 해당자인데 말하지
않았을 수 있고(한부모 가정인데 굳이 말하지 않는다), 빠지면 찾을 방법이
없지만 내려가 있으면 '혹시 관심 있으실 수도'에서 볼 수 있다.
"""
import logging
from typing import Any, Dict, Iterable, List, Sequence, Set, Tuple

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
}

# 자격 한정어가 걸렸을 때의 감점. 이쪽은 원문 근거라 태그보다 신뢰도가 높다.
EXCLUSIVE_TERM_FACTOR = 0.5

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


def user_claimed_groups(user_text: str) -> Set[str]:
    """사용자가 스스로 해당한다고 말한 자격 그룹.

    대화 원문에서 찾는다. 태그(household)만 보면 놓친다 — 복지로 어휘에
    '가정폭력피해자'나 '산재'가 아예 없어서 태그로는 표현할 방법이 없다.
    """
    claimed = set()
    for group, (_, user_terms) in EXCLUSIVE_GROUPS.items():
        if _contains_any(user_text, user_terms):
            claimed.add(group)
    return claimed


def penalty_for(
    row: Dict[str, Any],
    user_text: str,
    user_household: Sequence[str],
    claimed: Set[str],
) -> Tuple[float, List[str], bool]:
    """감점 배율, 이유, 그리고 '맞춤에서 빼야 하는가'.

    세 번째 값은 지원대상 원문에 자격이 적혀 있을 때만 True다. 태그는
    72%가 LLM이 붙인 것이라 오태깅이 섞여 있어서(노인 의료 제도에
    '한부모·조손'이 달려 있다) 섹션을 좌우하게 두면 정작 필요한 제도가
    맞춤에서 빠진다. 태그는 순위만 낮춘다.
    """
    factor = 1.0
    reasons: List[str] = []
    from_source = False

    haystack = "%s %s" % (row.get("serv_nm") or "", row.get("target_detail") or "")
    for group, (inst_terms, _) in EXCLUSIVE_GROUPS.items():
        if group in claimed:
            continue
        if _contains_any(haystack, inst_terms):
            factor *= EXCLUSIVE_TERM_FACTOR
            reasons.append(group)
            from_source = True

    mine = set(user_household or [])
    narrow = (set(row.get("household_tags") or []) & EXCLUSIVE_HOUSEHOLD) - mine
    if narrow:
        factor *= EXCLUSIVE_HOUSEHOLD_FACTOR
        reasons.extend(sorted(narrow))

    return factor, reasons, from_source


def apply(
    rows: List[Dict[str, Any]],
    user_text: str,
    user_household: Sequence[str],
) -> List[Dict[str, Any]]:
    """자격이 어긋나는 제도의 rrf를 낮춘다. 목록에서 빼지는 않는다."""
    claimed = user_claimed_groups(user_text or "")
    if claimed:
        logger.info("[eligibility] 사용자가 밝힌 자격: %s", ", ".join(sorted(claimed)))

    demoted = 0
    excluded = 0
    for row in rows:
        factor, reasons, from_source = penalty_for(
            row, user_text or "", user_household, claimed)
        if factor >= 1.0:
            continue
        row["rrf"] = float(row.get("rrf") or 0.0) * factor
        row["eligibility_notes"] = reasons
        demoted += 1
        if from_source:
            # 순위만 낮추면 경계에서 흔들린다. 실제로 '가정폭력피해자 의료비'가
            # 감점 뒤에도 8위/9위를 오갔다. 지원대상 원문에 자격이 적힌 건은
            # '맞춤'이라 부르지 않는다 — 목록에는 남고 섹션만 내려간다.
            row["eligibility_excluded"] = True
            excluded += 1
    if demoted:
        logger.info("[eligibility] 감점 %d건 (그중 맞춤 제외 %d건)", demoted, excluded)
    return rows
