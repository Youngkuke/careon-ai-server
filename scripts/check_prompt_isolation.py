"""배치 프롬프트가 실시간 답변 경로로 새어나가지 않는지 검사한다.

배치(app/cb/prompts_batch/)는 '근거가 얕아도 추정을 허용한다'이고,
실시간(app/cb/prompts/)은 '원문에 없는 것은 말하지 않는다'이다. 두 방침이
섞이면 사용자가 챗봇 답변을 믿고 주민센터에 갔다가 헛걸음한다.

경계는 관례가 아니라 검사로 지킨다. CI나 배포 전에 돌린다:

    python scripts/check_prompt_isolation.py

종료 코드 0이면 통과, 1이면 위반이다.
"""
import ast
import io
import re
import sys
import tokenize
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
CB_DIR = ROOT / "app" / "cb"
REALTIME_PROMPT_DIR = CB_DIR / "prompts"
BATCH_PROMPT_DIR = CB_DIR / "prompts_batch"

# 실시간 답변 경로. 이 파일들이 배치 쪽을 건드리면 안 된다.
REALTIME_MODULES = ("nodes.py", "explain.py", "graph.py", "search.py",
                    "eligibility.py", "prompts.py")

# 배치가 채우는 컬럼. 실시간 경로가 SELECT하면 안 된다.
BATCH_COLUMNS = ("required_documents_ai", "required_documents_source",
                 "required_documents_generated_at", "apply_deadline",
                 "apply_period_start", "result_announcement_date",
                 "is_demo_deadline",
                 "apply_guide_easy", "apply_guide_generated_at")


def code_without_prose(path: Path) -> str:
    """주석과 docstring을 지운 소스. 검사는 '코드가 하는 일'만 봐야 한다.

    문자열 리터럴은 남긴다 — SQL 안의 컬럼명은 진짜 참조이기 때문이다.
    지우는 것은 주석과 docstring뿐이다.

    이 구분이 없으면 "이 컬럼은 배치 전용이라 여기서 읽지 않는다"라고 적어둔
    주석 자체가 위반으로 잡힌다. 실제로 explain.py에서 그렇게 잡혔고,
    거짓 양성이 쌓이면 사람이 검사 결과를 안 보게 된다.
    """
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines()

    blanked = set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not body or not isinstance(body[0], ast.Expr):
            continue
        value = body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            for ln in range(body[0].lineno, (body[0].end_lineno or body[0].lineno) + 1):
                blanked.add(ln)

    comment_at = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                comment_at.setdefault(tok.start[0], tok.start[1])
    except (tokenize.TokenError, IndentationError):
        pass

    out = []
    for i, line in enumerate(lines, 1):
        if i in blanked:
            out.append("")
        elif i in comment_at:
            out.append(line[:comment_at[i]])
        else:
            out.append(line)
    return "\n".join(out)


def check_no_batch_import(failures: List[str]) -> None:
    """실시간 모듈이 prompts_batch를 import하거나 문자열로 참조하지 않는가."""
    for name in REALTIME_MODULES:
        path = CB_DIR / name
        if not path.exists():
            continue
        if "prompts_batch" in code_without_prose(path):
            failures.append(f"{path.relative_to(ROOT)}: prompts_batch를 참조한다")


def check_load_args_are_literal(failures: List[str]) -> None:
    """prompts.load()에 넘기는 이름이 전부 literal이고, 경로 조작이 없는가.

    load()는 PROMPT_DIR / f"{name}.md"라서 '../prompts_batch/x' 같은 값을
    넘기면 배치 프롬프트를 읽을 수 있다. 호출부를 AST로 확인한다.
    """
    for path in CB_DIR.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            failures.append(f"{path.relative_to(ROOT)}: 파싱 실패 {exc}")
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_load = (
                (isinstance(func, ast.Attribute) and func.attr == "load"
                 and isinstance(func.value, ast.Name) and func.value.id == "prompts")
                or (isinstance(func, ast.Name) and func.id == "load")
            )
            if not is_load or not node.args:
                continue
            arg = node.args[0]
            where = f"{path.relative_to(ROOT)}:{node.lineno}"
            if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
                failures.append(f"{where}: prompts.load()에 literal이 아닌 값을 넘긴다")
            elif "/" in arg.value or "\\" in arg.value or ".." in arg.value:
                failures.append(f"{where}: prompts.load('{arg.value}') 경로 조작")
            elif not (REALTIME_PROMPT_DIR / f"{arg.value}.md").exists():
                failures.append(f"{where}: prompts/{arg.value}.md 가 없다")


def check_realtime_prompts_untouched(failures: List[str]) -> None:
    """실시간 프롬프트에 배치의 관대한 문구가 옮겨붙지 않았는가.

    문구 표절을 완벽히 잡을 수는 없다. 배치 프롬프트에만 있어야 할 표현이
    실시간 쪽에 나타나는지만 확인한다 — 복사해 오는 실수를 잡는 것이 목적이다.

    '억지로 채우지 않는다'는 여기서 찾지 않는다. 그건 배치 문구가 아니라
    실시간 프롬프트가 원래 갖고 있던 금지 문구다(explain.md:9,
    intent_extract.md:51). 금지형과 허용형이 같은 낱말을 쓰기 때문에, 낱말이
    아니라 '실시간에는 있을 수 없는 행위'를 가리키는 표현만 본다.
    """
    banned = re.compile(r"추정을 허용|웹 검색|웹에서 찾|web_search|검색 결과에서")
    for path in REALTIME_PROMPT_DIR.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        hit = banned.search(text)
        if hit:
            failures.append(
                f"{path.relative_to(ROOT)}: 배치용 문구로 보이는 '{hit.group()}' 발견"
            )


def check_batch_columns_not_read(failures: List[str]) -> None:
    """실시간 경로가 배치 컬럼을 SELECT하지 않는가.

    주석·docstring은 제외한다. "이 컬럼은 여기서 읽지 않는다"라고 적어둔 설명이
    위반으로 잡히면 안 된다.
    """
    for name in REALTIME_MODULES:
        path = CB_DIR / name
        if not path.exists():
            continue
        code = code_without_prose(path)
        for column in BATCH_COLUMNS:
            if column in code:
                failures.append(
                    f"{path.relative_to(ROOT)}: 배치 컬럼 '{column}'을 참조한다"
                )


def main() -> int:
    if not BATCH_PROMPT_DIR.exists():
        print("prompts_batch 디렉토리가 없습니다. 검사할 것이 없습니다.")
        return 0

    failures: List[str] = []
    check_no_batch_import(failures)
    check_load_args_are_literal(failures)
    check_realtime_prompts_untouched(failures)
    check_batch_columns_not_read(failures)

    print("=" * 68)
    print(" 프롬프트 격리 검사")
    print("=" * 68)
    print(f"  실시간 프롬프트 : {len(list(REALTIME_PROMPT_DIR.glob('*.md')))}개")
    print(f"  배치 프롬프트   : {len(list(BATCH_PROMPT_DIR.glob('*.md')))}개")

    if failures:
        print(f"\n  위반 {len(failures)}건:")
        for item in failures:
            print(f"    ✗ {item}")
        print("=" * 68)
        return 1

    print("\n  ✓ 실시간 경로가 배치 프롬프트/컬럼을 참조하지 않는다")
    print("  ✓ prompts.load() 인자가 전부 literal이고 실재하는 파일이다")
    print("  ✓ 실시간 프롬프트에 배치용 문구가 없다")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
