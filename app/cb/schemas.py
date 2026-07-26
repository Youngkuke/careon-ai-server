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
    """자유대화 전에 먼저 확정하는 2가지. 아직 모르면 null이다."""

    age: Optional[int] = None
    target_for: Optional[str] = None     # self | caree


class TurnResponse(BaseModel):
    thread_id: str
    phase: str                          # gathering | ready
    message: str
    filters: Filters
    intake: Intake = Field(default_factory=Intake)
    # phase=gathering이면 항상 null이다.
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
class InstitutionDetail(InstitutionCard):
    """카드 + 긴 본문.

    카드에 본문을 실으면 20장 응답이 수만 자가 된다. 본문은 여기서만 내려준다.
    비어 있는 필드는 null로 나간다 — 프론트가 섹션을 통째로 숨길 수 있게.
    """

    target_detail: Optional[str] = None
    select_criteria: Optional[str] = None
    service_content: Optional[str] = None
    apply_method: Optional[str] = None
    criteria_year: Optional[int] = None
    extra_info: Dict[str, Any] = Field(default_factory=dict)


class TranslateResponse(BaseModel):
    serv_id: str
    name: str
    # 쉬운 말로 푼 설명. 원문을 대체하는 것이 아니라 옆에 붙는다.
    easy_text: str


class MessageOnly(BaseModel):
    message: str
