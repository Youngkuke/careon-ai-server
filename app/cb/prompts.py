"""cb 전용 프롬프트 로더.

기존 app/prompts.py와 별개다. 그쪽은 PROMPTS_DIR 환경변수를 보지만
cb 프롬프트는 패키지에 붙어 다녀야 배포가 단순하다.
"""
import functools
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


@functools.lru_cache(maxsize=None)
def load(name: str) -> str:
    """프롬프트 md를 읽는다. 파일이 없으면 즉시 실패시킨다.

    프롬프트가 비어 있으면 LLM이 조용히 엉뚱한 출력을 내서 원인 추적이
    어렵다. 기동 시점에 터지는 편이 낫다.
    """
    path = PROMPT_DIR / f"{name}.md"
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"프롬프트가 비어 있습니다: {path}")
    return text
