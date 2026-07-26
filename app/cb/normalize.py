"""API 원문(목록 + 상세) → cb.cb_institutions 행으로 변환.

두 API의 필드명이 다르다. 실측(각 12건 상세조회)으로 확인한 대응 관계:

                     중앙부처                    지자체
  지원대상           tgtrDtlCn                  sprtTrgtCn
  선정기준           slctCritCn                 slctCritCn        (공통)
  서비스내용         alwServCn                  alwServCn         (공통)
  신청방법           applmetList[] (평균 5개)    aplyMtdCn (약 33% 결측)
  소관/담당          jurMnofNm                  bizChrDeptNm
  대표연락처         rprsCtadr                  (없음 → 문의처 첫 항목에서 파생)
  기준연도           crtrYr                     (없음)
  시행기간           (없음)                      enfcBgngYmd ~ enfcEndYmd
  3종 필터           lifeArray 등               lifeNmArray 등    (Nm 삽입, 구분자 ", ")

한쪽에만 있는 필드는 억지로 채우지 않고 NULL로 둔다.
"""
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.cb import constants

logger = logging.getLogger(__name__)

# 상세조회의 리스트형 필드 → extra_info JSONB 키.
# 복지로 화면의 "추가정보" 탭에 해당한다.
EXTRA_INFO_KEYS = {
    "baslawList": "baslaw",             # 근거법령
    "basfrmList": "basfrm",             # 서식·구비서류
    "inqplCtadrList": "inqpl_ctadr",    # 문의처 연락처
    "inqplHmpgReldList": "inqpl_hmpg",  # 관련 사이트
}


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).replace("\r", "\n").strip()
    return s or None


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _int(value: Any) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _normalize_entry(entry: Any) -> Optional[Dict[str, Optional[str]]]:
    """리스트 항목을 {name, value, code} 로 통일한다.

    자식 필드명이 API마다 다르다:
      중앙   servSeCode / servSeDetailNm / servSeDetailLink
      지자체 wlfareInfoDtlCd / wlfareInfoReldNm / wlfareInfoReldCn
    """
    if not isinstance(entry, dict):
        text = _text(entry)
        return {"name": text, "value": None, "code": None} if text else None

    name = _text(entry.get("servSeDetailNm") or entry.get("wlfareInfoReldNm"))
    value = _text(entry.get("servSeDetailLink") or entry.get("wlfareInfoReldCn"))
    code = _text(entry.get("servSeCode") or entry.get("wlfareInfoDtlCd"))
    if not (name or value):
        return None
    return {"name": name, "value": value, "code": code}


def build_extra_info(detail: Dict[str, Any]) -> Dict[str, List[Dict[str, Optional[str]]]]:
    """근거법령/서식/문의처/관련사이트를 정규화해서 모은다.

    지자체 제도는 복지로 UI에 '추가정보' 탭이 없지만 API에는 데이터가 있다.
    복지로 UI의 렌더링 선택을 우리 데이터 모델의 기준으로 삼지 않는다.
    """
    out: Dict[str, List[Dict[str, Optional[str]]]] = {}
    for src_key, dst_key in EXTRA_INFO_KEYS.items():
        entries = [e for e in (_normalize_entry(x) for x in _as_list(detail.get(src_key))) if e]
        if entries:
            out[dst_key] = entries
    return out


def _central_apply_method(detail: Dict[str, Any]) -> Optional[str]:
    """중앙부처 applmetList[]를 사람이 읽을 텍스트로 병합한다.

    건당 평균 5개(신청기관/조사기관/결정기관/지급기관/사후관리기관)가 들어온다.
    """
    lines: List[str] = []
    for raw in _as_list(detail.get("applmetList")):
        entry = _normalize_entry(raw)
        if not entry:
            continue
        name, value = entry["name"], entry["value"]
        if name and value:
            lines.append(f"{name}: {value}")
        else:
            lines.append(name or value or "")
    return "\n".join(l for l in lines if l) or None


def to_row(
    source: str,
    list_item: Dict[str, Any],
    detail: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """목록 항목 + 상세를 합쳐 DB 행 dict를 만든다."""
    detail = detail or {}
    merged = {**list_item, **detail}  # 상세가 목록을 덮어쓴다
    is_central = source == "central"

    if is_central:
        life_raw = merged.get("lifeArray")
        household_raw = merged.get("trgterIndvdlArray")
        theme_raw = merged.get("intrsThemaArray")
        target_detail = _text(merged.get("tgtrDtlCn"))
        apply_method = _central_apply_method(merged)
        # 상세의 jurMnofNm은 이미 '보건복지부 보험급여과'처럼 부서까지 포함할 때가 있다.
        # 목록의 jurOrgNm을 그대로 덧붙이면 '보험급여과 보험급여과'로 중복된다.
        jur_org_nm = _text(merged.get("jurMnofNm"))
        org = _text(merged.get("jurOrgNm"))
        if jur_org_nm and org and org not in jur_org_nm:
            jur_org_nm = f"{jur_org_nm} {org}"
        ctpv_nm = None
        sgg_nm = None
    else:
        life_raw = merged.get("lifeNmArray")
        household_raw = merged.get("trgterIndvdlNmArray")
        theme_raw = merged.get("intrsThemaNmArray")
        target_detail = _text(merged.get("sprtTrgtCn"))
        apply_method = _text(merged.get("aplyMtdCn"))
        jur_org_nm = _text(merged.get("bizChrDeptNm"))
        ctpv_nm = _text(merged.get("ctpvNm"))
        sgg_nm = _text(merged.get("sggNm"))

    for kind, raw in (
        ("life_cycle", life_raw), ("household", household_raw), ("theme", theme_raw)
    ):
        dropped = constants.unknown_tags(raw, kind)
        if dropped:
            logger.warning(
                "어휘에 없는 태그 무시 servId=%s kind=%s values=%s",
                merged.get("servId"), kind, dropped,
            )

    extra_info = build_extra_info(merged)

    # 지자체에는 rprsCtadr가 없다. 문의처 첫 항목을 대표 연락처로 파생한다.
    contact = _text(merged.get("rprsCtadr"))
    if not contact:
        ctadr = extra_info.get("inqpl_ctadr") or []
        if ctadr:
            contact = ctadr[0].get("value") or ctadr[0].get("name")

    return {
        "serv_id": _text(merged.get("servId")),
        "source": source,
        "serv_nm": _text(merged.get("servNm")),
        "serv_dgst": _text(merged.get("servDgst")) or _text(merged.get("wlfareInfoOutlCn")),
        "life_cycle_tags": constants.parse_tags(life_raw, "life_cycle"),
        "household_tags": constants.parse_tags(household_raw, "household"),
        "theme_tags": constants.parse_tags(theme_raw, "theme"),
        "tags_source": "api",
        "ctpv_nm": ctpv_nm,
        "sgg_nm": sgg_nm,
        "region_scope": constants.region_scope(source, sgg_nm),
        "target_detail": target_detail,
        "select_criteria": _text(merged.get("slctCritCn")),
        "service_content": _text(merged.get("alwServCn")),
        "apply_method": apply_method,
        "extra_info": extra_info,
        "jur_org_nm": jur_org_nm,
        "support_cycle": _text(merged.get("sprtCycNm")),
        "provision_type": _text(merged.get("srvPvsnNm")),
        "apply_method_nm": _text(merged.get("aplyMtdNm")),
        "detail_link": _text(merged.get("servDtlLink")),
        "contact": contact,
        "criteria_year": _int(merged.get("crtrYr")),
        "enforce_begin_ymd": _text(merged.get("enfcBgngYmd")),
        "enforce_end_ymd": _text(merged.get("enfcEndYmd")),
        "origin_modified_ymd": _text(merged.get("lastModYmd")),
        "raw_list": list_item,
        "raw_detail": detail,
        "last_fetched_at": datetime.now(timezone.utc),
        "detail_fetched_at": datetime.now(timezone.utc) if detail else None,
    }


# --- 임베딩 텍스트 ------------------------------------------------------------
def embedding_text(row: Dict[str, Any]) -> str:
    """임베딩 대상 텍스트.

    유저 발화("월세가 부담돼요")와 매칭되어야 하므로 제도명·요약·지원대상·
    선정기준·서비스내용을 합친다. 신청방법/연락처는 검색 의미가 없어 제외한다.
    빈 필드는 아예 넣지 않는다 (빈 라벨이 노이즈가 된다).
    """
    parts = [
        ("제도명", row.get("serv_nm")),
        ("요약", row.get("serv_dgst")),
        ("지원대상", row.get("target_detail")),
        ("선정기준", row.get("select_criteria")),
        ("서비스내용", row.get("service_content")),
    ]
    tags = (row.get("life_cycle_tags") or []) + (row.get("household_tags") or []) \
        + (row.get("theme_tags") or [])
    if tags:
        parts.append(("분류", ", ".join(tags)))
    return "\n".join(f"{label}: {value}" for label, value in parts if value)


def content_hash(text: str, model: str) -> str:
    """임베딩 재생성 판단용 해시. 모델이 바뀌면 해시도 바뀌게 모델명을 섞는다."""
    return hashlib.sha256(f"{model}\n{text}".encode("utf-8")).hexdigest()
