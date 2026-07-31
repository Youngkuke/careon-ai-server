"""LangGraph 그래프 조립 + Postgres checkpointer.

    START ─┬─ 첫 진입(발화 없음) ──→ greet ──→ END              phase=gathering
           └─ 사용자 발화 ↓
        extract_intent ─┬─ 대상/나이 미확인 ──→ ask_intake ──→ END  phase=gathering
                        ├─ 아직 부족 ────────→ converse ────→ END  phase=gathering
                        ├─ 충분 & 정도/소득 미확인 → converse → END  phase=gathering
                        ├─ 충분 & 등급 미확인 → ask_narrow ──→ END  phase=gathering
                        └─ 충분 ↓
                     search_institutions ─┬─ 0건 & 미완화 ──→ relax_filters ─┐
                                          │←──────────────────────────────────┘
                                          └─ 결과 있음 ↓
                                        wrap_up → END            phase=ready

검색은 대화가 끝나는 시점에 한 번만 돈다. gathering 턴에는 임베딩도 SQL도 없다.

checkpointer는 psycopg(동기 드라이버 계열의 async 구현)를 쓴다. 나머지 cb 코드는
asyncpg를 쓰므로 Postgres 드라이버가 두 개 공존한다. langgraph 공식 구현을
그대로 쓰기 위한 의도된 선택이다.
"""
import asyncio
import logging
import sys
from typing import Any, Dict, Optional

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from psycopg_pool import AsyncConnectionPool

from app.cb import nodes
from app.cb.config import cb_settings
from app.cb.state import CbState

logger = logging.getLogger(__name__)


def _ensure_selector_event_loop() -> None:
    """Windows에서 psycopg의 async 모드는 SelectorEventLoop를 요구한다.

    Python 3.8+ Windows 기본값인 ProactorEventLoop에서는 커넥션이 하나도
    열리지 않고 30초 뒤 PoolTimeout으로만 나타나서 원인을 찾기 어렵다.
    (asyncpg는 Proactor에서 잘 되기 때문에 더 헷갈린다.)

    정책은 이벤트 루프가 만들어지기 '전'에 정해야 하므로 import 시점에 건다.
    운영은 Linux라 이 분기를 타지 않는다. Selector 루프는 서브프로세스를
    지원하지 않지만 이 서버는 서브프로세스를 쓰지 않는다.
    """
    if sys.platform != "win32":
        return
    policy = asyncio.get_event_loop_policy()
    if isinstance(policy, asyncio.WindowsSelectorEventLoopPolicy):
        return
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    logger.info("[cb.graph] Windows: 이벤트 루프 정책을 Selector로 전환 (psycopg 요구사항)")


_ensure_selector_event_loop()

_pool: Optional[AsyncConnectionPool] = None
_graph = None
_checkpointer: Optional[AsyncPostgresSaver] = None


# --- 분기 ---------------------------------------------------------------------
def route_from_start(state: CbState) -> str:
    """첫 진입이면 봇이 먼저 인사한다.

    사용자 발화가 하나도 없는 상태로 들어오는 경로는 스레드 생성뿐이다
    (graph.start_thread). 그때는 의도를 뽑을 대화가 없으므로 extract_intent를
    태우면 LLM 호출만 낭비된다.
    """
    return "extract_intent" if nodes.user_turns(state) else "greet"


def route_after_intent(state: CbState) -> str:
    """인테이크(대상·나이)를 끝내고, 대화를 더 할지 검색으로 갈지 정한다.

    검색으로 넘어가기 직전에 상태·등급을 한 번 확인한다. 이 질문은 대화를
    한 턴 늘리지만, 안 물으면 자격이 안 맞는 제도가 결과의 절반을 차지한다.

    검색으로 넘어갈 때가 됐어도 아직 딸 것이 남았으면 대화 턴을 더 끼운다.
    순서는 장애 정도(needs_severity_probe) → 소득(needs_income_probe)이다.
    중증도가 없으면 grading_adjust의 배율이 아예 작동하지 않는 반면, 소득은
    못 잡아도 '모르면 배제하지 않는다'로 넘어가기 때문이다.

    상태·등급 질문(ask_narrow)은 '마지막 질문'이라고 말하고 나가므로 반드시
    이 둘보다 뒤에 온다.
    """
    if not nodes.intake_done(state):
        return "ask_intake"
    if not nodes.is_ready(state):
        return "converse"
    if nodes.needs_severity_probe(state) or nodes.needs_income_probe(state):
        return "converse"
    return "ask_narrow" if nodes.needs_narrow(state) else "search_institutions"


def route_after_search(state: CbState) -> str:
    """0건이고 아직 완화 안 했으면 조건을 풀어 한 번 더."""
    if not state.get("candidates") and not state.get("relaxed"):
        return "relax_filters"
    return "wrap_up"


# --- 조립 ---------------------------------------------------------------------
def build_graph(checkpointer=None):
    builder = StateGraph(CbState)

    builder.add_node("greet", nodes.greet)
    builder.add_node("extract_intent", nodes.extract_intent)
    builder.add_node("ask_intake", nodes.ask_intake)
    builder.add_node("ask_narrow", nodes.ask_narrow)
    builder.add_node("converse", nodes.converse)
    builder.add_node("search_institutions", nodes.search_institutions)
    builder.add_node("relax_filters", nodes.relax_filters)
    builder.add_node("wrap_up", nodes.wrap_up)

    builder.add_conditional_edges(
        START, route_from_start,
        {"greet": "greet", "extract_intent": "extract_intent"},
    )
    builder.add_edge("greet", END)
    builder.add_conditional_edges(
        "extract_intent", route_after_intent,
        {"ask_intake": "ask_intake", "ask_narrow": "ask_narrow",
         "converse": "converse", "search_institutions": "search_institutions"},
    )
    builder.add_edge("ask_intake", END)
    builder.add_edge("ask_narrow", END)
    builder.add_edge("converse", END)
    builder.add_conditional_edges(
        "search_institutions", route_after_search,
        {"relax_filters": "relax_filters", "wrap_up": "wrap_up"},
    )
    # 완화한 뒤에는 반드시 다시 검색한다. relaxed=True라 두 번 완화되지 않는다.
    builder.add_edge("relax_filters", "search_institutions")
    builder.add_edge("wrap_up", END)

    return builder.compile(checkpointer=checkpointer)


# --- 수명주기 -----------------------------------------------------------------
def _checkpointer_dsn() -> str:
    """checkpointer 전용 DSN.

    테이블을 cb 스키마에 만들도록 search_path를 고정한다. 안 하면 public에
    checkpoints 테이블이 생겨서 cb 격리 원칙이 깨진다.
    asyncpg용 DSN에 붙어 있는 쿼리 파라미터는 psycopg가 모를 수 있어 그대로 쓴다.
    """
    dsn = cb_settings.database_url
    if not dsn:
        raise RuntimeError("DATABASE_URL이 설정되지 않았습니다 (.env 확인)")
    # asyncpg 표기(postgresql+asyncpg://)가 섞여 있으면 psycopg가 못 읽는다.
    return dsn.replace("postgresql+asyncpg://", "postgresql://")


async def startup() -> None:
    """앱 기동 시 1회. 풀을 열고 checkpointer 테이블을 준비한다."""
    global _pool, _graph, _checkpointer
    if _graph is not None:
        return

    _pool = AsyncConnectionPool(
        conninfo=_checkpointer_dsn(),
        min_size=2,
        max_size=20,
        # LangGraph checkpointer는 autocommit을 요구한다.
        # prepared statement는 Supabase 풀러(transaction mode)와 충돌한다.
        kwargs={"autocommit": True, "prepare_threshold": None,
                "options": "-c search_path=cb"},
        open=False,
    )
    await _pool.open(wait=True)

    _checkpointer = AsyncPostgresSaver(_pool)
    # cb.checkpoints / cb.checkpoint_writes / cb.checkpoint_blobs 를 만든다.
    # 이미 있으면 아무 일도 하지 않는다.
    await _checkpointer.setup()

    _graph = build_graph(_checkpointer)
    logger.info("[cb.graph] 준비 완료 (checkpointer=cb 스키마)")


async def shutdown() -> None:
    global _pool, _graph, _checkpointer
    _graph = None
    _checkpointer = None
    if _pool is not None:
        await _pool.close()
        _pool = None


def ready() -> bool:
    """startup()이 성공했는지. 라우터가 503을 내려줄지 판단하는 데 쓴다."""
    return _graph is not None


def graph():
    if _graph is None:
        raise RuntimeError("cb 그래프가 초기화되지 않았습니다. startup()을 먼저 부르세요.")
    return _graph


def checkpointer() -> AsyncPostgresSaver:
    """스레드 삭제('다시 시작')처럼 그래프를 거치지 않는 조작에 쓴다."""
    if _checkpointer is None:
        raise RuntimeError("cb 그래프가 초기화되지 않았습니다. startup()을 먼저 부르세요.")
    return _checkpointer


# --- 실행 ---------------------------------------------------------------------
async def start_thread(
    thread_id: str,
    user_id: int,
    region_sgg: Optional[str] = None,
) -> Dict[str, Any]:
    """대화를 열고 봇의 첫 인사를 받는다. 사용자 발화 없이 호출한다."""
    config = {"configurable": {"thread_id": thread_id}}
    payload: Dict[str, Any] = {
        "user_id": user_id,
        "messages": [],
        "age": None,
        "target_for": None,
        "intake_asked": 0,
        "phase": "gathering",
    }
    if region_sgg is not None:
        payload["region_sgg"] = region_sgg

    result = await graph().ainvoke(payload, config)
    return {"answer": result.get("answer", ""), "phase": "gathering"}


async def run_turn(
    thread_id: str,
    user_id: int,
    message: str,
    region_sgg: Optional[str] = None,
) -> Dict[str, Any]:
    """한 턴을 실행한다. 이전 대화는 checkpointer가 thread_id로 복원한다."""
    from langchain_core.messages import HumanMessage

    config = {"configurable": {"thread_id": thread_id}}
    payload: Dict[str, Any] = {
        "user_id": user_id,
        "messages": [HumanMessage(content=message)],
        # 매 턴 초기화해야 하는 것들. 지난 턴의 완화/종료 판단이 남으면
        # 이번 턴이 엉뚱하게 동작한다 (특히 ready가 남으면 대화가 곧장 끝난다).
        "relaxed": False,
        "relaxed_axes": [],
        "asked_followup": False,
        "ready": False,
    }
    if region_sgg is not None:
        payload["region_sgg"] = region_sgg

    result = await graph().ainvoke(payload, config)
    return {
        "answer": result.get("answer", ""),
        "phase": result.get("phase") or "gathering",
        "filters": {
            "life_cycle": result.get("life_cycle") or [],
            "household": result.get("household") or [],
            "theme": result.get("theme") or [],
        },
        "relaxed_axes": result.get("relaxed_axes") or [],
        # 건수만. 카드는 결과 API에서만 나간다.
        "result_summary": result.get("result_summary") or None,
        # 초반에 확정하는 것들. 프론트가 진행 상태를 보여줄 수 있게 함께 내려준다.
        "target_for": result.get("target_for"),
        "age": result.get("age"),
        "caree_age": result.get("caree_age"),
    }
