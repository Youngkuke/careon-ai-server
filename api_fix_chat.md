아래 내용을 그대로 AI 서버 담당자에게 보내면 됩니다.

---

안녕하세요. 현재 웹 프론트에서 사용하는 챗봇/AI API 전체 목록과 사용 목적을 정리했습니다. Railway 운영 서버 기준으로 엔드포인트 배포 여부와 계약 확인 부탁드립니다.

AI 서버 Base URL:

```text
https://careon-ai-server-production.up.railway.app
```

모든 API에 Spring이 발급한 access token을 사용합니다.

```http
Authorization: Bearer {access_token}
```

## 1. 최근 완료 대화 조회

```http
GET /api/v1/cb/threads/latest
```

사용 시점:

- 로그인 직후
- 저장된 access token으로 새로고침 세션을 복원할 때

목적:

- 사용자가 이전에 완료한 cb 상담 결과가 있는지 확인
- 완료된 thread가 있으면 결과 화면을 복원
- 없으면 새 상담 화면으로 진입

기대 응답:

```json
{
  "thread_id": "cb-9f2a41c0b7e3",
  "phase": "ready",
  "generated_at": "2026-07-27T14:32:05+09:00",
  "result_summary": {
    "matched": 7,
    "maybe": 8,
    "region_label": "송파구"
  }
}
```

완료된 대화가 없을 때도 404가 아니라 다음 `200 OK`를 기대합니다.

```json
{
  "thread_id": null,
  "phase": null,
  "generated_at": null,
  "result_summary": null
}
```

프론트 동작:

```text
thread_id 있고 phase=ready
  → GET /api/v1/cb/threads/{thread_id}/results
  → 결과 화면 복원

thread_id 없음
  → 새 상담 화면
  → POST /api/v1/cb/threads
```

현재 문제:

```text
GET https://careon-ai-server-production.up.railway.app/api/v1/cb/threads/latest
→ 404 Not Found
```

현재 이 API가 로그인 흐름에서 호출되기 때문에 404가 발생하면 로그인이 완료되지 않습니다. Railway 운영 서버에 실제로 배포됐는지, 경로가 정확한지 가장 먼저 확인 부탁드립니다.

## 2. 새 대화 시작

```http
POST /api/v1/cb/threads
```

Request Body:

```text
없음
```

`carer_id`는 보내지 않고 Bearer 토큰에서 식별합니다.

사용 시점:

- 완료된 기존 thread가 없을 때
- 사용자가 추가 상담 화면에 들어갈 때
- 맞춤 제도 화면에서 재상담을 시작할 때
- 기존 thread가 404로 만료됐을 때

기대 응답:

```json
{
  "thread_id": "cb-9f2a41c0b7e3",
  "phase": "gathering",
  "message": "안녕하세요! 필요한 지원을 함께 찾아드릴게요."
}
```

프론트는 `thread_id`를 화면 상태에 저장하고 `message`를 첫 봇 말풍선으로 표시합니다.

## 3. 사용자 메시지 전송

```http
POST /api/v1/cb/messages
Content-Type: application/json
```

Request Body:

```json
{
  "thread_id": "cb-9f2a41c0b7e3",
  "message": "엄마 병원비 때문에 힘들어요."
}
```

사용 시점:

- 사용자가 챗봇 입력창에서 메시지를 전송할 때마다 호출

기대 응답:

```json
{
  "thread_id": "cb-9f2a41c0b7e3",
  "phase": "gathering",
  "message": "조금 더 자세히 알려주세요.",
  "filters": {
    "life_cycle": ["청년"],
    "household": [],
    "theme": ["신체건강"]
  },
  "intake": {
    "target_for": "caree",
    "age": 24,
    "caree_age": 60
  },
  "result_summary": null
}
```

상담이 완료된 경우:

```json
{
  "thread_id": "cb-9f2a41c0b7e3",
  "phase": "ready",
  "message": "필요한 제도를 모두 찾았어요.",
  "result_summary": {
    "matched": 7,
    "maybe": 8,
    "region_label": "송파구"
  }
}
```

프론트 동작:

- `phase: gathering` → 응답 말풍선을 표시하고 다음 입력 대기
- `phase: ready` → 결과 조회 API 호출
- 별도의 `/match` API는 호출하지 않음
- 404 `THREAD_NOT_FOUND` → 새 thread를 만든 후 사용자에게 다시 입력하도록 안내
- 401 → 로그인 만료 처리

프론트에서 빈 문자열은 `trim()` 후 전송하지 않습니다.

## 4. 상담 결과 조회

```http
GET /api/v1/cb/threads/{thread_id}/results
```

사용 시점:

- 메시지 응답이 `phase: ready`가 된 직후
- `GET /threads/latest`에서 완료된 thread를 발견해 결과를 복원할 때

목적:

- AI 서버가 저장한 검색 결과 카드를 조회
- Spring의 기존 맞춤 제도 API는 cb 결과 조회에 사용하지 않음

프론트에서 사용하는 응답 필드:

```text
thread_id
generated_at
region
filters
relaxed_axes
banner
matched
maybe
```

각 섹션에서 사용하는 카드 필드:

```text
serv_id
name
agency
summary
region
tags
support
apply
link
```

프론트는 `banner`, `matched`, `maybe` 순서로 표시하며 각 배열의 순서를 변경하지 않습니다. `match.rank`, `score`, `distance`, `matched_by`는 사용자 화면에 표시하지 않습니다.

예상 오류:

- `409 RESULTS_NOT_READY`: `ready` 전에 결과를 요청한 경우
- `403 FORBIDDEN`: 다른 사용자의 thread
- `404 THREAD_NOT_FOUND`: 없는 또는 삭제된 thread

## 5. cb 제도 상세 조회

```http
GET /api/v1/cb/institutions/{serv_id}
```

사용 시점:

- cb 결과 카드 클릭
- Spring 찜 목록에서 `policy_id: null`, `serv_id`가 있는 제도를 클릭

목적:

- 복지로 제도의 전체 원문 상세 조회

프론트에서 사용하는 필드:

```text
serv_id
name
agency
summary
region
tags
support
apply
link
target_detail
select_criteria
service_content
apply_method
criteria_year
```

값이 `null`인 상세 섹션은 화면에서 숨깁니다.

## 6. cb 제도 쉬운 말 설명

```http
POST /api/v1/cb/institutions/{serv_id}/translate
```

Request Body:

```text
없음
```

사용 시점:

- cb 제도 상세 화면에서 사용자가 `쉬운 말로 보기` 버튼을 눌렀을 때만 호출
- 목록 진입 시 미리 호출하지 않음

기대 응답:

```json
{
  "serv_id": "WLF00000123",
  "name": "재난적 의료비 지원사업",
  "easy_text": "이 제도는 의료비 부담이 큰 가구를 지원하는 제도예요."
}
```

프론트는 `easy_text`를 상세 화면에 표시합니다.

## 7. 대화 삭제

```http
DELETE /api/v1/cb/threads/{thread_id}
```

사용 시점:

- 결과 화면에서 사용자가 `새로 상담하기`를 누를 때

프론트 동작:

```text
DELETE 기존 thread
→ POST /api/v1/cb/threads
→ 새 상담 시작
```

기대 응답:

```json
{
  "message": "대화가 초기화되었습니다."
}
```

## 8. 기존 제도 번역 API

```http
POST /api/v1/policies/{policy_id}/translate
```

현재도 실제 호출 중입니다.

사용 시점:

- Spring의 기존 숫자 `policy_id` 제도 상세 화면 진입
- cb 제도가 아닌 기존 제도의 쉬운 설명 표시

기대 응답:

```json
{
  "policy_id": 74,
  "explanation": "쉬운 말 설명"
}
```

cb 제도에는 이 API를 사용하지 않고 `/api/v1/cb/institutions/{serv_id}/translate`를 사용합니다.

기존 숫자 제도 지원을 계속 유지해야 하는지, 이 API도 운영 서버에서 계속 제공되는지 확인 부탁드립니다.

## 9. 프론트 API 모듈에는 남아 있지만 현재 화면에서 사용하지 않는 기존 API

아래 API 함수는 코드에 남아 있지만 현재 챗봇 화면에서는 호출하지 않습니다.

```text
POST   /api/v1/chat/sessions
POST   /api/v1/chat/sessions/{session_id}/messages
POST   /api/v1/chat/sessions/{session_id}/match
GET    /api/v1/chat/state
DELETE /api/v1/chat/state
GET    /api/v1/policies?ids=...
```

신규 cb 흐름에서는 다음으로 대체됐습니다.

```text
session_id → thread_id
/api/v1/chat/... → /api/v1/cb/...
ready_to_match/done → ready
별도 /match → 사용하지 않음
Spring 맞춤 제도 재조회 → cb /results 사용
```

완전히 폐기해도 되는 API라면 프론트의 미사용 함수도 제거할 예정이니 알려주세요.

## 확인 요청 요약

1. Railway 운영 서버에 `GET /api/v1/cb/threads/latest`가 실제 배포됐는지 확인
2. 대화가 없을 때 `404`가 아니라 `200 + thread_id: null`이 맞는지 확인
3. 위 cb API 7개가 모두 운영 서버에 배포됐는지 확인
4. 기존 `POST /api/v1/policies/{policy_id}/translate`를 계속 유지해야 하는지 확인
5. `/api/v1/chat/*` 기존 API들을 프론트에서 완전히 제거해도 되는지 확인

현재 가장 급한 문제는 `/api/v1/cb/threads/latest`가 운영 서버에서 404를 반환해 로그인 흐름이 중단되는 부분입니다.
