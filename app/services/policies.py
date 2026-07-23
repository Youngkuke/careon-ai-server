"""제도(policies) 조회 + 매칭 프롬프트용 텍스트 블록 변환.

매칭 프롬프트 문서 '입력' 2번:
  "제도 목록 (57건, policies + connect_policy_policy_types +
   connect_policy_documents 조인 결과를 텍스트 블록으로 변환한 것)"
"""
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from app import db

logger = logging.getLogger(__name__)

# 매칭 판단에 쓰는 컬럼만 고른다.
# original_notice(공고 원문 전체)는 토큰만 잡아먹고 판단에는 안 쓰여서 제외.
POLICY_COLUMNS = """
    p.policy_id, p.policy_name, p.agency_id, a.agency_name, p.category,
    p.summary, p.support_period, p.cost, p.duration,
    p.application_method, p.application_region, p.schedule_type,
    p.age_min, p.age_max, p.exception_age,
    p.income_criteria, p.qualification_text, p.support_target,
    p.duplication_restriction, p.notes,
    p.deadline_type, p.deadline_date_raw, p.application_deadline,
    p.result_note, p.result_date, p.link, p.contact, p.is_lifetime_limit_once,
    p.info_reference_year
"""

_CATALOG_CACHE: Optional[List[Dict[str, Any]]] = None
_CATALOG_AT: float = 0.0
_CATALOG_TTL = 600.0  # 10분


async def fetch_policies(policy_ids: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    """정책 + 유형(다대다) + 필요서류를 조인해서 가져온다."""
    where = ""
    args: List[Any] = []
    if policy_ids is not None:
        if not policy_ids:
            return []
        where = "WHERE p.policy_id = ANY($1::int[])"
        args = [policy_ids]

    rows = await db.fetch(
        """
        SELECT {cols},
               COALESCE(t.type_names, ARRAY[]::text[])        AS policy_types,
               COALESCE(t.type_ids, ARRAY[]::int[])           AS policy_type_ids,
               COALESCE(d.document_ids, ARRAY[]::int[])       AS document_ids,
               COALESCE(d.document_names, ARRAY[]::text[])    AS document_names
        FROM policies p
        JOIN agencies a ON a.agency_id = p.agency_id
        LEFT JOIN (
            SELECT cppt.policy_id,
                   array_agg(pt.type_name      ORDER BY pt.policy_type_id) AS type_names,
                   array_agg(pt.policy_type_id ORDER BY pt.policy_type_id) AS type_ids
            FROM connect_policy_policy_types cppt
            JOIN policy_types pt ON pt.policy_type_id = cppt.policy_type_id
            GROUP BY cppt.policy_id
        ) t ON t.policy_id = p.policy_id
        LEFT JOIN (
            SELECT cpd.policy_id,
                   array_agg(doc.document_id   ORDER BY doc.document_id) AS document_ids,
                   array_agg(doc.document_name ORDER BY doc.document_id) AS document_names
            FROM connect_policy_documents cpd
            JOIN documents doc ON doc.document_id = cpd.document_id
            GROUP BY cpd.policy_id
        ) d ON d.policy_id = p.policy_id
        {where}
        ORDER BY p.policy_id
        """.format(cols=POLICY_COLUMNS, where=where),
        *args,
    )
    return rows


async def get_catalog() -> List[Dict[str, Any]]:
    """매칭에 쓸 전체 제도 목록 (TTL 캐시)."""
    global _CATALOG_CACHE, _CATALOG_AT
    if _CATALOG_CACHE is not None and time.time() - _CATALOG_AT < _CATALOG_TTL:
        return _CATALOG_CACHE
    _CATALOG_CACHE = await fetch_policies()
    _CATALOG_AT = time.time()
    logger.info("제도 카탈로그 %d건 로드", len(_CATALOG_CACHE))
    return _CATALOG_CACHE


# --- 화면 표시용 변환 (API_SPEC 5) -------------------------------------------

# fetch_policies가 돌려주는 행에서 상세 응답으로 그대로 옮기는 컬럼
_DETAIL_FIELDS = (
    "policy_id",
    "policy_name",
    "agency_id",
    "agency_name",
    "category",
    "summary",
    "support_period",
    "cost",
    "age_min",
    "age_max",
    "exception_age",
    "application_method",
    "deadline_type",
    "deadline_date_raw",
    "application_deadline",
    "result_note",
    "result_date",
    "link",
    "contact",
    "is_lifetime_limit_once",
)


def to_detail(row: Dict[str, Any]) -> Dict[str, Any]:
    """DB 행을 API_SPEC 5번 상세 응답 형태로 변환한다.

    document_ids / document_names는 같은 순서로 집계된 배열이라 zip으로 묶는다.
    """
    detail = {key: row.get(key) for key in _DETAIL_FIELDS}
    # api.md 기준 필드명은 policy_types이고 {policy_type_id, type_name} 객체 배열이다.
    # (예전엔 type_name만 담은 field였다. 유형 2개 이상인 제도가 절반이라 배열이다.)
    detail["policy_types"] = [
        {"policy_type_id": type_id, "type_name": type_name}
        for type_id, type_name in zip(
            row.get("policy_type_ids") or [], row.get("policy_types") or []
        )
    ]
    detail["required_documents"] = [
        {"document_id": doc_id, "document_name": doc_name}
        for doc_id, doc_name in zip(
            row.get("document_ids") or [], row.get("document_names") or []
        )
    ]
    return detail


# --- 텍스트 블록 변환 --------------------------------------------------------

_FIELD_LABELS = [
    ("policy_name", "제도명"),
    ("agency_name", "기관"),
    ("policy_types", "유형"),
    ("application_region", "지역"),
    ("age_min", "최소연령"),
    ("age_max", "최대연령"),
    ("exception_age", "연령예외"),
    ("income_criteria", "소득기준"),
    ("support_target", "지원대상"),
    ("qualification_text", "자격요건"),
    ("summary", "지원내용"),
    ("support_period", "지원기간/금액"),
    ("cost", "자부담금"),
    ("duplication_restriction", "중복수혜제한"),
    ("deadline_type", "마감유형"),
    ("deadline_date_raw", "마감일(원문)"),
    ("application_deadline", "마감일"),
    ("is_lifetime_limit_once", "생애1회"),
    ("notes", "비고"),
]


def _format_value(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return str(value)


def policy_to_block(row: Dict[str, Any]) -> str:
    lines = ["[policy_id: {}]".format(row["policy_id"])]
    for key, label in _FIELD_LABELS:
        value = row.get(key)
        if value is None or value == "" or value == []:
            continue
        lines.append("{}: {}".format(label, _format_value(value)))
    docs = row.get("document_names") or []
    if docs:
        lines.append("필요서류: " + ", ".join(docs))
    return "\n".join(lines)


def catalog_to_text(rows: List[Dict[str, Any]]) -> str:
    return "\n\n".join(policy_to_block(r) for r in rows)


# --- 지역 선필터 -------------------------------------------------------------


def split_by_region(
    rows: List[Dict[str, Any]], region_sigungu: Optional[str]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(LLM이 판단할 후보, 지역 불일치로 확정 부적합) 으로 나눈다.

    application_region은 NULL(서울시 전역) 또는 자치구명 하나로 정규화돼 있어서
    지역 판정이 완전히 결정론적이다. 이걸 LLM에 맡기면 출력 토큰의 절반을
    "OO구 거주자 대상이나 XX구 거주" 문장을 57번 쓰는 데 쓰게 된다.
    """
    if not region_sigungu:
        return list(rows), []
    candidates: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []
    for row in rows:
        region = row.get("application_region")
        if region is None or region == region_sigungu:
            candidates.append(row)
        else:
            excluded.append(row)
    return candidates, excluded
