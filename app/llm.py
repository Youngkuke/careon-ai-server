"""Anthropic Claude 호출 래퍼."""
import logging
from typing import Any, Dict, List, Optional

from anthropic import AsyncAnthropic

from app.config import settings

logger = logging.getLogger(__name__)

_client: Optional[AsyncAnthropic] = None


def client() -> AsyncAnthropic:
    global _client
    if _client is None:
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY가 설정되지 않았습니다 (.env 확인)")
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


# claude-sonnet-5에서는 temperature/top_p/top_k가 제거돼서 보내면 400이 난다.
# 또 thinking을 생략하면 adaptive thinking이 기본으로 켜지므로,
# 대화 턴처럼 지연이 중요한 호출은 명시적으로 꺼둔다.
THINKING_OFF = {"type": "disabled"}


async def complete_text(
    system: str,
    messages: List[Dict[str, Any]],
    max_tokens: Optional[int] = None,
    effort: str = "low",
) -> str:
    """자연어 답변 한 개를 생성한다."""
    resp = await client().messages.create(
        model=settings.claude_model,
        max_tokens=max_tokens or settings.reply_max_tokens,
        thinking=THINKING_OFF,
        output_config={"effort": effort},
        system=system,
        messages=messages,
    )
    return "".join(block.text for block in resp.content if block.type == "text").strip()


async def complete_tool(
    system: str,
    messages: List[Dict[str, Any]],
    tool: Dict[str, Any],
    max_tokens: Optional[int] = None,
    effort: str = "low",
) -> Dict[str, Any]:
    """tool_choice를 강제해서 구조화된 JSON을 받아온다."""
    resp = await client().messages.create(
        model=settings.claude_model,
        max_tokens=max_tokens or settings.extract_max_tokens,
        thinking=THINKING_OFF,
        output_config={"effort": effort},
        system=system,
        messages=messages,
        tools=[tool],
        tool_choice={"type": "tool", "name": tool["name"]},
    )
    return _tool_input(resp, tool["name"])


async def complete_tool_cached(
    system: Any,
    messages: List[Dict[str, Any]],
    tool: Dict[str, Any],
    max_tokens: int,
    effort: str = "high",
    thinking: bool = False,
) -> Dict[str, Any]:
    """system을 블록 리스트로 받아 prompt caching을 쓰는 tool 호출 (매칭용).

    thinking=True면 adaptive thinking을 켠다 — 57건 대조처럼 판단이 무거운 호출용.
    """
    resp = await client().messages.create(
        model=settings.claude_model,
        max_tokens=max_tokens,
        thinking={"type": "adaptive"} if thinking else THINKING_OFF,
        output_config={"effort": effort},
        system=system,
        messages=messages,
        tools=[tool],
        tool_choice={"type": "tool", "name": tool["name"]},
    )
    if resp.stop_reason == "max_tokens":
        # tool input JSON이 중간에서 잘려 항목이 깨질 수 있다
        logger.error("매칭 응답이 max_tokens(%s)에서 잘렸습니다", max_tokens)
    usage = resp.usage
    logger.info(
        "매칭 usage: input=%s cache_write=%s cache_read=%s output=%s",
        usage.input_tokens,
        getattr(usage, "cache_creation_input_tokens", None),
        getattr(usage, "cache_read_input_tokens", None),
        usage.output_tokens,
    )
    return _tool_input(resp, tool["name"])


def _tool_input(resp: Any, tool_name: str) -> Dict[str, Any]:
    for block in resp.content:
        if block.type == "tool_use" and block.name == tool_name:
            return dict(block.input or {})
    logger.warning(
        "tool_use 블록이 없습니다 (stop_reason=%s): %s", resp.stop_reason, resp.content
    )
    return {}
