"""세션 저장소 (인메모리).

나중에 Redis로 바꿀 수 있도록 SessionStore 인터페이스만 갈아끼우면 되게 해둔다.
"""
import asyncio
import secrets
import threading
import time
from typing import Any, Dict, List, Optional

from app.config import settings
from app.profile import Profile

# 내부 phase 값 종류: 1,2,3,4,5,"matching",6,"done"
PHASE_MATCHING = "matching"
PHASE_DONE = "done"

# api.md '채팅 메시지 전송'의 phase(String) — 프론트 화면 제어용 두 가지뿐이다.
LABEL_INFO_GATHERING = "info_gathering"
LABEL_READY_TO_MATCH = "ready_to_match"

# 내부 phase → user_conversation_state.current_phase (DB가 integer 컬럼이라 숫자여야 한다)
# "matching"은 정보 수집이 끝난 시점이라 5, "done"은 후속질문까지 끝난 상태라 6으로 본다.
_PHASE_NUMBERS = {PHASE_MATCHING: 5, PHASE_DONE: 6}

INFO_GATHERING_PHASES = (1, 2, 3, 4, 5)


def phase_label(current_phase: Any) -> str:
    """내부 phase → 프론트 제어용 문자열."""
    return (
        LABEL_INFO_GATHERING
        if current_phase in INFO_GATHERING_PHASES
        else LABEL_READY_TO_MATCH
    )


def phase_number(current_phase: Any) -> int:
    """내부 phase → 정수 단계."""
    if isinstance(current_phase, int):
        return current_phase
    return _PHASE_NUMBERS.get(current_phase, 1)


class Session:
    def __init__(self, session_id: str, profile: Profile):
        self.session_id = session_id
        self.profile = profile
        self.current_phase: Any = 1
        self.turns_in_phase: int = 0
        self.history: List[Dict[str, str]] = []
        # 매칭 결과 캐시 (3번 API가 읽어간다)
        self.match_result: Optional[Dict[str, Any]] = None
        # phase5 확인 직후 백그라운드로 돌려두는 매칭 작업.
        # 3번 API는 이 작업을 await하므로, 중복 실행 없이 결과를 기다린다.
        self.match_task: Optional["asyncio.Task"] = None
        self.followup_done: bool = False
        self.followup_turns: int = 0
        self.created_at = time.time()
        self.updated_at = time.time()

    def add_turn(self, role: str, content: str) -> None:
        self.history.append({"role": role, "content": content})
        self.updated_at = time.time()

    def messages_for_llm(self, limit: int = 40) -> List[Dict[str, str]]:
        """Anthropic messages 포맷.

        messages는 user로 시작해야 하는데 첫 턴은 챗봇 인사말이라,
        앞에 더미 user 턴을 하나 붙인다 (히스토리에는 저장하지 않는다).
        """
        msgs = [{"role": m["role"], "content": m["content"]} for m in self.history[-limit:]]
        if not msgs or msgs[0]["role"] != "user":
            msgs.insert(0, {"role": "user", "content": "(대화 시작)"})
        return msgs


class SessionStore:
    def __init__(self) -> None:
        self._sessions: Dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, profile: Profile) -> Session:
        session_id = "sess_" + secrets.token_hex(4)
        session = Session(session_id, profile)
        with self._lock:
            self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> Optional[Session]:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if time.time() - session.updated_at > settings.session_ttl_seconds:
                del self._sessions[session_id]
                return None
            return session


store = SessionStore()
