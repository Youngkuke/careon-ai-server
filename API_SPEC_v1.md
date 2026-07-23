# CareOn AI 서버 API 명세 v1

대상: 2단계 매칭 챗봇(phase1~6) + 제도번역기
서버: 별도 AI 서버(FastAPI 가정), 백엔드와는 `carer_id`로만 연결

---

## 0. 설계 원칙

- 프론트는 대화 한 턴마다 이 API를 호출하고, `reply`(챗봇 말)와
  `current_phase`(진행 상태)만 그대로 렌더링하면 된다. phase 전환
  로직/추출 로직은 서버 안에 숨긴다.
- 매칭 결과(적합/확인_불가/부적합)는 프론트가 그대로 리스트 UI로
  뿌릴 수 있는 구조로 내려준다. `institution_id`는 `policies.external_ref`
  또는 내부 `policy_id` 중 하나로 통일 — **여기서는 policy_id(INT)로 통일**.
- 제도 상세 화면(카드 클릭 시)은 매칭 API가 준 policy_id로 별도
  조회하는 구조 (매칭 응답 자체에 전체 필드를 다 안 실어서 payload를 가볍게 유지).

---

## 1. 세션 시작

### `POST /api/v1/chat/sessions`

1단계(온보딩) 완료 후, 2단계 챗봇을 시작할 때 호출.

**Request**
```json
{
  "carer_id": 123,
  "age": 22,
  "region_sigungu": "관악구",
  "selected_types": ["돌봄가사", "생계주거"],
  "case_number": 1
}
```

**Response**
```json
{
  "session_id": "sess_abcd1234",
  "current_phase": 1,
  "reply": "안녕하세요 OO님, 편하게 몇 가지 여쭤볼게요. 지금 같이 사는 가족이 몇 분이세요?"
}
```

- `session_id`는 서버 메모리/Redis에 대화 히스토리 + 누적 profile을 들고 있는 키.
  (DB의 `user_conversation_state.conversation_state_id`와 매핑)

---

## 2. 대화 턴 진행

### `POST /api/v1/chat/sessions/{session_id}/messages`

**Request**
```json
{ "message": "지금 엄마랑 둘이 살고 전세예요" }
```

**Response**
```json
{
  "reply": "그러시군요. 어머니랑 같이 사시는 거죠? 혹시 지금 일하시거나 아르바이트하고 계세요?",
  "current_phase": 1,
  "phase_advanced": false,
  "extracted_fields": {
    "household_members": 2,
    "housing_type": "전세",
    "living_with_relation": "모(동거)"
  }
}
```

- `phase_advanced: true`가 오면 프론트는 화면 전환 애니메이션 등을 트리거해도 됨 (필수 아님, 참고용).
- phase5(요약/확인) 완료 시 `current_phase`가 `"matching"`으로 오고, 이 시점에 프론트는
  자동으로 3번(매칭 조회) API를 호출한다.

**phase 값 종류**: `1`,`2`,`3`,`4`,`5`,`"matching"`,`6`,`"done"`

---

## 3. 매칭 결과 조회

phase5 확인이 끝나면 서버가 내부적으로 매칭 프롬프트를 실행해두고,
프론트는 아래로 결과를 가져온다.

### `GET /api/v1/chat/sessions/{session_id}/match`

**Response**
```json
{
  "적합": [
    {
      "policy_id": 2,
      "policy_name": "서울시 가족돌봄청소년·청년 자기돌봄비 지원 사업",
      "reason": "나이(22세), 소득 기준(중위소득 150% 이하 추정), 돌봄 대상(모, 뇌병변장애) 충족",
      "caution": null
    }
  ],
  "확인_불가": [
    {
      "policy_id": 7,
      "policy_name": "서울시 청년월세지원 사업",
      "missing_field": "housing_deposit",
      "reason": "임차보증금 정보가 아직 확인되지 않아 자격 여부를 판단할 수 없음"
    }
  ],
  "부적합": [
    {
      "policy_id": 16,
      "policy_name": "서울청년문화패스",
      "reason": "대상 연령(21~23세)에 해당하지 않음"
    }
  ],
  "has_followup_available": true
}
```

- `has_followup_available: true`면 프론트에 "몇 가지만 더 확인하면
  더 찾아드릴 수 있어요" 버튼을 노출 → 누르면 4번 API(phase6) 진행.
- 화면에는 보통 `적합`만 카드로 크게 보여주고, `확인_불가`는 "조건 확인 필요"
  배지로, `부적합`은 기본적으로 숨기거나 "안 맞는 제도 보기" 접힘 메뉴로 처리 권장.

---

## 4. 보완 질문 라운드 (phase 6)

`확인_불가`가 있을 때만 호출. 대화 형태는 2번 API와 동일한 포맷 재사용.

### `POST /api/v1/chat/sessions/{session_id}/followup`

**Request**
```json
{ "message": "보증금은 7천만원 정도예요" }
```

**Response**
```json
{
  "reply": "감사해요! 그럼 이제 다시 한번 확인해드릴게요.",
  "followup_done": true,
  "rematch_result": { "적합": [...], "확인_불가": [...], "부적합": [...] }
}
```

- `followup_done: true`가 오면 이 세션의 phase6 라운드는 종료(재질문 없음).
  `rematch_result`를 3번 API 응답과 동일한 방식으로 화면 갱신.

---

## 5. 제도 상세 조회 (화면 표시용)

매칭 결과 카드를 클릭했을 때, 또는 저장한 제도 목록 화면에서 사용.

### `GET /api/v1/policies/{policy_id}`

**Response**
```json
{
  "policy_id": 7,
  "policy_name": "서울시 청년월세지원 사업",
  "agency_name": "서울시 (서울주택도시개발공사 청년월세지원센터)",
  "summary": "주거비 부담 완화를 위해...",
  "policy_types": ["생계주거"],
  "support_period": "최대 12개월 / 240만원 (생애 1회)",
  "cost": null,
  "age_min": 19,
  "age_max": 39,
  "exception_age": "의무복무 제대군인 청년 대상...",
  "application_method": "서울주거포털(housing.seoul.go.kr) 온라인 신청 및 접수",
  "deadline_type": "고정일",
  "deadline_date_raw": "2026. 5. 19. 18:00",
  "application_deadline": "2026-05-19T18:00:00",
  "result_note": "7월 초 심사결과 통보, 7월 말 최종 발표",
  "link": "https://housing.seoul.go.kr/...",
  "contact": "SH 청년월세지원센터 1833-2030",
  "is_lifetime_limit_once": true,
  "required_documents": [
    { "document_id": 41, "document_name": "지원신청서" },
    { "document_id": 42, "document_name": "개인정보 동의서" }
  ]
}
```

### `GET /api/v1/policies?ids=2,7,16` (배치 조회, 매칭 리스트 렌더링용)

카드 여러 개를 한 번에 그릴 때 N+1 호출 방지용. 응답은 위 형태의 배열.

---

## 6. 제도 번역기 (용어/서류/자부담금 설명)

화면에 이미 떠 있는 제도 하나에 대해, 사용자가 궁금한 걸 물어보는 사이드 챗봇.

### `POST /api/v1/policies/{policy_id}/translate`

**Request**
```json
{
  "carer_id": 123,
  "question": "차상위계층이 뭐예요?"
}
```

**Response**
```json
{
  "answer": "차상위계층은 기초생활수급자보다는 형편이 조금 낫지만, 여전히 소득이 낮아 지원이 필요한 가구를 말해요. 이 제도는 차상위계층이 아니어도 신청 가능하니 걱정 안 하셔도 돼요."
}
```

- `question`이 없으면(빈 문자열) 제도 전체를 쉬운 말로 한 번 풀어서 설명하는 기본 요약을 반환.
- 서버는 응답 생성 시 `carer_id` 기준으로 `user_document_history`를 조회해서,
  이미 발급받은 서류가 이 제도의 `required_documents`와 겹치면
  "지난번에 발급받으신 [서류명]을 다시 제출하시면 됩니다" 문구를 자동으로 끼워 넣는다.
- **주민센터 운영시간/방문 권장일** 같은 안내는 우리 DB에 없는 정보라(공공데이터
  별도 연동 필요), 1차 구현에서는 일반적인 안내("평일 9시~18시, 마감 최소 3일
  전 방문 권장" 같은 정적 문구)로 대체하고, 실제 기관별 운영시간 API는 2차
  개선 항목으로 남겨둔다.
- 자부담금(`cost` 필드)이 있는 제도면, 답변 끝에 "이 제도는 [cost] 정도
  본인이 부담하셔야 해요"를 자동으로 덧붙인다 (질문 여부와 무관하게 항상).

---

## 7. 세션 상태 조회 (디버깅/재접속용)

앱을 껐다 켰을 때 이어서 대화하기 위한 조회.

### `GET /api/v1/chat/sessions/{session_id}`

```json
{
  "session_id": "sess_abcd1234",
  "current_phase": 3,
  "profile": { "...누적된 전체 필드..." },
  "conversation_history": [
    { "role": "assistant", "content": "..." },
    { "role": "user", "content": "..." }
  ]
}
```

---

## 8. 에러 처리 공통 포맷

```json
{
  "error": "SESSION_NOT_FOUND",
  "message": "세션이 만료되었거나 존재하지 않습니다."
}
```

주요 에러 코드: `SESSION_NOT_FOUND`, `PHASE_MISMATCH`(이미 매칭 단계인데 phase1~4 API 호출 등), `POLICY_NOT_FOUND`

---

## 9. 구현 순서 제안 (Claude Code 작업 단위)

1. `POST /chat/sessions` + `POST /chat/sessions/{id}/messages` (phase1~5 loop) — 여기까지 되면 대화만으로 profile이 다 채워지는지 검증 가능
2. `GET /chat/sessions/{id}/match` — 매칭 프롬프트 연결
3. `POST /chat/sessions/{id}/followup` — phase6 연결
4. `GET /policies/{id}`, `GET /policies?ids=` — 프론트 카드/상세 화면 붙일 수 있게
5. `POST /policies/{id}/translate` — 제도번역기

이 순서대로 하면 1~3번만 끝나도 "대화 → 매칭 결과"까지 프론트에 붙여서
1차 성능 검증(진짜 원하는 제도가 나오는지)을 바로 시작할 수 있어요.
