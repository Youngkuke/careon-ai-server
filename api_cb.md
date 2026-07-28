# 챗봇 검색엔진(cb) API 명세

> 기준일: 2026-07-27
> 기준 브랜치: `feature/chatbot-search-engine`
> 기준 코드: `app/routers/cb.py`, `app/cb/schemas.py`, `app/cb/cards.py`, `app/cb/nodes.py`
> 대체 대상: `api_chat.md`(기존 챗봇 API 가이드)

이 문서는 새로 만든 챗봇(이하 **cb**)이 실제로 내려주는 계약을 코드 기준으로 정리한 것입니다.
기존 챗봇(`/api/v1/chat/...`)은 그대로 살아 있고, cb는 `/api/v1/cb/...` 아래에서 **완전히 별개로** 동작합니다.

기존과 달라진 이유는 두 가지입니다.

1. **데이터 소스가 바뀌었습니다.** 자체 `policies` 64건 → 공공데이터포털 복지서비스(중앙부처 + 지자체) 856건.
   제도 식별자가 `policy_id`(Integer)에서 `serv_id`(String, 복지로 서비스ID)로 바뀌었고, 카드에 담기는 필드가 전부 달라졌습니다.
2. **매칭 방식이 바뀌었습니다.** 조건 판정(적합/확인_불가)이 아니라 하이브리드 검색(벡터 + 키워드)입니다.
   그래서 `match_group` 대신 **맞춤 / 혹시 관심 있으실 수도** 두 구간으로 나눕니다.

---

## 1. 기존 명세(api_chat.md)와의 대응표

프론트가 무엇을 어떻게 바꿔야 하는지부터 봅니다.

| 기존 (api_chat.md) | 신규 (cb) | 비고 |
| --- | --- | --- |
| `POST /api/v1/chat/sessions` (body `{carer_id}`) | `POST /api/v1/cb/threads` (body 없음) | `carer_id`는 Bearer 토큰에서만 읽습니다. body로 받지 않습니다 |
| `session_id` | `thread_id` | 문자열. 형식 `cb-` + hex 12자 |
| `POST /chat/sessions/{sid}/messages` | `POST /api/v1/cb/messages` | 세션 id를 path가 아니라 **body**에 넣습니다 |
| `phase: info_gathering` | `phase: gathering` | |
| `phase: ready_to_match` / `matching` / `done` | `phase: ready` | cb는 이 셋을 구분하지 않습니다. 3번 절 참고 |
| `POST /chat/sessions/{sid}/match` | **없음** | 검색이 대화 마지막 턴 안에서 이미 끝납니다 |
| `GET /api/web/policies/matched` (Spring) | `GET /api/v1/cb/threads/{thread_id}/results` | **매칭된 제도를 AI 서버가 카드까지 직접 내려줍니다.** Spring 재조회 불필요 |
| `POST /api/v1/policies/{policy_id}/translate` | `POST /api/v1/cb/institutions/{serv_id}/translate` | 응답 필드 `explanation` → `easy_text` |
| (제도 상세는 Spring) | `GET /api/v1/cb/institutions/{serv_id}` | 제도 원문 상세도 AI 서버가 내려줍니다 |
| `GET /api/web/users/me` → `diagnosis_completed` | `GET /api/v1/cb/threads/latest` | cb는 `carers`에 쓰지 않습니다. 완료 여부를 cb가 직접 답합니다 |
| `GET/DELETE /api/v1/chat/state` | `DELETE /api/v1/cb/threads/{thread_id}` | 진행 상태 조회 API는 없습니다 |
| `policy_id` (Integer) | `serv_id` (String) | 예: `"WLF00000123"` |
| `matched_policy_id` | **없음** | cb는 매칭 결과를 따로 저장하지 않습니다 |
| `match_group: 적합 / 확인_불가` | 섹션 `matched` / `maybe` | 구간이 배열 소속으로 표현됩니다 |

### 그대로 유지되는 것

- 인증 방식 (`Authorization: Bearer {access_token}`, Local Storage 키 `careon:webAccessToken`)
- 오류 응답 형식 (`{ "error": "CODE", "message": "..." }`)
- 비스트리밍 (SSE/WebSocket 없음, 메시지 1개당 HTTP 요청 1개)
- 세션을 서버 메모리가 아니라 DB에 두지만, 프론트가 대화 본문을 복원하는 API는 여전히 없음

### cb가 하지 않는 것 (프론트가 알아야 할 것)

- **`carers` 테이블을 쓰지 않습니다.** `diagnosis_completed`를 갱신하지 않고, `matched_policy`에도 쓰지 않습니다.
  기존 흐름의 `GET /api/web/users/me` / `GET /api/web/policies/matched`는 cb 결과와 무관합니다.
  화면 복원은 [6-2](#6-2-마지막으로-마친-대화-조회--화면-복원)를 쓰세요.
- **제도 저장(찜) API가 없습니다.** cb는 저장 엔드포인트를 제공하지 않습니다.
  찜은 Spring에서 처리하고, 키만 `policy_id`(Integer)가 아니라 `serv_id`(String)로 잡으면 됩니다.
  `serv_id` 안정성은 [10. 필드 사전](#serv_id-는-안정적인가)을 참고하세요.

---

## 2. 공통 호출 규칙

### 2.1 Base URL

기존과 같습니다. AI 서버 base URL 뒤에 `/api/v1/cb`를 붙입니다.

```env
VITE_AI_API_BASE_URL=https://ai.careon.site
```

로컬은 `http://localhost:8000` (Spring은 8080이라 반드시 분리해서 지정해야 합니다).

### 2.2 인증

모든 cb API가 로그인을 요구합니다.

```http
Authorization: Bearer {access_token}
```

- 토큰은 Spring이 발급한 access token입니다 (HS512, `sub`에 `carer_id` 문자열).
- `type`이 `access`가 아니면 401입니다. refresh 토큰으로는 호출할 수 없습니다.
- **body에 `carer_id`를 넣지 않습니다.** 서버는 토큰의 `sub`만 신원으로 씁니다.
- 대화(`thread_id`)의 소유자가 토큰 사용자와 다르면 403입니다.

### 2.3 응답 방식

기존과 동일하게 비스트리밍입니다.

- 사용자 메시지 1개 = HTTP 요청 1개.
- 응답 전체가 JSON으로 도착한 뒤 말풍선을 추가합니다.
- 타이핑 효과는 프론트의 UI 효과입니다.

응답 시간 참고값:

| 호출 | 대략 소요 | 이유 |
| --- | --- | --- |
| `POST /threads` | 즉시 | 고정 인사말. LLM/DB 호출 없음 |
| `POST /messages` (대화 중) | 1~3초 | LLM 2회(의도 추출 + 답변) |
| `POST /messages` (마지막 턴, `ready` 전환) | 3~6초 | 위 + 임베딩 + 벡터/키워드 검색 |
| `GET /threads/{id}/results` | 즉시 | 저장해 둔 카드를 읽기만 함 |
| `POST /institutions/{id}/translate` | 2~4초 | LLM 1회 |

### 2.4 세션 수명

`thread_id`는 LangGraph 체크포인터(Postgres `cb` 스키마)에 저장됩니다. 서버가 재시작돼도 살아 있습니다.

- 화면에 들어올 때마다 새 대화를 시작해도 되고, `thread_id`를 보관했다가 이어가도 됩니다.
- 다만 **대화 본문(말풍선 목록)을 돌려주는 API는 없습니다.** 이어가기를 하면 서버는 문맥을 기억하지만 화면은 비어 있습니다.
- 결과 화면은 `thread_id`만 있으면 몇 번이든 다시 열 수 있습니다 (`GET /threads/{id}/results`).
- 삭제는 `DELETE /threads/{thread_id}`입니다.

### 2.5 공통 오류

기존과 같은 형식입니다.

```json
{
  "error": "THREAD_NOT_FOUND",
  "message": "대화를 찾을 수 없습니다. 새로 시작해주세요."
}
```

| Status | `error` | `message` | 발생 조건 |
| --- | --- | --- | --- |
| `401` | `UNAUTHORIZED` | 로그인이 필요합니다. | 토큰 없음/만료/서명 불일치/refresh 토큰 |
| `403` | `FORBIDDEN` | 본인의 대화가 아닙니다. | 남의 `thread_id` |
| `404` | `THREAD_NOT_FOUND` | 대화를 찾을 수 없습니다. 새로 시작해주세요. | 없는 `thread_id`, 삭제된 대화 |
| `404` | `INSTITUTION_NOT_FOUND` | 해당 제도를 찾을 수 없습니다. | 없는 `serv_id` |
| `409` | `RESULTS_NOT_READY` | 아직 대화가 진행 중입니다. 대화를 마친 뒤 결과를 볼 수 있습니다. | `phase`가 `ready`가 되기 전에 결과 요청 |
| `422` | `VALIDATION_ERROR` | (아래 참고) | 요청 body가 스키마에 안 맞음 |
| `503` | `CB_UNAVAILABLE` | 챗봇 검색엔진을 사용할 수 없습니다. 잠시 후 다시 시도해주세요. | 서버 기동 시 cb 초기화 실패 |
| `503` | `DATABASE_UNAVAILABLE` | 제도 정보를 조회할 수 없습니다. 서버 DB 연결을 확인해주세요. | DB 연결 실패 |

**모든 오류가 위 형식입니다.** 스키마 검증 실패(422)도 예외가 아닙니다.
FastAPI 기본 응답인 `{"detail": [...]}`가 나가는 경로는 없으므로, 프론트 오류 파서는 `{error, message}` 하나만 보면 됩니다.

`422`의 `message`는 어긋난 필드를 그대로 알려줍니다.

| 요청 | 응답 `message` |
| --- | --- |
| `{"thread_id": "cb-..."}` (message 누락) | `message: 값이 필요합니다` |
| `{"message": 123}` | `message: 문자열이어야 합니다` |
| 깨진 JSON | `요청 본문이 올바른 JSON이 아닙니다.` |

개발 중 계약을 맞추기 위한 문구라 사용자에게 그대로 보여주기엔 딱딱합니다.
`422`는 프론트 버그일 때만 발생하므로, 말풍선에는 고정 문구를 쓰고 `message`는 콘솔에만 남기는 편을 권합니다.

빈 문자열(`""`)은 스키마상 유효해서 서버가 막지 않고 정상 턴으로 처리합니다.
**프론트가 `trim()` 후 빈 입력을 걸러 주세요.**

---

## 3. 전체 호출 흐름

기존 챗봇과 가장 크게 달라진 부분입니다. **`/match` 호출 단계가 없습니다.**
대화가 충분해졌다고 판단되는 그 턴 안에서 서버가 검색까지 끝내고 `phase: "ready"`로 응답합니다.

```text
로그인 직후 / 새로고침
  → GET /api/v1/cb/threads/latest
      ├─ thread_id 있음 → 결과 화면으로 (6-2 참고)
      └─ null          → 아래 상담 흐름으로

화면 진입
  → POST /api/v1/cb/threads              (봇이 먼저 인사)
  → thread_id 저장, 인사말 말풍선 표시
  → POST /api/v1/cb/messages 반복
  → 응답 phase 확인
      ├─ gathering : 다음 사용자 입력 대기
      └─ ready     : 입력창을 잠그고 마무리 멘트 표시
                     → GET /api/v1/cb/threads/{thread_id}/results
                     → 결과 화면 (배너 / 맞춤 제도 / 혹시 관심 있으실 수도)

결과 카드 클릭
  → GET  /api/v1/cb/institutions/{serv_id}            (제도 원문 상세)
  → POST /api/v1/cb/institutions/{serv_id}/translate  (쉬운 말 설명, 사용자가 눌렀을 때만)

다시 시작
  → DELETE /api/v1/cb/threads/{thread_id} → POST /api/v1/cb/threads
```

### 3.1 대화가 `ready`로 넘어가는 조건

서버가 판단합니다. 프론트가 관여하지 않습니다.

1. 3종 필터(생애주기/가구상황/관심주제) 중 하나 이상이 채워졌고,
2. 초반 확인이 끝났고 — 도움의 대상 → 본인 나이 → (돌봄이면) 돌보는 분 연세 순으로 묻습니다,
3. 의료·돌봄 주제라면 상태·등급을 한 번 확인했고 (아래),
4. LLM이 "충분하다"고 판단했거나 사용자 발화가 6턴에 도달했을 때.

관심주제에 `신체건강`·`정신건강`·`보호·돌봄`이 있으면 검색 직전에 질문이 **한 번** 더 나갑니다.

```text
봇 > 찾기 전에 하나만 더 여쭤볼게요. 아버님은 장기요양등급을 받으셨거나
     장애등록이 되어 있으실까요? 아직이시거나 잘 모르시면 그렇게만 알려주셔도 돼요.
```

의료·돌봄 제도는 장기요양등급이나 장애등록을 신청 조건으로 거는 것이 많아서, 이 답이 없으면
해당되지 않는 제도가 결과의 상당 부분을 차지합니다. 진단명이나 소득 액수는 묻지 않습니다.

사용자가 "아니요"라고 답하면 해당 자격이 걸린 제도가 결과에서 빠지고, **"모르겠어요"는 "아니요"로 처리하지 않습니다.**
이 질문은 한 번만 나가며, 답하지 않고 다른 이야기를 해도 다음 턴에 검색으로 넘어갑니다.

검색 결과가 0건이면 `ready`로 넘어가지 않고 `gathering`을 유지한 채 되묻습니다.
즉 **`ready`를 받았다는 것은 보여줄 카드가 1장 이상 확정됐다는 뜻**입니다.

### 3.2 `phase` 값

| `phase` | 뜻 | 프론트 동작 |
| --- | --- | --- |
| `gathering` | 정보 수집 중 | 응답 표시 후 다음 입력 대기 |
| `ready` | 검색 완료, 결과 있음 | 입력 잠금 → 결과 API 호출 → 결과 화면 |

기존의 `ready_to_match`, `matching`, `done`에 해당하는 값은 없습니다.
검색이 응답 안에서 끝나므로 "매칭 중" 상태가 프론트에 노출되지 않고, 결과 API는 저장된 카드를 읽기만 해서 몇 번을 불러도 같은 응답입니다.

---

## 4. 대화 시작

화면을 열자마자 호출합니다. 사용자가 먼저 말하지 않아도 봇이 인사를 건넵니다.

### Request

```http
POST /api/v1/cb/threads
Authorization: Bearer {access_token}
```

Request Body는 **없습니다**.

### Response `201 Created`

```json
{
  "thread_id": "cb-9f2a41c0b7e3",
  "phase": "gathering",
  "message": "안녕하세요! 필요한 지원을 함께 찾아드릴게요.\n요즘 어떤 부분이 가장 부담되세요? 월세나 집 문제, 병원비, 일자리처럼 떠오르는 대로 편하게 말씀해 주세요."
}
```

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `thread_id` | String | 이후 모든 대화/결과 호출에 사용 |
| `phase` | String | 항상 `gathering` |
| `message` | String | 봇의 첫 인사. 고정 문구라 즉시 응답 |

### 화면 처리

1. 요청 중에는 입력창을 비활성화합니다.
2. `thread_id`를 컴포넌트 상태에 저장합니다.
3. `message`를 봇 말풍선으로 표시합니다.
4. 화면이 응답 전에 사라졌다면 `ignore` 플래그로 상태 업데이트를 막습니다.
5. 인사말에 사용자 이름을 붙이고 싶으면 **프론트에서** 붙입니다. 서버는 이름을 모릅니다.

### cURL

```bash
curl -X POST "${AI_API_BASE_URL}/api/v1/cb/threads" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

---

## 5. 메시지 전송

### Request

```http
POST /api/v1/cb/messages
Authorization: Bearer {access_token}
Content-Type: application/json
```

```json
{
  "thread_id": "cb-9f2a41c0b7e3",
  "message": "엄마 병원비 때문에 힘들어요. 저는 스물넷이에요."
}
```

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `thread_id` | String | N | 생략하면 서버가 새 대화를 시작하고 발급한 id를 응답에 실어줍니다. 4번을 먼저 부르는 것을 권장합니다 |
| `message` | String | Y | 사용자가 입력한 자연어 |

모르는 `thread_id`를 보내면 새로 만들지 않고 `404 THREAD_NOT_FOUND`입니다.

### Response `200 OK`

```json
{
  "thread_id": "cb-9f2a41c0b7e3",
  "phase": "gathering",
  "message": "많이 힘드셨겠어요. 어머니 병원비 말고도 생활비나 주거 쪽으로 부담되는 게 있으실까요?",
  "filters": {
    "life_cycle": ["청년"],
    "household": [],
    "theme": ["신체건강"]
  },
  "intake": {
    "target_for": "caree",
    "age": 24,
    "caree_age": null
  },
  "result_summary": null
}
```

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `thread_id` | String | 요청한 값 그대로(또는 새로 발급된 값) |
| `phase` | String | `gathering` \| `ready` |
| `message` | String | 봇 말풍선 내용 |
| `filters` | Object | 지금까지 대화로 **누적된** 3종 필터. 매 턴 덮어쓰지 않고 쌓입니다 |
| `filters.life_cycle` | String[] | 생애주기 태그 |
| `filters.household` | String[] | 가구상황 태그 |
| `filters.theme` | String[] | 관심주제 태그 |
| `intake.target_for` | String \| null | `self`(본인) \| `caree`(돌보는 분) \| `null` |
| `intake.age` | Integer \| null | 확인된 본인 나이. 아직 모르면 `null` |
| `intake.caree_age` | Integer \| null | 돌보는 분 연세. `target_for`가 `caree`일 때만 확인합니다 |
| `result_summary` | Object \| null | `phase`가 `gathering`이면 항상 `null` |

`phase`가 `ready`일 때의 응답:

```json
{
  "thread_id": "cb-9f2a41c0b7e3",
  "phase": "ready",
  "message": "이제 다 확인했어요! 은평구 기준으로 맞춤 제도 6건을 찾았어요.",
  "filters": {
    "life_cycle": ["청년"],
    "household": ["저소득"],
    "theme": ["신체건강", "주거"]
  },
  "intake": { "target_for": "caree", "age": 24, "caree_age": 82 },
  "result_summary": {
    "matched": 6,
    "maybe": 14,
    "region_label": "은평구"
  }
}
```

| `result_summary` 필드 | 타입 | 설명 |
| --- | --- | --- |
| `matched` | Integer | 맞춤 제도 건수 |
| `maybe` | Integer | 혹시 관심 있으실 수도 건수 |
| `region_label` | String | 화면에 쓸 지역 표기. 자치구를 모르면 `"전국·서울시"` |

`result_summary`는 전환 화면의 배지·문구용 **건수 정보일 뿐**이며 제도 카드는 들어 있지 않습니다.
대화 턴 응답에는 제도가 한 건도 실리지 않습니다.

### 프론트 입력 처리

- 전송 전에 `trim()`, 빈 문자열은 보내지 않습니다.
- 요청 중에는 추가 전송을 막습니다.
- 한글 IME 조합 중 Enter는 중복 전송하지 않습니다. `Enter` 전송, `Shift+Enter` 줄바꿈.
- API 호출 전에 사용자 말풍선을 먼저 그립니다.
- 응답의 `message`를 봇 말풍선으로 추가합니다.
- `phase === "ready"`면 입력창을 잠그고 결과 API로 넘어갑니다.

### 주요 오류

| Status | `error` | 조건 |
| --- | --- | --- |
| `401` | `UNAUTHORIZED` | 토큰 없음/만료 |
| `403` | `FORBIDDEN` | 남의 `thread_id` |
| `404` | `THREAD_NOT_FOUND` | 없는/삭제된 `thread_id` |
| `503` | `CB_UNAVAILABLE` | cb 초기화 실패 |

### cURL

```bash
curl -X POST "${AI_API_BASE_URL}/api/v1/cb/messages" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"thread_id":"cb-9f2a41c0b7e3","message":"엄마 병원비 때문에 힘들어요."}'
```

---

## 6. 매칭 결과 조회 — 매칭된 제도를 받는 곳

`phase`가 `ready`가 된 직후 호출합니다. **기존 명세의 `POST /match` + `GET /api/web/policies/matched`를 한 번에 대체합니다.**

검색을 다시 돌리지 않습니다. 대화가 끝나는 턴에 만들어 저장해 둔 카드를 그대로 읽습니다.
그래서 즉시 응답하고, 여러 번 호출해도 같은 결과입니다(멱등).

### Request

```http
GET /api/v1/cb/threads/{thread_id}/results
Authorization: Bearer {access_token}
```

### Response `200 OK`

```json
{
  "thread_id": "cb-9f2a41c0b7e3",
  "generated_at": "2026-07-27T14:32:05+09:00",
  "region": {
    "sgg": "은평구",
    "source": "user_profile"
  },
  "filters": {
    "life_cycle": ["청년"],
    "household": ["저소득"],
    "theme": ["신체건강", "주거"]
  },
  "relaxed_axes": [],
  "banner": {
    "title": "이런 지원도 받을 수 있어요",
    "count": 0,
    "institutions": [],
    "source": "curated"
  },
  "matched": {
    "title": "맞춤 제도",
    "count": 6,
    "institutions": [
      {
        "serv_id": "WLF00000123",
        "name": "가족돌봄청년 자기돌봄비 지원",
        "agency": "서울특별시 은평구",
        "summary": "가족을 돌보는 청년에게 연 200만 원의 자기돌봄비를 지원합니다.",
        "region": {
          "scope": "district",
          "label": "은평구",
          "ctpv_nm": "서울특별시",
          "sgg_nm": "은평구"
        },
        "tags": {
          "life_cycle": ["청년"],
          "household": ["저소득"],
          "theme": ["보호·돌봄", "생활지원"]
        },
        "support": {
          "cycle": "년",
          "provision_type": "현금"
        },
        "apply": {
          "method_name": "온라인 신청",
          "contact": "02-000-0000"
        },
        "link": "https://www.bokjiro.go.kr/...",
        "match": {
          "rank": 3,
          "score": 0.1841,
          "distance": 0.4137,
          "matched_by": "both"
        }
      }
    ]
  },
  "maybe": {
    "title": "혹시 관심 있으실 수도",
    "count": 14,
    "institutions": []
  }
}
```

### 최상위 필드

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `thread_id` | String | 요청한 대화 |
| `generated_at` | String(ISO8601, KST) | 검색이 끝난 시각. 결과 화면 "n분 전 기준" 표기에 사용 |
| `region.sgg` | String \| null | 검색에 쓴 자치구 |
| `region.source` | String | `user_profile`(carers.region에서 복사) \| `unset`(자치구 모름) |
| `filters` | Object | 대화로 누적된 3종 필터. `relaxed_axes`에 적힌 축은 이번 검색에서 제외됐지만 값 자체는 남아 있습니다 |
| `relaxed_axes` | String[] | 0건이라 **이번 검색에서 제외한 축**. 비어 있으면 조건을 안 풀었다는 뜻 |
| `banner` | Section | 큐레이션 배너 |
| `matched` | Section | 맞춤 제도 |
| `maybe` | Section | 혹시 관심 있으실 수도 |

`relaxed_axes`에 값이 있으면 "조건을 조금 넓혀서 찾았어요" 같은 안내를 붙일 수 있습니다.
값은 `life_cycle` / `household` 중 하나 이상입니다. `theme`(관심주제)는 절대 풀지 않습니다.

### Section 구조

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `title` | String | 화면에 그대로 쓸 섹션 제목 |
| `count` | Integer | `institutions.length`와 같습니다 |
| `institutions` | Card[] | 제도 카드 목록 |
| `source` | String | `banner`에만 있습니다. 항상 `"curated"` |

**배너는 현재 항상 비어 있습니다** (`count: 0`). 검색이 아니라 명시적 큐레이션 목록에서 오는 자리인데
아직 목록을 채우지 않았습니다. 프론트는 이 섹션을 숨기거나 기존 더미를 유지하면 됩니다.

### 맞춤 / 혹시 관심의 기준

| 구분 | 기준 | 최대 건수 |
| --- | --- | --- |
| `matched` | 전체 검색 순위 상위 8위 이내 + 1위와의 코사인 거리 차가 0.10 이내 | 8 |
| `maybe` | 나머지 | 8 |

- 두 섹션을 합쳐 **최대 16건**입니다. 실제로는 10~15건이 나옵니다.
- `matched`는 **가까운 순(distance)으로 정렬**되어 있습니다. 배열 순서 그대로 그리면 됩니다.
- `maybe`는 검색 순위(RRF) 순서입니다.

**자격이 어긋나는 제도는 목록에 아예 없습니다.** 지원대상 원문에 자격이 박혀 있는데
사용자가 대화에서 해당한다고 말한 적이 없으면 서버가 걷어냅니다.

| 걷어내는 축 | 예 |
| --- | --- |
| 신원·사건 | 가정폭력·성폭력 피해, 범죄피해, 북한이탈주민, 외국인근로자, 노숙인, 출소자, 산재근로자, 보훈대상, 환경오염피해, 농어업인 |
| 질환 | 암, 희귀·난치질환, 치매, 중증정신질환, 결핵·한센 등 감염병 |

사용자가 "저희 아버지가 암이세요"처럼 밝히면 해당 축은 걷어내지 않습니다.

장애등록·장기요양등급·기초생활수급·차상위는 **반대로 동작합니다.** 말하지 않았다고 빼지 않고,
사용자가 [3.1의 확인 질문](#31-대화가-ready로-넘어가는-조건)에 **"아니요"라고 답했을 때만** 걷어냅니다.
거동이 불편한 어르신은 실제로 등급이 있을 가능성이 높은데 사용자가 먼저 말하지는 않기 때문입니다.

### 카드(Card) 필드

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `serv_id` | String | 제도 식별자. 상세/쉬운말 API의 path에 사용 |
| `name` | String | 제도명 |
| `agency` | String \| null | 소관 기관 |
| `summary` | String \| null | 한 줄 요약. 없으면 `null` |
| `region.scope` | String | `national`(전국) \| `metro`(서울시) \| `district`(자치구) |
| `region.label` | String | 화면 표기용. `전국` / `서울시` / `은평구` |
| `region.ctpv_nm` | String \| null | 시도명 원문 |
| `region.sgg_nm` | String \| null | 시군구명 원문 |
| `tags.life_cycle` | String[] | 생애주기 태그 |
| `tags.household` | String[] | 가구상황 태그 |
| `tags.theme` | String[] | 관심주제 태그 |
| `support.cycle` | String \| null | 지원주기 (`월`, `수시`, `1회성` …) |
| `support.provision_type` | String \| null | 제공유형 (`현금`, `현물`, `서비스` …) |
| `apply.method_name` | String \| null | 신청방법 요약 |
| `apply.contact` | String \| null | 문의처 |
| `link` | String \| null | 복지로 상세 링크 |
| `match` | Object \| null | 매칭 근거. 배너 카드는 `null` |

카드에는 **긴 본문이 들어 있지 않습니다.** 20장에 본문을 실으면 응답이 수만 자가 되기 때문입니다.
본문은 7번 상세 API에서만 내려갑니다.

### `match` 객체 — 화면에 표시하지 마세요

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `rank` | Integer | 구간을 나누기 전 전체 검색 순위. **배열 순서와 다릅니다** |
| `score` | Float | RRF 점수. 질의마다 스케일이 달라 "몇 % 일치" 같은 표시에 쓸 수 없습니다 |
| `distance` | Float \| null | 코사인 거리. 작을수록 가깝습니다. 키워드로만 걸린 건은 `null` |
| `matched_by` | String | `vector` \| `keyword` \| `both` |

디버깅과 로그 대조용입니다. 사용자에게 노출할 값이 아닙니다.
화면 번호가 필요하면 `rank`가 아니라 **배열 순서**를 쓰세요.

### 주요 오류

| Status | `error` | 조건 |
| --- | --- | --- |
| `409` | `RESULTS_NOT_READY` | `phase`가 아직 `gathering` |
| `403` | `FORBIDDEN` | 남의 `thread_id` |
| `404` | `THREAD_NOT_FOUND` | 없는/삭제된 `thread_id` |

`409`는 정상적인 흐름을 지키면 발생하지 않습니다. `phase === "ready"`를 받은 뒤에만 호출하세요.

### cURL

```bash
curl "${AI_API_BASE_URL}/api/v1/cb/threads/${THREAD_ID}/results" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

---

## 6-2. 마지막으로 마친 대화 조회 — 화면 복원

새로고침이나 재로그인 뒤에 **상담 화면으로 보낼지, 결과 화면으로 보낼지** 정할 때 씁니다.

cb는 `carers.diagnosis_completed`를 갱신하지 않습니다. 그 플래그는 기존 1·2단계 진단의 상태값이고,
cb는 격리 원칙상 `carers`에 쓰지 않습니다. 대신 **cb가 자기 완료 여부를 직접 답합니다.**

### Request

```http
GET /api/v1/cb/threads/latest
Authorization: Bearer {access_token}
```

### Response `200 OK` — 마친 대화가 있을 때

```json
{
  "thread_id": "cb-9f2a41c0b7e3",
  "phase": "ready",
  "generated_at": "2026-07-27T14:32:05+09:00",
  "result_summary": { "matched": 7, "maybe": 8, "region_label": "송파구" }
}
```

### Response `200 OK` — 아직 없을 때

```json
{ "thread_id": null, "phase": null, "generated_at": null, "result_summary": null }
```

**아직 상담을 안 한 것은 오류가 아니라 정상 상태라 `404`가 아닙니다.** `thread_id`가 `null`인지로 판단하세요.
대화를 `DELETE`로 지웠으면 기록이 남아 있어도 `null`로 응답합니다.

### 프론트 분기

```text
로그인 후
  → GET /api/v1/cb/threads/latest
      ├─ thread_id 있음 → GET /threads/{thread_id}/results → 결과 화면
      └─ thread_id null → POST /threads → 상담 화면
```

기존 `diagnosis_completed` 분기보다 **앞에** 두면 됩니다. localStorage에 `thread_id`를 같이 저장해도 되지만,
기기나 브라우저를 바꾸면 날아가므로 이 API가 기준입니다.

카드는 들어 있지 않습니다. 결과 화면을 그리려면 `thread_id`로 결과 API를 한 번 더 부르세요.

---

## 7. 제도 상세

카드를 눌러 상세 화면으로 들어갈 때 호출합니다.

### Request

```http
GET /api/v1/cb/institutions/{serv_id}
Authorization: Bearer {access_token}
```

### Response `200 OK`

> **상세 응답은 카드(6번)와 필드 구성이 다릅니다.** 카드는 목록용 요약이라
> `support`/`apply`를 중첩 객체로 묶지만, 상세 화면은 같은 값을 뱃지와 본문
> 2단으로 쪼개 씁니다. 그래서 상세는 카드를 상속하지 않고 평평한 필드로 나갑니다.

#### 계약의 핵심: 값이 없으면 키도 없습니다

값이 없는 필드는 `null`이 아니라 **키 자체가 빠진 채로** 내려갑니다.
프론트는 값을 검사하지 말고 **키가 있는지만 보고** 행·섹션을 그리면 됩니다.

```js
if ("required_forms" in d) { /* 필요 서식 섹션을 그린다 */ }
```

`null`과 `""`와 `[]`를 모두 "없음"으로 취급하게 만들면 언젠가 한쪽을 빠뜨려
빈 행이 남습니다. 그래서 "없음"의 표현을 한 가지로 고정했습니다.

**신청 기간·결과 발표일에 해당하는 필드는 스키마에 없습니다.** 원본(복지로)에
대응하는 데이터가 없어서, `null`을 내려 '언젠가 채워질 자리'처럼 보이게 하지
않고 아예 뺐습니다. 대신 `detail_link`(공식 사이트)를 화면 최상단에 둡니다.

#### 예시 (실제 데이터: `WLF00006351`)

```json
{
  "serv_id": "WLF00006351",
  "name": "서울특별시 강서구 전세피해임차인 지원사업",
  "agency": "서울특별시 강서구 도시관리국 부동산정보과",
  "summary": "전세피해 주택임차인의 피해 회복 지원으로 주거안정 및 주거복지 향상 기여",
  "region": {
    "scope": "district", "label": "강서구",
    "ctpv_nm": "서울특별시", "sgg_nm": "강서구"
  },
  "tags": {
    "life_cycle": ["청년", "중장년", "노년"],
    "household": ["보훈대상자"],
    "theme": ["서민금융"]
  },
  "detail_link": "https://www.bokjiro.go.kr/ssis-tbu/twataa/wlfareInfo/moveTWAT52011M.do?wlfareInfoId=WLF00006351&wlfareInfoReldBztpCd=02",

  "support_cycle": "1회성",
  "support_cycle_label": "지급 주기",
  "provision_type_badge": "현금지급",
  "apply_method_badge": "방문, 인터넷",
  "apply_method_detail": "(오프라인)서울 강서구청 1층 부동산정보과 방문 신청(온라인) 정부24에서 강서구 전세피해지원금 검색 후 신청",

  "contact_list": [
    { "name": "서울특별시 강서구청 부동산정보과", "phone": "02-2600-6907" },
    { "name": "서울특별시 강서구청 부동산정보과", "phone": "02-2600-6891" }
  ],

  "required_forms": [
    { "name": "서울특별시 강서구 전세피해 및 전세사기피해자 지원 조례.hwp",
      "url": "https://www.bokjiro.go.kr/ssis-tbu/CmmFileUtil/siteQnaInfoDownload.do?atcflId=20260610UUWBM1116360196245114&atcflSn=1" },
    { "name": "전세사기 피해지원 신청서.hwp",
      "url": "https://www.bokjiro.go.kr/ssis-tbu/CmmFileUtil/siteQnaInfoDownload.do?atcflId=20260610UUWBM1135420196252686&atcflSn=1" },
    { "name": "개인정보 수집·이용 등 동의서.hwp",
      "url": "https://www.bokjiro.go.kr/ssis-tbu/CmmFileUtil/siteQnaInfoDownload.do?atcflId=20260610UUWBM1136470196253806&atcflSn=1" },
    { "name": "전세사기 피해지원 신청 위임장.hwp",
      "url": "https://www.bokjiro.go.kr/ssis-tbu/CmmFileUtil/siteQnaInfoDownload.do?atcflId=20260610UUWBM1138070196253961&atcflSn=1" }
  ],

  "target_detail": "아래 요건 모두 충족하는 경우 지원 가능 (공통) 1. 서울시 강서구의 주택을 임차한 사람 ...",
  "select_criteria": "(선정기준 상세내용)  아래 요건 모두 충족하는 경우 지원 가능 ...",
  "service_content": "전세피해자 지원금(택1, 중복불가, 소급적용, 소득기준 적용 없음) ① 전세보증금 반환보증 보증료 지원: 100만원 이내(실비)/1회 ...",

  "extra_info": {
    "baslaw": [
      { "code": "030", "name": "서울특별시 강서구 전세피해 및 전세사기피해자 지원 조례 제7조" }
    ]
  }
}
```

이 응답에는 `contact`, `criteria_year`가 없습니다. 문의처가 2곳이라
`contact` 대신 `contact_list`가 나갔고, 기준연도는 이 제도에 없습니다.

#### 필드

**정체 / 머리말**

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `serv_id` | String | 항상 있습니다 |
| `name` | String | 항상 있습니다 |
| `agency` | String? | 담당 부처·부서 |
| `summary` | String? | 한 줄 요약 |
| `region` | Object | 카드와 같은 형태. `ctpv_nm`/`sgg_nm`은 없으면 키가 빠집니다 |
| `tags` | Object? | 3축이 **모두** 비면 통째로 빠집니다. 나갈 때는 세 축이 늘 함께 나갑니다(개별 축은 `[]`일 수 있음) |
| `detail_link` | String? | 복지로 원문 링크. **화면 최상단에 유지합니다.** 카드의 `link`와 같은 값이지만 이름이 다릅니다 |

**요약 행 / 뱃지**

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `support_cycle` | String? | **지급 주기**입니다. 신청 기간이 아닙니다 |
| `support_cycle_label` | String? | 항상 `"지급 주기"`. `support_cycle`이 있을 때만 함께 나갑니다 |
| `provision_type_badge` | String? | 지원 내용 앞에 붙일 뱃지 |
| `apply_method_badge` | String? | 짧은 신청 수단 라벨(예: `"방문"`). **없으면 뱃지를 그리지 마세요** |
| `apply_method_detail` | String? | 신청 절차 전문. 본문으로 그립니다 |

- `support_cycle` 실측 값(856건): `월`(249) `1회성`(215) `수시`(194) `년`(126)
  `반기`(35) `분기`(26) `주`(9) `기타`(1) `부정기`(1). **9종이며 열거형으로
  하드코딩하지 마세요.**
- `apply_method_badge`는 **856건 중 551건(64%)이 없습니다.** 지자체 제도에만
  주로 붙습니다. 뱃지가 없는 화면이 기본값이라고 보고 레이아웃을 잡으세요.
- `apply_method_badge`와 `provision_type_badge`는 **쉼표로 이어 붙은 복수 값이
  올 수 있습니다** (`"방문, 인터넷"`, `"현금지급, 현물지급"`). 원본이 그렇게
  들어옵니다. `provision_type_badge`는 실측 50종이고 최장
  `"프로그램/서비스(서비스), 자원봉사, 현물지급, 현금대여(융자), 현금지급"`입니다.
  뱃지 칩은 넘칠 때 줄이거나 잘라 주세요.

**문의처 — 둘 중 하나만 나갑니다**

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `contact` | String? | 대표 연락처 하나 |
| `contact_list` | Array? | `{name?, phone}` 목록. 연락처가 **2곳 이상**일 때 `contact` 대신 나갑니다 |

- `contact`와 `contact_list`는 **동시에 나오지 않습니다.** `contact_list`가
  있으면 목록으로, 없으면 `contact` 한 줄로 그리면 됩니다.
- 856건 중 `contact_list`가 나가는 건은 153건(18%)입니다. 나머지는 `contact`입니다.
- **목록이 길 수 있습니다.** 최대 26개(자치구별 창구를 모두 싣는 광역 제도)이고
  16개 이상인 제도가 11건입니다. 접었다 펴는 UI를 권합니다.
- `name`이 같고 번호만 다른 항목이 있습니다(같은 부서의 회선 2개). 이름으로
  묶지 말고 `phone` 기준으로 한 줄씩 그리세요. `name`이 없는 항목도 있습니다.

**필요 서식**

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `required_forms` | Array? | `{name, url?}` 목록. 비어 있으면 **키가 없습니다** — 섹션을 숨기세요 |

- 856건 중 651건(76%)에 서식이 있습니다.
- `url`은 복지로 파일 다운로드 링크입니다. 드물게 `url`이 없는 항목이 있고,
  그때는 이름만 표시하고 링크를 걸지 않으면 됩니다.
- 파일명은 원본 그대로입니다. 관리자가 잘못 올린 이름(`"오류.hwpx"` 등)이
  섞여 있으니 화면에서 자르되 가공하지는 않습니다.

**자격 / 내용 상세 (아코디언 3단)**

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `target_detail` | String? | "지원 대상" 섹션 |
| `select_criteria` | String? | "선정 기준" 섹션 |
| `service_content` | String? | "지원 내용" 섹션 |

**이 세 필드는 가공하지 않은 원문입니다.** 검색·랭킹이 읽는 텍스트와 한 글자도
다르지 않습니다. 사용자가 "왜 이 제도가 나왔는지"를 확인하는 자리이므로
서버가 요약하거나 다듬지 않습니다. 문단 구분이 거친 것도 원본 그대로입니다 —
읽기 힘든 경우는 쉬운 말 설명(8번)이 보완합니다.

**나머지**

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `criteria_year` | Integer? | 기준연도 |
| `extra_info` | Object? | 위에서 못 뽑은 부가 정보(`baslaw` 근거법령, `inqpl_hmpg` 관련 사이트). 비어 있으면 키가 빠집니다 |

- `extra_info`에서 `inqpl_ctadr`와 `basfrm`은 **빠져 있습니다.** 각각
  `contact_list`와 `required_forms`로 이미 나갔기 때문입니다. 같은 값을 두
  군데로 내려서 프론트가 어느 쪽을 그릴지 정하게 만들지 않습니다.
- 운영이 종료된 제도도 404가 아니라 정상 응답합니다. 저장해 둔 사용자가 열었을 때
  빈 화면을 보는 것보다 낫기 때문입니다.

#### 카드(6번)에서 이름이 바뀐 필드

상세 응답에만 해당합니다. 결과 목록의 카드 필드는 그대로입니다.

| 카드 | 상세 |
| --- | --- |
| `link` | `detail_link` |
| `support.cycle` | `support_cycle` |
| `support.provision_type` | `provision_type_badge` |
| `apply.method_name` | `apply_method_badge` |
| `apply.contact` | `contact` 또는 `contact_list` |
| `apply_method` | `apply_method_detail` |
| `match` | (없음 — 상세는 검색 결과가 아니라 항상 `null`이었습니다) |

### 주요 오류

| Status | `error` | 조건 |
| --- | --- | --- |
| `404` | `INSTITUTION_NOT_FOUND` | 없는 `serv_id` |
| `503` | `DATABASE_UNAVAILABLE` | DB 연결 실패 |

---

## 8. 말풍선 A — 개인화된 쉬운 말 설명

기존 `POST /api/v1/policies/{policy_id}/translate`를 대체합니다.

> **캐시하지 마세요.** 이 응답은 제도 원문뿐 아니라 **현재 대화의 State**를 함께
> 읽습니다. 사용자마다 다르고, 같은 사용자도 대화가 진행되면 달라집니다.
> 말풍선 B(`apply_guide_easy`)나 필요서류는 제도 원문만 보고 미리 만들어 둔
> 값이라 상세 API에 실려 오지만, 이건 매번 새로 만듭니다.
>
> **이 호출만 따로 비동기로 띄우세요.** 왼쪽 정보 영역이나 말풍선 B의 렌더링을
> 여기에 묶으면, LLM 응답(2~4초)만큼 화면 전체가 늦어집니다.

### Request

```http
POST /api/v1/cb/institutions/{serv_id}/translate
Authorization: Bearer {access_token}
Content-Type: application/json
```

```json
{ "thread_id": "cb-a1b2c3d4e5f6" }
```

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `thread_id` | String | 아니오 | 개인화에 쓸 대화. 생략하면 3번 섹션 없이 1·2번만 내려갑니다 |

Body 자체를 생략해도 됩니다(기존 클라이언트 호환). 남의 `thread_id`를 넣으면 `403`입니다.

### Response `200 OK`

```json
{
  "serv_id": "WLF00003181",
  "name": "장애인의료비지원",
  "sections": {
    "summary_easy": "장애인의료비지원은 등록된 장애가 있는 분이 병원에 가거나 약을 살 때 본인이 직접 내야 하는 돈을 대신 내주는 제도예요. 다만 건강보험이 적용되는 진료에 한해서만 지원돼요.",
    "target_general": "이 제도는 원래 의료급여 2종 수급자이거나, 기초생활수급자 중 근로 능력이 있는 세대에 속한 등록장애인, 또는 건강보험 차상위 본인부담 경감대상자인 등록장애인을 위해 만들어졌어요. 만성질환이 있거나 18세 미만인 장애아동도 대상이 될 수 있어요.",
    "personal_fit": "돌보시는 분이 장애 등록이 되어 계신 걸로 확인됐으니, 이 제도의 기본 조건 중 하나에는 해당될 수 있어요. 다만 의료급여 2종이나 차상위 본인부담 경감대상 같은 세부 조건까지 맞는지는 아직 확인되지 않아서, 조금 더 알아보시는 게 좋아요."
  },
  "personalized": true,
  "grounded_on": [
    "지금 돌보는 분을 위한 지원을 찾고 있음",
    "본인 나이: 만 24세",
    "돌보는 분 연세: 만 80세",
    "돌보는 분 장애등록에 해당한다고 확인됨"
  ],
  "easy_text": "…세 섹션을 이어붙인 텍스트…"
}
```

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `sections.summary_easy` | String \| null | **① 쉬운 설명** — 이 제도가 뭘 해주는지 |
| `sections.target_general` | String \| null | **② 원래 어떤 계층을 위한 제도인지** — 개인화 아님. 제도 자체 설명 |
| `sections.personal_fit` | String \| null | **③ 왜 지금 특히 해당될 수 있는지** — 근거가 없으면 `null` |
| `personalized` | Boolean | ③이 실제로 채워졌는가 |
| `grounded_on` | String[] | ③이 근거로 삼은 '이번 대화에서 확인된 사실' |
| `easy_text` | String | 세 섹션을 이어붙인 텍스트. **하위호환용**이며 새 화면은 `sections`를 쓰세요 |

**세 섹션은 순서가 고정입니다.** ①→②→③ 순으로 그리세요.

### `personal_fit`이 `null`인 경우

세 가지입니다. 셋 다 정상이고, 프론트는 **③ 영역을 통째로 숨기면** 됩니다.

1. `thread_id`를 안 보냈다
2. 보냈지만 그 대화에서 확인된 사실이 아직 하나도 없다 (`grounded_on`이 빈 배열)
3. 확인된 사실은 있는데 이 제도의 조건과 이어지지 않는다 — 예를 들어 80세 어르신을 돌보는 상황에서 아이돌봄서비스를 열었을 때. 억지로 잇지 않고 비웁니다

### 이 말풍선이 하지 않는 것

- **신청 방법·기관·연락처·제출 서류를 말하지 않습니다.** 그건 말풍선 B(`apply_guide_easy`)와 `required_documents_ai`의 역할이라, 겹치면 같은 말이 두 번 나옵니다.
- **자격을 단정하지 않습니다.** "받으실 수 있어요"가 아니라 "해당될 수 있어요"로 나갑니다. 실제 심사 기준은 서버가 알 수 없습니다.
- **확인되지 않은 정보를 언급하지 않습니다.** 대화에서 소득을 확인하지 못했으면 소득 이야기는 아예 나오지 않습니다("소득은 모르지만…" 같은 말도 하지 않습니다). `grounded_on`에 있는 항목만 ③의 근거입니다.

### 화면 처리

1. **사용자가 눌렀을 때만** 호출합니다. 목록에서 미리 불러두지 마세요.
2. 호출 시작과 함께 `제도를 쉬운 말로 풀어보고 있어요.`를 먼저 표시합니다.
3. 성공하면 `sections`로 교체합니다. `personal_fit`이 `null`이면 ③은 그리지 않습니다.
4. 상세 화면이 바뀐 뒤 도착한 응답은 `ignore` 플래그로 무시합니다.

**생성에 실패해도 500이 아닙니다.** 원문은 이미 상세 API로 볼 수 있기 때문에,
세 섹션이 모두 `null`이고 `easy_text`에 아래 문구가 담긴 `200`이 내려갑니다.

```json
{
  "serv_id": "...", "name": "...",
  "sections": { "summary_easy": null, "target_general": null, "personal_fit": null },
  "personalized": false, "grounded_on": [],
  "easy_text": "쉬운 말 설명을 준비하지 못했어요. 잠시 후 다시 시도해주세요."
}
```

### 주요 오류

| Status | `error` | 조건 |
| --- | --- | --- |
| `404` | `INSTITUTION_NOT_FOUND` | 없는 `serv_id` |
| `404` | `THREAD_NOT_FOUND` | 없는 `thread_id` |
| `403` | `FORBIDDEN` | 남의 `thread_id` |
| `401` | `UNAUTHORIZED` | 토큰 없음/만료 |

---

## 9. 다시 시작

### Request

```http
DELETE /api/v1/cb/threads/{thread_id}
Authorization: Bearer {access_token}
```

### Response `200 OK`

```json
{ "message": "대화가 초기화되었습니다." }
```

체크포인트를 지웁니다. 같은 `thread_id`로 이후 호출하면 `404 THREAD_NOT_FOUND`입니다.
새 대화는 `POST /api/v1/cb/threads`로 다시 시작합니다.

삭제 전에 소유권을 확인하므로 남의 대화는 지울 수 없습니다(403).

---

## 10. 필드 사전

### 3종 필터 어휘

복지로 체크박스 값 그대로입니다. 서버가 이 목록 밖의 값을 내려보내지 않습니다.

| 축 | 값 |
| --- | --- |
| `life_cycle` | 임신·출산, 영유아, 아동, 청소년, 청년, 중장년, 노년 |
| `household` | 저소득, 장애인, 한부모·조손, 다자녀, 다문화·탈북민, 보훈대상자 |
| `theme` | 신체건강, 정신건강, 생활지원, 주거, 일자리, 문화·여가, 안전·위기, 임신·출산, 보육, 교육, 입양·위탁, 보호·돌봄, 서민금융, 법률, 에너지 |

가운뎃점은 `·`(U+00B7)입니다. 필터 칩을 그릴 때 문자열 비교에 주의하세요.

### 열거형 값

| 필드 | 가능한 값 |
| --- | --- |
| `phase` | `gathering`, `ready` |
| `intake.target_for` | `self`, `caree`, `null` |
| 상태·등급 (내부 판정용, 응답에는 없음) | `장기요양등급`, `장애등록`, `기초생활수급`, `차상위` |
| `region.scope` | `national`, `metro`, `district` |
| `region.source` (결과) | `user_profile`, `unset` |
| `match.matched_by` | `vector`, `keyword`, `both` |
| `banner.source` | `curated` |

### `serv_id`는 안정적인가

**네. 찜 기록의 키로 써도 됩니다.**

- `serv_id`는 공공데이터포털(복지로)이 발급한 서비스ID를 **그대로** 쓰는 값입니다. AI 서버가 새로 만들거나 다시 매기지 않습니다.
- 동기화는 `ON CONFLICT (serv_id) DO UPDATE`입니다. 같은 제도는 내용만 갱신되고 id는 유지됩니다.
- 원본에서 사라진 제도도 **행을 지우지 않고** `is_active = false`로 내립니다.
  상세 API(`GET /institutions/{serv_id}`)는 내려간 제도도 `200`으로 응답하므로, 찜해 둔 제도가 404가 되는 일은 없습니다.
  다만 검색 결과에는 더 이상 나오지 않습니다.
- 남는 위험은 하나뿐입니다: 공공데이터포털이 같은 제도에 **다른 servId를 새로 발급**하는 경우.
  그때는 우리 쪽에서 신규 제도로 들어오고 옛 id는 비활성됩니다. 현재까지 관측된 적은 없습니다.

찜 목록 화면에서 `is_active` 상태를 알려면 상세 API 응답에 필드를 노출해야 합니다.
지금은 내려보내지 않으니 필요하면 알려주세요.

### 지역 범위

| `scope` | 뜻 | `label` 예시 |
| --- | --- | --- |
| `national` | 중앙부처 제도. 전국 어디서나 | `전국` |
| `metro` | 서울시 제도 | `서울시` |
| `district` | 자치구 제도 | `은평구` |

결과에는 이 셋이 **항상 함께** 들어옵니다. 자치구 제도만 따로 거르지 마세요.
사용자의 자치구를 모르면(`region.source === "unset"`) `district` 제도가 아예 검색되지 않고 `region_label`은 `"전국·서울시"`가 됩니다.

---

## 11. 구현 체크리스트

### 프론트가 기존 챗봇 화면에서 옮겨올 때

- base path를 `/api/v1/chat` → `/api/v1/cb`로 바꿉니다.
- `session_id` → `thread_id`로 이름을 바꿉니다.
- 세션 생성 요청 body에서 `carer_id`를 **뺍니다**. 서버가 토큰에서 읽습니다.
- 메시지 전송을 path 방식(`/sessions/{id}/messages`)에서 body 방식(`/messages` + `thread_id`)으로 바꿉니다.
- `phase` 비교값을 `info_gathering` → `gathering`, `ready_to_match` → `ready`로 바꿉니다.
- **`/match` 호출을 삭제합니다.** `ready`를 받으면 곧바로 결과 API를 부릅니다.
- 결과 카드 조회를 `GET /api/web/policies/matched`(Spring) → `GET /api/v1/cb/threads/{id}/results`(AI)로 바꿉니다.
- 카드 렌더링을 새 스키마(`serv_id`, `region`, `tags`, `support`, `apply`)에 맞춥니다.
- 제도 상세를 Spring이 아니라 `GET /api/v1/cb/institutions/{serv_id}`에서 받습니다.
- 번역기 응답 필드를 `explanation` → `easy_text`로 바꿉니다.
- 결과 화면을 지역별이 아니라 **배너 / 맞춤 / 혹시 관심** 3단으로 그립니다. 배너는 당분간 빈 배열입니다.
- `match` 객체를 화면에 노출하지 않습니다.
- 로그인·새로고침 분기 맨 앞에 `GET /threads/latest`를 둡니다. `diagnosis_completed`로 cb 완료를 판단하지 않습니다.
- 찜은 Spring에 그대로 두되 키를 `serv_id`(String)로 잡습니다. cb에는 저장 API가 없습니다.

### 오류 처리

- `401` → 토큰을 지우고 로그인 화면으로 보냅니다(기존과 동일).
- `404 THREAD_NOT_FOUND` → "대화가 만료되었어요. 새로 시작할게요" 안내 후 `POST /threads`로 재시작합니다.
- `409 RESULTS_NOT_READY` → 결과 화면 진입 조건 버그입니다. `phase === "ready"`를 먼저 확인하세요.
- `422 VALIDATION_ERROR` → 요청 body가 계약과 다릅니다. 프론트 버그이므로 `message`를 콘솔로 확인하세요.
- `503 CB_UNAVAILABLE` → 서버 점검 안내. 재시도해도 같은 응답이면 백엔드에 알려주세요.
- 네트워크 오류 → 기존과 같이 말풍선으로 표시합니다.

---

## 12. 관련 코드 위치

| 역할 | 파일 |
| --- | --- |
| 엔드포인트 정의 | `app/routers/cb.py` |
| 요청/응답 스키마 | `app/cb/schemas.py` |
| 카드 변환, 맞춤/혹시관심 구간 | `app/cb/cards.py` |
| 대화 그래프(인사·인테이크·대화·검색·마무리) | `app/cb/graph.py`, `app/cb/nodes.py` |
| 하이브리드 검색 | `app/cb/search.py` |
| 쉬운 말 설명 | `app/cb/explain.py` |
| 대화 소유권/삭제 | `app/cb/threads.py` |
| 오류 코드 | `app/errors.py` |
| 터미널에서 API 그대로 써 보기 | `scripts/cb_repl.py` |
| 기존 챗봇 API 가이드(대체 대상) | `api_chat.md` |
