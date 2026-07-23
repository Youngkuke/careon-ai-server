# CareOn AI 서버 — 프론트 연동 API 명세

`API_SPEC_v1.md`는 설계 문서이고, 이 문서는 **현재 실제로 동작하는 API**의 연동 명세입니다.
필드 타입과 채움률은 실 DB(Supabase) 기준으로 검증했습니다.

- **Base URL**: `http://<host>:8000`
- **Content-Type**: `application/json` (요청/응답 공통, UTF-8)
- **인증**: 없음 (백엔드와는 `carer_id`로만 연결)

---

## 목차

| # | Method | Endpoint | 설명 | 상태 |
|---|---|---|---|---|
| 1 | `POST` | `/api/v1/chat/sessions` | 챗봇 세션 시작 | ✅ |
| 2 | `POST` | `/api/v1/chat/sessions/{sessionId}/messages` | 대화 턴 진행 | ✅ |
| 3 | `GET` | `/api/v1/chat/sessions/{sessionId}/match` | 매칭 결과 조회 | ✅ |
| 4 | `GET` | `/api/v1/chat/sessions/{sessionId}` | 세션 상태 조회 | ✅ |
| 5 | `GET` | `/api/v1/policies/{policyId}` | 제도 상세 조회 | ✅ |
| 6 | `GET` | `/api/v1/policies?ids=` | 제도 배치 조회 | ✅ |
| 7 | `GET` | `/health` | 서버 상태 확인 | ✅ |
| 8 | `POST` | `/api/v1/chat/sessions/{sessionId}/followup` | 보완 질문 라운드 | ❌ 미구현 |
| 9 | `POST` | `/api/v1/policies/{policyId}/translate` | 제도 번역기 | ❌ 미구현 |

---

## 0. 공통 규격

### 0.1 공통 에러 응답 형식

성공(2xx)이 아닌 모든 응답은 아래 형식입니다.

```json
{
  "error": "SESSION_NOT_FOUND",
  "message": "세션이 만료되었거나 존재하지 않습니다."
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `error` | `string` | 에러 코드 (분기 처리용, 고정 문자열) |
| `message` | `string` | 사용자 노출 가능한 한국어 메시지 |

### 0.2 전체 에러 코드

| 코드 | HTTP | 설명 | 프론트 대응 |
|---|---|---|---|
| `BAD_REQUEST` | `400` | 파라미터 형식 오류 | 요청 수정 |
| `SESSION_NOT_FOUND` | `404` | 세션 없음 / TTL(24h) 만료 | 세션 재생성 후 처음부터 |
| `POLICY_NOT_FOUND` | `404` | 요청한 policy_id가 전부 없음 | 에러 표시 |
| `PHASE_MISMATCH` | `409` | 현재 단계에서 호출 불가한 API | `phase` 재확인 |
| `DATABASE_UNAVAILABLE` | `503` | 서버 DB 미연결 | **서버 문제** — 담당자 전달 |

> ⚠️ FastAPI 자체 타입 검증 실패(예: `/policies/abc`)는 위 형식이 아닌
> **422 Unprocessable Entity**가 나갑니다. 이 경우 응답 본문 형식이 다릅니다.

### 0.3 전체 흐름

```
POST /sessions                          → phase: 1
  ↓
POST /sessions/{id}/messages   (반복)   → phase: 1 → 2 → 3 → 4 → 5
  ↓ phase === "matching" 이 되면
GET  /sessions/{id}/match               → 매칭 결과 (policy_id + match_group)
  ↓ 카드 렌더링용 상세 정보
GET  /policies?ids=2,7,16
```

### 0.4 `phase` 값

| 값 | 타입 | 의미 |
|---|---|---|
| `1` `2` `3` `4` `5` | `number` | 정보 수집 진행 중 (진행도 바 5단계) |
| `"matching"` | `string` | 수집 완료 → 매칭 조회 가능 |

> ⚠️ **number와 string이 섞여서 옵니다.** `String(phase) === "matching"` 형태로
> 비교하세요. 설계 문서의 `6`, `"done"`은 8번 API 구현 후 등장하며 **현재는 나오지 않습니다.**

---

## 1. 챗봇 세션 시작

```
POST /api/v1/chat/sessions
```

온보딩(1단계) 완료 후 2단계 챗봇을 시작할 때 호출합니다.

### Request Body

```json
{
  "carer_id": 123,
  "age": 22,
  "region_sigungu": "관악구",
  "selected_types": ["돌봄가사", "생계주거"],
  "case_number": 1
}
```

| 필드 | 타입 | 필수 | 기본값 | 설명 |
|---|---|---|---|---|
| `carer_id` | `number` | ✅ | — | 돌봄청년 ID |
| `age` | `number \| null` | ❌ | `null` | 생략 시 `carers` 테이블에서 조회 |
| `region_sigungu` | `string \| null` | ❌ | `null` | 자치구명. 예: `"관악구"`. 생략 시 DB 조회 |
| `selected_types` | `string[]` | ❌ | `[]` | 관심 분야. 아래 4개 값만 허용 |
| `case_number` | `number \| null` | ❌ | `null` | 케이스 번호 |

**`selected_types` 허용값 (정확히 4개)**
`"돌봄가사"` · `"의료건강"` · `"심리청년특화"` · `"생계주거"`

### Response `200 OK`

```json
{
  "session_id": "sess_acefd111",
  "phase": 1,
  "message": "OO님 안녕하세요, 편하게 몇 가지 여쭤볼게요. 지금 같이 사는 가족이 몇 분이세요?"
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `session_id` | `string` | 이후 모든 대화 API에 사용. 형식 `sess_` + hex 8자 |
| `phase` | `number` | 항상 `1` |
| `message` | `string` | 챗봇 첫 말풍선. 그대로 렌더링 |

> `session_id`는 **DB PK가 아니라 AI 서버 메모리 세션 키**입니다. 백엔드에서 조회할 수
> 있는 값이 아닙니다. 서버 재시작·TTL 만료 시 404가 나므로 그때는 세션을 새로 만들어야 합니다.

### 응답 코드

| 코드 | 조건 |
|---|---|
| `200 OK` | 정상 |
| `422 Unprocessable Entity` | `carer_id` 누락 또는 타입 오류 |

### 주의

- `message`는 LLM 생성이라 **매번 문구가 다릅니다.** 문자열 비교로 상태 판단 금지.
- 응답까지 **2~5초** 소요. 로딩 UI 필요. 권장 타임아웃 **30초**.

---

## 2. 대화 턴 진행

```
POST /api/v1/chat/sessions/{sessionId}/messages
```

### Path Parameter

| 이름 | 타입 | 설명 |
|---|---|---|
| `sessionId` | `string` | 1번 API에서 받은 `session_id` |

### Request Body

```json
{
  "message": "지금 엄마랑 둘이 살고 전세예요"
}
```

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `message` | `string` | ✅ | 사용자 발화 원문 |

### Response `200 OK`

```json
{
  "message": "감사해요, 잘 들었어요. 그럼 지금 아르바이트나 일 같은 소득활동은 하고 계세요?",
  "phase": 1,
  "phase_advanced": false,
  "extracted_fields": {
    "household_members": 2,
    "housing_type": "전세",
    "living_with_relation": "모(동거)"
  }
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `message` | `string` | 챗봇 답변. 그대로 말풍선에 렌더링 |
| `phase` | `number \| string` | 이 턴이 **끝난 후**의 단계 |
| `phase_advanced` | `boolean` | 이번 턴에 단계가 넘어갔는지 (전환 애니메이션용, 무시 가능) |
| `extracted_fields` | `object` | 이번 턴에 새로 파악된 값. **키 구성 가변** |

> `phase_advanced`와 `extracted_fields`는 프론트 계약에 없는 **보조 필드**입니다.
> 안 쓰셔도 됩니다.

> ⚠️ `extracted_fields`는 영문 필드명이고 키가 매번 달라집니다.
> **화면에 그대로 뿌리지 마세요.** 디버깅/진행률 표시용입니다.

### 응답 코드

| 코드 | `error` | 조건 |
|---|---|---|
| `200 OK` | — | 정상 |
| `404 Not Found` | `SESSION_NOT_FOUND` | 세션 없음 / 만료 |
| `409 Conflict` | `PHASE_MISMATCH` | `phase`가 이미 `"matching"` |
| `422` | — | `message` 누락 |

### 예외 응답 예시

```json
// 404 — 세션 만료
{
  "error": "SESSION_NOT_FOUND",
  "message": "세션이 만료되었거나 존재하지 않습니다."
}
```

```json
// 409 — 정보 수집이 이미 끝난 세션에 메시지를 보낸 경우
{
  "error": "PHASE_MISMATCH",
  "message": "이미 정보 수집이 끝난 세션입니다 (phase=matching)."
}
```

### 주의

- **`phase`가 `"matching"`이 되면 이 API를 더 호출하지 말고 3번으로 넘어가세요.**
  이때 `message`는 "알맞은 제도를 찾았어요!" 류의 마무리 인사가 나옵니다.
  이 말풍선을 띄운 뒤 로딩 아이콘을 보여주고 3번을 호출하면 됩니다.
- 응답까지 **3~8초** 소요 (필드 추출 + 답변 생성으로 LLM 2회 호출). 권장 타임아웃 **30초**.

---

## 3. 매칭 결과 조회

```
GET /api/v1/chat/sessions/{sessionId}/match
```

### Path Parameter

| 이름 | 타입 | 설명 |
|---|---|---|
| `sessionId` | `string` | 세션 ID |

### Response `200 OK`

```json
{
  "matches": [
    { "policy_id": 1,  "match_group": "적합" },
    { "policy_id": 3,  "match_group": "적합" },
    { "policy_id": 7,  "match_group": "확인_불가" }
  ]
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `matches` | `Match[]` | 화면에 뿌릴 제도 목록 |

**`Match` 객체**

| 필드 | 타입 | 설명 |
|---|---|---|
| `policy_id` | `number` | 제도 상세 조회 키 (`policies.policy_id`와 동일) |
| `match_group` | `string` | `"적합"` 또는 `"확인_불가"` |

- **`적합`이 먼저, `확인_불가`가 뒤에** 오도록 정렬돼 있습니다.
- **`부적합`(40건 내외)은 응답에 포함되지 않습니다.** 화면에 노출하지 않기로 했습니다.
- **판단 근거(`reason`/`caution`)는 내려주지 않습니다.** 사용자가 제도 상세 화면에서
  조건을 직접 확인하는 구조입니다.
- 보통 **15~25건** 정도가 옵니다.

### 응답 코드

| 코드 | `error` | 조건 |
|---|---|---|
| `200 OK` | — | 정상 |
| `404 Not Found` | `SESSION_NOT_FOUND` | 세션 없음 / 만료 |
| `409 Conflict` | `PHASE_MISMATCH` | `phase`가 아직 `1`~`5` |
| `500` | — | 매칭 실행 실패 (LLM/DB 오류) |

### 예외 응답 예시

```json
// 409 — 아직 정보 수집 중인데 매칭을 조회한 경우
{
  "error": "PHASE_MISMATCH",
  "message": "아직 정보 수집 중입니다 (phase=1). 대화가 끝난 뒤 호출하세요."
}
```

### 주의

- **⚠️ 응답까지 최대 60초 이상 걸릴 수 있습니다.** **타임아웃을 90초 이상**으로 잡으세요.
  `phase`가 `"matching"`이 된 시점에 서버가 백그라운드로 미리 실행하므로,
  대화 종료 직후 호출하면 대기가 짧아집니다. 결과는 캐시되어 **2번째 호출부터는 즉시 응답**합니다.
- 이 API는 `policy_id`만 줍니다. 제도명·기관·마감일 등 카드에 필요한 정보는
  **별도 조회**가 필요합니다 (6번 API 또는 기존 백엔드 제도 조회 API).

---

## 4. 세션 상태 조회

```
GET /api/v1/chat/sessions/{sessionId}
```

앱 재진입 시 대화 복원, 또는 디버깅용입니다.

### Response `200 OK`

```json
{
  "session_id": "sess_acefd111",
  "phase": 3,
  "profile": {
    "carer_id": 123,
    "name": null,
    "age": 22,
    "region_sigungu": "관악구",
    "selected_types": ["돌봄가사"],
    "household_members": 2,
    "housing_type": "전세"
  },
  "conversation_history": [
    { "role": "assistant", "content": "안녕하세요, 몇 가지 여쭤볼게요..." },
    { "role": "user", "content": "네" }
  ]
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `session_id` | `string` | |
| `phase` | `number \| string` | |
| `profile` | `object` | 누적 프로필. **키 구성 가변** |
| `conversation_history` | `{role, content}[]` | `role`은 `"assistant"` 또는 `"user"` |

### 응답 코드

| 코드 | `error` | 조건 |
|---|---|---|
| `200 OK` | — | 정상 |
| `404 Not Found` | `SESSION_NOT_FOUND` | 세션 없음 / 만료 |

### 주의

- `profile`은 내부 스키마라 **화면에 직접 렌더링하지 마세요.** 필드가 예고 없이 늘어납니다.
- `conversation_history`는 말풍선 복원에 바로 쓸 수 있습니다.

---

## 5. 제도 상세 조회 (단건)

```
GET /api/v1/policies/{policyId}
```

매칭 결과 카드 클릭, 저장한 제도 목록 화면에서 사용합니다.

### Path Parameter

| 이름 | 타입 | 설명 |
|---|---|---|
| `policyId` | `number` | 매칭 결과의 `policy_id` |

### Response `200 OK`

```json
{
  "policy_id": 7,
  "policy_name": "서울시 청년월세지원 사업",
  "agency_name": "서울시 (서울주택도시개발공사 청년월세지원센터)",
  "summary": "주거비 부담 완화를 위해 서울시에 월세로 거주하는 19~39세 무주택 청년가구에게 월 최대 20만원 이하의 월세를 최장 12개월간 지원",
  "field": ["생계주거"],
  "support_period": "최대 12개월 / 240만원 (생애 1회)",
  "cost": null,
  "age_min": 19,
  "age_max": 39,
  "exception_age": "의무복무 제대군인 청년 대상, 군 복무기간 고려 최대 3살(42세)까지 지원 연령 상한 연장",
  "application_method": "서울주거포털(housing.seoul.go.kr) 온라인 신청 및 접수",
  "deadline_type": "고정일",
  "deadline_date_raw": "2026. 5. 19. 18:00",
  "application_deadline": "2026-05-19T18:00:00",
  "result_note": "7월 초 심사결과 통보, 7월 말 최종 발표",
  "link": "https://housing.seoul.go.kr/...",
  "contact": "SH 청년월세지원센터 1833-2030",
  "is_lifetime_limit_once": true,
  "required_documents": [
    { "document_id": 87, "document_name": "지원신청서" },
    { "document_id": 9,  "document_name": "개인정보 동의서" }
  ]
}
```

### Response 필드

| 화면 라벨 | 필드 | 타입 | null 가능 | 채움 (57건 중) |
|---|---|---|---|---|
| — | `policy_id` | `number` | ❌ | 57 |
| 제도명 | `policy_name` | `string` | ❌ | 57 |
| 주관기관 | `agency_name` | `string` | ❌ | 57 |
| — | `summary` | `string` | ❌ | 57 |
| 분야 | `field` | `string[]` | ❌ (빈 배열 가능) | 50 |
| 지원기간 | `support_period` | `string` | ✅ | 52 |
| 자부담금 | `cost` | `string` | ✅ | **12** |
| 연령 하한 | `age_min` | `number` | ✅ | |
| 연령 상한 | `age_max` | `number` | ✅ | |
| 연령 예외 | `exception_age` | `string` | ✅ | |
| 신청방법 | `application_method` | `string` | ✅ | 56 |
| 신청마감 유형 | `deadline_type` | `string` | ✅ | 57 |
| 신청마감 원문 | `deadline_date_raw` | `string` | ✅ | 41 |
| 신청마감 일시 | `application_deadline` | `string` (ISO 8601) | ✅ | **28** |
| 결과발표 | `result_note` | `string` | ✅ | **28** |
| 공식사이트 | `link` | `string` | ✅ | 57 |
| 문의처 | `contact` | `string` | ✅ | **44** |
| 생애 1회 | `is_lifetime_limit_once` | `boolean` | ✅ | |
| 필요서류 | `required_documents` | `Document[]` | ❌ (빈 배열 가능) | |

**`Document` 객체**

| 필드 | 타입 | 설명 |
|---|---|---|
| `document_id` | `number` | |
| `document_name` | `string` | 서류명. **발급방법 설명은 현재 없음** |

### 주의 — `field`(분야)는 배열입니다

제도 하나가 유형을 여러 개 가질 수 있습니다. **57건 중 30건이 2개 이상**입니다.

| 유형 개수 | 제도 수 |
|---|---|
| 0개 (빈 배열) | 7 |
| 1개 | 27 |
| 2개 | 27 |
| 3개 | 3 |

화면에 하나만 표시하려면 `field[0]`을 쓰시되, **빈 배열(7건)일 때 처리**가 필요합니다.
값은 `"돌봄가사"` · `"의료건강"` · `"심리청년특화"` · `"생계주거"` 4개뿐입니다.

### 응답 코드

| 코드 | `error` | 조건 |
|---|---|---|
| `200 OK` | — | 정상 |
| `404 Not Found` | `POLICY_NOT_FOUND` | 해당 `policy_id` 없음 |
| `503 Service Unavailable` | `DATABASE_UNAVAILABLE` | 서버 DB 미연결 |
| `422` | — | `policyId`가 정수가 아님 |

### 예외 응답 예시

```json
// 404
{
  "error": "POLICY_NOT_FOUND",
  "message": "policy_id=99999 제도를 찾을 수 없습니다."
}
```

```json
// 503 — 프론트 문제가 아니라 서버 설정 문제
{
  "error": "DATABASE_UNAVAILABLE",
  "message": "제도 정보를 조회할 수 없습니다. 서버 DB 연결을 확인해주세요."
}
```

### 주의 — 날짜 필드

**날짜 타입은 `application_deadline` 하나뿐입니다.**

- 형식: `"2026-05-19T18:00:00"` (ISO 8601, **타임존 정보 없음 → KST로 간주**)
- `result_note`는 **날짜가 아니라 자유 텍스트**입니다.
  실제 값 예: `"6월 중"`, `"구청 개별 통지"`, `"2026. 11. 3. (예정)"` → **파싱하지 말고 그대로 표시**
- `support_period`도 자유 텍스트입니다. 예: `"최대 12개월 / 240만원 (생애 1회)"`

### 주의 — 신청마감 렌더링 분기

`deadline_type`은 아래 4값 중 하나입니다.

| 값 | 건수 | 권장 표시 |
|---|---|---|
| `"고정일"` | 15 | `application_deadline` 포맷 + D-day 표시 |
| `"회차형"` | 11 | `deadline_date_raw` 그대로 (회차 정보 포함) |
| `"상시"` | 15 | `"상시 모집"` — 날짜 표시 안 함 |
| `"예산소진시마감"` | 16 | `"예산 소진 시 마감"` |

> ⚠️ `deadline_date_raw`에 내부 주석이 섞여 있습니다.
> 예: `"2025.9.12. (출처 1 공고 기준)"`, `"2026. 5. 29. 16:00 (2차 추가모집 기준)"`
> **`application_deadline`이 있으면 그것을 우선 사용**하고, 없을 때만 원문으로 폴백하세요.

### 주의 — 마감일 알림받기 버튼

- **`application_deadline`이 `null`이면 버튼을 숨겨주세요.** 57건 중 **28건만** 값이 있습니다.
  나머지는 상시/예산소진 방식이라 마감일 개념 자체가 없습니다.
- 알림 등록/발송은 **백엔드 담당**입니다 (`notifications` 테이블). AI 서버는 관여하지 않습니다.
- `notification_type_enum`의 `RESULT_DDAY`(결과발표 알림)는 **현재 사용 불가**입니다.
  결과발표일이 파싱된 날짜로 저장돼 있지 않습니다.

---

## 6. 제도 배치 조회

```
GET /api/v1/policies?ids=2,7,16
```

매칭 결과 카드를 여러 개 그릴 때 N+1 호출을 방지합니다.

### Query Parameter

| 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `ids` | `string` | ✅ | 쉼표로 구분한 정수 목록. 예: `2,7,16` |

### Response `200 OK`

`policies` 배열 안에 5번 API와 **동일한 형태의 객체**가 담깁니다.

```json
{
  "policies": [
    { "policy_id": 2,  "policy_name": "서울시 가족돌봄청소년·청년 자기돌봄비 지원 사업", "field": ["의료건강", "심리청년특화"], "...": "..." },
    { "policy_id": 7,  "policy_name": "서울시 청년월세지원 사업", "field": ["생계주거"], "...": "..." },
    { "policy_id": 16, "policy_name": "서울청년문화패스", "field": ["심리청년특화"], "...": "..." }
  ]
}
```

**동작 규칙**

| 상황 | 동작 |
|---|---|
| 정상 | **요청한 순서 그대로** 반환 |
| 중복 id (`?ids=7,7,2`) | 중복 제거 후 반환 (2건) |
| **일부** id가 없음 | 없는 것만 빼고 `200` 반환 → **`policies` 길이가 요청보다 짧을 수 있음** |
| **전부** id가 없음 | `404 POLICY_NOT_FOUND` |

> ⚠️ 일부 누락이 가능하므로 **인덱스가 아니라 `policy_id`로 매칭**해서 사용하세요.

> ℹ️ 제도 목록·상세 화면을 **기존 백엔드 API로 이미 구현**했다면 이 API는 쓰지 않아도 됩니다.
> `policy_id`가 `policies.policy_id`로 동일하므로 3번 응답을 그대로 넘기면 됩니다.

### 응답 코드

| 코드 | `error` | 조건 |
|---|---|---|
| `200 OK` | — | 1건 이상 조회됨 |
| `400 Bad Request` | `BAD_REQUEST` | `ids` 형식 오류 / 빈 값 |
| `404 Not Found` | `POLICY_NOT_FOUND` | 요청한 id가 전부 없음 |
| `503 Service Unavailable` | `DATABASE_UNAVAILABLE` | 서버 DB 미연결 |
| `422` | — | `ids` 파라미터 자체가 없음 |

### 예외 응답 예시

```json
// 400 — ?ids=abc
{
  "error": "BAD_REQUEST",
  "message": "ids는 쉼표로 구분한 정수 목록이어야 합니다: 'abc'"
}
```

```json
// 404 — ?ids=99998,99999
{
  "error": "POLICY_NOT_FOUND",
  "message": "존재하지 않는 policy_id입니다: 99998, 99999"
}
```

---

## 7. 서버 상태 확인

```
GET /health
```

### Response `200 OK`

```json
{
  "status": "ok",
  "model": "claude-sonnet-5",
  "db": true,
  "prompts_dir": "/app"
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `status` | `string` | 항상 `"ok"` |
| `model` | `string` | 사용 중인 LLM 모델 |
| `db` | `boolean` | **`false`면 제도 조회·매칭이 전부 실패합니다** |
| `prompts_dir` | `string` | 프롬프트 문서 경로 |

연동 시작 전 이 API로 `db: true`를 먼저 확인하세요.

---

## 8. 보완 질문 라운드 — ❌ 미구현

```
POST /api/v1/chat/sessions/{sessionId}/followup
```

매칭 후 `확인_불가` 항목을 되물어 재매칭하는 라운드입니다.
**현재 `404`가 반환됩니다.** "더 찾아보기" 버튼을 노출하지 마세요.

---

## 9. 제도 번역기 — ❌ 미구현

```
POST /api/v1/policies/{policyId}/translate
```

어려운 정책 용어·서류 내용을 쉬운 말로 풀어주는 사이드 챗봇입니다.
**현재 `404`가 반환됩니다.**

---

## 10. 서버 제약 (연동 전 필독)

### 10.1 세션은 서버 메모리에만 존재합니다

- **서버 재배포/재시작 시 진행 중인 모든 세션이 소실됩니다.** 이후 호출은 전부
  `404 SESSION_NOT_FOUND`가 납니다. **시연 중 배포 금지.**
- **서버 인스턴스가 2대 이상이면 대화가 깨집니다.** 턴마다 다른 인스턴스로 붙으면
  세션을 찾지 못합니다. 현재는 **단일 인스턴스 전제**입니다.
- 세션 TTL은 **마지막 활동 기준 24시간**입니다.
- `404 SESSION_NOT_FOUND` 수신 시 **세션을 새로 만들고 처음부터** 진행해야 합니다.
  (대화 이어받기 기능은 없습니다.)

### 10.2 권장 타임아웃

| API | 예상 응답 시간 | 권장 타임아웃 |
|---|---|---|
| 1. 세션 시작 | 2~5초 | 30초 |
| 2. 대화 턴 | 3~8초 | 30초 |
| 3. **매칭 조회** | **10~60초+** | **90초** |
| 4. 세션 상태 | 10ms 미만 | 10초 |
| 5·6. 제도 조회 | 100ms 미만 | 10초 |

### 10.3 기타

- **CORS**: 전체 허용(`*`)
- **상태 판단은 항상 `phase`로** 하세요. `message` 문구는 매 호출 달라집니다.
- 매칭 응답의 `policy_id`는 `policies.policy_id`와 동일합니다. 기존 백엔드의 저장·알림 API에 그대로 넘길 수 있습니다.
