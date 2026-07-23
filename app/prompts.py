"""phase*.md / matching_prompt.md 를 런타임에 읽어 프롬프트로 쓴다.

문서가 곧 프롬프트다. md 파일을 고치면 서버 재시작만으로 챗봇 화법이 바뀐다.
(개발 편의를 위해 파일 mtime 기준 캐시 무효화)
"""
import re
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.config import settings

_SECTION_RE = re.compile(r"^##\s+(.*)$", re.MULTILINE)

# 앱에 붙박이로 딸려가는 프롬프트 (PROMPTS_DIR과 무관하게 코드와 같이 배포된다)
APP_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

_cache: Dict[str, Tuple[float, "MarkdownDoc"]] = {}
_text_cache: Dict[str, Tuple[float, str]] = {}
_lock = threading.Lock()


class MarkdownDoc:
    """`## ` 헤딩 단위로 쪼갠 마크다운 문서."""

    def __init__(self, title: str, sections: List[Tuple[str, str]]):
        self.title = title
        self.sections = sections  # [(heading, body), ...] 원문 순서 유지

    def section(self, *name_startswith: str) -> Optional[str]:
        for heading, body in self.sections:
            for prefix in name_startswith:
                if heading.startswith(prefix):
                    return body
        return None

    def render(self, exclude_prefixes: Tuple[str, ...] = ()) -> str:
        parts = ["# " + self.title]
        for heading, body in self.sections:
            if any(heading.startswith(p) for p in exclude_prefixes):
                continue
            parts.append("## {}\n{}".format(heading, body))
        return "\n\n".join(parts).strip()


def _parse(text: str) -> MarkdownDoc:
    lines = text.splitlines()
    title = ""
    for line in lines:
        if line.startswith("# "):
            title = line[2:].strip()
            break

    matches = list(_SECTION_RE.finditer(text))
    sections: List[Tuple[str, str]] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append((m.group(1).strip(), text[m.end():end].strip()))
    return MarkdownDoc(title, sections)


def load_doc(filename_glob: str) -> MarkdownDoc:
    """PROMPTS_DIR에서 glob으로 md 한 개를 찾아 파싱한다."""
    matches = sorted(settings.prompts_dir.glob(filename_glob))
    if not matches:
        raise FileNotFoundError(
            "프롬프트 문서를 찾을 수 없습니다: {} (PROMPTS_DIR={})".format(
                filename_glob, settings.prompts_dir
            )
        )
    path: Path = matches[0]
    mtime = path.stat().st_mtime
    with _lock:
        cached = _cache.get(str(path))
        if cached and cached[0] == mtime:
            return cached[1]
        doc = _parse(path.read_text(encoding="utf-8"))
        _cache[str(path)] = (mtime, doc)
        return doc


def load_app_prompt(filename: str) -> str:
    """app/prompts/ 아래 md를 통째로 읽어 시스템 프롬프트로 쓴다 (mtime 캐시).

    phase*.md와 달리 섹션을 쪼개지 않는다. 문구만 고쳐서 재배포하면 바로 반영된다.
    """
    path = APP_PROMPTS_DIR / filename
    if not path.is_file():
        raise FileNotFoundError("프롬프트 파일이 없습니다: {}".format(path))
    mtime = path.stat().st_mtime
    with _lock:
        cached = _text_cache.get(str(path))
        if cached and cached[0] == mtime:
            return cached[1]
        text = path.read_text(encoding="utf-8").strip()
        _text_cache[str(path)] = (mtime, text)
        return text


def phase_doc(phase: int) -> MarkdownDoc:
    return load_doc("phase{}_*.md".format(phase))


def matching_doc() -> MarkdownDoc:
    return load_doc("matching_prompt.md")


# --- 문서에서 뽑아 쓰는 조각들 -------------------------------------------------

EXTRACTION_HEADINGS = ("추출 규칙",)


def reply_guide(phase: int) -> str:
    """답변 생성용: 추출 규칙 섹션만 빼고 문서 전체를 그대로 쓴다."""
    return phase_doc(phase).render(exclude_prefixes=EXTRACTION_HEADINGS)


def extraction_guide(phase: int) -> str:
    """추출용: 목표 + 추출 규칙 섹션."""
    doc = phase_doc(phase)
    goal = doc.section("목표", "입력") or ""
    rule = doc.section(*EXTRACTION_HEADINGS) or ""
    return "## 목표\n{}\n\n## 추출 규칙\n{}".format(goal, rule).strip()
