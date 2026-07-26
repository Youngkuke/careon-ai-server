"""LangGraph 대화 State.

3종 필터는 '누적'된다. 매 턴 덮어쓰면 대화가 길어질수록 앞에서 얻은 정보가
사라진다. "청년이에요" 다음 턴에 "월세가 걱정돼요"라고 하면
life_cycle=[청년]과 theme=[주거]가 둘 다 살아 있어야 한다.

이 State가 대화 필터의 단일 소스다. cb_user_profile에 이중 저장하지 않는다
(scripts/migrations/001_cb_schema.sql의 주석 참고). 어긋나기 때문이다.
"""
from typing import Annotated, Any, Dict, List, Optional

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from app.cb import constants


def merge_tags(current: Optional[List[str]], incoming: Optional[List[str]]) -> List[str]:
    """태그를 합집합으로 누적한다 (LangGraph reducer).

    순서를 유지하면서 중복만 제거한다. set을 쓰면 매 턴 순서가 흔들려
    프롬프트가 달라지고 캐시도 못 탄다.
    """
    out = list(current or [])
    for tag in incoming or []:
        if tag not in out:
            out.append(tag)
    return out


class CbState(TypedDict, total=False):
    # --- 입력 ---------------------------------------------------------------
    user_id: int
    messages: Annotated[list, add_messages]

    # --- extract_intent 산출 (턴을 넘어 누적) --------------------------------
    life_cycle: Annotated[List[str], merge_tags]
    household: Annotated[List[str], merge_tags]
    theme: Annotated[List[str], merge_tags]

    # 지역은 누적 대상이 아니다. cb_user_profile에서 1회 복사한 뒤 고정이다.
    region_sgg: Optional[str]

    # 발화 그대로가 아니라 검색에 쓰기 좋게 정제한 문장.
    # 매 턴 새로 만든다 (누적하면 과거 관심사가 계속 섞여 검색이 흐려진다).
    query_text: str

    # --- 흐름 제어 -----------------------------------------------------------
    # 되묻기를 한 턴에 두 번 하지 않기 위한 표시.
    asked_followup: bool
    # 필터 완화 재시도를 1회로 제한한다. 없으면 0건일 때 무한루프가 된다.
    relaxed: bool
    # '이번 검색에서만' 뺄 축. 누적 태그를 지우는 대신 이 목록으로 우회한다.
    # 사용자가 말한 사실은 검색이 0건이라고 해서 거짓이 되지 않는다.
    relaxed_axes: List[str]

    # extract_intent가 매 턴 다시 판단하는 '이제 찾아봐도 되는가'.
    # 누적하지 않는다 — 지난 턴에 켜졌다고 이번 턴도 켜져 있으면 안 된다.
    ready: bool

    # --- 검색/답변 산출 ------------------------------------------------------
    candidates: List[Dict[str, Any]]
    answer: str

    # gathering(대화 중) | ready(결과 준비됨). 프론트는 ready를 받으면
    # 입력창을 잠그고 결과 화면으로 넘어간다.
    phase: str

    # wrap_up이 만들어 둔 결과 카드. 결과 API는 이걸 그대로 읽어 내려준다.
    # 화면 전환 때 검색을 다시 돌리지 않기 위한 것이다 (임베딩+SQL 1회 절약,
    # 그리고 대화 종료 시점과 결과가 어긋나지 않는다).
    results: Dict[str, Any]
    result_summary: Dict[str, Any]


def active_filters(state: CbState) -> Dict[str, List[str]]:
    """현재 살아있는 3종 필터."""
    return {
        "life_cycle": state.get("life_cycle") or [],
        "household": state.get("household") or [],
        "theme": state.get("theme") or [],
    }


def has_any_filter(state: CbState) -> bool:
    return any(active_filters(state).values())


def initial_state(user_id: int, region_sgg: Optional[str] = None) -> CbState:
    return CbState(
        user_id=user_id,
        messages=[],
        life_cycle=[], household=[], theme=[],
        region_sgg=region_sgg,
        query_text="",
        asked_followup=False,
        relaxed=False,
        relaxed_axes=[],
        ready=False,
        candidates=[],
        answer="",
        phase="gathering",
        results={},
        result_summary={},
    )


# 어휘를 State 쪽에서도 노출해 둔다. 프롬프트가 이 목록을 그대로 쓴다.
VOCABULARY = constants.TAG_VOCABULARY
