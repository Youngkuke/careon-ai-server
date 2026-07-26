# 프론트엔드 챗봇 API 사용 가이드

> 기준일: 2026-07-27  
> 기준 코드: `src/lib/api.js`, `src/pages/FollowupQuestionPage.jsx`, `src/pages/ProgramChatPage.jsx`, `src/pages/ProgramDetailPage.jsx`

이 문서는 CareOn 웹 프론트엔드에서 챗봇 및 AI 기능에 실제로 사용하는 API를 코드 기준으로 정리한 문서입니다.

전체 API 명세를 나열하는 대신 다음 내용을 중심으로 설명합니다.

- 프론트가 현재 실제 호출하는 API
- 각 API를 언제, 어떤 순서로 호출하는지
- 서버 원본 JSON과 프론트에서 정규화한 값의 차이
- 화면별 후속 처리
- 인증, 오류 처리, 세션 관리 시 주의할 점
- 호출 함수는 존재하지만 현재 화면에서 사용하지 않는 API

---

## 1. 현재 사용 현황 요약

### 1.1 실제 화면에서 직접 사용하는 AI API

| 기능 | Method | Path | 인증 | 현재 호출 화면 |
| --- | --- | --- | --- | --- |
| 채팅 세션 생성 | `POST` | `/api/v1/chat/sessions` | 필요 | 추가 진단, 맞춤 제도 재상담 |
| 채팅 메시지 전송 | `POST` | `/api/v1/chat/sessions/{session_id}/messages` | 필요 | 추가 진단, 맞춤 제도 재상담 |
| 제도 매칭 실행 | `POST` | `/api/v1/chat/sessions/{session_id}/match` | 필요 | 추가 진단, 맞춤 제도 재상담 |
| 제도 쉬운 말 번역 | `POST` | `/api/v1/policies/{policy_id}/translate` | 필요 | 제도 상세 |

### 1.2 챗봇 완료 후 이어서 사용하는 Spring API

| 기능 | Method | Path | 인증 | 사용 목적 |
| --- | --- | --- | --- | --- |
| 맞춤 제도 새로 조회 | `GET` | `/api/web/policies/matched` | 필요 | 챗봇 매칭 완료 후 화면 목록 갱신 |
| 저장한 제도 새로 조회 | `GET` | `/api/web/users/me/saved-policies` | 필요 | 재상담 후 저장 상태를 포함한 전체 목록 갱신 |
| 내 정보 조회 | `GET` | `/api/web/users/me` | 필요 | 최초 추가 진단 완료 후 `diagnosis_completed` 등 사용자 상태 갱신 |

### 1.3 API 함수는 있지만 현재 화면에서는 호출하지 않는 API

다음 함수는 `src/lib/api.js`에 구현되어 있지만 현재 컴포넌트에서는 사용하지 않습니다.

| API 함수 | Method | Path | 현재 상태 |
| --- | --- | --- | --- |
| `api.getChatState()` | `GET` | `/api/v1/chat/state` | 미사용 |
| `api.resetChatState()` | `DELETE` | `/api/v1/chat/state` | 미사용 |
| `api.getPoliciesByIds(policyIds)` | `GET` | `/api/v1/policies?ids=...` | 미사용 |

이 API들을 실제 화면에 연결할 때는 아래의 [9. 현재 미사용 API](#9-현재-미사용-api)를 참고합니다.

---

## 2. 공통 호출 규칙

### 2.1 Base URL

프론트는 Spring API와 AI API의 Base URL을 분리해서 설정할 수 있습니다.

```js
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || ''
const AI_API_BASE_URL = import.meta.env.VITE_AI_API_BASE_URL || API_BASE_URL
```

| 환경 변수 | 용도 | 미설정 시 |
| --- | --- | --- |
| `VITE_API_BASE_URL` | Spring 웹 API Base URL | 빈 문자열 |
| `VITE_AI_API_BASE_URL` | 챗봇/AI API Base URL | `VITE_API_BASE_URL` 값 사용 |

예시:

```env
VITE_API_BASE_URL=https://api.careon.site
VITE_AI_API_BASE_URL=https://ai.careon.site
```

로컬 개발에서 두 환경 변수가 모두 비어 있으면 Vite의 `/api` 프록시를 사용합니다. 현재 `vite.config.js`의 프록시 대상은 다음과 같습니다.

```text
http://localhost:8080
```

AI 서버가 Spring 서버와 다른 포트에서 실행되는 환경이라면 반드시 `VITE_AI_API_BASE_URL`을 별도로 지정해야 합니다.

### 2.2 인증

챗봇 관련 AI API는 모두 로그인 후 사용하며 다음 헤더를 보냅니다.

```http
Authorization: Bearer {access_token}
```

프론트는 로그인 또는 회원가입 성공 시 받은 토큰을 아래 Local Storage 키에 저장합니다.

```text
careon:webAccessToken
```

요청 본문이 있는 경우 다음 헤더도 자동으로 추가합니다.

```http
Content-Type: application/json
```

주의할 점:

- `auth: true`인 요청이라도 Local Storage에 토큰이 없으면 프론트 요청 유틸은 `Authorization` 헤더를 생략합니다.
- 따라서 챗봇 화면으로 진입하기 전에 로그인 상태와 `user.carerId`가 모두 준비되어 있어야 합니다.
- 서버는 요청 본문의 `carer_id`만 신뢰하지 말고 Bearer 토큰의 사용자와 동일한 사용자로 검증해야 합니다.

### 2.3 응답 방식

현재 챗봇 API는 스트리밍 방식이 아닙니다.

- SSE를 사용하지 않습니다.
- WebSocket을 사용하지 않습니다.
- 사용자 메시지 1개마다 HTTP 요청 1개를 보냅니다.
- 서버 응답 전체가 JSON으로 도착한 뒤 챗봇 메시지를 화면에 추가합니다.
- 화면의 타이핑 효과는 서버 스트리밍이 아니라, 받은 문자열을 프론트에서 20ms 간격으로 한 글자씩 보여주는 UI 효과입니다.

### 2.4 세션 수명과 화면 재진입

`session_id`는 DB PK가 아니라 AI 서버가 발급하는 메모리 세션 키입니다. 대화 본문도 현재 프론트에서는 별도로 저장하지 않습니다.

- 추가 진단 또는 재상담 화면을 열 때마다 새 세션을 생성합니다.
- 화면을 나갔다가 다시 들어오면 이전 `session_id`를 복원하지 않습니다.
- 브라우저 새로고침 후에도 이전 메시지와 `session_id`를 복원하지 않습니다.
- AI 서버가 재시작되거나 세션이 만료되면 기존 `session_id`로 메시지를 보낼 때 `404`가 발생할 수 있습니다.
- `GET /api/v1/chat/state` 함수는 준비되어 있지만 현재 연결되어 있지 않아, DB 진행 상태를 이용한 화면 복원도 하지 않습니다.

### 2.5 공통 오류 처리

AI 서버 오류의 기본 형식은 다음과 같습니다.

```json
{
  "error": "ERROR_CODE",
  "message": "사용자에게 보여줄 오류 메시지"
}
```

프론트는 성공하지 않은 HTTP 응답을 다음 `ApiError`로 변환합니다.

```js
{
  name: 'ApiError',
  message: '서버의 message 또는 기본 오류 문구',
  status: 401,
  code: 'ERROR_CODE'
}
```

처리 규칙:

- 서버 JSON에 `message`가 있으면 해당 문구를 사용합니다.
- `message`가 없거나 응답이 올바른 JSON이 아니면 `요청을 처리하지 못했어요.`를 사용합니다.
- 세션 생성 중 `401`이 발생하면 저장된 토큰을 지우고 로그인 화면으로 이동합니다.
- 메시지 전송이나 매칭 중 오류가 발생하면 현재 구현에서는 오류의 `message`를 채팅 말풍선으로 표시합니다.
- `fetch` 자체가 실패한 네트워크 오류는 일반 JavaScript `Error`로 전달되며, 역시 오류 문구를 말풍선으로 표시합니다.

---

## 3. 전체 챗봇 호출 흐름

### 3.1 최초 추가 진단

로그인 또는 회원가입 후 `diagnosis_completed`가 `false`이면 `FollowupQuestionPage`로 이동합니다.

```text
추가 진단 화면 진입
  → POST /api/v1/chat/sessions
  → session_id 저장
  → POST /api/v1/chat/sessions/{session_id}/messages 반복
  → 응답 phase 확인
      ├─ info_gathering: 다음 사용자 입력 대기
      ├─ ready_to_match: POST /match 실행 후 진단 완료 처리
      └─ done: 추가 /match 없이 진단 완료 처리
  → GET /api/web/users/me
  → 분석 중 화면
  → GET /api/web/policies/matched
  → 맞춤 제도 화면
```

`ready_to_match`에서만 프론트가 명시적으로 `/match`를 호출합니다. 메시지 응답이 이미 `done`이면 매칭이 서버에서 완료된 것으로 보고 `/match`를 다시 호출하지 않습니다.

### 3.2 맞춤 제도 재상담

이미 진단을 마친 사용자가 맞춤 제도 화면에서 상담 채팅을 열면 `ProgramChatPage`를 사용합니다.

```text
재상담 화면 진입
  → POST /api/v1/chat/sessions
  → session_id 저장
  → POST /api/v1/chat/sessions/{session_id}/messages 반복
  → 응답 phase 확인
      ├─ info_gathering: 다음 사용자 입력 대기
      ├─ ready_to_match:
      │    POST /match
      │    GET /api/web/policies/matched
      │    GET /api/web/users/me/saved-policies
      └─ done:
           GET /api/web/policies/matched
           GET /api/web/users/me/saved-policies
```

재상담에서는 매칭이 완료돼도 화면을 자동으로 닫지 않습니다. 새 맞춤 제도와 저장 상태만 다시 불러오고 대화를 계속 보여줍니다.

### 3.3 제도번역기

로그인한 사용자가 제도 상세 화면을 열면 선택한 제도 ID로 쉬운 설명을 요청합니다.

```text
제도 상세 화면 진입
  → POST /api/v1/policies/{policy_id}/translate
  → explanation을 읽기 전용 챗봇 말풍선으로 표시
```

제도번역기는 대화 세션을 만들지 않으며, 메시지를 입력하는 일반 챗봇과 독립적으로 작동합니다.

---

## 4. 채팅 세션 생성

새 추가 진단 화면이나 재상담 화면에 들어올 때 한 번 호출합니다.

### Request

```http
POST /api/v1/chat/sessions
Authorization: Bearer {access_token}
Content-Type: application/json
```

```json
{
  "carer_id": 12
}
```

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `carer_id` | Integer | Y | 로그인한 사용자 ID. `GET /api/web/users/me` 결과를 정규화한 `user.carerId` 사용 |

프론트 호출:

```js
const session = await api.createChatSession(user.carerId)
```

`api.createChatSession()`이 만드는 실제 요청:

```js
aiRequest('/api/v1/chat/sessions', {
  method: 'POST',
  body: { carer_id: carerId },
  auth: true,
})
```

### Response

```json
{
  "session_id": "uuid-or-string",
  "conversation_state_id": 3,
  "phase": "info_gathering",
  "current_phase": 1,
  "active_policy_id": 0,
  "message": "상황을 조금 더 자세히 알려주세요."
}
```

| 서버 필드 | 타입 | 필수도 | 프론트 필드 | 사용 여부 |
| --- | --- | --- | --- | --- |
| `session_id` | String | 필수 | `sessionId` | 이후 메시지 및 매칭 API path에 사용 |
| `conversation_state_id` | Integer | 선택 | `conversationStateId` | 정규화하지만 현재 화면에서는 사용하지 않음 |
| `phase` | String | 선택 | `phase` | 정규화하지만 세션 생성 직후에는 분기하지 않음 |
| `current_phase` | Integer | 선택 | `currentPhase` | 정규화하지만 현재 화면에서는 사용하지 않음 |
| `active_policy_id` | Integer 또는 `null` | 선택 | `activePolicyId` | 정규화하지만 현재 화면에서는 사용하지 않음 |
| `message` | String | 선택 | `message` | 값이 있으면 첫 서버 챗봇 메시지로 추가 |

프론트가 받는 정규화 결과:

```js
{
  sessionId: 'uuid-or-string',
  conversationStateId: 3,
  phase: 'info_gathering',
  phaseLabel: '정보 수집',
  currentPhase: 1,
  activePolicyId: 0,
  message: '상황을 조금 더 자세히 알려주세요.'
}
```

### 화면 처리

1. 요청 중에는 채팅 입력 버튼을 비활성화하고 입력 중 표시를 보여줍니다.
2. 성공하면 `session.sessionId`를 컴포넌트 상태에 저장합니다.
3. `session.message`가 있으면 봇 말풍선으로 추가합니다.
4. 화면이 API 응답 전에 사라진 경우 `ignore` 플래그로 상태 업데이트를 막습니다.
5. `user.carerId`가 없으면 요청하지 않고 로딩만 종료합니다.
6. 화면을 나갔다가 다시 들어오면 기존 세션을 재사용하지 않고 새 세션을 생성합니다.

세션 생성 전 사용자가 전송을 시도하면 API를 호출하지 않고 다음 로컬 메시지를 반환합니다.

- 추가 진단: `진단 세션을 준비하고 있어요. 잠시 후 다시 입력해 주세요.`
- 재상담: `상담 세션을 준비하고 있어요. 잠시 후 다시 입력해 주세요.`

### 주요 오류

| Status | 대표 `message` | 조건 |
| --- | --- | --- |
| `400` | `값이 누락되었습니다.` | `carer_id`가 없거나 올바르지 않음 |
| `401` | `로그인이 필요합니다.` | 토큰 없음, 만료 또는 인증 실패 |
| `404` | `존재하지 않는 유저입니다.` | `carer_id`에 해당하는 사용자가 없음 |

### cURL 예시

```bash
curl -X POST "${AI_API_BASE_URL}/api/v1/chat/sessions" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"carer_id":12}'
```

---

## 5. 채팅 메시지 전송

사용자가 채팅 입력창에서 메시지를 보낼 때마다 호출합니다.

### Request

```http
POST /api/v1/chat/sessions/{session_id}/messages
Authorization: Bearer {access_token}
Content-Type: application/json
```

Path Variable:

| 이름 | 타입 | 설명 |
| --- | --- | --- |
| `session_id` | String | 세션 생성 응답으로 받은 AI 서버 세션 키 |

Body:

```json
{
  "message": "엄마랑 둘이 살고 있고 알바로 생활비를 벌고 있어요."
}
```

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `message` | String | Y | 사용자가 입력한 자연어 문장 |

프론트 호출:

```js
const response = await api.sendChatMessage(sessionId, message)
```

`api.sendChatMessage()`가 만드는 실제 요청:

```js
aiRequest(`/api/v1/chat/sessions/${sessionId}/messages`, {
  method: 'POST',
  body: { message },
  auth: true,
})
```

### 프론트 입력 처리

- 전송 전에 문자열 앞뒤 공백을 제거합니다.
- 공백만 있는 메시지는 보내지 않습니다.
- 요청 중에는 추가 전송을 막습니다.
- 한글 IME 조합 중 Enter가 눌린 경우 중복 전송하지 않습니다.
- `Enter`는 전송, `Shift + Enter`는 줄바꿈입니다.
- API 호출 전에 사용자 메시지를 화면에 먼저 추가합니다.
- API 응답의 `message`가 있으면 봇 말풍선으로 추가합니다.

### Response

```json
{
  "message": "가구원 수와 현재 소득 상황을 확인했어요.",
  "phase": "info_gathering",
  "current_phase": 1,
  "active_policy_id": 0
}
```

| 서버 필드 | 타입 | 프론트 필드 | 사용 방식 |
| --- | --- | --- | --- |
| `message` | String | `message` | 봇 말풍선 내용 |
| `phase` | String | `phase` | 다음 동작을 결정하는 핵심 필드 |
| `current_phase` | Integer | `currentPhase` | 정규화하지만 현재 UI에서는 표시하지 않음 |
| `active_policy_id` | Integer 또는 `null` | `activePolicyId` | 정규화하지만 현재 UI에서는 사용하지 않음 |

프론트가 받는 정규화 결과:

```js
{
  message: '가구원 수와 현재 소득 상황을 확인했어요.',
  phase: 'info_gathering',
  phaseLabel: '정보 수집',
  currentPhase: 1,
  activePolicyId: 0
}
```

### `phase`별 프론트 동작

| `phase` | 프론트 표시명 | 추가 진단 화면 | 재상담 화면 |
| --- | --- | --- | --- |
| `info_gathering` | 정보 수집 | 응답 표시 후 다음 입력 대기 | 응답 표시 후 다음 입력 대기 |
| `ready_to_match` | 매칭 준비 | `/match` 호출 후 진단 완료 처리 | `/match` 호출 후 맞춤/저장 제도 새로 조회 |
| `matching` | 매칭 중 | 별도 분기 없음 | 별도 분기 없음 |
| `done` | 완료 | `/match`를 다시 호출하지 않고 진단 완료 처리 | `/match`를 다시 호출하지 않고 맞춤/저장 제도 새로 조회 |
| 그 외 문자열 | 원문 그대로 | 별도 분기 없음 | 별도 분기 없음 |

중요:

- 현재 자동 매칭 호출은 `phase === 'ready_to_match'`와 정확히 일치할 때만 실행됩니다.
- 대소문자나 다른 표현(`READY_TO_MATCH`, `ready`, 숫자 코드 등)은 인식하지 않습니다.
- `phase`가 누락되면 일반 응답처럼 메시지만 보여주고 다음 입력을 기다립니다.
- `ready_to_match` 응답을 받은 뒤 같은 메시지를 재전송하지 않도록 서버와 프론트 양쪽에서 중복 매칭에 대비하는 것이 안전합니다.

### 주요 오류

| Status | 대표 `message` | 조건 |
| --- | --- | --- |
| `400` | `메시지를 입력해주세요.` | `message`가 없거나 빈 문자열 |
| `401` | `로그인이 필요합니다.` | 토큰 없음, 만료 또는 인증 실패 |
| `404` | `존재하지 않는 세션입니다.` | 세션 ID가 잘못됐거나 AI 서버 세션이 만료됨 |

현재 메시지 전송 중 `401`이 발생해도 세션 생성 오류처럼 로그인 화면으로 자동 이동하지는 않습니다. 오류 문구가 채팅에 표시되므로, 전송 단계에서도 인증 만료를 일관되게 처리하려면 별도 보완이 필요합니다.

### cURL 예시

```bash
curl -X POST "${AI_API_BASE_URL}/api/v1/chat/sessions/${SESSION_ID}/messages" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"message":"엄마랑 둘이 살고 있고 알바로 생활비를 벌고 있어요."}'
```

---

## 6. 제도 매칭 실행

메시지 전송 응답의 `phase`가 `ready_to_match`일 때 호출합니다.

### Request

```http
POST /api/v1/chat/sessions/{session_id}/match
Authorization: Bearer {access_token}
```

Request Body는 없습니다.

프론트 호출:

```js
const result = await api.matchChatSession(sessionId)
```

`api.matchChatSession()`이 만드는 실제 요청:

```js
aiRequest(`/api/v1/chat/sessions/${sessionId}/match`, {
  method: 'POST',
  auth: true,
})
```

### Response

```json
{
  "matches": [
    {
      "matched_policy_id": 101,
      "policy_id": 16,
      "match_group": "적합"
    },
    {
      "matched_policy_id": 102,
      "policy_id": 7,
      "match_group": "확인_불가"
    }
  ]
}
```

| 서버 필드 | 타입 | 프론트 필드 | 설명 |
| --- | --- | --- | --- |
| `matches` | Array | `matches` | 매칭된 제도 목록. 누락 시 빈 배열로 처리 |
| `matched_policy_id` | Integer | `matchedPolicyId` | 저장된 매칭 결과 ID |
| `policy_id` | Integer | `policyId` | 매칭된 제도 ID |
| `match_group` | String | `matchGroup` | 예: `적합`, `확인_불가` |

프론트가 받는 정규화 결과:

```js
{
  matches: [
    {
      matchedPolicyId: 101,
      policyId: 16,
      matchGroup: '적합'
    }
  ]
}
```

### 현재 화면에서의 사용 방식

현재 화면은 `/match` 응답의 `matches` 배열로 직접 카드를 그리지 않습니다.

매칭 실행 성공 여부만 확인한 뒤 Spring API의 `GET /api/web/policies/matched`를 다시 호출하여 카드 전체 데이터를 받습니다. 따라서 다음 조건이 필요합니다.

1. AI 서버가 매칭 결과를 서버 저장소에 반영해야 합니다.
2. `/match` 성공 응답이 반환되는 시점에는 Spring API에서 새 매칭 결과를 조회할 수 있어야 합니다.
3. 저장과 조회 사이에 지연이 있다면 프론트가 즉시 조회했을 때 이전 목록이 보일 수 있습니다.

재상담 화면은 `ready_to_match` 응답 문구 뒤에 다음 문장을 덧붙입니다.

```text
맞춤 제도를 새로 살펴봤어요.
```

원본 `response.message`가 비어 있으면 `상황을 반영했어요.`를 대신 사용합니다.

### 주요 오류

| Status | 대표 `message` | 조건 |
| --- | --- | --- |
| `401` | `로그인이 필요합니다.` | 토큰 없음, 만료 또는 인증 실패 |
| `404` | `존재하지 않는 세션입니다.` | 세션 ID가 잘못됐거나 AI 서버 세션이 만료됨 |

매칭 요청이 실패하면 후속 맞춤 제도 조회도 실행하지 않습니다. 오류는 호출한 메시지 전송의 실패로 처리되어 채팅 말풍선에 표시됩니다.

### cURL 예시

```bash
curl -X POST "${AI_API_BASE_URL}/api/v1/chat/sessions/${SESSION_ID}/match" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

---

## 7. 챗봇 완료 후 맞춤 제도 갱신

이 API들은 AI 서버 API가 아니라 Spring 웹 API이지만, 현재 챗봇 완료 흐름에 직접 연결되어 있습니다.

### 7.1 맞춤 제도 조회

```http
GET /api/web/policies/matched
Authorization: Bearer {access_token}
```

프론트 호출:

```js
const groups = await api.getMatchedPolicies()
const programs = normalizeMatchedPolicyGroups(groups)
```

서버 응답은 유형별 그룹 배열이며, 프론트는 각 그룹의 `policies`를 하나의 제도 배열로 펼쳐 화면에 저장합니다.

재상담에서는 `/match` 성공 직후 호출합니다. 최초 추가 진단에서는 완료 처리 후 사용자 상태가 갱신되면서 맞춤 제도 조회가 이어집니다.

### 7.2 저장한 제도 조회

```http
GET /api/web/users/me/saved-policies
Authorization: Bearer {access_token}
```

재상담 완료 시 기존 저장 상태가 사라지지 않도록 맞춤 제도와 함께 다시 불러옵니다.

```js
await refreshMatchedPolicies()
await refreshSavedPolicies()
```

현재 두 요청은 병렬이 아니라 순차적으로 실행됩니다.

---

## 8. 제도번역기

로그인한 사용자가 제도 상세 화면에 진입할 때 제도를 쉬운 말로 풀어달라고 요청합니다.

### Request

```http
POST /api/v1/policies/{policy_id}/translate
Authorization: Bearer {access_token}
```

Path Variable:

| 이름 | 타입 | 설명 |
| --- | --- | --- |
| `policy_id` | Integer | 현재 상세 화면에 표시 중인 제도 ID |

Request Body는 없습니다.

프론트 호출:

```js
const response = await api.translatePolicy(program.id)
```

### Response

```json
{
  "policy_id": 16,
  "explanation": "이 제도는 신청 조건과 지원 내용을 쉽게 설명하면 다음과 같아요..."
}
```

| 필드 | 타입 | 사용 여부 | 설명 |
| --- | --- | --- | --- |
| `policy_id` | Integer | 현재 미사용 | 요청한 제도 ID |
| `explanation` | String | 사용 | 읽기 전용 챗봇 말풍선으로 표시 |

### 화면 처리

1. `program.id`와 로그인 사용자 정보가 모두 있을 때만 호출합니다.
2. 호출을 시작하면 `제도를 쉬운 말로 풀어보고 있어요.`를 먼저 표시합니다.
3. 성공하면 해당 문구를 `response.explanation`으로 교체합니다.
4. `explanation`이 비어 있으면 번역 말풍선을 추가하지 않습니다.
5. 실패하면 서버의 `message`를 표시합니다.
6. 서버 오류 문구도 없으면 `쉬운 설명을 불러오지 못했어요.`를 표시합니다.
7. 상세 화면이 바뀌거나 사라진 뒤 도착한 응답은 `ignore` 플래그로 무시합니다.

제도번역기는 일반 채팅과 달리 사용자 입력창이 없는 읽기 전용 `SideChatPanel`에 표시됩니다.

### 주요 오류

| Status | 대표 `message` | 조건 |
| --- | --- | --- |
| `401` | `로그인이 필요합니다.` | 토큰 없음, 만료 또는 인증 실패 |
| `404` | `해당 제도를 찾을 수 없습니다.` | 존재하지 않는 `policy_id` |

제도번역기 오류는 현재 로그인 화면 이동이나 재시도를 수행하지 않고, 상세 화면의 읽기 전용 말풍선에 오류 문구를 표시합니다.

### cURL 예시

```bash
curl -X POST "${AI_API_BASE_URL}/api/v1/policies/16/translate" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

---

## 9. 현재 미사용 API

아래 API는 프론트 API 모듈에 준비되어 있지만 현재 어떤 화면에서도 호출하지 않습니다. 새 기능을 만들 때 바로 사용할 수 있으나, 현재 서비스 동작을 설명할 때 “사용 중”인 API로 보면 안 됩니다.

### 9.1 챗봇 진행 상태 조회

```http
GET /api/v1/chat/state
Authorization: Bearer {access_token}
```

프론트 호출 함수:

```js
const state = await api.getChatState()
```

예상 서버 응답:

```json
{
  "conversation_state_id": 3,
  "carer_id": 12,
  "phase": "info_gathering",
  "current_phase": 1,
  "active_policy_id": 16,
  "updated_at": "2026-07-27T10:00:00+09:00"
}
```

정규화 결과:

```js
{
  conversationStateId: 3,
  carerId: 12,
  phase: 'info_gathering',
  phaseLabel: '정보 수집',
  currentPhase: 1,
  activePolicyId: 16,
  updatedAt: '2026-07-27T10:00:00+09:00'
}
```

현재 미사용이므로 새로 연결하려면 다음 정책을 먼저 정해야 합니다.

- 저장된 진행 상태만 복원할지
- AI 서버 메모리에 남아 있는 실제 메시지 내역도 복원할지
- 상태는 있지만 세션이 만료된 경우 새 세션을 만들지
- 완료 상태에서 추가 진단 화면을 건너뛸지

### 9.2 챗봇 진행 상태 초기화

```http
DELETE /api/v1/chat/state
Authorization: Bearer {access_token}
```

프론트 호출 함수:

```js
await api.resetChatState()
```

현재 `다시 진단하기` 동작은 로컬의 자가진단 상태만 초기화하며 이 API를 호출하지 않습니다. 서버의 챗봇 진행 상태까지 초기화해야 하는 요구사항이 생기면 다시 진단 흐름에 별도로 연결해야 합니다.

### 9.3 ID 기반 제도 조회

```http
GET /api/v1/policies?ids=2,7,16
Authorization: Bearer {access_token}
```

프론트 호출 함수:

```js
const policies = await api.getPoliciesByIds([2, 7, 16])
```

구현 특징:

- ID 배열을 쉼표로 연결합니다.
- 전체 문자열을 `encodeURIComponent()`로 인코딩합니다.
- 응답이 `{ "policies": [...] }`이면 `policies`를 반환합니다.
- 서버가 배열 자체를 반환해도 해당 배열을 반환합니다.
- 둘 다 없으면 빈 배열을 반환합니다.

현재 매칭 결과 카드는 이 API가 아니라 `GET /api/web/policies/matched`로 조회합니다.

---

## 10. 서버 원본 필드와 프론트 필드 매핑

API 서버는 `snake_case`, React 화면 내부에서는 주로 `camelCase`를 사용합니다.

| 서버 원본 | 프론트 정규화 |
| --- | --- |
| `session_id` | `sessionId` |
| `conversation_state_id` | `conversationStateId` |
| `current_phase` | `currentPhase` |
| `active_policy_id` | `activePolicyId` |
| `matched_policy_id` | `matchedPolicyId` |
| `policy_id` | `policyId` |
| `match_group` | `matchGroup` |
| `carer_id` | `carerId` |
| `updated_at` | `updatedAt` |

정규화 함수는 호환성을 위해 서버가 이미 `camelCase`로 응답해도 처리합니다. 다만 API 계약의 기본 필드명은 `snake_case`로 유지하는 것을 권장합니다.

### Phase 표시명

| 서버 `phase` | 프론트 `phaseLabel` |
| --- | --- |
| `info_gathering` | `정보 수집` |
| `ready_to_match` | `매칭 준비` |
| `matching` | `매칭 중` |
| `done` | `완료` |
| 기타 값 | 원본 문자열 |
| 값 없음 | 빈 문자열 |

`phaseLabel`은 현재 화면에 직접 출력하지 않으며 디버깅이나 추후 상태 UI에 사용할 수 있도록 정규화만 하고 있습니다.

---

## 11. 구현 시 체크리스트

### 프론트에서 새 챗봇 화면을 연결할 때

- 로그인 토큰이 저장되어 있는지 확인합니다.
- `user.carerId`가 준비된 뒤 세션을 생성합니다.
- 세션 생성 중에는 입력을 막습니다.
- 서버에서 받은 `session_id`를 그대로 이후 path에 사용합니다.
- 사용자 입력은 `trim()` 후 빈 문자열을 거절합니다.
- 한 요청이 끝나기 전에 중복 전송하지 않습니다.
- 메시지 응답의 `phase`를 반드시 확인합니다.
- `ready_to_match`일 때만 `/match`를 호출합니다.
- `done`일 때 `/match`를 중복 호출하지 않습니다.
- 매칭 후에는 `GET /api/web/policies/matched`로 실제 카드 데이터를 다시 조회합니다.
- `401`, 세션 만료 `404`, 네트워크 오류를 각각 사용자에게 이해 가능한 방식으로 처리합니다.
- 화면이 사라진 뒤 비동기 응답으로 상태를 업데이트하지 않도록 취소 또는 ignore 처리를 합니다.

### AI 서버 응답을 변경할 때

- `session_id`는 문자열로 유지합니다.
- 사용자에게 보여줄 모든 응답에는 가능한 한 `message`를 포함합니다.
- 프론트 제어가 필요한 응답에는 정확한 소문자 `phase` 값을 포함합니다.
- `ready_to_match` 응답 이후 `/match`가 즉시 호출되어도 저장 결과를 조회할 수 있게 합니다.
- 동일 세션에 `/match`가 중복 호출될 가능성을 고려해 멱등성을 보장하는 것이 안전합니다.
- 오류 응답에는 기계 판별용 `error`와 사용자 표시용 `message`를 함께 반환합니다.
- Bearer 토큰 사용자와 body의 `carer_id`가 일치하는지 검증합니다.

---

## 12. 관련 코드 위치

| 역할 | 파일 |
| --- | --- |
| API Base URL, 인증, 공통 오류, 챗봇 API 함수, 응답 정규화 | `src/lib/api.js` |
| 최초 추가 진단 챗봇 흐름 | `src/pages/FollowupQuestionPage.jsx` |
| 맞춤 제도 재상담 챗봇 흐름 | `src/pages/ProgramChatPage.jsx` |
| 메시지 입력, 전송 잠금, 말풍선, 오류 표시, 타이핑 UI | `src/components/layout/SideChatPanel.jsx` |
| 제도번역기 호출 및 표시 | `src/pages/ProgramDetailPage.jsx` |
| 로그인 이후 화면 전환, 완료 후 사용자/제도 갱신 | `src/App.jsx` |
| 전체 백엔드 API 명세 | `api.md` |
