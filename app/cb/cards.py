"""검색 결과 행 → 결과 카드, 그리고 맞춤/혹시관심 구간 나누기.

결과 화면은 지역이 아니라 3단(배너/맞춤/혹시관심)으로 나뉜다.
지역(national/metro/district)은 카드 안 region 필드로 표시만 하고
그룹의 기준으로 쓰지 않는다.
"""
from typing import Any, Dict, List, Optional, Tuple

from app.cb import constants
from app.cb.schemas import SUPPORT_CYCLE_LABEL

# 검색에서 가져올 건수. 대화 중에는 검색을 아예 하지 않으므로 이 한 번의
# 검색에서 두 구간을 모두 채운다.
#
# 화면에 나가는 건수보다 넉넉하다. 자격이 어긋나는 건이 eligibility에서
# 빠지기 때문에(app/cb/eligibility.py), 딱 맞게 가져오면 걷어낸 만큼
# 목록이 얇아진다.
SEARCH_LIMIT = 30

# '혹시 관심 있으실 수도'에 실을 최대 건수.
#
# 상한이 없을 때 이 섹션이 14~17건까지 나왔다. 그 정도면 읽지 않고 넘긴다.
# 맞춤(최대 8건)과 합쳐 한 화면에서 훑을 수 있는 분량으로 끊는다.
MAYBE_LIMIT = 8

# --- 맞춤 / 혹시관심 구간 기준 ------------------------------------------------
# score(RRF)로 절대 컷을 잡지 않는다. RRF는 순위 역수 합이라 유사도가 아니고,
# 가중치가 벡터 1.0 / 키워드 2.5라 최대값이 3.5/(10+1) ≈ 0.318이다.
# 예컨대 0.15 컷은 '벡터 1위(0.0909)'를 떨어뜨리고 '키워드 16위'를 통과시킨다.
# 즉 유사도 컷이 아니라 "키워드에 걸렸는가" 컷이 되어버린다.
#
# 그래서 순위 + distance(코사인 거리)로 나눈다. distance도 절대 컷은 쓰지 않는다.
# 6개 질의 실측(2026-07-26)에서 1위 거리가 질의마다 크게 달랐다:
#   '저소득 생계비' 0.381 / '청년 일자리' 0.430 / '청년 월세' 0.479 / '정신건강' 0.501
# 절대 0.55로 자르면 일자리는 20건 중 대부분이 통과하고, 주거는 명백히 관련 있는
# '주거안정 월세대출 보증'(0.562)이 떨어졌다. 같은 컷이 분야마다 다른 뜻이 된다.
#
# 그래서 '이 질의에서 가장 가까운 것'을 기준으로 상대 비교한다.
MATCHED_TOP_N = 8
# 1위보다 이만큼 넘게 멀면 혹시관심으로 내린다.
RELATIVE_DISTANCE_MARGIN = 0.10
# 다만 검색 자체가 어긋난 질의는 1위부터 멀다. 그때는 상대 기준이 무의미하므로
# 절대 상한도 함께 둔다 (전부 혹시관심으로 내려가고 멘트도 그에 맞게 나간다).
MAX_DISTANCE_FOR_MATCHED = 0.65


def matched_by(row: Dict[str, Any]) -> str:
    """어느 신호로 걸렸는지. keyword는 벡터가 놓친 걸 키워드가 건진 항목이다."""
    has_vector = row.get("vec_rank") is not None
    has_keyword = row.get("lex_rank") is not None
    if has_vector and has_keyword:
        return "both"
    return "vector" if has_vector else "keyword"


def region_label(scope: str, ctpv_nm: Optional[str], sgg_nm: Optional[str]) -> str:
    if scope == constants.REGION_NATIONAL:
        return "전국"
    if scope == constants.REGION_DISTRICT:
        return (sgg_nm or "").strip() or "우리 동네"
    # metro. '서울특별시'는 화면에서 '서울시'로 읽는 편이 자연스럽다.
    name = (ctpv_nm or "").strip()
    return name.replace("특별시", "시").replace("광역시", "시") or "서울시"


def to_card(row: Dict[str, Any], rank: Optional[int] = None) -> Dict[str, Any]:
    """검색 행(또는 DB 행)을 카드 dict로.

    본문(target_detail/service_content/apply_method)은 넣지 않는다.
    20장에 본문을 실으면 응답이 수만 자가 된다. 본문은 상세 API에서만 나간다.
    """
    scope = row.get("region_scope") or constants.REGION_NATIONAL
    card: Dict[str, Any] = {
        "serv_id": row["serv_id"],
        "name": row.get("serv_nm") or "",
        "agency": row.get("jur_org_nm"),
        "summary": (row.get("serv_dgst") or "").strip() or None,
        "region": {
            "scope": scope,
            "label": region_label(scope, row.get("ctpv_nm"), row.get("sgg_nm")),
            "ctpv_nm": row.get("ctpv_nm"),
            "sgg_nm": row.get("sgg_nm"),
        },
        "tags": {
            "life_cycle": list(row.get("life_cycle_tags") or []),
            "household": list(row.get("household_tags") or []),
            "theme": list(row.get("theme_tags") or []),
        },
        "support": {
            "cycle": row.get("support_cycle"),
            "provision_type": row.get("provision_type"),
        },
        "apply": {
            "method_name": row.get("apply_method_nm"),
            "contact": row.get("contact"),
        },
        "link": row.get("detail_link"),
        "match": None,
    }
    if rank is not None:
        distance = row.get("dist")
        card["match"] = {
            "rank": rank,
            "score": round(float(row.get("rrf") or 0.0), 4),
            "distance": round(float(distance), 4) if distance is not None else None,
            "matched_by": matched_by(row),
        }
    return card


def to_detail(row: Dict[str, Any]) -> Dict[str, Any]:
    """DB 행 → 상세 화면 dict.

    카드와 달리 본문을 싣고, support/apply 중첩을 풀어 화면 역할대로 편다.
    비어 있는 값은 None으로 남겨둔다 — 응답에서 키를 빼는 일은 라우터의
    response_model_exclude_none이 한 번에 처리한다.
    """
    scope = row.get("region_scope") or constants.REGION_NATIONAL
    extra_info = dict(row.get("extra_info") or {})

    # 3축은 늘 함께 나간다(Filters는 결과·턴 응답과 공유하는 고정 형태다).
    # 다만 세 축이 모두 비면 tags를 통째로 뺀다.
    tags = {
        "life_cycle": list(row.get("life_cycle_tags") or []),
        "household": list(row.get("household_tags") or []),
        "theme": list(row.get("theme_tags") or []),
    }

    support_cycle = _text(row.get("support_cycle"))
    contacts = _contact_entries(extra_info)
    # 연락처가 2곳 이상일 때만 목록으로 바꾼다. 1곳이면 contact 컬럼이 이미
    # 그 값이라(normalize.py 참고) 목록으로 감쌀 이유가 없다.
    multiple = len(contacts) >= 2

    return {
        "serv_id": row["serv_id"],
        "name": row.get("serv_nm") or "",
        "agency": _text(row.get("jur_org_nm")),
        "summary": _text(row.get("serv_dgst")),
        "region": {
            "scope": scope,
            "label": region_label(scope, row.get("ctpv_nm"), row.get("sgg_nm")),
            "ctpv_nm": row.get("ctpv_nm"),
            "sgg_nm": row.get("sgg_nm"),
        },
        # 빈 배열 3개를 내려주면 프론트가 칩 줄을 그릴지를 값 검사로 다시
        # 판단해야 한다. 하나라도 있을 때만 내보낸다.
        "tags": tags if any(tags.values()) else None,
        "detail_link": _text(row.get("detail_link")),

        "support_cycle": support_cycle,
        "support_cycle_label": SUPPORT_CYCLE_LABEL if support_cycle else None,
        "provision_type_badge": _text(row.get("provision_type")),
        "apply_method_badge": _text(row.get("apply_method_nm")),
        "apply_method_detail": _text(row.get("apply_method")),
        # 배치가 만들어 둔 쉬운 말 신청 가이드. 가공하지 않고 그대로 내보낸다.
        # _text를 거치는 것은 앞뒤 공백 정리와 빈 문자열 → None 뿐이다.
        "apply_guide_easy": _text(row.get("apply_guide_easy")),

        "contact": None if multiple else _text(row.get("contact")),
        "contact_list": contacts if multiple else None,
        "required_forms": _required_forms(extra_info) or None,

        "target_detail": _text(row.get("target_detail")),
        "select_criteria": _text(row.get("select_criteria")),
        "service_content": _text(row.get("service_content")),
        "criteria_year": row.get("criteria_year"),
        # 위에서 뽑아 쓴 두 키는 뺀다. 같은 값이 두 군데로 나가면 프론트가
        # 어느 쪽을 그릴지 정해야 하고, 그 판단은 여기서 이미 끝냈다.
        "extra_info": _remaining_extra_info(extra_info) or None,
    }


# to_detail이 전용 필드로 승격시켜 내보내는 extra_info 키.
_EXTRACTED_EXTRA_KEYS = {"inqpl_ctadr", "basfrm"}


def _text(value: Optional[str]) -> Optional[str]:
    text = (value or "").strip()
    return text or None


def _remaining_extra_info(extra_info: Dict[str, Any]) -> Dict[str, Any]:
    """전용 필드로 승격시키고 남은 부가 정보 (근거법령·관련 사이트 등).

    extra_info는 Dict로 그대로 나가기 때문에 라우터의 exclude_none이 안까지
    닿지 않는다. 여기서 null 키를 직접 걷어내야 '값이 없으면 키도 없다'는
    응답 전체의 약속이 이 블록에서만 깨지지 않는다.
    """
    out: Dict[str, Any] = {}
    for key, entries in extra_info.items():
        if key in _EXTRACTED_EXTRA_KEYS or not isinstance(entries, list):
            continue
        cleaned = [
            {k: v for k, v in entry.items() if v is not None}
            for entry in entries
            if isinstance(entry, dict) and any(v is not None for v in entry.values())
        ]
        if cleaned:
            out[key] = cleaned
    return out


def _contact_entries(extra_info: Dict[str, Any]) -> List[Dict[str, Optional[str]]]:
    """extra_info.inqpl_ctadr → [{name, phone}].

    번호(value)가 없는 항목은 버린다. 기관명만 있으면 사용자가 연락할 수 없고,
    문의처 목록에 이름만 한 줄 뜨는 것은 정보가 아니라 잡음이다.
    """
    out: List[Dict[str, Optional[str]]] = []
    for item in extra_info.get("inqpl_ctadr") or []:
        if not isinstance(item, dict):
            continue
        phone = _text(item.get("value"))
        if phone:
            out.append({"name": _text(item.get("name")), "phone": phone})
    return out


def _required_forms(extra_info: Dict[str, Any]) -> List[Dict[str, Optional[str]]]:
    """extra_info.basfrm → [{name, url}].

    이름이 비고 링크만 있는 항목은 링크를 이름 자리에 그대로 쓴다. 행을 버리면
    '받아야 할 서식이 있다'는 사실 자체가 화면에서 사라지기 때문이다.
    """
    out: List[Dict[str, Optional[str]]] = []
    for item in extra_info.get("basfrm") or []:
        if not isinstance(item, dict):
            continue
        name, url = _text(item.get("name")), _text(item.get("value"))
        if name or url:
            out.append({"name": name or url, "url": url})
    return out


def split_sections(rows: List[Dict[str, Any]]) -> Tuple[List[Dict], List[Dict]]:
    """검색 결과를 (맞춤, 혹시관심)으로 나눈다.

    구간을 나누는 것은 RRF 순위이고, 맞춤 섹션 '안에서의 정렬'은 distance다.
    두 척도가 다르기 때문에 섞으면 어긋난다. 실제로 벡터로만 걸린
    '청소년특별지원'(거리 0.437)이 맞춤 1위(0.414) 다음으로 가까운데도 RRF
    20위로 밀린 사례가 있었다. 키워드 가중치가 2.5, 벡터가 1.0이라 벡터 단독
    건은 최대 1.0/(10+1)=0.0909밖에 못 받아 순위 경쟁에서 진다.

    그래서 맞춤 섹션은 화면에 보일 때 거리 순으로 다시 세운다. 사용자가 가장
    먼저 보는 자리는 '가장 가까운 것'이어야 한다.
    혹시관심은 RRF 순서를 그대로 둔다 — 어차피 곁다리로 보는 목록이고,
    거리로 다시 세우면 구간 경계에서 순서가 요동친다.

    rank는 나뉘기 전 전체 RRF 순위를 유지한다(배열 순서와 다를 수 있다).
    구간이 바뀌어도 같은 제도가 같은 번호를 갖고 있어야 실측할 때 로그와
    응답을 맞춰볼 수 있다.
    """
    distances = [float(r["dist"]) for r in rows if r.get("dist") is not None]
    # 키워드로만 걸린 건은 거리가 없다. 그런 건만 있으면 상대 기준을 못 만들므로
    # 순위로만 나눈다.
    cutoff = min(distances) + RELATIVE_DISTANCE_MARGIN if distances else None

    matched: List[Dict[str, Any]] = []
    maybe: List[Dict[str, Any]] = []
    for index, row in enumerate(rows, 1):
        card = to_card(row, rank=index)
        # 자격이 확인된 건(중증도·소득 구간이 사용자와 맞아떨어진 건)은 거리 컷을
        # 면제한다. 거리는 '말이 비슷한가'이고 자격은 '받을 수 있는가'인데,
        # 후자가 확인됐다면 표현이 좀 달라도 맞춤에 있어야 한다.
        # 순위 컷(MATCHED_TOP_N)은 그대로 적용한다 — 면제까지 하면 맞춤 섹션이
        # 8건을 넘어 화면 설계가 깨진다.
        confirmed = bool(row.get("grading_confirmed"))
        if index <= MATCHED_TOP_N and (confirmed or not _too_far(row.get("dist"), cutoff)):
            matched.append(card)
        else:
            maybe.append(card)

    matched.sort(key=_distance_order)
    # 혹시관심은 RRF 순서라 앞에서 자르는 것이 곧 '가장 가능성 있는 것부터'다.
    return matched, maybe[:MAYBE_LIMIT]


def _distance_order(card: Dict[str, Any]):
    """맞춤 섹션 정렬 키: 가까운 것부터, 거리를 모르는 것은 맨 뒤.

    거리가 없다는 건 벡터 후보군(search.CANDIDATES건) 안에도 못 들었다는
    뜻이다. 키워드가 건져 올린 건이라 버리지는 않지만 의미상 가깝다는 근거는
    없으므로 맨 앞자리를 주지 않는다. 그 안에서는 RRF 순위를 지킨다.
    """
    match = card.get("match") or {}
    distance = match.get("distance")
    return (1, match.get("rank") or 0) if distance is None else (0, distance)


def _too_far(distance: Optional[float], cutoff: Optional[float]) -> bool:
    if distance is None:
        # 벡터가 못 찾고 키워드가 건진 건. 거리로 판단할 근거가 없으니
        # 순위를 믿는다 (제도명에 걸린 건이라 대개 정확하다).
        return False
    value = float(distance)
    if value > MAX_DISTANCE_FOR_MATCHED:
        return True
    return cutoff is not None and value > cutoff
