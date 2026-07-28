"""표준 증명서류 → 발급처 URL 고정 매핑.

배치 전용이다 (scripts/backfill_required_documents.py). 실시간 답변 경로는
읽지 않는다.

# 왜 LLM이 아니라 사전인가

발급처 URL은 제도마다 달라지지 않는다. 가족관계증명서는 어느 제도에서 요구하든
대법원 전자가족관계등록시스템에서 뗀다. 856건마다 LLM에게 물으면 비용이 856배가
되고, 그때마다 조금씩 다른(그리고 종종 틀린) URL이 나온다.

# URL 검증 (2026-07-28)

전부 실제로 열어서 확인했다. 검색 결과만 믿었다면 두 건이 잘못 들어갔을 것이다:

  - 정부24 CappBizCD=12100000021 은 검색에서 '소득금액증명'으로 나왔지만
    실제로 열어보니 '주민등록표 등본' 페이지였다 → 홈택스로 대체
  - 정부24 CappBizCD=14600000273 (장애인증명서) 은 '요청하신 서비스를 찾을 수
    없습니다' 오류 페이지였다 → 정부24 루트로 대체

그래서 딥링크는 직접 확인한 것만 쓰고, 나머지는 발급 기관 루트로 둔다.
루트 URL은 덜 편하지만 404보다 낫다.

# 발급 URL이 없는 서류

신분증·통장 사본·진단서는 실측 빈도 1~3위지만 URL이 없다. 온라인 발급이라는
개념 자체가 없거나(신분증), 본인 보관물이거나(통장 사본), 의료기관이 발급해서
공통 주소가 없다(진단서). 이런 서류는 NO_URL_DOCUMENTS에 두고 url=None으로
내보낸다 — 억지로 링크를 붙이지 않는다.
"""
from typing import Dict, List, NamedTuple, Optional

# url_type 값. 링크의 성격이지 우리가 찾은 방법이 아니다
# (출처는 required_documents_source가 따로 담는다).
URL_TYPE_FORM = "form_download"            # 서식 파일 직접 다운로드
URL_TYPE_CERTIFICATE = "certificate_issuance"  # 증명서 발급처
URL_TYPE_INFO = "info_page"                # 그 외 안내 페이지

_GOV24 = "https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=%s"
_EFAMILY = "https://efamily.scourt.go.kr"
_HOMETAX = "https://www.hometax.go.kr"
_NHIS = "https://www.nhis.or.kr/nhis/index.do"
_GOV24_ROOT = "https://www.gov.kr"


class DocumentUrl(NamedTuple):
    url: str
    issuer: str
    verified: bool  # 해당 URL을 직접 열어 내용을 확인했는가


# 정규화된 서류명 → 발급처. 키는 _normalize()를 거친 형태여야 한다.
DOCUMENT_URLS: Dict[str, DocumentUrl] = {
    # --- 정부24 (딥링크 직접 확인) ---------------------------------------
    "주민등록등본": DocumentUrl(_GOV24 % "13100000015", "정부24", True),
    "주민등록초본": DocumentUrl(_GOV24 % "13100000015", "정부24", True),
    "주민등록표등본": DocumentUrl(_GOV24 % "13100000015", "정부24", True),
    "주민등록표초본": DocumentUrl(_GOV24 % "13100000015", "정부24", True),
    "차상위계층확인서": DocumentUrl(_GOV24 % "13520000098", "정부24", True),
    "수급자증명서": DocumentUrl(_GOV24 % "14600000280", "정부24", True),
    "국민기초생활수급자증명서": DocumentUrl(_GOV24 % "14600000280", "정부24", True),
    "한부모가족증명서": DocumentUrl(_GOV24 % "10601000001", "정부24", True),

    # --- 대법원 전자가족관계등록시스템 ------------------------------------
    # 루트만 쓴다. 증명서 종류는 사이트 안에서 고른다.
    # 직접 fetch는 빈 응답(JS 렌더링)이라 verified=False로 둔다.
    "가족관계증명서": DocumentUrl(_EFAMILY, "대법원 전자가족관계등록시스템", False),
    "기본증명서": DocumentUrl(_EFAMILY, "대법원 전자가족관계등록시스템", False),
    "혼인관계증명서": DocumentUrl(_EFAMILY, "대법원 전자가족관계등록시스템", False),

    # --- 국민건강보험공단 (직접 확인) -------------------------------------
    "건강보험자격득실확인서": DocumentUrl(_NHIS, "국민건강보험공단", True),
    "자격득실확인서": DocumentUrl(_NHIS, "국민건강보험공단", True),
    "건강보험료납부확인서": DocumentUrl(_NHIS, "국민건강보험공단", True),
    "건강보험납부확인서": DocumentUrl(_NHIS, "국민건강보험공단", True),

    # --- 국세청 홈택스 -----------------------------------------------------
    # 정부24 딥링크가 엉뚱한 페이지였으므로 홈택스 루트를 쓴다.
    "소득금액증명": DocumentUrl(_HOMETAX, "국세청 홈택스", False),
    "소득금액증명원": DocumentUrl(_HOMETAX, "국세청 홈택스", False),
    "사업자등록증명": DocumentUrl(_HOMETAX, "국세청 홈택스", False),
    "사업자등록증명원": DocumentUrl(_HOMETAX, "국세청 홈택스", False),

    # --- 정부24 루트 (딥링크가 죽어 있음) ---------------------------------
    "장애인증명서": DocumentUrl(_GOV24_ROOT, "정부24", False),
    "장애인등록증": DocumentUrl(_GOV24_ROOT, "정부24", False),
}

# 발급 URL이 존재하지 않는 서류. 검색 대상에서도 뺀다.
# 실측 빈도 1~3위(통장 사본 37 / 신분증 30 / 진단서 29)가 전부 여기다.
NO_URL_DOCUMENTS = (
    "신분증", "신분증사본", "본인신분증", "대리인신분증", "공적신분증",
    "통장사본", "본인통장사본", "예금통장사본",
    "진단서", "소견서", "의사소견서", "의료진단서",
    "임대차계약서", "임대차계약서사본",
    "재직증명서", "위임장", "각서", "동의서",
    "개인정보수집이용동의서", "개인정보수집·이용동의서",
)


def _normalize(name: str) -> str:
    """비교용 정규화: 공백·중점·괄호 내용을 걷어낸다."""
    text = name.strip()
    # 괄호 안 설명은 매칭에 방해만 된다: '소득금액증명(신고사실없음)' → '소득금액증명'
    while "(" in text and ")" in text:
        start, end = text.find("("), text.find(")")
        if start > end:
            break
        text = text[:start] + text[end + 1:]
    for ch in " \t·ㆍ・.:_-—–":
        text = text.replace(ch, "")
    return text


def lookup(name: str) -> Optional[DocumentUrl]:
    """서류명으로 발급처를 찾는다. 없으면 None.

    완전 일치 → 부분 일치 순으로 본다. 부분 일치를 허용하는 이유는 LLM이
    '2026년 주민등록등본'처럼 수식어를 붙여 내놓는 경우가 있어서다.
    """
    key = _normalize(name)
    if not key:
        return None
    if key in DOCUMENT_URLS:
        return DOCUMENT_URLS[key]
    # 긴 키부터 본다. '주민등록등본'과 '등본'이 둘 다 걸리면 긴 쪽이 정확하다.
    for candidate in sorted(DOCUMENT_URLS, key=len, reverse=True):
        if candidate in key:
            return DOCUMENT_URLS[candidate]
    return None


def has_no_url(name: str) -> bool:
    """발급 URL이 원래 없는 서류인가. 그렇다면 검색으로도 찾지 않는다."""
    key = _normalize(name)
    return any(marker in key for marker in NO_URL_DOCUMENTS)


def verified_entries() -> List[str]:
    """직접 열어 확인한 URL 목록 (리포트용)."""
    return sorted({e.url for e in DOCUMENT_URLS.values() if e.verified})
