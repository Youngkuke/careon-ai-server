"""대화 턴 오케스트레이션.

한 턴 = ① phase N의 '추출 규칙'으로 필드 추출
      → ② 코드가 phase N의 '다음 단계 판단'으로 phase 전이 결정
      → ③ 결정된 phase의 '대화 원칙'으로 답변 생성

추출과 생성을 나눈 이유: phase가 넘어가는 턴에서도 답변이 항상
'넘어간 뒤의 phase' 프롬프트로 생성되어야 대화가 자연스럽게 이어진다.
"""
import asyncio
import json
import logging
from typing import Any, Dict, Optional, Tuple

from app import llm, prompts
from app.extraction_schemas import extraction_tool
from app.profile import Profile
from app.session_store import PHASE_DONE, PHASE_MATCHING, Session

logger = logging.getLogger(__name__)

PERSONA = """당신은 '케어온'의 상담 챗봇입니다.
장애나 질병이 있는 가족을 돌보느라 학업·취업·자기돌봄이 밀린 청년(가족돌봄청년)에게
맞는 복지제도를 찾아주기 위해, 대화로 필요한 정보를 모으는 역할입니다."""

OUTPUT_RULES = """[답변 작성 규칙]
- 한국어 존댓말, 대화체. 챗봇 말풍선 하나 분량(2~4문장)으로 짧게.
- JSON, 필드명(household_members 같은 영문 키), 마크다운 헤딩/불릿을 답변에 절대 쓰지 않는다.
- 이미 파악한 정보는 다시 묻지 않는다.
- 한 번에 최대 2개 질문까지만 한다.
- 사용자가 모른다고 답한 항목은 다시 캐묻지 않는다.
- 답변 텍스트만 출력한다. 설명이나 메타 코멘트를 붙이지 않는다."""

# phase 전이 순서 (phase5 완료 후에는 매칭으로 넘어간다)
NEXT_PHASE = {1: 2, 2: 3, 3: 4, 4: 5, 5: PHASE_MATCHING}


def _known_info_block(profile: Profile) -> str:
    data = profile.to_dict()
    return json.dumps(data, ensure_ascii=False, indent=2)


def _pending_block(profile: Profile, phase: Any) -> str:
    if not isinstance(phase, int):
        return "(없음)"
    pending = profile.pending_fields(phase)
    return ", ".join(pending) if pending else "(없음 — 이 단계에서 확인할 건 다 확인했습니다)"


def build_reply_system(
    session: Session, phase: Any, just_learned: Optional[Dict[str, Any]] = None
) -> str:
    profile = session.profile
    parts = [PERSONA]

    if phase == PHASE_MATCHING:
        parts.append(
            "지금은 정보 수집이 모두 끝난 시점입니다. 아래 phase 5 지침의 마무리 화법에 따라, "
            "확인해줘서 고맙다는 인사와 함께 이제 조건에 맞는 제도를 찾아보겠다고 자연스럽게 "
            "한두 문장으로 마무리하세요. 새로운 질문은 하지 않습니다."
        )
        parts.append(prompts.reply_guide(5))
    else:
        parts.append("[지금 단계: phase {} — 아래 지침을 그대로 따르세요]".format(phase))
        parts.append(prompts.reply_guide(phase))

    parts.append("[온보딩(1단계)에서 이미 받은 정보 + 지금까지 대화로 파악한 정보]\n" + _known_info_block(profile))

    if just_learned:
        parts.append(
            "[방금 마지막 발화에서 새로 알게 된 내용 — 위 정보에도 이미 반영해뒀지만, "
            "사용자 입장에서는 지금 처음 말한 것이다. "
            "'이미 말씀하셨네요' / '이미 확인된 내용이에요' 같은 반응을 절대 하지 말 것]\n"
            + json.dumps(just_learned, ensure_ascii=False)
        )

    parts.append("[이번 단계에서 아직 확인 못 한 항목]\n" + _pending_block(profile, phase))

    if profile.unknown_fields:
        parts.append(
            "[사용자가 모른다고 답해서 다시 물으면 안 되는 항목]\n"
            + ", ".join(sorted(profile.unknown_fields))
        )
    if profile.selected_types:
        parts.append(
            "[온보딩에서 사용자가 고른 관심 유형]\n" + ", ".join(profile.selected_types)
        )
    parts.append(OUTPUT_RULES)
    return "\n\n".join(parts)


def build_extraction_system(session: Session, phase: int) -> str:
    return "\n\n".join(
        [
            PERSONA,
            "당신은 지금 대화 로그에서 정보를 추출하는 역할입니다.",
            "[phase {} 추출 규칙]".format(phase),
            prompts.extraction_guide(phase),
            "[이미 파악된 정보 — 이 값들과 같은 내용은 다시 추출하지 않는다]\n"
            + _known_info_block(session.profile),
            "[추출 원칙]\n"
            "- 마지막 사용자 발화에서 새로 확인되거나 바뀐 필드만 추출한다.\n"
            "- 언급되지 않은 필드는 절대 포함하지 않는다. 추측해서 채우지 않는다.\n"
            "- 사용자가 다음 단계에서 물어볼 정보를 먼저 말했더라도, 이 스키마에 있는 필드면 함께 추출한다.\n"
            "- 사용자가 '모른다/기억 안 난다'고 답했으면 그 필드명을 unknown_fields에 넣는다.",
        ]
    )


# 모델이 tool input을 한 겹 더 감싸서 주는 경우가 있어서 벗겨낸다
_WRAPPER_KEYS = ("parameters", "input", "fields", "extracted_fields")


def _unwrap(raw: Dict[str, Any]) -> Dict[str, Any]:
    for key in _WRAPPER_KEYS:
        inner = raw.get(key)
        if isinstance(inner, dict):
            merged = {k: v for k, v in raw.items() if k != key}
            merged.update(inner)
            return merged
    return raw


async def extract_fields(session: Session, phase: int) -> Dict[str, Any]:
    tool = extraction_tool(phase)
    try:
        raw = await llm.complete_tool(
            system=build_extraction_system(session, phase),
            messages=session.messages_for_llm(),
            tool=tool,
        )
    except Exception:  # 추출 실패가 대화 자체를 막으면 안 된다
        logger.exception("phase %s 필드 추출 실패", phase)
        return {}
    return _unwrap(raw)


async def generate_reply(
    session: Session, phase: Any, just_learned: Optional[Dict[str, Any]] = None
) -> str:
    return await llm.complete_text(
        system=build_reply_system(session, phase, just_learned),
        messages=session.messages_for_llm(),
    )


async def start_session(session: Session) -> str:
    """세션 시작 인사말 + phase1 첫 질문."""
    reply = await generate_reply(session, 1)
    session.add_turn("assistant", reply)
    return reply


def start_matching(session: Session) -> "asyncio.Task":
    """매칭을 백그라운드로 시작한다 (이미 돌고 있으면 그 작업을 재사용)."""
    from app.services import matching  # 순환 import 방지

    task = session.match_task
    if task is not None and not task.done():
        return task
    if task is not None and task.done() and task.exception() is None:
        return task

    async def _run() -> Dict[str, Any]:
        result = await matching.run_matching(session.profile)
        session.match_result = result
        return result

    session.match_task = asyncio.ensure_future(_run())
    return session.match_task


async def get_match_result(session: Session) -> Dict[str, Any]:
    """매칭 결과를 돌려준다. 아직 실행 중이면 끝날 때까지 기다린다."""
    if session.match_result is not None:
        return session.match_result
    task = start_matching(session)
    try:
        return await task
    except Exception:
        session.match_task = None  # 실패한 작업은 버리고 다음 호출에서 재시도 가능하게
        raise


async def process_turn(session: Session, message: str) -> Tuple[str, bool, Dict[str, Any]]:
    """대화 한 턴. (reply, phase_advanced, extracted_fields) 반환."""
    phase = session.current_phase
    session.add_turn("user", message)

    extracted: Dict[str, Any] = {}
    if isinstance(phase, int):
        raw = await extract_fields(session, phase)
        extracted = session.profile.merge(raw)

    session.turns_in_phase += 1

    advanced = False
    if isinstance(phase, int) and session.profile.phase_complete(phase, session.turns_in_phase):
        next_phase = NEXT_PHASE.get(phase)
        if next_phase is not None:
            session.current_phase = next_phase
            session.turns_in_phase = 0
            advanced = True
            if next_phase == PHASE_MATCHING:
                # 문서/명세대로 "서버가 내부적으로 매칭을 실행해둔다".
                # 3번 API는 이 작업을 await하므로 중복 실행이 없다.
                start_matching(session)

    reply = await generate_reply(session, session.current_phase, extracted)
    session.add_turn("assistant", reply)
    return reply, advanced, extracted
