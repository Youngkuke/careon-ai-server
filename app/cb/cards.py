"""검색 결과 행 → 결과 카드, 그리고 맞춤/혹시관심 구간 나누기.

결과 화면은 지역이 아니라 3단(배너/맞춤/혹시관심)으로 나뉜다.
지역(national/metro/district)은 카드 안 region 필드로 표시만 하고
그룹의 기준으로 쓰지 않는다.
"""
from typing import Any, Dict, List, Optional, Tuple

from app.cb import constants

# 결과 화면에 쓸 검색 건수. 대화 중에는 검색을 아예 하지 않으므로
# 이 한 번의 검색에서 두 구간을 모두 채운다.
RESULT_LIMIT = 20

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
        if index <= MATCHED_TOP_N and not _too_far(row.get("dist"), cutoff):
            matched.append(card)
        else:
            maybe.append(card)

    matched.sort(key=_distance_order)
    return matched, maybe


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
