"""cb API 요청/응답 스키마.

기존 app/schemas.py와 분리한다. 챗봇 검색엔진은 응답 구조가 전혀 다르고,
기존 스키마에 필드를 얹으면 두 챗봇이 서로의 계약에 묶인다.

핵심 계약 두 가지:
  1) 채팅 턴 응답에는 제도가 하나도 실리지 않는다. 대화는 순수 대화다.
  2) 결과는 지역이 아니라 배너/맞춤/혹시관심 3단으로 나눈다.
     지역(national/metro/district)은 카드 안 region 필드로 표시만 한다.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

PHASE_GATHERING = "gathering"
PHASE_READY = "ready"


# --- 공통 조각 ----------------------------------------------------------------
class Filters(BaseModel):
    """대화로 누적된 복지로 3종 필터."""

    life_cycle: List[str] = Field(default_factory=list)
    household: List[str] = Field(default_factory=list)
    theme: List[str] = Field(default_factory=list)


class CardRegion(BaseModel):
    scope: str                          # national | metro | district
    label: str                          # '전국' | '서울시' | '은평구'
    ctpv_nm: Optional[str] = None
    sgg_nm: Optional[str] = None


class CardSupport(BaseModel):
    cycle: Optional[str] = None         # support_cycle (월/수시/1회성 ...)
    provision_type: Optional[str] = None


class CardApply(BaseModel):
    method_name: Optional[str] = None
    contact: Optional[str] = None


class MatchInfo(BaseModel):
    """유사도 매칭 근거.

    score는 RRF(순위 역수 합)라 질의를 넘나들며 비교할 수 없다.
    구간을 실측할 때는 distance(코사인 거리)를 쓴다.
    """

    # 검색 전체에서의 RRF 순위. 맞춤 섹션은 화면에서 distance 순으로 다시
    # 세우므로 배열 순서와 일치하지 않는다. 화면 번호는 배열 순서를 쓰고,
    # 이 값은 로그와 응답을 맞춰볼 때 쓴다.
    rank: int
    score: float
    distance: Optional[float] = None    # 키워드로만 걸린 건은 벡터 거리가 없다
    matched_by: str                     # vector | keyword | both


class InstitutionCard(BaseModel):
    serv_id: str
    name: str
    agency: Optional[str] = None
    summary: Optional[str] = None
    region: CardRegion
    tags: Filters
    support: CardSupport
    apply: CardApply
    link: Optional[str] = None
    # 배너는 검색 결과가 아니라 큐레이션이라 매칭 근거가 없다.
    match: Optional[MatchInfo] = None


# --- 대화 턴 ------------------------------------------------------------------
class TurnRequest(BaseModel):
    # 첫 턴에는 생략한다. 서버가 새 thread_id를 발급해서 응답에 실어준다.
    thread_id: Optional[str] = None
    message: str


class ResultSummary(BaseModel):
    """마무리 멘트와 전환 화면 배지에 쓰는 최소 정보. 카드는 들어가지 않는다."""

    matched: int
    maybe: int
    region_label: str


class Intake(BaseModel):
    """자유대화 전에 먼저 확정하는 것들. 아직 모르면 null이다.

    확인 순서는 대상 → 본인 나이 → (돌봄이면) 돌보는 분 연세다. 대상을 먼저
    확인하는 이유는 의료·돌봄 이야기가 본인 것인지 돌보는 분 것인지에 따라
    찾아야 할 제도가 통째로 달라지기 때문이다.
    """

    target_for: Optional[str] = None      # self | caree
    age: Optional[int] = None             # 본인 나이
    caree_age: Optional[int] = None       # 돌보는 분 연세 (target_for=caree일 때만 확인)


class TurnResponse(BaseModel):
    thread_id: str
    phase: str                          # gathering | ready
    message: str
    filters: Filters
    intake: Intake = Field(default_factory=Intake)
    # phase=gathering이면 항상 null이다.
    result_summary: Optional[ResultSummary] = None


class LatestThreadResponse(BaseModel):
    """마지막으로 결과까지 마친 대화. 없으면 thread_id가 null이다.

    새로고침이나 재로그인 뒤에 프론트가 결과 화면으로 돌아갈지, 상담을 새로
    시작할지 정하는 데 쓴다. 카드는 들어가지 않는다 — 결과 API를 부르면 된다.
    """

    thread_id: Optional[str] = None
    phase: Optional[str] = None
    generated_at: Optional[datetime] = None
    result_summary: Optional[ResultSummary] = None


class ThreadStartResponse(BaseModel):
    """대화를 열 때의 응답. 사용자가 먼저 말하지 않아도 봇이 인사를 건넨다."""

    thread_id: str
    phase: str = PHASE_GATHERING
    message: str


# --- 결과 화면 ----------------------------------------------------------------
class ResultSection(BaseModel):
    title: str
    count: int
    institutions: List[InstitutionCard] = Field(default_factory=list)


class BannerSection(ResultSection):
    # 검색이 아니라 명시적 큐레이션에서 온다는 표시.
    source: str = "curated"


class ResultRegion(BaseModel):
    sgg: Optional[str] = None
    source: str                         # user_profile | unset


class ResultsResponse(BaseModel):
    thread_id: str
    generated_at: datetime
    region: ResultRegion
    filters: Filters
    relaxed_axes: List[str] = Field(default_factory=list)
    banner: BannerSection
    matched: ResultSection
    maybe: ResultSection


# --- 제도 상세 / 쉬운 말 설명 --------------------------------------------------
# 지급 주기 행에 붙일 화면 라벨. 서버가 내려주는 이유는 이 값(support_cycle)이
# 한때 '지원 기간'으로 표기되던 것을 바로잡기 위해서다. 신청 기간이 아니라
# 지급 주기이며, 라벨을 서버가 고정해두면 화면에서 다시 어긋나지 않는다.
SUPPORT_CYCLE_LABEL = "지급 주기"


class ContactEntry(BaseModel):
    """문의처가 여럿일 때의 한 줄. extra_info.inqpl_ctadr에서 온다."""

    name: Optional[str] = None          # 기관명. 이름 없이 번호만 있는 건도 있다
    phone: str


class RequiredForm(BaseModel):
    """필요 서식 한 줄. extra_info.basfrm에서 온다."""

    name: str                           # 파일명
    url: Optional[str] = None           # 다운로드 링크. 없으면 키 자체가 빠진다


class InstitutionDetail(BaseModel):
    """상세 화면 한 장을 그리는 데 필요한 것 전부.

    카드(InstitutionCard)를 상속하지 않는다. 카드는 목록용 요약이라
    support/apply를 중첩 객체로 묶어두는데, 상세 화면은 같은 값을 뱃지와 본문
    2단으로 쪼개 쓴다. 상속하면 support.cycle과 support_cycle이 함께 나가서
    프론트가 어느 쪽을 믿어야 할지 모르게 된다.

    필드명은 DB 컬럼명을 따른다. 한 컬럼이 화면에서 두 역할로 갈라질 때만
    역할 접미사를 붙인다 (apply_method → apply_method_badge/_detail).

    비어 있는 값은 null이 아니라 '키 자체가 없는' 상태로 나간다
    (라우터의 response_model_exclude_none). 프론트는 값 검사 없이 키 존재
    여부만으로 행·섹션을 그릴지 정하면 된다.

    신청 기간·결과 발표일에 대응하는 컬럼은 원본(복지로)에 없다. null을
    내려서 '언젠가 채워질 자리'처럼 보이게 하지 않고, 스키마에서 아예 뺀다.
    """

    # --- 정체 --------------------------------------------------------------
    serv_id: str
    name: str
    agency: Optional[str] = None
    summary: Optional[str] = None
    region: CardRegion
    # 3축이 모두 비면 통째로 빠진다. 나갈 때는 세 축이 늘 함께 나간다.
    tags: Optional[Filters] = None
    # 공식 사이트(복지로 원문) 링크. 화면 최상단에 유지한다.
    # 카드에서는 link지만 여기서는 DB 컬럼명 그대로 detail_link다.
    detail_link: Optional[str] = None

    # --- 요약 행 / 뱃지 -----------------------------------------------------
    support_cycle: Optional[str] = None         # 년 | 월 | 1회성 | 수시
    support_cycle_label: Optional[str] = None   # 항상 "지급 주기". 값이 있을 때만 나간다
    provision_type_badge: Optional[str] = None  # 현금지급 | 현물지급 | 기타
    apply_method_badge: Optional[str] = None    # apply_method_nm. 짧은 라벨(예: "방문")
    apply_method_detail: Optional[str] = None   # apply_method. 절차 전문
    # 신청 절차를 쉬운 말로 푼 가이드. 배치가 미리 만들어 둔 값이라 이 응답에
    # 실려 온다(말풍선 A와 달리 캐시된다). apply_method_detail 원문을 대체하지
    # 않고 나란히 나간다. 값이 없으면 다른 필드와 마찬가지로 키가 빠진다.
    apply_guide_easy: Optional[str] = None

    # 문의처는 둘 중 하나만 나간다. 연락처가 2곳 이상이면 contact를 빼고
    # contact_list로 대체한다 — 대표 1개만 보여주면 나머지 창구가 숨는다.
    contact: Optional[str] = None
    contact_list: Optional[List[ContactEntry]] = None

    required_forms: Optional[List[RequiredForm]] = None

    # --- 아코디언 본문 ------------------------------------------------------
    # 셋 다 가공하지 않은 원문이다. 검색·랭킹이 읽는 텍스트와 한 글자도
    # 다르면 안 된다 — 사용자가 매칭 근거를 확인하는 자리이기 때문이다.
    target_detail: Optional[str] = None
    select_criteria: Optional[str] = None
    service_content: Optional[str] = None

    criteria_year: Optional[int] = None
    # 근거법령(baslaw)·관련 사이트(inqpl_hmpg) 등 위에서 못 뽑은 나머지.
    # 비어 있으면 키가 빠진다.
    extra_info: Optional[Dict[str, Any]] = None


class TranslateRequest(BaseModel):
    """말풍선 A 요청.

    thread_id를 주면 그 대화에서 확인된 사실로 3번 섹션을 채운다. 없으면
    1·2번만 만들고 personal_fit은 null이다 — 근거 없이 개인화하지 않는다.
    """

    thread_id: Optional[str] = None


class ExplainSections(BaseModel):
    """말풍선 A의 3부분. 순서가 곧 화면 순서다."""

    # 1. 이 제도가 뭘 해주는지 (service_content를 쉬운 말로)
    summary_easy: Optional[str] = None
    # 2. 원래 어떤 계층을 위한 제도인지 (target_detail/select_criteria 기반, 개인화 아님)
    target_general: Optional[str] = None
    # 3. 왜 지금 특히 해당될 수 있는지. 확인된 사실이 없으면 null이다.
    personal_fit: Optional[str] = None


class TranslateResponse(BaseModel):
    """말풍선 A 응답.

    캐시할 수 없다. 대화 State를 읽으므로 사용자마다·턴마다 달라진다.
    프론트는 이 호출만 따로 비동기로 띄우고, 상세 화면의 나머지(왼쪽 정보,
    말풍선 B=apply_guide_easy)를 이 응답으로 붙잡아 두지 않아야 한다.
    """

    serv_id: str
    name: str
    sections: ExplainSections
    # 3번 섹션이 실제로 채워졌는가. thread_id를 줬어도 확인된 사실이 하나도
    # 없으면 false다.
    personalized: bool = False
    # 3번 섹션이 근거로 삼은 '이번 대화에서 확인된 사실'. 화면에 그대로 뿌리는
    # 용도가 아니라, 개인화가 무엇에 기대고 있는지 검증하기 위한 값이다.
    grounded_on: List[str] = Field(default_factory=list)
    # 하위호환. 세 섹션을 이어붙인 텍스트다. 기존 클라이언트가 쓰던 필드라
    # 남겨두지만, 새 화면은 sections를 쓰는 편이 낫다.
    easy_text: str


class MessageOnly(BaseModel):
    message: str
