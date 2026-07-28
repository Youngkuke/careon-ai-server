"""하이브리드 검색: 벡터 유사도 + 키워드 매칭을 RRF로 융합한다.

왜 벡터만으로 부족한가 (실측):
    질의 "치매 걸린 부모님 돌봄 지원"에 대해
        '한부모가족 유급 자녀돌봄휴가비'  거리 0.3757  ← 1위
        '동작형 치매의료비 지원사업'      거리 0.5700  ← 230위
    임베딩이 '부모님·돌봄·지원'의 어휘 겹침을 '치매'라는 핵심 주제어보다
    강하게 본다. '한부모'가 '부모님'에 끌려온 것이다.
    임베딩 텍스트 구성을 바꿔도 개선되지 않았다(230위 → 286위로 악화).
    즉 텍스트 문제가 아니라 밀집 검색 단독의 한계다.

키워드 쪽 설계 — 형태소 분석기 없이 한국어를 다루는 방법:
    한국어는 어간이 앞, 조사/어미가 뒤에 붙는다('치매를', '부모님').
    그래서 각 토큰의 '접두 부분문자열'을 후보어로 만든다.
    '부모님' → 부모, 부모님 / '치매를' → 치매, 치매를
    이러면 쓰레기 후보('활동보' 같은)도 같이 나오는데, IDF가 알아서 죽인다.
    문서에 안 나오면(df=0) 가중치 0, 너무 흔하면('지원') 역시 0이다.
    덕분에 불용어 사전도 형태소 분석기도 필요 없다.

규모 판단:
    856행이라 ILIKE 순차 스캔이 밀리초 단위다. pg_trgm 확장을 운영 DB에
    설치하지 않는다. 수만 건으로 커지면 그때 GIN 인덱스를 얹으면 된다.
"""
import re
from typing import Any, Dict, List, Optional, Sequence

from app.cb import db, embedding

# RRF 상수. 작을수록 상위권 순위차를 크게 본다.
# 원논문 기본값은 60이지만 그건 수백만 건 코퍼스 기준이다. 856건 · 후보 120건
# 규모에서 60은 1위와 20위의 차이를 거의 없애버린다. 10이 실측에서 가장 좋았다.
RRF_K = 10

# 두 신호의 상대 비중. 키워드를 벡터보다 높게 둔다.
# 직관과 반대 같지만, 키워드는 '핵심어가 있을 때만' 점수를 준다 —
# 질의에 변별력 있는 어휘가 없으면 아무 문서도 잡지 못하고 조용히 빠진다.
# 그래서 비중을 높여도 벡터를 밀어내지 않고, 확실한 신호가 있을 때만 이긴다.
#
# 실측(10개 질의, 정답 순위): 벡터만      1위4건/5위내8건 /평균4.7
#                            lex0.8 k60  1위7건/5위내9건 /평균2.9
#                            lex1.5 k20  1위8건/5위내9건 /평균1.8
#                            lex2.5 k20  1위8건/5위내10건/평균1.3
#                            lex2.5 k10  1위9건/5위내10건/평균1.1  ← 채택
WEIGHT_VECTOR = 1.0
WEIGHT_LEXICAL = 2.5

# 각 신호에서 몇 등까지 후보로 볼지. 856행 기준 넉넉하다.
CANDIDATES = 120

# 전체의 이 비율을 넘게 등장하는 어휘는 변별력이 없다고 보고 버린다.
# '지원'(대부분의 제도명에 등장) 같은 것이 여기서 걸러진다.
MAX_DF_RATIO = 0.25

# 한글/영문/숫자 덩어리만 후보 토큰으로 쓴다.
_TOKEN_RE = re.compile(r"[가-힣]+|[A-Za-z]{2,}|[0-9]{2,}")

# 접두어를 최대 몇 자까지 잘라볼지. 한국어 명사 어간은 대개 2~4자다.
_MAX_PREFIX = 5
_MAX_TERMS = 24


def candidate_terms(query: str) -> List[str]:
    """질의에서 검색어 후보를 뽑는다 (호출부 편의용 — 그룹 정보는 버린다)."""
    return [term for term, _ in candidate_terms_grouped(query)]


def candidate_terms_grouped(query: str) -> List[tuple]:
    """(후보어, 원본 토큰 번호) 목록.

    조사/어미를 떼려고 접두 부분문자열을 함께 넣는다. 정확도는 IDF에 맡긴다.

    같은 토큰에서 나온 후보어들은 '같은 그룹'으로 묶어서 점수를 한 번만 센다.
    묶지 않으면 '도우미' 토큰이 '도우'와 '도우미' 두 후보어를 만들고, 둘이
    같은 문서에 걸려 점수가 2배가 된다. 실제로 그 탓에
    '출산 후 산모 도우미' 질의에서 '산림복지일자리(산림서비스도우미)'가
    '산모·신생아 건강관리'를 눌러 1위가 됐다.
    """
    out: List[tuple] = []
    seen = set()
    for group, token in enumerate(_TOKEN_RE.findall(query)):
        if len(token) < 2:
            continue
        variants = [token[:size] for size in range(2, min(len(token), _MAX_PREFIX) + 1)]
        if len(token) > _MAX_PREFIX:
            variants.append(token)
        for variant in variants:
            if variant not in seen:
                seen.add(variant)
                out.append((variant, group))
    return out[:_MAX_TERMS]


# 고정 플레이스홀더($1~$9) 개수. 필터 인자는 이 뒤부터 번호를 받는다.
_FIXED_ARGS = 9


def _filter_sql(
    life_cycle: Optional[Sequence[str]],
    household: Optional[Sequence[str]],
    theme: Optional[Sequence[str]],
    region_keys: Optional[Sequence[str]],
    args: List[Any],
) -> str:
    """extract_intent가 뽑아준 3종 필터 + 지역을 WHERE 절로 만든다.

    태그는 '겹치면 통과'(&&)다. 사용자가 '청년'이라고 했다고 해서
    청년만 대상인 제도로 좁히면, 청년을 포함하는 폭넓은 제도가 사라진다.

    args에 값을 덧붙이면서 $9부터 번호를 매긴다.
    """
    clauses = ["is_active", "embedding IS NOT NULL"]
    for values, column in (
        (life_cycle, "life_cycle_tags"),
        (household, "household_tags"),
        (theme, "theme_tags"),
    ):
        if values:
            args.append(list(values))
            clauses.append(f"{column} && ${_FIXED_ARGS + len(args)}::TEXT[]")
    if region_keys:
        args.append(list(region_keys))
        clauses.append(f"region_key = ANY(${_FIXED_ARGS + len(args)}::TEXT[])")
    return " AND ".join(clauses)


_SQL = """
WITH base AS (
    SELECT * FROM cb.cb_institutions WHERE {filters}
), total AS (
    SELECT GREATEST(count(*), 1)::float8 AS n FROM base
), term AS (
    SELECT term, grp FROM unnest($2::TEXT[], $3::INT[]) AS t(term, grp)
), hit AS (
    -- 필드별 가중치: 제도명 3, 요약 2, 본문 1.
    -- 이름에 걸린 것이 본문 어딘가에 스친 것보다 훨씬 강한 신호다.
    SELECT b.serv_id, t.term, t.grp,
           (CASE WHEN b.serv_nm ILIKE '%' || t.term || '%' THEN 3 ELSE 0 END
          + CASE WHEN coalesce(b.serv_dgst, '') ILIKE '%' || t.term || '%' THEN 2 ELSE 0 END
          + CASE WHEN coalesce(b.target_detail, '') ILIKE '%' || t.term || '%'
                   OR coalesce(b.service_content, '') ILIKE '%' || t.term || '%'
                 THEN 1 ELSE 0 END) AS field_score
    FROM base b CROSS JOIN term t
), df AS (
    SELECT term, count(*) FILTER (WHERE field_score > 0)::float8 AS df FROM hit GROUP BY term
), weight AS (
    -- df=0(문서에 없는 후보어)과 너무 흔한 어휘는 0으로 죽인다.
    SELECT d.term,
           CASE WHEN d.df = 0 OR d.df > total.n * $4 THEN 0
                ELSE ln(1 + total.n / d.df) END AS w
    FROM df d CROSS JOIN total
), per_group AS (
    -- 같은 원본 토큰에서 나온 후보어들('도우', '도우미')은 최고점 하나만 센다.
    -- 합치면 같은 단어를 두 번 세어 그 토큰이 부당하게 강해진다.
    SELECT h.serv_id, h.grp, max(h.field_score * w.w) AS score
    FROM hit h JOIN weight w ON w.term = h.term
    WHERE h.field_score > 0 AND w.w > 0
    GROUP BY h.serv_id, h.grp
), lexical AS (
    SELECT serv_id, sum(score) AS score FROM per_group GROUP BY serv_id
), lex_rank AS (
    SELECT serv_id, row_number() OVER (ORDER BY score DESC, serv_id) AS rank, score
    FROM lexical ORDER BY score DESC, serv_id LIMIT $5
), vec_rank AS (
    SELECT serv_id, row_number() OVER (ORDER BY embedding <=> $1::vector, serv_id) AS rank,
           embedding <=> $1::vector AS dist
    FROM base ORDER BY embedding <=> $1::vector, serv_id LIMIT $5
), fused AS (
    SELECT coalesce(v.serv_id, l.serv_id) AS serv_id,
           v.rank AS vec_rank, l.rank AS lex_rank, v.dist, l.score AS lex_score,
           -- ::float8 캐스트가 없으면 asyncpg가 파라미터를 bigint로 추론해
           -- 1/61 이 정수 나눗셈으로 0이 된다. RRF 전체가 0이 되어
           -- 결과가 조용히 '순수 벡터 순서'로 돌아간다.
           coalesce($6::float8 / ($8::float8 + v.rank), 0)
         + coalesce($7::float8 / ($8::float8 + l.rank), 0) AS rrf
    FROM vec_rank v FULL OUTER JOIN lex_rank l ON l.serv_id = v.serv_id
)
-- 결과 카드에 실리는 컬럼 + 자격 판정에 쓰는 지원대상 원문.
-- 나머지 본문(service_content/apply_method)은 상세 API에서만 읽는다.
-- target_detail은 응답에 나가지 않는다 — 자격이 한정된 제도를 걸러내려면
-- 태그만으로는 부족해서 서버 안에서만 쓴다 (app/cb/eligibility.py).
-- select_criteria도 마찬가지로 서버 안에서만 쓴다 — 좁은 질환에 게이트가 걸린
-- 제도를 찾는 데 필요하다(eligibility.narrow_disease_terms). 지원대상에는
-- 거주·연령만 적고 진단 요건은 선정기준에 적는 제도가 있다.
-- disability_severity/income_pct_max도 같은 이유로 읽는다. 이 둘은 WHERE에
-- 넣지 않는다 — hard filter로 걸면 사용자가 소득을 말하지 않은 대다수 대화에서
-- 아무 효과가 없거나(값이 없으니까) 반대로 필요한 제도를 통째로 날린다.
-- 순위 가감은 대화 맥락을 아는 파이썬 쪽에서 한다 (app/cb/eligibility.py).
SELECT f.serv_id, f.vec_rank, f.lex_rank, f.dist, f.lex_score, f.rrf,
       i.serv_nm, i.source, i.region_scope, i.ctpv_nm, i.sgg_nm, i.serv_dgst,
       i.life_cycle_tags, i.household_tags, i.theme_tags, i.detail_link,
       i.jur_org_nm, i.support_cycle, i.provision_type, i.apply_method_nm, i.contact,
       i.target_detail, i.select_criteria, i.disability_severity, i.income_pct_max
FROM fused f JOIN cb.cb_institutions i ON i.serv_id = f.serv_id
ORDER BY f.rrf DESC, f.dist NULLS LAST
LIMIT $9
"""


async def search(
    query_text: str,
    *,
    life_cycle: Optional[Sequence[str]] = None,
    household: Optional[Sequence[str]] = None,
    theme: Optional[Sequence[str]] = None,
    region_keys: Optional[Sequence[str]] = None,
    limit: int = 10,
    candidates: int = CANDIDATES,
) -> List[Dict[str, Any]]:
    """자연어 질의로 제도를 찾는다.

    3종 필터(life_cycle/household/theme)는 extract_intent가 대화에서 뽑아준
    값을 그대로 받는다. 이 함수는 필터 안에서의 '유사도 매칭'만 담당한다.
    """
    vector = (await embedding.embed_texts([query_text]))[0]
    literal = "[" + ",".join(f"{x:.7f}" for x in vector) + "]"

    filter_args: List[Any] = []
    filters = _filter_sql(life_cycle, household, theme, region_keys, filter_args)

    grouped = candidate_terms_grouped(query_text)
    args: List[Any] = [
        literal,                        # $1 질의 벡터
        [term for term, _ in grouped],  # $2 후보 검색어
        [grp for _, grp in grouped],    # $3 후보어가 나온 원본 토큰 번호
        MAX_DF_RATIO,                   # $4 흔한 어휘 컷오프
        candidates,                     # $5 신호별 후보 개수
        WEIGHT_VECTOR,                  # $6
        WEIGHT_LEXICAL,                 # $7
        float(RRF_K),                   # $8
        limit,                          # $9
    ]
    args.extend(filter_args)        # $9 이후

    pool = await db.connect()
    async with pool.acquire() as conn:
        rows = await conn.fetch(_SQL.format(filters=filters), *args)
    return [dict(r) for r in rows]
