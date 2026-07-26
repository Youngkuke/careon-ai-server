"""LangGraph 그래프 조립 + Postgres checkpointer.

    START → extract_intent ─┬─ 아직 부족 ──→ converse ──→ END   phase=gathering
                            └─ 충분 ↓
                         search_institutions ─┬─ 0건 & 미완화 ──→ relax_filters ─┐
                                              │←──────────────────────────────────┘
                                              └─ 결과 있음 ↓
                                            wrap_up → END        phase=ready

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
def route_after_intent(state: CbState) -> str:
    """대화를 더 할지, 이제 찾아볼지 (판단 기준은 nodes.is_ready)."""
    return "search_institutions" if nodes.is_ready(state) else "converse"


def route_after_search(state: CbState) -> str:
    """0건이고 아직 완화 안 했으면 조건을 풀어 한 번 더."""
    if not state.get("candidates") and not state.get("relaxed"):
        return "relax_filters"
    return "wrap_up"


# --- 조립 ---------------------------------------------------------------------
def build_graph(checkpointer=None):
    builder = StateGraph(CbState)

    builder.add_node("extract_intent", nodes.extract_intent)
    builder.add_node("converse", nodes.converse)
    builder.add_node("search_institutions", nodes.search_institutions)
    builder.add_node("relax_filters", nodes.relax_filters)
    builder.add_node("wrap_up", nodes.wrap_up)

    builder.add_edge(START, "extract_intent")
    builder.add_conditional_edges(
        "extract_intent", route_after_intent,
        {"converse": "converse", "search_institutions": "search_institutions"},
    )
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
    }
