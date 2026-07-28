# 제도 상세 화면 API 명세 (프론트엔드용)

챗봇 검색엔진(cb)의 **제도 상세 페이지** 한 장을 그리는 데 필요한 것만 모았습니다.
전체 대화 흐름은 `api_cb.md`를 보세요.

기준 브랜치: `feature/chatbot-search-engine`

---

## 1. 화면 항목 ↔ API 필드

| 화면 항목 | API 필드 | 비고 |
| --- | --- | --- |
| 제도명 | `name` | 항상 있음 |
| 주관 기관 | `agency` | |
| 한 줄 요약 | `summary` | |
| 공식 사이트 | `detail_link` | 복지로 원문. 최상단 |
| **지원 기간** | `support_cycle` + `support_cycle_label` | 실제로는 **지급 주기**. 아래 주의 |
| 지원 형태 | `provision_type_badge` | `현금지급` 등. 복수 값 가능 |
| **신청 방법** | `apply_method_nm` | 짧은 라벨(`"방문"`). 85% 커버 |
| 신청 절차 원문 | `apply_method` | 93% 커버 |
| **신청 기간** | `apply_period_start` ~ `apply_deadline` | |
| **결과 발표일** | `result_announcement_date` | |
| **문의처** | `contact` 또는 `contact_list` | 둘 중 하나만 나감 |
| **필요 서류** | `required_documents[]` | |
| 첨부 서식 | `required_forms[]` | 복지로 원본 파일. 아래 주의 |
| 신청 안내 말풍선 | `apply_guide_easy` | 제도 번역기가 만든 쉬운 말 |
| 지원 대상 / 선정 기준 / 지원 내용 | `target_detail` `select_criteria` `service_content` | 아코디언. 원문 그대로 |
| 기준연도 | `criteria_year` | |
| **왜 추천했는지** | `POST .../translate` → `sections` | 별도 호출. 아래 5절 |

### 주의 3가지

**① "지원 기간"은 기간이 아니라 주기입니다.**
`support_cycle` 값은 `월` `1회성` `수시` `년` `반기` `분기` `주` `기타` `부정기` 9종입니다.
서버가 `support_cycle_label`로 항상 `"지급 주기"`를 함께 내려줍니다. 이 라벨을 쓰세요.
신청 기간은 별도로 `apply_period_start ~ apply_deadline`입니다.

**② `apply_method_nm`은 856건 중 127건(15%)이 없습니다.**
DB 컬럼 값을 그대로 내려줍니다(서버가 계산하거나 추론하지 않습니다).
`중앙 394/461 · 지자체 335/395`로 양쪽 다 채워져 있습니다.
없는 127건은 뱃지 없이 그려야 하니, **뱃지가 빠져도 어색하지 않은 레이아웃**으로 잡으세요.
`apply_method`(절차 원문)는 93%(797건)에 있습니다.

**③ `required_forms`와 `required_documents`는 다릅니다.**

| | 출처 | 내용 |
| --- | --- | --- |
| `required_documents` | 정제된 서류 목록 | **"내가 준비해야 할 것"**. 이걸 쓰세요 |
| `required_forms` | 복지로 첨부파일 원본 | 조례·사업안내·`첨부파일없음.hwp` 같은 잡음이 섞여 있음 |

---

## 2. 호출 순서

```
결과 카드 클릭 (카드의 serv_id 사용)
        │
        ├─→ GET  /api/v1/cb/institutions/{serv_id}            [필수, 즉시]
        │       화면 대부분. 캐시 가능
        │
        └─→ POST /api/v1/cb/institutions/{serv_id}/translate  [선택, 비동기]
                "왜 추천했는지" 말풍선. 2~4초. 캐시 불가
```

**두 호출을 묶지 마세요.** translate는 LLM 호출이라 느립니다. 상세를 먼저 그리고
말풍선만 나중에 채워야 합니다.

---

## 3. `GET /api/v1/cb/institutions/{serv_id}`

```http
GET /api/v1/cb/institutions/WLF00000025
Authorization: Bearer {access_token}
```

### 응답 (실제 데이터)

```json
{
  "serv_id": "WLF00000025",
  "name": "장애인일자리지원",
  "agency": "보건복지부 장애인자립기반과",
  "summary": "18세 이상 미취업 장애인에게 공공형 일자리를 제공하여 사회참여 확대와 소득보장을 도모합니다.",
  "region": { "scope": "national", "label": "전국" },
  "tags": { "life_cycle": ["청년","중장년"], "household": ["장애인"], "theme": ["일자리"] },
  "detail_link": "https://www.bokjiro.go.kr/ssis-tbu/twataa/wlfareInfo/moveTWAT52011M.do?wlfareInfoId=WLF00000025&wlfareInfoReldBztpCd=01",

  "support_cycle": "월",
  "support_cycle_label": "지급 주기",
  "provision_type_badge": "현금지급",
  "apply_method_nm": "방문",
  "apply_method": "신청기관연락처목록: 거주지 읍/면/동 주민센터, 시군구에서 '서비스 신청'\n조사기관연락처목록: 담당 시/군/구청 또는 시군구에서 조사 및 심사\n…",

  "apply_period_start": "2026-07-17",
  "apply_deadline": "2026-08-16",
  "result_announcement_date": "2026-08-29",

  "contact": "129",

  "required_documents": [
    { "name": "장애인일자리사업 참여신청서",
      "url": "https://bokjiro.go.kr/ssis-tbu/CmmFileUtil/getDownload.do?atcflId=2455GOWF12GOWF122455&atcflSn=1",
      "url_type": "form_download" },
    { "name": "개인정보 수집·이용 동의서" },
    { "name": "신분증" },
    { "name": "장애인등록증", "url": "https://www.gov.kr", "url_type": "certificate_issuance" },
    { "name": "소득금액증명원", "url": "https://www.hometax.go.kr", "url_type": "certificate_issuance" }
  ],

  "apply_guide_easy": "[내가 할 일]\n거주지 읍·면·동 주민센터나 시·군·구를 방문해 서비스 신청을 해요. …\n\n[그다음 진행 과정]\n…\n\n[용어 설명]\n- 읍·면·동 주민센터: 거주지의 생활 행정 업무를 처리하는 곳이에요.\n…",

  "required_forms": [
    { "name": "서식7 장애인일자리사업 참여신청서.hwp", "url": "https://bokjiro.go.kr/…" }
  ],

  "target_detail": "…", "select_criteria": "…", "service_content": "…",
  "criteria_year": 2026
}
```

### 필드 목록

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `serv_id` `name` `region` | — | **항상 있음** |
| `agency` | String? | 주관 기관 |
| `summary` | String? | 한 줄 요약 |
| `tags` | Object? | `{life_cycle[], household[], theme[]}`. 3축 모두 비면 통째로 빠짐 |
| `detail_link` | String? | 복지로 원문 링크 |
| `support_cycle` | String? | 지급 주기 (9종) |
| `support_cycle_label` | String? | 항상 `"지급 주기"` |
| `provision_type_badge` | String? | 지원 형태. 42종이며 `"현금지급, 현물지급"`처럼 복수 값 가능 |
| `apply_method_nm` | String? | 신청 수단 짧은 라벨. 복수 값 가능(`"방문, 인터넷"`) |
| `apply_method` | String? | 신청 절차 원문 |
| `apply_period_start` | String? | `YYYY-MM-DD` |
| `apply_deadline` | String? | `YYYY-MM-DD` |
| `result_announcement_date` | String? | `YYYY-MM-DD` |
| `contact` | String? | 대표 문의처 |
| `contact_list` | Object[]? | `{name?, phone}`. 연락처 2곳 이상일 때 `contact` 대신 |
| `required_documents` | Object[]? | `{name, url?, url_type?}` |
| `required_forms` | Object[]? | `{name, url?}` 복지로 첨부 원본 |
| `apply_guide_easy` | String? | 쉬운 신청 가이드 |
| `target_detail` `select_criteria` `service_content` | String? | 아코디언 본문 |
| `criteria_year` | Int? | 기준연도 |
| `extra_info` | Object? | 근거법령·관련 사이트 |

### 렌더링 규칙 — 값이 없으면 키가 없습니다

`null`도 `""`도 내려가지 않습니다. **키 자체가 빠집니다.**

```js
if ("apply_guide_easy" in d) { /* 말풍선 */ }
if ("required_documents" in d) { /* 필요 서류 섹션 */ }
if ("contact_list" in d) { /* 목록 */ } else if ("contact" in d) { /* 한 줄 */ }
if ("apply_deadline" in d) { /* 신청 기간 행 */ } else { /* "상시 접수" */ }
```

### 결측 현황

2026-07-29 기준 실측입니다.

| 필드 | 없는 건수 | 이유 |
| --- | --- | --- |
| 신청 일정 3종 | 194 / 856 (23%) | `support_cycle='수시'`. 상시 접수라 마감 개념이 없음 → **"상시 접수"로 표시** |
| `apply_method_nm` | 127 / 856 (15%) | 원본에 값이 없는 제도 |
| `apply_method` | 59 / 856 (7%) | |
| `required_documents` | 29 / 856 (3%) | 신청을 아예 받지 않는 제도 |
| `apply_guide_easy` | 29 / 856 (3%) | 위와 **같은 29건** |
| `contact` | 1 / 856 (0.1%) | |

`contact_list`(연락처 2곳 이상)로 나가는 건은 153건입니다.

---

## 4. `required_documents` — 필요 서류

```json
{ "name": "장애인등록증", "url": "https://www.gov.kr", "url_type": "certificate_issuance" }
{ "name": "신분증" }
```

| `url_type` | 뜻 |
| --- | --- |
| `form_download` | 서식 파일 직접 다운로드 |
| `certificate_issuance` | 증명서 발급처 (정부24·홈택스·전자가족관계등록시스템 등) |
| `info_page` | 그 외 안내 페이지 |

**`url`이 없는 서류가 더 많습니다.** 전체 2,652개 중 링크가 있는 건 582개(22%)입니다.
신분증·통장사본·진단서는 온라인 발급이라는 개념이 없어서 링크가 없는 것이 정상입니다.
**링크가 있는 항목만 누를 수 있게** 그리세요.

제도당 서류는 보통 2~5개이고, 서류 없는 제도는 키 자체가 없습니다.

---

## 5. `apply_guide_easy` — 신청 안내 말풍선

DB 값을 **가공 없이 그대로** 내려줍니다. 줄바꿈이 살아 있으니 `white-space: pre-wrap`으로
렌더링하세요. 형태는 항상 3단입니다.

```
[내가 할 일]
거주지 읍·면·동 주민센터나 시·군·구를 방문해 서비스 신청을 해요.
장애인일자리사업 참여신청서, 개인정보 수집·이용 동의서, 신분증을 준비해요.

[그다음 진행 과정]
신청 후에는 담당 시·군·구청에서 조사와 심사를 거쳐 보장 여부를 결정하고,
대상자에게 서비스를 제공해요. 이 과정에서 내가 따로 할 일은 없어요.

[용어 설명]
- 읍·면·동 주민센터: 거주지의 생활 행정 업무를 처리하는 곳이에요.
- 소득금액증명원: 신고된 소득 금액을 확인하는 서류예요.
```

미리 만들어 둔 값이라 상세 응답에 그냥 실려 옵니다. 별도 호출이 필요 없습니다.

---

## 6. `POST /api/v1/cb/institutions/{serv_id}/translate` — "왜 추천했는지"

**캐시 불가 · 비동기 로딩.** 대화 State를 함께 읽어서 사용자마다, 대화 진행에 따라
결과가 달라집니다.

### Request

```json
{ "thread_id": "cb-a1b2c3d4e5f6" }
```

`thread_id`는 선택입니다. 생략하면 ①② 섹션만 나갑니다. body 자체를 생략해도 됩니다.

### Response

```json
{
  "serv_id": "WLF00003181",
  "name": "장애인의료비지원",
  "sections": {
    "summary_easy":   "장애인의료비지원은 등록된 장애가 있는 분이 병원에 가거나 약을 살 때 본인이 직접 내야 하는 돈을 대신 내주는 제도예요. …",
    "target_general": "이 제도는 원래 의료급여 2종 수급자이거나, 기초생활수급자 중 근로 능력이 있는 세대에 속한 등록장애인을 위해 만들어졌어요. …",
    "personal_fit":   "돌보시는 분이 장애 등록이 되어 계신 걸로 확인됐으니, 이 제도의 기본 조건 중 하나에는 해당될 수 있어요. 다만 세부 조건까지 맞는지는 아직 확인되지 않았어요."
  },
  "personalized": true,
  "grounded_on": ["돌보는 분 연세: 만 80세", "돌보는 분 장애등록에 해당한다고 확인됨"],
  "easy_text": "…세 섹션을 이어붙인 텍스트(하위호환)…"
}
```

| 섹션 | 내용 |
| --- | --- |
| `summary_easy` | ① 이 제도가 뭘 해주는지 |
| `target_general` | ② 원래 어떤 계층을 위한 제도인지 (개인화 아님) |
| `personal_fit` | ③ 왜 지금 특히 해당될 수 있는지 |

- **순서 고정** ①→②→③.
- `personal_fit`이 `null`이면 ③ 영역을 숨기세요. 세 경우입니다:
  `thread_id` 미전달 / 확인된 사실이 아직 없음 / 확인된 사실이 이 제도와 이어지지 않음.
- `grounded_on`은 ③의 근거입니다. **화면에 뿌리는 값이 아니라 검증용**입니다.
- 이 말풍선은 **신청 방법을 다루지 않습니다.** 5절 `apply_guide_easy`와 겹치지 않습니다.
- 자격을 단정하지 않습니다. "받으실 수 있어요"(✗) / "해당될 수 있어요"(✓).
- 생성 실패해도 500이 아닙니다. 세 섹션이 `null`이고 `easy_text`에 안내 문구가 담긴 `200`입니다.

---

## 7. 결과 카드와 상세는 필드명이 다릅니다

| 결과 카드 (`/threads/{id}/results`) | 상세 (`/institutions/{id}`) |
| --- | --- |
| `link` | `detail_link` |
| `support.cycle` | `support_cycle` |
| `support.provision_type` | `provision_type_badge` |
| `apply.method_name` | `apply_method_nm` |
| `apply.contact` | `contact` / `contact_list` |
| `match` | (상세에는 없음) |

카드는 중첩 객체, 상세는 평평한 구조입니다. 두 화면을 잇는 것은 **`serv_id` 하나**입니다.

---

## 8. 오류

| Status | `error` | 조건 |
| --- | --- | --- |
| `404` | `INSTITUTION_NOT_FOUND` | 없는 `serv_id` |
| `404` | `THREAD_NOT_FOUND` | 없는 `thread_id` (translate) |
| `403` | `FORBIDDEN` | 남의 `thread_id` (translate) |
| `401` | `UNAUTHORIZED` | 토큰 없음/만료 |
| `503` | `CB_UNAVAILABLE` | cb 엔진 초기화 실패 |

운영이 종료된 제도도 `404`가 아니라 정상 응답합니다.

---

## 9. 알아두실 것

신청 기간·결과 발표일은 **데모용으로 생성한 날짜**입니다. 복지로 원본에는 신청 일정
데이터가 없어서, 화면을 채우려고 만든 값입니다. 실제 마감일이 아닙니다.

필요 서류는 제도 원문과 복지로 첨부 서식을 근거로 정리한 값이며, 근거를 찾지 못한
제도는 최소 서류(신분증·신청서 등)만 들어 있습니다.
