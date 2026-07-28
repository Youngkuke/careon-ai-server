"""제도별 필요서류 백필 (required_documents_ai).

이 배치는 '근거가 얕아도 추정을 허용한다'는 방침 위에 있다. 실시간 챗봇 답변의
"지어내지 않는다" 원칙과 정반대라서, 프롬프트를 app/cb/prompts_batch/에 따로
두고 app/cb/prompts.py의 load()를 쓰지 않는다 (경계 설명은 그 디렉토리의
README.md 참고). 결과 컬럼도 실시간 경로가 읽지 않는다.

# 근거 3단 — 856건 전체를 검색하지 않기 위한 장치

실측(2026-07-28, 856건)에서 근거의 분포는 이랬다:

    basfrm + 원문 서류 문구 둘 다 있음   146건
    basfrm만 있음                        505건
    원문 서류 문구만 있음                 38건   ← 여기까지 689건(80%)은 검색 불필요
    둘 다 없음                           167건   ← 이 20%만 검색한다

basfrm은 복지로가 준 '실제 서식 파일 목록'이다. 76%의 제도가 이걸 갖고 있어서,
대부분은 지어낼 필요가 없고 파일명을 사람이 읽는 서류명으로 다듬는 일에 가깝다.
그래서 LLM에게 파일 목록을 그대로 넘기고, 지침·고시·사업안내처럼 제출 서류가
아닌 항목을 걸러내게 한다.

검색은 근거가 아예 없는 167건에만 붙는다. 856건 전부에 붙이는 것보다 5배 싸고
빠르다.

# 비용·시간 (856건 전체 기준, gpt-4o)

    검색 없는 689건   입력 ~0.97M · 출력 ~62K 토큰   ≈ $3
    검색하는 167건    검색 호출 + 본문 토큰          ≈ $8~12
    합계                                             ≈ $11~15 / 15~20분

# 실행 예

    # 무엇을 어느 근거로 처리할지 계획만 본다 (LLM을 부르지 않는다)
    python scripts/backfill_required_documents.py --plan

    # 20건만 실제로 돌려보고 결과를 눈으로 확인한다 (DB에 쓰지 않는다)
    python scripts/backfill_required_documents.py --limit 20 --csv

    # 856건 전체. 여전히 dry-run이다
    python scripts/backfill_required_documents.py --csv

    # 실제로 쓴다
    python scripts/backfill_required_documents.py --apply --csv

    # 중단된 배치를 이어서 (required_documents_ai가 NULL인 행만)
    python scripts/backfill_required_documents.py --only-missing --apply

    # 검색을 아예 끄고 원문/서식만으로 (가장 싸고 빠름)
    python scripts/backfill_required_documents.py --no-search --apply

기본이 dry-run이다. 실제로 쓰려면 --apply를 붙여야 한다.
"""
import argparse
import asyncio
import csv
import json
import logging
import re
import sys
import time
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.cb import db, document_urls, embedding  # noqa: E402
from app.cb.config import cb_settings  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("req-docs")

# 배치 전용 프롬프트. app/cb/prompts.py의 PROMPT_DIR과 다른 디렉토리다.
BATCH_PROMPT_DIR = (
    Path(__file__).resolve().parent.parent / "app" / "cb" / "prompts_batch"
)
DEFAULT_CSV = Path(__file__).resolve().parent / "out" / "required_documents_review.csv"

# 근거 판정용. 원문에 서류 이야기가 있는지.
_DOC_WORD = re.compile(
    r"증명서|증명원|신분증|신청서|구비서류|제출서류|서류|통장\s*사본|등본|초본"
    r"|동의서|진단서|계약서|확인서|위임장"
)

# basfrm 항목 중 '제출 서류가 아닌 것'. 지침·고시·사업안내 PDF가 섞여 들어온다.
_NOT_SUBMISSION = re.compile(
    r"지침|고시|훈령|규정|조례|사업\s*안내|안내서|업무처리|매뉴얼|운영\s*규정|계획\s*수립"
)

SOURCE_VALUES = ("basfrm", "source_text", "web_search", "generic_fallback", "none")

# 어디에도 근거가 없을 때 넣는 최소 서류. 전부 발급 URL이 없다
# (신청서는 제도마다 이름이 달라 고정 URL을 붙일 수 없다).
#
# 현금이 오가는 제도는 받을 계좌를 확인해야 하므로 통장사본이 사실상 항상
# 붙는다. 현물·서비스 제공은 그렇지 않아서 넣으면 오히려 틀린 안내가 된다.
GENERIC_DOCUMENTS_CASH = ("신분증", "신청서", "통장사본")
GENERIC_DOCUMENTS_DEFAULT = ("신분증", "신청서")

# provision_type이 이 낱말을 포함하면 현금 계열로 본다.
# 실측 856건 중 413건이 해당한다. '현금지급, 현물지급'처럼 여러 값이 쉼표로
# 이어진 경우도 현금이 섞여 있으면 계좌가 필요하므로 포함시킨다.
# '현금대여(융자)'는 제외한다 — 대여는 별도 약정 서류를 따로 받는다.
_CASH_MARKER = "현금지급"


def generic_documents(row: Dict[str, Any]) -> Tuple[str, ...]:
    """근거가 없을 때 넣을 최소 서류를 provision_type으로 고른다."""
    provision = row.get("provision_type") or ""
    return GENERIC_DOCUMENTS_CASH if _CASH_MARKER in provision else GENERIC_DOCUMENTS_DEFAULT

# LLM이 돌려줄 JSON 형태. strict json_schema로 고정한다.
# documents는 객체 배열이다 — 검색 갈래에서 LLM이 발급처 URL을 함께 돌려줄 수
# 있어야 하기 때문이다. 표준 증명서류의 URL은 LLM이 아니라 사전이 붙인다.
_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["documents", "source", "confidence", "note"],
    "properties": {
        "documents": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "url"],
                "properties": {
                    "name": {"type": "string"},
                    # 못 찾았으면 빈 문자열. strict 모드에서 null을 허용하려면
                    # 타입 배열을 써야 하는데 모델이 자주 어긴다.
                    "url": {"type": "string"},
                },
            },
        },
        "source": {"type": "string", "enum": list(SOURCE_VALUES)},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "note": {"type": "string"},
    },
}

# 프롬프트에 넣을 원문 필드와 자르는 길이 (explain.py와 같은 방식).
_SECTIONS = (
    ("제도명", "serv_nm", 200),
    ("한 줄 요약", "serv_dgst", 300),
    ("지원대상", "target_detail", 900),
    ("선정기준", "select_criteria", 900),
    ("서비스내용", "service_content", 700),
    ("신청방법", "apply_method", 700),
    ("담당기관", "jur_org_nm", 200),
)


def load_batch_prompt(name: str = "required_documents_backfill") -> str:
    """배치 프롬프트를 읽는다.

    app/cb/prompts.py의 load()를 일부러 쓰지 않는다. 그쪽은 실시간 답변용
    디렉토리에 묶여 있고, 두 방침이 한 로더를 공유하면 경계가 흐려진다.
    """
    path = BATCH_PROMPT_DIR / f"{name}.md"
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"배치 프롬프트가 비어 있습니다: {path}")
    return text


# --- 근거 판정 -----------------------------------------------------------------
def form_names(row: Dict[str, Any]) -> List[str]:
    """extra_info.basfrm의 파일명 목록 (중복 제거, 순서 유지)."""
    out: List[str] = []
    for item in (row.get("extra_info") or {}).get("basfrm") or []:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        if name and name not in out:
            out.append(name)
    return out


def submission_forms(names: List[str]) -> List[str]:
    """파일명 중 '제출 서류로 보이는 것'만. 지침·고시류는 뺀다.

    최종 판단은 LLM이 한다 — 여기서는 '근거가 있는지'를 세는 용도라서
    거칠게만 거른다. 목록 자체는 통째로 프롬프트에 넘긴다.
    """
    return [n for n in names if not _NOT_SUBMISSION.search(n)]


def source_body(row: Dict[str, Any]) -> str:
    return "\n".join(
        (row.get(key) or "") for key in
        ("target_detail", "select_criteria", "service_content", "apply_method")
    )


def evidence_of(row: Dict[str, Any]) -> Tuple[str, bool]:
    """(근거 종류, 검색이 필요한가).

    근거 종류는 LLM에게 강제하는 값이 아니라 '어느 갈래로 보낼지'의 판단이다.
    실제로 무엇을 근거로 삼았는지는 LLM이 응답의 source로 돌려준다.
    """
    if submission_forms(form_names(row)):
        return "basfrm", False
    if _DOC_WORD.search(source_body(row)):
        return "source_text", False
    return "web_search", True


def build_user_block(row: Dict[str, Any]) -> str:
    """원문 + 서식 파일 목록 + 지역. 비어 있는 항목은 아예 넣지 않는다.

    빈 값을 "없음"으로 넣으면 LLM이 "구비서류가 없습니다" 같은 단정을 만든다
    (explain.py / 001 마이그레이션 주석과 같은 이유).
    """
    lines: List[str] = []
    for label, key, limit in _SECTIONS:
        value = (row.get(key) or "").strip()
        if value:
            lines.append(f"[{label}]\n{value[:limit]}")

    region = " ".join(x for x in (row.get("ctpv_nm"), row.get("sgg_nm")) if x)
    if region:
        lines.append(f"[지역]\n{region}")

    names = form_names(row)
    if names:
        listed = "\n".join(f"- {n}" for n in names[:20])
        lines.append(f"[서식 파일 목록]\n{listed}")

    if row.get("detail_link"):
        lines.append(f"[복지로 원문]\n{row['detail_link']}")

    return "\n\n".join(lines)


# --- LLM 호출 ------------------------------------------------------------------
def _clean_documents(values: Any) -> List[Dict[str, Optional[str]]]:
    """LLM 출력에서 {name, url} 목록을 뽑아 정규화한다.

    문자열 배열로 돌려주는 경우도 받아준다 (프롬프트를 어겼을 때의 방어).
    """
    out: List[Dict[str, Optional[str]]] = []
    seen = set()
    for value in values if isinstance(values, list) else []:
        if isinstance(value, dict):
            raw_name, raw_url = value.get("name"), value.get("url")
        else:
            raw_name, raw_url = value, None
        name = " ".join(str(raw_name or "").split()).strip(" ·-")
        # 40자를 넘는 것은 서류명이 아니라 설명 문장이다.
        if not name or len(name) > 40 or name in seen:
            continue
        seen.add(name)
        url = (str(raw_url or "").strip() or None)
        if url and not url.startswith(("http://", "https://")):
            url = None
        out.append({"name": name, "url": url})
    return out[:8]


def _basfrm_index(row: Dict[str, Any]) -> List[Tuple[str, str]]:
    """(정규화된 파일명, 다운로드 URL) 목록. 서식 이름 매칭에 쓴다."""
    index: List[Tuple[str, str]] = []
    for item in (row.get("extra_info") or {}).get("basfrm") or []:
        if not isinstance(item, dict):
            continue
        name, url = (item.get("name") or "").strip(), (item.get("value") or "").strip()
        if name and url:
            index.append((_match_key(name), url))
    return index


def _match_key(text: str) -> str:
    """서식 파일명과 LLM이 다듬은 서류명을 비교하기 위한 키.

    확장자·별지번호·근거법령 괄호·공백을 걷어낸다. LLM이
    '[별지 제100호서식] (육아휴직) 급여 신청서(고용보험법 시행규칙).hwp'를
    '육아휴직 급여 신청서'로 다듬어 내놓기 때문에, 양쪽을 같은 방식으로
    깎아야 서로 만난다.

    괄호를 전부 걷어내지는 않는다. '(육아휴직) 급여 신청서'에서 괄호를 지우면
    키가 '급여신청서'로 짧아져, 같은 제도의 '출산전후휴가 급여 신청서'와
    구분되지 않는다. 근거법령·고시처럼 서류를 특정하지 않는 괄호만 지운다.
    """
    body = re.sub(r"\.(hwpx?|pdf|docx?|xlsx?|zip)$", "", text.strip(), flags=re.I)
    body = re.sub(r"\[[^\]]*\]", "", body)                    # [별지 제100호서식]
    # (고용보험법 시행규칙), (제2026-11호), (2) 처럼 서류를 특정하지 않는 괄호
    body = re.sub(r"\((?=[^)]*(?:법|령|규칙|고시|훈령|조례|지침|시행|제\d))[^)]*\)", "", body)
    body = re.sub(r"\(\s*\d+\s*\)", "", body)
    body = re.sub(r"별지\s*제?\s*\d+\s*호(의\d+)?\s*서식", "", body)
    body = re.sub(r"[\s·ㆍ・_\-—–,¸()]", "", body)
    return body


def _find_form_url(name: str, index: List[Tuple[str, str]]) -> Optional[str]:
    """서류명이 실제 서식 파일 중 어느 것인지 찾는다. 없으면 None.

    포함 관계를 먼저 보고, 안 되면 유사도로 본다. 임계값 0.62는 실측에서
    '육아휴직급여신청서' ↔ '육아휴직육아기근로시간단축급여신청서'는 붙고
    '개인정보수집이용동의서' ↔ '소득재산신고서'는 안 붙는 지점이다.
    """
    key = _match_key(name)
    if len(key) < 3:
        return None
    for form_key, url in index:
        if key in form_key or form_key in key:
            return url
    best_url, best_score = None, 0.0
    for form_key, url in index:
        score = SequenceMatcher(None, key, form_key).ratio()
        if score > best_score:
            best_url, best_score = url, score
    return best_url if best_score >= 0.62 else None


def attach_urls(
    documents: List[Dict[str, Optional[str]]], row: Dict[str, Any]
) -> List[Dict[str, Optional[str]]]:
    """서류마다 url + url_type을 붙인다.

    우선순위가 중요하다:
      1) 고정 사전   — 사람이 직접 열어 확인한 URL이다. LLM 출력보다 믿는다.
      2) basfrm 서식 — 이 제도의 실제 첨부 파일이라 가장 구체적이다.
      3) LLM이 검색으로 찾은 URL — 검증되지 않았으므로 마지막이다.
      4) 없으면 null — 억지로 만들지 않는다.

    신분증·통장 사본처럼 발급처가 원래 없는 서류는 2·3단계를 건너뛴다.
    LLM이 엉뚱한 링크를 붙여 오는 것을 막기 위해서다.
    """
    index = _basfrm_index(row)
    out: List[Dict[str, Optional[str]]] = []
    for doc in documents:
        name = doc["name"]
        url: Optional[str] = None
        url_type: Optional[str] = None

        entry = document_urls.lookup(name)
        if entry is not None:
            url, url_type = entry.url, document_urls.URL_TYPE_CERTIFICATE
        elif document_urls.has_no_url(name):
            url, url_type = None, None
        else:
            form_url = _find_form_url(name, index)
            if form_url:
                url, url_type = form_url, document_urls.URL_TYPE_FORM
            elif doc.get("url"):
                url, url_type = doc["url"], document_urls.URL_TYPE_INFO

        item: Dict[str, Optional[str]] = {"name": name, "url": url}
        if url:
            item["url_type"] = url_type
        out.append(item)
    return out


async def classify_no_search(row: Dict[str, Any], system: str) -> Dict[str, Any]:
    """원문·서식만으로 판단. chat.completions + strict json_schema."""
    resp = await embedding.with_retry(
        lambda: embedding.client().chat.completions.create(
            model=cb_settings.openai_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": build_user_block(row)},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "required_documents", "strict": True, "schema": _SCHEMA,
                },
            },
            temperature=0,
        ),
        label="req-docs servId=%s" % row.get("serv_id"),
    )
    return json.loads(resp.choices[0].message.content or "{}")


# 검색을 허용할 도메인. 대한민국 공공기관만 남긴다.
#   go.kr   중앙부처·지자체·공공기관 (bokjiro.go.kr, nrc.go.kr, scourt.go.kr …)
#   gov.kr  정부24
#   or.kr   공단·공사 계열 (nhis.or.kr, nps.or.kr …)
# 블로그·카페·뉴스가 근거로 섞여 들어오는 것을 프롬프트가 아니라 도구 수준에서
# 막는다. 프롬프트만으로는 실제로 새어 들어왔다 (농어가목돈마련저축 건).
SEARCH_ALLOWED_DOMAINS = ["go.kr", "gov.kr", "or.kr"]


def _search_tool(tool_type: str, restrict: bool) -> Dict[str, Any]:
    tool: Dict[str, Any] = {"type": tool_type}
    if restrict:
        tool["filters"] = {"allowed_domains": SEARCH_ALLOWED_DOMAINS}
    return tool


async def classify_with_search(
    row: Dict[str, Any], system: str, tool_type: str, restrict: bool = True
) -> Dict[str, Any]:
    """웹 검색을 붙여 판단. Responses API의 web_search 도구를 쓴다.

    검색 도구가 붙으면 strict json_schema를 함께 쓸 수 없는 조합이 있어서,
    형식은 프롬프트로 지시하고 응답에서 JSON을 직접 파싱한다.

    allowed_domains 필터를 지원하지 않는 모델·도구 조합이 있어서, 거부당하면
    필터 없이 한 번 더 시도한다 — 그때는 프롬프트의 출처 규칙에만 의존한다.
    """
    async def call(tool: Dict[str, Any]):
        return await embedding.client().responses.create(
            model=cb_settings.openai_model,
            tools=[tool],
            input=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": build_user_block(row)
                    + "\n\n[지시]\n이 제도의 공식 신청 안내를 웹에서 찾아 필요서류를"
                      " 확인한 뒤, 지정된 JSON 객체 하나만 출력하세요."
                      " 출처는 반드시 .go.kr / .gov.kr / .or.kr 공식 사이트여야 합니다.",
                },
            ],
        )

    try:
        resp = await embedding.with_retry(
            lambda: call(_search_tool(tool_type, restrict)),
            label="req-docs(web) servId=%s" % row.get("serv_id"),
        )
    except Exception as exc:  # noqa: BLE001
        if not restrict or "filters" not in str(exc):
            raise
        logger.warning("도메인 필터를 지원하지 않아 필터 없이 재시도합니다: %s", exc)
        _FILTER_UNSUPPORTED.add(True)
        resp = await embedding.with_retry(
            lambda: call(_search_tool(tool_type, False)),
            label="req-docs(web,nofilter) servId=%s" % row.get("serv_id"),
        )
    return _parse_loose_json(resp.output_text or "")


# 도메인 필터가 거부당한 적이 있는가. 리포트에서 알려주기 위한 표시.
_FILTER_UNSUPPORTED: set = set()


def _parse_loose_json(text: str) -> Dict[str, Any]:
    """코드펜스나 앞뒤 설명이 섞여 나와도 JSON 객체를 건져낸다."""
    body = text.strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", body).strip()
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        pass
    start, end = body.find("{"), body.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(body[start:end + 1])
        except json.JSONDecodeError:
            pass
    logger.warning("JSON 파싱 실패 (앞 200자): %s", body[:200])
    return {}


async def classify_one(
    row: Dict[str, Any], system: str, use_search: bool, tool_type: str
) -> Dict[str, Any]:
    planned_source, needs_search = evidence_of(row)
    searched = needs_search and use_search

    if searched:
        raw = await classify_with_search(row, system, tool_type)
    else:
        raw = await classify_no_search(row, system)

    documents = _clean_documents(raw.get("documents"))
    source = raw.get("source")
    if source not in SOURCE_VALUES:
        # LLM이 엉뚱한 값을 주면 우리 판정으로 되돌린다. 검색을 안 돌린 건에
        # web_search가 붙는 일이 없어야 신뢰도 집계가 의미를 갖는다.
        source = planned_source if not needs_search else ("web_search" if searched else "none")
    if not searched and source == "web_search":
        source = planned_source

    confidence = raw.get("confidence") or "low"
    note = (raw.get("note") or "").strip()

    if not documents:
        # 어디에도 근거가 없는 건. 데모 화면에 빈 섹션이 뜨는 것보다 최소한을
        # 채운다. 지어낸 값임은 source=generic_fallback이 기록한다.
        # note는 덮어쓰지 않는다 — LLM이 왜 못 찾았는지가 사람이 볼 정보다.
        # (그래서 note와 documents가 어긋날 수 있다. CSV 첫 줄에 적어둔다.)
        documents = [{"name": n, "url": None} for n in generic_documents(row)]
        source = "generic_fallback"
        confidence = "low"
        note = note or "원문·서식·검색 어디에도 근거가 없어 최소 서류만 넣음"

    documents = attach_urls(documents, row)

    return {
        "serv_id": row["serv_id"],
        "serv_nm": row.get("serv_nm") or "",
        "documents": documents,
        "source": source,
        "confidence": confidence,
        "note": note,
        "planned_source": planned_source,
        "searched": searched,
        "form_count": len(form_names(row)),
        "url_count": sum(1 for d in documents if d.get("url")),
    }


async def classify_many(
    rows: List[Dict[str, Any]], system: str, use_search: bool,
    tool_type: str, concurrency: int, search_concurrency: int,
    save_every: int = 0,
) -> List[Dict[str, Any]]:
    """두 갈래를 각자의 동시성으로 돌린다.

    검색 갈래를 낮게 잡는 이유는 호출당 8~15초가 걸리고 토큰도 크기 때문이다.
    검색 없는 갈래의 기본값이 2인 것은 embedding.infer_tags_many와 같은
    이유다 — gpt-4o TPM에 부딪히면 재시도로 시간이 더 든다.
    """
    plain = [r for r in rows if not evidence_of(r)[1]]
    searching = [r for r in rows if evidence_of(r)[1]]
    logger.info(
        "검색 없이 %d건(동시 %d) / 검색 %d건(동시 %d)",
        len(plain), concurrency,
        len(searching) if use_search else 0, search_concurrency,
    )

    results: List[Dict[str, Any]] = []
    failures: List[str] = []
    done = [0]
    total = len(rows)
    # 아직 저장하지 않은 결과. save_every마다 비운다.
    pending: List[Dict[str, Any]] = []
    saved = [0]
    save_lock = asyncio.Lock()

    async def flush(force: bool = False) -> None:
        """중간 커밋. 856건을 끝까지 들고 있다가 한 번에 쓰면, 3시간짜리 실행이
        중간에 끊길 때 전부 날아가고 --only-missing 재개도 무의미해진다.
        """
        if not save_every or (not force and len(pending) < save_every):
            return
        async with save_lock:
            if not pending:
                return
            batch, pending[:] = list(pending), []
        try:
            saved[0] += await db.save_required_documents(batch)
            logger.info("중간 커밋 %d건 (누적 저장 %d/%d)", len(batch), saved[0], total)
        except Exception:  # noqa: BLE001 — 저장 실패가 남은 작업을 멈추면 안 된다
            logger.exception("중간 커밋 실패 %d건 — 이 묶음은 --only-missing으로 재시도됨",
                             len(batch))

    async def worker(row: Dict[str, Any], sem: asyncio.Semaphore) -> None:
        async with sem:
            try:
                result = await classify_one(row, system, use_search, tool_type)
                results.append(result)
                pending.append(result)
            except Exception as exc:  # noqa: BLE001 — 1건 실패가 배치를 멈추면 안 된다
                failures.append("{}({})".format(row.get("serv_id"), type(exc).__name__))
            done[0] += 1
            if done[0] % 25 == 0 or done[0] == total:
                logger.info("진행 %d/%d", done[0], total)
            await flush()

    plain_sem = asyncio.Semaphore(concurrency)
    search_sem = asyncio.Semaphore(search_concurrency)
    tasks = [worker(r, plain_sem) for r in plain]
    tasks += [worker(r, search_sem) for r in (searching if use_search else [])]
    await asyncio.gather(*tasks)

    if not use_search and searching:
        # --no-search: 근거가 없는 건은 LLM을 부르지 않고 최소 서류만 넣는다.
        skipped = [{
            "serv_id": r["serv_id"], "serv_nm": r.get("serv_nm") or "",
            "documents": attach_urls(
                [{"name": n, "url": None} for n in generic_documents(r)], r),
            "source": "generic_fallback", "confidence": "low",
            "note": "--no-search로 건너뜀 (원문·서식에 근거 없음)",
            "planned_source": "web_search", "searched": False,
            "form_count": len(form_names(r)), "url_count": 0,
        } for r in searching]
        results.extend(skipped)
        pending.extend(skipped)

    # 남은 것을 마저 저장한다. --no-search 건까지 pending에 넣은 뒤에 부른다.
    await flush(force=True)

    if failures:
        logger.error(
            "최종 실패 %d/%d건: %s%s",
            len(failures), total, ", ".join(failures[:10]),
            " …" if len(failures) > 10 else "",
        )
    return results


# --- 리포트 --------------------------------------------------------------------
def print_plan(rows: List[Dict[str, Any]]) -> None:
    counts = Counter(evidence_of(r)[0] for r in rows)
    total = len(rows)
    print()
    print("=" * 72)
    print(f" 필요서류 백필 계획 — 전체 {total}건 (LLM을 부르지 않았습니다)")
    print("=" * 72)
    print(f"  basfrm      (실제 서식 파일에서 추출)  {counts['basfrm']:>4}건  검색 불필요")
    print(f"  source_text (원문 서류 문구에서 추출)  {counts['source_text']:>4}건  검색 불필요")
    print(f"  web_search  (근거 없음 → 웹 보강)      {counts['web_search']:>4}건  검색 대상")
    no_search = counts["basfrm"] + counts["source_text"]
    print(f"\n  검색 없이 처리 가능: {no_search}건 ({no_search / total * 100:.0f}%)"
          if total else "")
    print("=" * 72)
    print()


def print_report(results: List[Dict[str, Any]], elapsed: float) -> None:
    total = len(results)
    filled = [r for r in results if r["documents"]]
    print()
    print("=" * 72)
    print(f" 필요서류 백필 결과 — {total}건 / {elapsed:.0f}초")
    print("=" * 72)
    print(f"  서류를 채운 건 : {len(filled):>4}건 "
          f"({len(filled) / total * 100:.0f}%)" if total else "")
    print(f"  빈 배열로 남긴 건: {total - len(filled):>4}건")

    print("\n  [근거별]")
    for source, n in Counter(r["source"] for r in results).most_common():
        print(f"    {source:<12} {n:>4}건")

    print("\n  [confidence]")
    for level, n in Counter(r["confidence"] for r in results).most_common():
        print(f"    {level:<12} {n:>4}건")

    if filled:
        lengths = Counter(len(r["documents"]) for r in filled)
        print("\n  [서류 개수]")
        for count, n in sorted(lengths.items()):
            print(f"    {count}개 {'#' * min(n // 5, 50)} {n}건")

        all_docs = [d for r in filled for d in r["documents"]]
        with_url = [d for d in all_docs if d.get("url")]
        print(f"\n  [URL] 서류 {len(all_docs)}개 중 링크 있음 {len(with_url)}개 "
              f"({len(with_url) / len(all_docs) * 100:.0f}%)")
        for kind, n in Counter(d["url_type"] for d in with_url).most_common():
            print(f"    {kind:<22} {n:>4}개")

        print("\n  [가장 자주 나온 서류]")
        for (name, url), n in Counter(
            (d["name"], d.get("url")) for d in all_docs
        ).most_common(15):
            mark = "🔗" if url else "  "
            print(f"    {mark} {name:<28} {n:>4}건")

        print("\n  [표본 5건]")
        for r in filled[:5]:
            print(f"    {r['serv_nm'][:36]:<38} {r['source']}")
            for d in r["documents"]:
                link = d.get("url") or "-"
                print(f"        {d['name']:<26} {link[:56]}")

    print("=" * 72)
    print()


_CSV_COLUMNS = [
    "serv_id", "serv_nm", "document", "url", "url_type",
    "source", "planned_source", "searched", "confidence", "note",
]


def write_csv(results: List[Dict[str, Any]], path: Path) -> int:
    """사람이 확인할 결과를 CSV로. 서류 1개 = 1행이라 URL을 눈으로 훑기 쉽다.

    Excel 한글 깨짐 방지로 utf-8-sig.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        # 헤더 앞 주석. source=generic_fallback인 행은 LLM이 '근거를 못 찾았다'고
        # 판단한 뒤 시스템이 최소 서류를 채운 것이라, note와 document가 서로
        # 다른 이야기를 한다. 읽는 사람이 모순으로 오해하지 않도록 적어둔다.
        handle.write("# note는 fallback 적용 전 LLM 판단이라 documents와 다를 수 있음"
                     " (source=generic_fallback인 행)\n")
        writer = csv.DictWriter(handle, fieldnames=_CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for r in sorted(results, key=lambda x: (x["source"], x["serv_id"])):
            for doc in r["documents"] or [{"name": "", "url": None}]:
                writer.writerow({
                    **r,
                    "document": doc["name"],
                    "url": doc.get("url") or "",
                    "url_type": doc.get("url_type") or "",
                })
                rows += 1
    logger.info("확인용 CSV %d행(서류 단위) → %s", rows, path)
    return rows


# --- 실행 ----------------------------------------------------------------------
async def run(args: argparse.Namespace) -> None:
    rows = await db.fetch_rows_for_required_documents(only_missing=args.only_missing)
    if args.limit:
        rows = rows[:args.limit]
    logger.info("대상 %d건 조회", len(rows))

    if args.plan:
        print_plan(rows)
        return

    print_plan(rows)
    system = load_batch_prompt()
    started = time.perf_counter()
    # --apply일 때만 중간 커밋한다. dry-run에서 쓰면 안 된다.
    save_every = args.save_every if args.apply else 0
    if save_every:
        logger.info("%d건마다 중간 커밋합니다. 끊겨도 --only-missing으로 이어서 돌 수 있습니다",
                    save_every)
    results = await classify_many(
        rows, system,
        use_search=not args.no_search,
        tool_type=args.search_tool,
        concurrency=args.concurrency,
        search_concurrency=args.search_concurrency,
        save_every=save_every,
    )
    elapsed = time.perf_counter() - started
    print_report(results, elapsed)

    if args.csv:
        write_csv(results, Path(args.csv))

    if not args.apply:
        logger.info("dry-run입니다. DB에 쓰지 않았습니다 — 실제로 쓰려면 --apply")
        return

    if save_every:
        # classify_many가 이미 전부 저장했다. 여기서 또 쓰면 856행을 통째로
        # 다시 UPDATE하게 되고, generated_at만 무의미하게 밀린다.
        logger.warning("필요서류 %d건 저장 완료 (중간 커밋) "
                       "— AI 추정값이다. 화면에서 그렇게 밝혀야 한다", len(results))
        return

    saved = await db.save_required_documents(results)
    logger.warning("필요서류 %d건 저장 (AI 추정값이다 — 화면에서 그렇게 밝혀야 한다)", saved)


async def main_async(args: argparse.Namespace) -> None:
    try:
        await run(args)
    finally:
        await embedding.close()
        await db.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="제도별 필요서류 백필 (AI 추정)")
    parser.add_argument("--apply", action="store_true",
                        help="실제로 DB에 쓴다. 없으면 dry-run이다")
    parser.add_argument("--plan", action="store_true",
                        help="근거 분포만 보고 끝낸다. LLM을 부르지 않는다")
    parser.add_argument("--limit", type=int, default=None,
                        help="앞에서 N건만 처리한다 (표본 확인용)")
    parser.add_argument("--only-missing", action="store_true",
                        help="required_documents_ai가 NULL인 행만 (중단된 배치 재개)")
    parser.add_argument("--save-every", type=int, default=50,
                        help="N건마다 중간 커밋한다 (기본 50, 0이면 끝에 한 번). "
                             "--apply일 때만 동작한다")
    parser.add_argument("--no-search", action="store_true",
                        help="웹 검색을 끈다. 근거 없는 건은 빈 배열로 남는다")
    parser.add_argument("--search-tool", default="web_search",
                        help="Responses API 검색 도구 타입 "
                             "(기본 web_search, 모델에 따라 web_search_preview)")
    parser.add_argument("--concurrency", type=int, default=2,
                        help="검색 없는 갈래의 동시 호출 수 (기본 2, TPM 때문에 낮다)")
    parser.add_argument("--search-concurrency", type=int, default=3,
                        help="검색 갈래의 동시 호출 수 (기본 3)")
    parser.add_argument("--csv", nargs="?", const=str(DEFAULT_CSV), default=None,
                        help="확인용 CSV 경로 (기본 scripts/out/required_documents_review.csv)")
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
