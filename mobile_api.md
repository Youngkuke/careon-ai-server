# 모바일 API 명세

## 공통 규칙

- Base URL은 환경별로 다르며, 문서에는 API path만 표기합니다.
- 모바일 앱 API는 `/api/app/*`를 사용합니다.
- `/api/app/*`는 Spring 백엔드(`https://api.careon.site`)가 담당합니다.
- 인증이 필요한 API는 `Authorization: Bearer {access_token}` 헤더를 사용합니다.
- 앱 로그인은 `access_token`과 `refresh_token`을 함께 발급합니다.
- `access_token` 유효 시간은 1시간, `refresh_token` 유효 시간은 90일입니다.
- 앱 회원가입은 제공하지 않습니다. 웹에서 가입한 계정으로 로그인합니다.
- 요청/응답 JSON 필드명은 DB 컬럼명과 맞춰 `snake_case`를 사용합니다.
- 날짜는 `YYYY-MM-DD`, 일시는 ISO-8601 문자열을 사용합니다.
- 빈 목록은 에러가 아니라 `200 OK`와 빈 배열 `[]`로 응답합니다.

### 모바일 전용 범위

- 모바일 자동 로그인 유지를 위한 `refresh_token` 발급/재발급/폐기
- 앱 캘린더용 저장 제도 목록
- 앱 알림 목록
- 앱 투두 목록과 투두 체크
- 신청 마감 후 신청 여부 응답

### 공통 에러 응답

```json
{
  "timestamp": "2026-07-21T15:49:07.014+09:00",
  "status": 400,
  "error": "Bad Request",
  "message": "에러 메시지",
  "path": "/api/app/..."
}
```

## 엔드포인트 요약

| 분류 | 이름 | Method | Path | 인증 |
| ---- | ---- | ------ | ---- | ---- |
| 앱 유저 관리 | 로그인 | POST | `/api/app/users/login` | 불필요 |
| 앱 유저 관리 | 로그아웃 | POST | `/api/app/users/logout` | 필요 |
| 앱 유저 관리 | 액세스 토큰 재발급 | POST | `/api/app/users/refresh` | 불필요 |
| 앱 유저 관리 | 내 정보 조회 | GET | `/api/app/users/me` | 필요 |
| 앱 유저 관리 | 회원정보 수정 | PATCH | `/api/app/users/me` | 필요 |
| 앱 유저 관리 | 회원 탈퇴 | DELETE | `/api/app/users/me` | 필요 |
| 앱 제도 관리 | 저장한 제도 목록 조회 | GET | `/api/app/users/me/saved-policies` | 필요 |
| 앱 제도 관리 | 제도 저장 취소 | DELETE | `/api/app/users/me/saved-policies/{saved_policy_id}` | 필요 |
| 앱 알림 관리 | 미읽음 알림 개수 조회 | GET | `/api/app/users/me/notifications/unread-count` | 필요 |
| 앱 알림 관리 | 알림 목록 조회 | GET | `/api/app/users/me/notifications` | 필요 |
| 앱 알림 관리 | 모든 알림 읽음 처리 | PATCH | `/api/app/users/me/notifications/read-all` | 필요 |
| 앱 투두 관리 | 투두 목록 조회 | GET | `/api/app/users/me/todos` | 필요 |
| 앱 투두 관리 | 신청 여부 응답 | POST | `/api/app/users/me/saved-policies/{saved_policy_id}/applied` | 필요 |
| 앱 투두 관리 | 투두 체크/체크 해제 | PATCH | `/api/app/users/me/todos/{todo_id}` | 필요 |

## 앱 유저 관리

### 로그인

웹에서 가입한 계정으로 로그인합니다. 성공 시 앱 자동 로그인 유지를 위한 `access_token`과 `refresh_token`을 함께 반환합니다.

**Request**

`POST /api/app/users/login`

```json
{
  "email": "pjs123@gmail.com",
  "password": "abcd1234!"
}
```

| 필드 | 타입 | 필수 | DB 컬럼 | 설명 |
| ---- | ---- | ---- | ------- | ---- |
| email | String | Y | `carers.email` | 로그인용 이메일 |
| password | String | Y | `carers.password` | 로그인용 비밀번호 |

**Response `200 OK`**

```json
{
  "carer_id": 1,
  "access_token": "access-token-value",
  "refresh_token": "refresh-token-value",
  "refresh_token_expires_at": "2026-10-19T00:00:00+09:00"
}
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 400 | `이메일과 비밀번호를 입력해주세요.` | 이메일 또는 비밀번호 누락 |
| 401 | `이메일 또는 비밀번호가 일치하지 않습니다.` | 이메일 없음 또는 비밀번호 불일치 |

### 로그아웃

앱 로그인 시 서버에 저장한 `refresh_token`을 무효화합니다.

**Request**

`POST /api/app/users/logout`

Request Body 없음.

**Response `200 OK`**

```json
{
  "message": "로그아웃되었습니다."
}
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 401 | `로그인이 필요합니다.` | 인증 실패 |

### 액세스 토큰 재발급

`access_token`이 만료되었을 때 `refresh_token`으로 새 토큰을 발급받습니다.

- 재발급 시 `refresh_token`도 새 값으로 교체됩니다.
- 기존 `refresh_token`은 이후 사용할 수 없습니다.
- 새 `refresh_token`의 만료일은 재발급 시점부터 다시 90일입니다.

**Request**

`POST /api/app/users/refresh`

```json
{
  "refresh_token": "refresh-token-value"
}
```

| 필드 | 타입 | 필수 | DB 컬럼 | 설명 |
| ---- | ---- | ---- | ------- | ---- |
| refresh_token | String | Y | `carers.refresh_token` | 로그인 또는 이전 재발급 시 받은 토큰 |

**Response `200 OK`**

```json
{
  "access_token": "new-access-token-value",
  "refresh_token": "new-refresh-token-value",
  "refresh_token_expires_at": "2026-10-19T00:00:00+09:00"
}
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 401 | `재로그인이 필요합니다.` | `refresh_token` 누락, 만료, 무효, 서버 저장값과 불일치 |

### 내 정보 조회

마이페이지에 표시할 로그인 유저 정보를 조회합니다.

**Request**

`GET /api/app/users/me`

Request Body 없음.

**Response `200 OK`**

```json
{
  "carer_id": 1,
  "name": "영크케",
  "email": "pjs123@gmail.com",
  "region": "동작구",
  "notification_enabled": true
}
```

| 필드 | 타입 | DB 컬럼 | 설명 |
| ---- | ---- | ------- | ---- |
| carer_id | Integer | `carers.carer_id` | 유저 ID |
| name | String | `carers.name` | 이름 또는 닉네임 |
| email | String | `carers.email` | 이메일 |
| region | String | `carers.region` | 거주 지역 |
| notification_enabled | Boolean | `carers.notification_enabled` | 알림 수신 여부 |

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 401 | `로그인이 필요합니다.` | 인증 실패 |

### 회원정보 수정

로그인한 유저 본인의 정보를 수정합니다. 모든 필드는 optional이며, 앱은 화면마다 필요한 필드 하나만 보내도 됩니다.

**Request**

`PATCH /api/app/users/me`

```json
{
  "name": "영크케",
  "notification_enabled": true
}
```

| 필드 | 타입 | 필수 | DB 컬럼 | 설명 |
| ---- | ---- | ---- | ------- | ---- |
| name | String | N | `carers.name` | 이름 또는 닉네임 |
| email | String | N | `carers.email` | 이메일 |
| password | String | N | `carers.password` | 새 비밀번호 |
| region | String | N | `carers.region` | 거주 지역 |
| notification_enabled | Boolean | N | `carers.notification_enabled` | 알림 수신 여부 |

**Response `200 OK`**

```json
{
  "message": "회원 정보가 수정되었습니다."
}
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 400 | `이메일 형식이 올바르지 않습니다.` | 이메일 형식 오류 |
| 400 | `비밀번호는 영문과 숫자를 포함하여 8~20자여야 합니다.` | 비밀번호 형식 오류 |
| 401 | `로그인이 필요합니다.` | 인증 실패 |
| 409 | `이미 사용 중인 이메일입니다.` | 이메일 중복 |

### 회원 탈퇴

로그인한 유저의 계정을 삭제합니다. 저장 제도, 투두, 알림, 관심 유형 연결 데이터도 함께 정리됩니다.

**Request**

`DELETE /api/app/users/me`

Request Body 없음.

**Response `200 OK`**

```json
{
  "message": "회원 탈퇴가 완료되었습니다."
}
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 401 | `로그인이 필요합니다.` | 인증 실패 |

## 앱 제도 관리

### 저장한 제도 목록 조회

로그인한 유저가 웹에서 저장한 제도 목록을 앱 캘린더 화면용으로 조회합니다.

- 캘린더 마커와 하단 카드 리스트에 사용합니다.
- 월 이동은 프론트에서 전체 데이터를 받아 필터링하므로 `year`, `month` 파라미터는 없습니다.
- 날짜가 지난 항목도 제외하지 않습니다.
- 정렬 기준은 예정 항목을 먼저 임박한 순으로 정렬하고, 이후 지난 항목을 최근 지난 순으로 이어붙입니다.
- `application_deadline_d_day`, `result_date_d_day`는 서버가 계산해 내려줍니다.
- 이미 지난 날짜는 캘린더에 계속 표시하지만 D+ 표기는 하지 않습니다.
- 이미 지난 신청 마감 항목은 `application_deadline` 날짜만 유지하고 `application_deadline_d_day: null`, `documents: []`로 내려줍니다.
- 이미 지난 결과 발표 항목은 `result_date` 날짜만 유지하고 `result_date_d_day: null`로 내려줍니다.

**Request**

`GET /api/app/users/me/saved-policies`

Request Body 없음.

**Response `200 OK`**

```json
[
  {
    "saved_policy_id": 42,
    "policy_id": 5,
    "policy_name": "가족돌봄청년 일상돌봄",
    "application_deadline": "2026-07-28",
    "application_deadline_d_day": "D-7",
    "documents": ["가족관계증명서", "진단서"],
    "result_date": null,
    "result_date_d_day": null
  },
  {
    "saved_policy_id": 43,
    "policy_id": 6,
    "policy_name": "지난 신청 제도",
    "application_deadline": "2026-07-01",
    "application_deadline_d_day": null,
    "documents": [],
    "result_date": null,
    "result_date_d_day": null
  },
  {
    "saved_policy_id": 44,
    "policy_id": 8,
    "policy_name": "청년 마음건강 바우처",
    "application_deadline": null,
    "application_deadline_d_day": null,
    "documents": [],
    "result_date": "2026-07-10",
    "result_date_d_day": null
  }
]
```

| 필드 | 타입 | DB 컬럼/출처 | 설명 |
| ---- | ---- | ------------ | ---- |
| saved_policy_id | Integer | `saved_policies.saved_policy_id` | 저장 제도 ID |
| policy_id | Integer | `policies.policy_id` | 제도 ID |
| policy_name | String | `policies.policy_name` | 제도명 |
| application_deadline | String \| null | `policies.application_deadline` | 신청 마감일 |
| application_deadline_d_day | String \| null | 서버 계산값 | 신청 마감일 기준 D-day |
| documents | String[] | `documents.document_name` | 필요 서류명 목록 |
| result_date | String \| null | `policies.result_date` | 결과 발표일 |
| result_date_d_day | String \| null | 서버 계산값 | 결과 발표일 기준 D-day |

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 401 | `로그인이 필요합니다.` | 인증 실패 |

### 제도 저장 취소

저장한 제도를 취소합니다. `saved_policy_id`가 로그인한 본인의 저장 항목인지 확인 후 삭제합니다.

- 저장 취소 시 해당 저장 항목에 연결된 `todos`, `notifications`도 함께 삭제됩니다.
- 투두리스트 화면에서 마감된 제도에 "아니오"로 응답할 때 사용합니다.

**Request**

`DELETE /api/app/users/me/saved-policies/{saved_policy_id}`

| Path Variable | 타입 | 설명 |
| ------------- | ---- | ---- |
| saved_policy_id | Integer | 삭제할 저장 항목 ID |

**Response `200 OK`**

```json
{
  "message": "저장이 취소되었습니다."
}
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 401 | `로그인이 필요합니다.` | 인증 실패 |
| 404 | `존재하지 않는 저장 항목입니다.` | 저장 항목 없음 또는 본인 소유 아님 |

## 앱 알림 관리

### 미읽음 알림 개수 조회

종 아이콘의 빨간 점/개수 뱃지 표시를 위해 미읽음 알림 개수를 조회합니다.

**Request**

`GET /api/app/users/me/notifications/unread-count`

Request Body 없음.

**Response `200 OK`**

```json
{
  "unread_count": 3
}
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 401 | `로그인이 필요합니다.` | 인증 실패 |

### 알림 목록 조회

종 아이콘 클릭 시 표시할 알림 목록을 최신순으로 조회합니다.

- 이 API 호출만으로는 알림을 읽음 처리하지 않습니다.
- 알림 화면을 사용자가 확인한 뒤 앱이 `PATCH /api/app/users/me/notifications/read-all`을 호출해 읽음 처리합니다.
- `relative_time`은 서버가 계산해 내려줍니다.
- 유형별 아이콘/문구는 프론트가 `notification_type` 값으로 처리합니다.
- 알림 탭 시 제도 박스로 이동할 수 있도록 `policy_id`를 포함합니다.

**Request**

`GET /api/app/users/me/notifications`

Request Body 없음.

**Response `200 OK`**

```json
[
  {
    "notification_id": 501,
    "saved_policy_id": 42,
    "policy_id": 5,
    "policy_name": "가족돌봄청년 일상돌봄",
    "notification_type": "DEADLINE_D7",
    "sent_at": "2026-07-06T10:00:00+09:00",
    "is_read": false,
    "relative_time": "1일 전"
  },
  {
    "notification_id": 500,
    "saved_policy_id": 43,
    "policy_id": 8,
    "policy_name": "청년 마음건강 바우처",
    "notification_type": "RESULT_DDAY",
    "sent_at": "2026-07-01T09:00:00+09:00",
    "is_read": false,
    "relative_time": "2주 전"
  }
]
```

| notification_type | 의미 |
| ----------------- | ---- |
| DEADLINE_D7 | 신청 마감 7일 전 |
| DEADLINE_D3 | 신청 마감 3일 전 |
| DEADLINE_D1 | 신청 마감 1일 전 |
| RESULT_DDAY | 결과 발표 당일 |

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 401 | `로그인이 필요합니다.` | 인증 실패 |

### 모든 알림 읽음 처리

앱에서 알림 목록을 사용자가 확인한 뒤 모든 미읽음 알림을 읽음 처리합니다.

**Request**

`PATCH /api/app/users/me/notifications/read-all`

Request Body 없음.

**Response `200 OK`**

```json
{
  "updated_count": 3,
  "message": "모든 알림을 읽음 처리했습니다."
}
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 401 | `로그인이 필요합니다.` | 인증 실패 |

## 앱 투두 관리

### 투두 목록 조회

로그인한 유저가 저장한 제도들의 서류 체크리스트를 조회합니다. 앱의 신청 일정/투두리스트 화면에서 사용합니다.

- 저장한 제도가 없으면 `[]`를 반환합니다.
- 날짜별 그룹핑은 프론트에서 `application_deadline` 기준으로 처리합니다.
- 신청 마감일이 지나지 않은 제도는 기존과 동일하게 서류 체크리스트를 포함하고 `is_expired: false`로 내려갑니다.
- 신청 마감일이 지난 제도 중 신청 여부 미응답 건은 서류 없이 `is_expired: true`, `documents: []`로 내려갑니다.
- `is_expired: true`인 항목에는 프론트에서 "이 제도를 신청하셨나요?" 예/아니오 버튼을 표시합니다.
- "예"로 응답한 제도와 "아니오"로 응답해 저장 취소된 제도는 이후 응답에서 제외됩니다.

**Request**

`GET /api/app/users/me/todos`

Request Body 없음.

**Response `200 OK`**

```json
[
  {
    "saved_policy_id": 42,
    "policy_id": 5,
    "policy_name": "가족돌봄청년 일상돌봄",
    "application_deadline": "2026-07-30",
    "link": "https://...",
    "is_expired": false,
    "documents": [
      {
        "todo_id": 101,
        "document_id": 1,
        "document_name": "가족관계증명서",
        "issuers": [
          {
            "document_issuer_id": 1,
            "issuer_name": "정부24",
            "issuer_site": "https://..."
          }
        ],
        "is_checked": false
      },
      {
        "todo_id": 102,
        "document_id": 2,
        "document_name": "진단서",
        "issuers": [
          {
            "document_issuer_id": 2,
            "issuer_name": "담당 병원",
            "issuer_site": null
          }
        ],
        "is_checked": true
      }
    ]
  },
  {
    "saved_policy_id": 43,
    "policy_id": 8,
    "policy_name": "주거지원 제도",
    "application_deadline": "2026-07-01",
    "link": "https://...",
    "is_expired": true,
    "documents": []
  }
]
```

| 필드 | 타입 | DB 컬럼/출처 | 설명 |
| ---- | ---- | ------------ | ---- |
| saved_policy_id | Integer | `saved_policies.saved_policy_id` | 저장 제도 ID |
| policy_id | Integer | `policies.policy_id` | 제도 ID |
| policy_name | String | `policies.policy_name` | 제도명 |
| application_deadline | String \| null | `policies.application_deadline` | 신청 마감일 |
| link | String \| null | `policies.link` | 공식 페이지 URL |
| is_expired | Boolean | 서버 계산값 | 신청 마감일 지남 여부 |
| documents | Array | `todos`, `documents`, `document_issuers` | 필요 서류 체크리스트 |

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 401 | `로그인이 필요합니다.` | 인증 실패 |

### 신청 여부 응답

신청 마감일이 지난 저장 제도에 대해 "신청했어요(예)"를 기록합니다.

- 최종 메서드는 `POST`입니다.
- 기록된 제도는 이후 투두 목록 조회에서 제외됩니다.
- "아니오"를 눌렀을 때는 이 API가 아니라 제도 저장 취소 API를 호출합니다.

**Request**

`POST /api/app/users/me/saved-policies/{saved_policy_id}/applied`

| Path Variable | 타입 | 설명 |
| ------------- | ---- | ---- |
| saved_policy_id | Integer | 투두 목록 조회 응답의 `saved_policy_id` |

```json
{
  "applied": true
}
```

| 필드 | 타입 | 필수 | DB 컬럼 | 설명 |
| ---- | ---- | ---- | ------- | ---- |
| applied | Boolean | Y | `saved_policies.applied` | `true` 고정. 신청함 표시 |

**Response `200 OK`**

```json
{
  "saved_policy_id": 42,
  "applied": true,
  "message": "저장되었습니다."
}
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 400 | `applied 값을 입력해주세요.` | `applied` 누락 |
| 401 | `로그인이 필요합니다.` | 인증 실패 |
| 404 | `존재하지 않는 저장 항목입니다.` | 저장 항목 없음 또는 본인 소유 아님 |

### 투두 체크/체크 해제

투두리스트 화면에서 서류 체크박스를 클릭했을 때 체크 상태를 변경합니다.

**Request**

`PATCH /api/app/users/me/todos/{todo_id}`

| Path Variable | 타입 | 설명 |
| ------------- | ---- | ---- |
| todo_id | Integer | 변경할 투두 항목 ID |

```json
{
  "is_checked": true
}
```

| 필드 | 타입 | 필수 | DB 컬럼 | 설명 |
| ---- | ---- | ---- | ------- | ---- |
| is_checked | Boolean | Y | `todos.is_checked` | 체크 시 `true`, 해제 시 `false` |

**Response `200 OK`**

```json
{
  "todo_id": 101,
  "is_checked": true,
  "message": "체크 상태가 변경되었습니다."
}
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 400 | `값이 누락되었습니다.` | `is_checked` 누락 |
| 401 | `로그인이 필요합니다.` | 인증 실패 |
| 404 | `존재하지 않는 투두 항목입니다.` | 투두 없음 또는 본인 소유 아님 |
