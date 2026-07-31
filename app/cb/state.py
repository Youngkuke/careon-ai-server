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

    # 같은 관심주제가 **누구 몫으로** 나온 것인가. theme의 부분집합이고,
    # 검색 필터로는 쓰지 않는다 (필터는 theme 하나로 계속 돈다) — 순위에서만 쓴다.
    #
    # 왜 필요한가 (2026-07-31 실측): 26세 사용자가 81세 할아버지 상담 중에
    # "저도 일자리가 빠듯하다"고 **본인** 이야기를 했는데 결과에
    # 「장애인일자리지원」(등록장애인 전용)이 섞였다. theme=[일자리]는 본인 몫이고
    # 장애 관련 사실은 할아버지 쪽인데, 두 축이 한 주머니에 들어 있어서 이 둘을
    # 곱한 제도를 걸러낼 근거가 아무 데도 없었다.
    #
    # 어느 쪽인지 불분명한 주제는 양쪽 어디에도 넣지 않는다. 잘못 귀속시키면
    # 멀쩡한 제도가 내려가는데, 안 넣으면 지금까지와 똑같이 동작할 뿐이다.
    self_themes: Annotated[List[str], merge_tags]
    caree_themes: Annotated[List[str], merge_tags]

    # 상태·등급. 3종 필터와 달리 '해당한다'와 '해당하지 않는다'를 나눠 들고 있다.
    #
    # 해당하지 않는다는 답은 결과를 좁히는 데 곧바로 쓰인다. 반면 답을 안 했거나
    # 모르는 것은 '해당하지 않음'이 아니다 — 두 경우가 섞이면 필요한 제도가
    # 사라지므로 명시적으로 아니라고 한 것만 denied에 들어간다.
    conditions: Annotated[List[str], merge_tags]
    denied_conditions: Annotated[List[str], merge_tags]
    # 상태·등급을 한 번 물었는가. 같은 질문을 두 번 하지 않기 위한 표시.
    narrow_asked: bool
    # 어디가 어떻게 불편하신지를 물어봤는가. 등급·소득보다 **먼저** 딱 한 번 묻는다.
    # 이게 없으면 "아프시다"는 말 하나로 곧장 매칭으로 넘어가서, 제도의
    # 서비스 내용과 대조할 상태 정보가 슬롯에 아예 없는 채로 검색이 돈다.
    condition_asked: bool
    # 소득을 물어본 converse 턴 수. 1단계(앵커) → 2단계(대략의 액수) →
    # 3단계(가구 안팎의 소득)로 넘어갈지 정하고, 동시에 상한이 된다.
    # 이 값이 없으면 소득 질문이 대화를 끝없이 늘린다.
    income_probes: int
    # 장애 정도를 물어봤는가. 돌봄 상태(장기요양등급·장애등록)가 확인된
    # 대화에서 소득보다 **먼저** 딱 한 번 묻는다. 등급을 모르는 사람이 많아서
    # 되물어봐야 나올 것이 없다.
    severity_asked: bool

    # --- 자격 축: 장애 정도와 소득 구간 -----------------------------------------
    # conditions('장애등록이 있다/없다')와 **별개 축**이다. 있다/없다와 정도를
    # 한 슬롯에 합치면 "장애는 있는데 정도는 모름"이라는 가장 흔한 상태를
    # 표현할 수 없다 (제도 쪽도 같은 이유로 컬럼을 나눴다 — 003 마이그레이션).
    #
    # 값은 사용자가 스스로 규정한 것이 아니라, 우회 질문("지금 받고 계신 지원이
    # 있으세요?")의 답에서 LLM이 앵커 제도명을 보고 역추론한 것이다.
    # 이용자 상당수가 자기가 어떤 행정 카테고리에 속하는지 모르기 때문이다.
    #
    # LLM은 여기까지(분류)만 하고, %로 바꾸는 변환과 856건과의 판정은
    # 전부 코드가 한다 (app/cb/grading.py).
    income_category: Optional[str]          # 생계급여 | 의료급여 | ... | 해당없음 | 모름
    disability_severity_hint: Optional[str]  # 심한 | 심하지않은 | 모름
    # 보조 경로. 자발적 발화이거나, 앵커 역추론(income_category)이 실패했을 때
    # converse가 2단계로 "대략 어느 정도"를 물어 받은 값이다. 캐묻지는 않는다
    # — 사용자가 답을 피하면 그대로 None으로 둔다 (converse.md 참고).
    monthly_income: Optional[int]           # 가구 월소득(원)
    household_size: Optional[int]           # 가구원수

    # 지역은 누적 대상이 아니다. cb_user_profile에서 1회 복사한 뒤 고정이다.
    region_sgg: Optional[str]

    # --- 초반에 먼저 확정하는 것들 ---------------------------------------------
    # 자유대화로 넘어가기 전에 확인한다. 설문처럼 순서를 고정하지는 않고,
    # 대화 중에 이미 나왔으면 묻지 않는다.
    #
    # 지금 필요한 도움이 누구를 위한 것인가: 'self' | 'caree'.
    # 영케어러는 본인 것과 돌보는 분 것이 섞여 있어서, 이걸 모르면 검색이
    # 엉뚱한 생애주기로 흐른다. 그래서 나이보다 이것을 먼저 확인한다.
    target_for: Optional[str]
    # 본인 나이. 생애주기 태그로 바꿔 검색에 쓴다. 사용자가 끝내 말하지 않으면
    # None인 채로 진행한다 (나이를 캐물으면 대화가 심문이 된다).
    age: Optional[int]
    # 돌보는 분의 연세. 돌봄 대상을 찾을 때는 이 나이가 생애주기를 정한다.
    # 본인 나이로 대신할 수 없다 — 25살이 80대 아버지를 돌보는 경우가 이 서비스의 전형이다.
    caree_age: Optional[int]
    # 위 항목들을 물어본 횟수. 답을 피하는 사용자를 붙잡아 두지 않기 위한 상한.
    intake_asked: int
    # 직전에 무엇을 물었는지. 같은 것을 두 번 물을 때 같은 문장을 되풀이하지
    # 않기 위해서만 쓴다.
    intake_last_asked: Optional[str]

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
        self_themes=[], caree_themes=[],
        conditions=[], denied_conditions=[], narrow_asked=False,
        condition_asked=False,
        income_probes=0, severity_asked=False,
        income_category=None, disability_severity_hint=None,
        monthly_income=None, household_size=None,
        region_sgg=region_sgg,
        target_for=None,
        age=None,
        caree_age=None,
        intake_asked=0,
        intake_last_asked=None,
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
