# 웹 API 명세

## 공통 규칙

- Base URL은 환경별로 다르며, 문서에는 API path만 표기합니다.
- 인증이 필요한 API는 `Authorization: Bearer {access_token}` 헤더를 사용합니다.
- 웹 `access_token` 유효 시간은 6시간입니다. 만료되면 재로그인이 필요합니다.
- `/api/web/*`는 Spring 백엔드(`https://api.careon.site`)가 담당합니다.
- `/api/v1/*`는 AI 서버가 담당합니다.
- 요청/응답 JSON 필드명은 DB 컬럼명과 맞춰 `snake_case`를 사용합니다.
- 날짜/시간은 별도 표기가 없으면 ISO 문자열을 사용합니다. 날짜만 필요한 화면에서는 `YYYY-MM-DD`로 잘라 표시해도 됩니다.
- 빈 목록은 에러가 아니라 `200 OK`와 빈 배열 `[]`로 응답합니다.
- 웹은 `refresh_token`을 사용하지 않습니다. 인증 API에서 `401`을 받으면 프론트는 저장된 `access_token`을 삭제하고 로그인 화면으로 이동합니다.
- `chat_sessions`, `chat_messages` 테이블은 현재 스키마에 없습니다. 챗봇 대화 본문은 AI 서버 메모리에만 저장하고, 필요한 진행 상태만 `user_conversation_state`에 저장합니다.

### DB 기준 핵심 매핑

| 도메인 | 테이블 | 주요 컬럼 |
| ------ | ------ | --------- |
| 유저 | `carers` | `carer_id`, `name`, `email`, `password`, `region`, `terms_agreed`, `diagnosis_completed`, `app_installed`, `install_prompt_count`, `notification_enabled` |
| 2단계 진단 프로필 | `carers` | `age`, `household_members_count`, `cared_count`, `housing_type`, `daily_care_hours_self`, `income_value`, `has_basic_livelihood_support`, `has_cha_sang_wi`, `military_service_status`, `household_asset_value`, `vehicle_value` 등 |
| 돌봄 대상자 | `cared` | `cared_id`, `carer_id`, `cared_relation`, `age`, `condition_summary`, `severity_level` |
| 소득 추론 근거 | `carer_income_signal` | `signal_id`, `carer_id`, `signal_type`, `raw_value`, `parsed_value`, `source`, `confidence` |
| 챗봇 진행 상태 | `user_conversation_state` | `conversation_state_id`, `carer_id`, `current_phase`, `active_policy_id`, `updated_at` |
| 제도 | `policies` | `policy_id`, `policy_name`, `agency_id`, `support_period`, `cost`, `summary`, `application_method`, `deadline_type`, `application_deadline`, `link`, `contact`, `category` 등 |
| 기관 | `agencies` | `agency_id`, `agency_name` |
| 제도 유형 | `policy_types` | `policy_type_id`, `type_name` |
| 제도-유형 연결 | `connect_policy_policy_types` | `policy_id`, `policy_type_id` |
| 필요 서류 | `documents`, `document_issuers`, `document_issues`, `connect_policy_documents` | `document_id`, `document_name`, `issuer_name`, `issuer_site` |
| 매칭 제도 | `matched_policy` | `matched_policy_id`, `carer_id`, `policy_id`, `was_benefited` |
| 저장 제도 | `saved_policies` | `saved_policy_id`, `carer_id`, `policy_id`, `applied` |
| 저장 제도 투두 | `todos` | `todo_id`, `saved_policy_id`, `document_id`, `is_checked` |
| 알림 | `notifications` | `notification_id`, `saved_policy_id`, `notification_type`, `sent_at`, `is_read` |
| 서류 이력 | `user_document_history` | `history_id`, `carer_id`, `document_id`, `policy_id`, `issued_date`, `valid_until`, `direct_utter`, `confirmed_by_user` |

### 스키마 확인 필요

- `matched_policy.match_group`은 챗봇 매칭 결과의 `적합`, `확인_불가`를 저장하기 위해 필요하지만 현재 `dbtablename.md`에는 없습니다. Spring 백엔드 배포 시 추가 예정이므로, 컬럼 추가 전 AI 서버에서 해당 컬럼 쓰기를 시도하면 안 됩니다.
- 챗봇 문서의 `field`는 DB 컬럼이 아니므로 `policy_types.type_name` 또는 `category`로 내려줍니다.
- 챗봇 문서의 `agency_name`은 `policies.agency_id`와 `agencies.agency_name` 조인 결과입니다.

### 웹 공통 에러 응답

```json
{
  "timestamp": "2026-07-21T15:49:07.014+09:00",
  "status": 400,
  "error": "Bad Request",
  "message": "에러 메시지",
  "path": "/api/web/..."
}
```

### AI 서버 에러 응답

`/api/v1/*`는 현재 AI 서버 1차 구현 기준으로 코드형 `error`와 사용자 표시용 `message`를 반환합니다.

```json
{
  "error": "POLICY_NOT_FOUND",
  "message": "해당 제도를 찾을 수 없습니다."
}
```

## 엔드포인트 요약

| 분류 | 이름 | Method | Path | 인증 |
| ---- | ---- | ------ | ---- | ---- |
| 유저 관리 | 로그인 | POST | `/api/web/users/login` | 불필요 |
| 유저 관리 | 회원가입 | POST | `/api/web/users/register` | 불필요 |
| 유저 관리 | 비밀번호 재설정 링크 발송 | POST | `/api/web/users/password/reset-link` | 불필요 |
| 유저 관리 | 비밀번호 재설정 | POST | `/api/web/users/password/reset` | 불필요 |
| 유저 관리 | 내 정보 조회 | GET | `/api/web/users/me` | 필요 |
| 유저 관리 | 회원정보 수정 | PATCH | `/api/web/users/me` | 필요 |
| 유저 관리 | 회원 탈퇴 | DELETE | `/api/web/users/me` | 필요 |
| 유저 관리 | 앱 설치 상태 응답 | PATCH | `/api/web/users/me/app-install-status` | 필요 |
| 진단 프로필 관리 | 내 진단 프로필 조회 | GET | `/api/web/users/me/diagnosis-profile` | 필요 |
| 진단 프로필 관리 | 내 진단 프로필 수정 | PATCH | `/api/web/users/me/diagnosis-profile` | 필요 |
| 진단 프로필 관리 | 돌봄 대상자 추가 | POST | `/api/web/users/me/cared` | 필요 |
| 진단 프로필 관리 | 돌봄 대상자 수정 | PATCH | `/api/web/users/me/cared/{cared_id}` | 필요 |
| 진단 프로필 관리 | 돌봄 대상자 삭제 | DELETE | `/api/web/users/me/cared/{cared_id}` | 필요 |
| 진단 프로필 관리 | 소득 추론 근거 조회 | GET | `/api/web/users/me/income-signals` | 필요 |
| 진단 프로필 관리 | 소득 추론 충돌 해결 | PATCH | `/api/web/users/me/income-signals/{signal_id}` | 필요 |
| 2단계 챗봇 진단 | 채팅 세션 생성 | POST | `/api/v1/chat/sessions` | 필요 |
| 2단계 챗봇 진단 | 채팅 메시지 전송 | POST | `/api/v1/chat/sessions/{session_id}/messages` | 필요 |
| 2단계 챗봇 진단 | 매칭 실행 | POST | `/api/v1/chat/sessions/{session_id}/match` | 필요 |
| 2단계 챗봇 진단 | 챗봇 진행 상태 조회 | GET | `/api/v1/chat/state` | 필요 |
| 2단계 챗봇 진단 | 챗봇 진행 상태 초기화 | DELETE | `/api/v1/chat/state` | 필요 |
| 기준 데이터 관리 | 기관 목록 조회 | GET | `/api/web/agencies` | 불필요 |
| 기준 데이터 관리 | 기관 상세 조회 | GET | `/api/web/agencies/{agency_id}` | 불필요 |
| 기준 데이터 관리 | 제도 유형 목록 조회 | GET | `/api/web/policy-types` | 불필요 |
| 기준 데이터 관리 | 서류 목록 조회 | GET | `/api/web/documents` | 불필요 |
| 기준 데이터 관리 | 서류 상세 조회 | GET | `/api/web/documents/{document_id}` | 불필요 |
| 관심 유형 관리 | 내 관심 유형 조회 | GET | `/api/web/users/me/interest-policy-types` | 필요 |
| 관심 유형 관리 | 내 관심 유형 수정 | PATCH | `/api/web/users/me/interest-policy-types` | 필요 |
| 제도 관리 | 제도 목록 조회 | GET | `/api/web/policies` | 불필요 |
| 제도 관리 | 대안 복지 조회 | GET | `/api/web/policies/alternatives` | 불필요 |
| 제도 관리 | ID 기반 제도 카드 조회 | GET | `/api/v1/policies?ids=2,7,16` | 필요 |
| 제도 관리 | 제도번역기 | POST | `/api/v1/policies/{policy_id}/translate` | 필요 |
| 맞춤 지원 제도 관리 | 맞춤 지원 제도 목록 조회 | GET | `/api/web/policies/matched` | 필요 |
| 맞춤 지원 제도 관리 | 매칭 제도 수혜 여부 수정 | PATCH | `/api/web/users/me/matched-policies/{matched_policy_id}` | 필요 |
| 맞춤 지원 제도 관리 | 제도 상세 조회 | GET | `/api/web/policies/{policy_id}` | 불필요 |
| 저장 제도 관리 | 제도 저장 | POST | `/api/web/users/me/saved-policies` | 필요 |
| 저장 제도 관리 | 저장한 제도 목록 조회 | GET | `/api/web/users/me/saved-policies` | 필요 |
| 저장 제도 관리 | 제도 저장 취소 | DELETE | `/api/web/users/me/saved-policies/{saved_policy_id}` | 필요 |
| 서류 이력 관리 | 내 서류 이력 조회 | GET | `/api/web/users/me/document-history` | 필요 |
| 서류 이력 관리 | 서류 이력 저장 | POST | `/api/web/users/me/document-history` | 필요 |
| 서류 이력 관리 | 서류 이력 수정 | PATCH | `/api/web/users/me/document-history/{history_id}` | 필요 |
| 서류 이력 관리 | 서류 이력 삭제 | DELETE | `/api/web/users/me/document-history/{history_id}` | 필요 |

## 유저 관리

### 로그인

이메일과 비밀번호로 로그인합니다. 응답의 `diagnosis_completed` 값에 따라 다음 화면을 결정합니다.

- `diagnosis_completed: false`: 2단계 챗봇 진단 페이지로 이동
- `diagnosis_completed: true`: 맞춤 지원 제도 페이지로 이동

**Request**

`POST /api/web/users/login`

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
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "diagnosis_completed": true
}
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 400 | `이메일과 비밀번호를 입력해주세요.` | 이메일 또는 비밀번호 누락 |
| 401 | `이메일 또는 비밀번호가 일치하지 않습니다.` | 이메일 없음 또는 비밀번호 불일치 |

### 회원가입

웹에서 신규 유저를 생성합니다. 성공 시 자동 로그인 처리를 위해 `access_token`을 함께 반환합니다.

**Request**

`POST /api/web/users/register`

```json
{
  "name": "영크케",
  "email": "pjs123@gmail.com",
  "password": "abcd1234!",
  "region": "관악구",
  "terms_agreed": true,
  "interest_policy_type_ids": [1, 3]
}
```

| 필드 | 타입 | 필수 | DB 컬럼/테이블 | 설명 |
| ---- | ---- | ---- | -------------- | ---- |
| name | String | Y | `carers.name` | 이름 또는 닉네임 |
| email | String | Y | `carers.email` | 이메일 |
| password | String | Y | `carers.password` | 8~20자, 영문+숫자 포함 |
| region | String | Y | `carers.region` | 거주 지역 |
| terms_agreed | Boolean | Y | `carers.terms_agreed` | 이용약관/개인정보처리방침 동의 여부 |
| interest_policy_type_ids | Integer[] | Y | `interest_policy_types.policy_type_id` | 관심 제도 유형 ID 목록 |

**Response `201 Created`**

```json
{
  "carer_id": 1,
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "diagnosis_completed": false
}
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 400 | `모든 항목을 입력해주세요.` | 필수 입력값 누락 |
| 400 | `이메일 형식이 올바르지 않습니다.` | 이메일 형식 오류 |
| 400 | `비밀번호는 영문과 숫자를 포함하여 8~20자여야 합니다.` | 비밀번호 형식 오류 |
| 400 | `이용약관 및 개인정보처리방침에 동의해야 합니다.` | 약관 미동의 |
| 400 | `관심 제도 유형을 1개 이상 선택해주세요.` | 관심 제도 유형 누락 |
| 404 | `존재하지 않는 제도 유형입니다.` | 관심 제도 유형 ID 없음 |
| 409 | `이미 사용 중인 이메일입니다.` | 이메일 중복 |

### 비밀번호 재설정 링크 발송

비밀번호 재설정 링크를 이메일로 발송합니다. 계정 존재 여부 노출을 막기 위해 가입 여부와 무관하게 동일한 성공 응답을 사용합니다.

**Request**

`POST /api/web/users/password/reset-link`

```json
{
  "email": "pjs123@gmail.com"
}
```

| 필드 | 타입 | 필수 | DB 컬럼 | 설명 |
| ---- | ---- | ---- | ------- | ---- |
| email | String | Y | `carers.email` | 가입 시 등록한 이메일 |

**Response `200 OK`**

```json
{
  "message": "비밀번호 재설정을 위해 이메일을 확인해보세요."
}
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 400 | `이메일을 입력해주세요.` | 이메일 누락 |
| 400 | `이메일 형식이 올바르지 않습니다.` | 이메일 형식 오류 |

### 비밀번호 재설정

이메일 링크에 포함된 `reset_token`과 새 비밀번호로 비밀번호를 재설정합니다. 링크는 발송 후 30분간 유효합니다.

**Request**

`POST /api/web/users/password/reset`

```json
{
  "reset_token": "a1b2c3d4e5f6g7h8",
  "new_password": "newpass1234!"
}
```

| 필드 | 타입 | 필수 | DB 컬럼 | 설명 |
| ---- | ---- | ---- | ------- | ---- |
| reset_token | String | Y | `carers.reset_token` | 이메일 링크에 담긴 토큰 |
| new_password | String | Y | `carers.password` | 새 비밀번호 |

**Response `200 OK`**

```json
{
  "message": "비밀번호가 재설정되었습니다."
}
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 400 | `잘못된 접근입니다.` | `reset_token` 누락 |
| 400 | `비밀번호는 영문과 숫자를 포함하여 8~20자여야 합니다.` | 새 비밀번호 형식 오류 |
| 400 | `유효하지 않거나 만료된 링크입니다. 다시 시도해주세요.` | 토큰 만료, 무효, 이미 사용됨 |

### 내 정보 조회

로그인한 유저의 프로필과 웹 화면 제어용 상태를 조회합니다.

**Request**

`GET /api/web/users/me`

Request Body 없음.

**Response `200 OK`**

```json
{
  "carer_id": 1,
  "name": "영크케",
  "email": "pjs123@gmail.com",
  "region": "동작구",
  "diagnosis_completed": true,
  "app_installed": false,
  "install_prompt_count": 1,
  "notification_enabled": true
}
```

| 필드 | 타입 | DB 컬럼 | 설명 |
| ---- | ---- | ------- | ---- |
| carer_id | Integer | `carers.carer_id` | 유저 ID |
| name | String | `carers.name` | 이름 또는 닉네임 |
| email | String | `carers.email` | 이메일 |
| region | String | `carers.region` | 거주 지역 |
| diagnosis_completed | Boolean | `carers.diagnosis_completed` | 2단계 진단 완료 여부 |
| app_installed | Boolean | `carers.app_installed` | 앱 설치 완료 처리 여부 |
| install_prompt_count | Integer | `carers.install_prompt_count` | 앱 설치 권유를 미룬 횟수 |
| notification_enabled | Boolean | `carers.notification_enabled` | 알림 수신 여부 |

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 401 | `로그인이 필요합니다.` | `access_token` 없음, 만료, 유효하지 않음 |

### 회원정보 수정

로그인한 유저 본인의 정보를 수정합니다. 모든 필드는 optional이며, 보낸 필드만 수정됩니다.

**Request**

`PATCH /api/web/users/me`

```json
{
  "name": "영크케",
  "email": "pjs123@gmail.com",
  "password": "abcd1234!",
  "region": "관악구",
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

`DELETE /api/web/users/me`

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

### 앱 설치 상태 응답

웹의 앱 설치 유도 모달에서 사용자가 선택한 값을 저장합니다.

- `app_installed: true`: 앱 설치 완료로 기록하고 이후 모달 미노출
- `app_installed: false`: 설치 권유 횟수 증가

**Request**

`PATCH /api/web/users/me/app-install-status`

```json
{
  "app_installed": false
}
```

| 필드 | 타입 | 필수 | DB 컬럼 | 설명 |
| ---- | ---- | ---- | ------- | ---- |
| app_installed | Boolean | Y | `carers.app_installed` | 앱 설치 완료 여부 |

**Response `200 OK`**

```json
{
  "message": "처리되었습니다.",
  "app_installed": false,
  "install_prompt_count": 2
}
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 400 | `값이 누락되었습니다.` | `app_installed` 누락 |
| 400 | `값이 올바르지 않습니다.` | Boolean이 아닌 값 |
| 401 | `로그인이 필요합니다.` | 인증 실패 |

## 진단 프로필 관리

### 내 진단 프로필 조회

로그인한 유저의 2단계 진단 프로필, 돌봄 대상자, 소득 추론 근거를 조회합니다.

**Request**

`GET /api/web/users/me/diagnosis-profile`

Request Body 없음.

**Response `200 OK`**

```json
{
  "carer_id": 1,
  "age": 24,
  "household_members_count": 2,
  "cared_count": 1,
  "know_housing_type": "알고있음",
  "housing_type": "월세",
  "biggest_burden_type": "생계",
  "burden_type_reason_summary": "생활비와 병원비 부담이 큼",
  "daily_care_hours_self": "4시간",
  "daily_care_hours_household": "6시간",
  "has_backup_caregiver": false,
  "backup_caregiver_relation": null,
  "medical_burden_level": "높음",
  "is_student": true,
  "has_income_activity": true,
  "income_value_status": "확인됨",
  "income_assessment_criteria": "월소득",
  "income_value": 800000,
  "median_income_ratio": "50% 이하",
  "income_variability_type": "불규칙",
  "income_related_utterance": "알바로 생활비를 대고 있어요",
  "has_basic_livelihood_support": false,
  "has_basic_livelihood_support_source": true,
  "has_cha_sang_wi": false,
  "has_cha_sang_wi_source": false,
  "military_service_status": "해당없음",
  "military_service_extension_years": null,
  "housing_deposit": 5000000,
  "housing_monthly_rent": 450000,
  "household_asset_value": 1000000,
  "vehicle_value": 0,
  "financial_detail_status": "일부확인",
  "diagnosis_completed": true,
  "cared": [
    {
      "cared_id": 3,
      "cared_relation": "어머니",
      "age": 52,
      "condition_summary": "장기 치료 중",
      "severity_level": "중증"
    }
  ],
  "income_signals": [
    {
      "signal_id": 8,
      "signal_type": "part_time_income",
      "raw_value": "알바로 월 80만원 정도",
      "parsed_value": 800000,
      "source": "chat",
      "confidence": "high",
      "contradicts_signal_id": null,
      "contradiction_resolved": true
    }
  ]
}
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 401 | `로그인이 필요합니다.` | 인증 실패 |

### 내 진단 프로필 수정

챗봇이 아닌 별도 화면에서 진단 프로필을 직접 수정할 때 사용합니다. 모든 필드는 optional이며 보낸 필드만 수정합니다.

**Request**

`PATCH /api/web/users/me/diagnosis-profile`

```json
{
  "age": 24,
  "household_members_count": 2,
  "housing_type": "월세",
  "income_value": 800000,
  "has_basic_livelihood_support": false,
  "has_cha_sang_wi": false,
  "household_asset_value": 1000000,
  "vehicle_value": 0
}
```

**Response `200 OK`**

```json
{
  "message": "진단 프로필이 수정되었습니다."
}
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 400 | `값이 올바르지 않습니다.` | 타입 또는 범위 오류 |
| 401 | `로그인이 필요합니다.` | 인증 실패 |

### 돌봄 대상자 추가

**Request**

`POST /api/web/users/me/cared`

```json
{
  "cared_relation": "어머니",
  "age": 52,
  "condition_summary": "장기 치료 중",
  "severity_level": "중증"
}
```

**Response `201 Created`**

```json
{
  "cared_id": 3,
  "message": "돌봄 대상자가 추가되었습니다."
}
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 401 | `로그인이 필요합니다.` | 인증 실패 |

### 돌봄 대상자 수정

**Request**

`PATCH /api/web/users/me/cared/{cared_id}`

| Path Variable | 타입 | 설명 |
| ------------- | ---- | ---- |
| cared_id | Integer | 수정할 돌봄 대상자 ID |

```json
{
  "cared_relation": "어머니",
  "age": 53,
  "condition_summary": "장기 치료 중",
  "severity_level": "중증"
}
```

**Response `200 OK`**

```json
{
  "message": "돌봄 대상자가 수정되었습니다."
}
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 401 | `로그인이 필요합니다.` | 인증 실패 |
| 404 | `존재하지 않는 돌봄 대상자입니다.` | 대상자 없음 또는 본인 소유 아님 |

### 돌봄 대상자 삭제

**Request**

`DELETE /api/web/users/me/cared/{cared_id}`

| Path Variable | 타입 | 설명 |
| ------------- | ---- | ---- |
| cared_id | Integer | 삭제할 돌봄 대상자 ID |

**Response `200 OK`**

```json
{
  "message": "돌봄 대상자가 삭제되었습니다."
}
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 401 | `로그인이 필요합니다.` | 인증 실패 |
| 404 | `존재하지 않는 돌봄 대상자입니다.` | 대상자 없음 또는 본인 소유 아님 |

### 소득 추론 근거 조회

AI가 사용자의 자연어 발화에서 추출한 소득 관련 근거를 조회합니다.

**Request**

`GET /api/web/users/me/income-signals`

Request Body 없음.

**Response `200 OK`**

```json
[
  {
    "signal_id": 8,
    "signal_type": "part_time_income",
    "raw_value": "월 80만원 정도 벌어요",
    "parsed_value": 800000,
    "source": "chat",
    "confidence": "high",
    "contradicts_signal_id": null,
    "contradiction_resolved": true,
    "created_at": "2026-07-21T15:49:07+09:00"
  }
]
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 401 | `로그인이 필요합니다.` | 인증 실패 |

### 소득 추론 충돌 해결

서로 모순되는 소득 추론 근거가 있을 때 사용자가 확인한 값을 기록합니다.

**Request**

`PATCH /api/web/users/me/income-signals/{signal_id}`

| Path Variable | 타입 | 설명 |
| ------------- | ---- | ---- |
| signal_id | Integer | 수정할 소득 추론 근거 ID |

```json
{
  "contradiction_resolved": true,
  "parsed_value": 800000
}
```

**Response `200 OK`**

```json
{
  "signal_id": 8,
  "contradiction_resolved": true,
  "message": "소득 추론 근거가 수정되었습니다."
}
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 400 | `값이 올바르지 않습니다.` | 타입 또는 범위 오류 |
| 401 | `로그인이 필요합니다.` | 인증 실패 |
| 404 | `존재하지 않는 소득 추론 근거입니다.` | 근거 없음 또는 본인 소유 아님 |

## 2단계 챗봇 진단

2단계 화면에서 사용자의 세부 돌봄 상황을 자연어로 수집하고, AI 서버가 `carers`, `cared`, `carer_income_signal`, `user_conversation_state`, `user_document_history`를 갱신합니다.

- `carer_id`는 `carers.carer_id`입니다.
- 대화 세션 키인 `session_id`는 DB PK가 아니며, AI 서버 메모리 세션 키입니다.
- 진행 상태는 `user_conversation_state.carer_id`, `current_phase`, `active_policy_id`에 저장합니다.
- 2단계가 끝나면 `carers.diagnosis_completed`를 `true`로 변경합니다.
- 매칭 결과 저장 시 `matched_policy.carer_id`, `matched_policy.policy_id`, `matched_policy.match_group`를 사용합니다. 단, `match_group` 컬럼은 DB 추가가 필요합니다.

### AI 업데이트 대상 필드

| 테이블 | 컬럼 |
| ------ | ---- |
| `carers` | `age`, `household_members_count`, `cared_count`, `know_housing_type`, `housing_type`, `biggest_burden_type`, `burden_type_reason_summary`, `daily_care_hours_self`, `daily_care_hours_household`, `has_backup_caregiver`, `backup_caregiver_relation`, `medical_burden_level`, `is_student`, `has_income_activity`, `income_value_status`, `income_assessment_criteria`, `income_value`, `median_income_ratio`, `income_variability_type`, `income_related_utterance`, `has_basic_livelihood_support`, `has_basic_livelihood_support_source`, `has_cha_sang_wi`, `has_cha_sang_wi_source`, `military_service_status`, `military_service_extension_years`, `housing_deposit`, `housing_monthly_rent`, `household_asset_value`, `vehicle_value`, `financial_detail_status`, `diagnosis_completed`, `updated_at` |
| `cared` | `carer_id`, `cared_relation`, `age`, `condition_summary`, `severity_level`, `created_at`, `updated_at` |
| `carer_income_signal` | `carer_id`, `signal_type`, `raw_value`, `parsed_value`, `source`, `confidence`, `contradicts_signal_id`, `contradiction_resolved`, `created_at` |
| `user_conversation_state` | `carer_id`, `current_phase`, `active_policy_id`, `updated_at` |
| `user_document_history` | `carer_id`, `document_id`, `policy_id`, `issued_date`, `valid_until`, `direct_utter`, `confirmed_by_user`, `created_at` |

### 채팅 세션 생성

2단계 챗봇 화면 진입 시 호출합니다.

**Request**

`POST /api/v1/chat/sessions`

```json
{
  "carer_id": 12
}
```

| 필드 | 타입 | 필수 | DB 컬럼 | 설명 |
| ---- | ---- | ---- | ------- | ---- |
| carer_id | Integer | Y | `carers.carer_id` | 로그인한 유저 ID |

**Response `201 Created`**

```json
{
  "session_id": "uuid-or-string",
  "conversation_state_id": 3,
  "current_phase": 1,
  "active_policy_id": 0,
  "message": "사용자님은 가족돌봄청년에 해당돼요! 특히 궁금해하신 돌봄·가사와 심리·청년 유형을 중심으로 제도를 찾아드릴게요."
}
```

| 필드 | 타입 | DB 컬럼 | 설명 |
| ---- | ---- | ------- | ---- |
| session_id | String | 없음 | AI 서버 메모리 세션 키 |
| conversation_state_id | Integer | `user_conversation_state.conversation_state_id` | 대화 상태 ID |
| current_phase | Integer | `user_conversation_state.current_phase` | 현재 대화 단계 |
| active_policy_id | Integer | `user_conversation_state.active_policy_id` | 대화 중인 제도 ID. 없으면 `0` |
| message | String | 없음 | 챗봇 첫 메시지 |

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 400 | `값이 누락되었습니다.` | `carer_id` 누락 |
| 401 | `로그인이 필요합니다.` | 인증 실패 |
| 404 | `존재하지 않는 유저입니다.` | 해당 `carer_id` 없음 |

### 채팅 메시지 전송

2단계 챗봇 대화 중 사용자가 메시지를 보낼 때마다 호출합니다. AI 서버는 메시지 내용을 바탕으로 업데이트 대상 필드를 갱신합니다.

**Request**

`POST /api/v1/chat/sessions/{session_id}/messages`

| Path Variable | 타입 | 설명 |
| ------------- | ---- | ---- |
| session_id | String | AI 서버 메모리 세션 |

```json
{
  "message": "엄마랑 둘이 살고 있어, 알바로 생활비 대는 중이야"
}
```

| 필드 | 타입 | 필수 | 설명 |
| ---- | ---- | ---- | ---- |
| message | String | Y | 사용자 입력 문장 |

**Response `200 OK`**

```json
{
  "message": "알바하시면서 어머니까지 챙기시느라 고생 많으세요. 가구원 수와 주거 형태 확인했어요!",
  "phase": "info_gathering",
  "current_phase": 1,
  "active_policy_id": 0
}
```

| 필드 | 타입 | DB 컬럼 | 설명 |
| ---- | ---- | ------- | ---- |
| message | String | 없음 | 챗봇 응답 메시지 |
| phase | String | 없음 | 프론트 제어용 단계. `info_gathering`, `ready_to_match` |
| current_phase | Integer | `user_conversation_state.current_phase` | 현재 대화 단계 |
| active_policy_id | Integer | `user_conversation_state.active_policy_id` | 대화 중인 제도 ID. 없으면 `0` |

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 400 | `메시지를 입력해주세요.` | `message` 누락 또는 빈 문자열 |
| 401 | `로그인이 필요합니다.` | 인증 실패 |
| 404 | `존재하지 않는 세션입니다.` | `session_id` 없음 또는 만료 |

### 매칭 실행

챗봇 메시지 응답의 `phase`가 `ready_to_match`가 되면 호출합니다. 서버는 적합/확인 불가 정책만 `matched_policy`에 저장하고, `carers.diagnosis_completed`를 `true`로 변경합니다.

**Request**

`POST /api/v1/chat/sessions/{session_id}/match`

| Path Variable | 타입 | 설명 |
| ------------- | ---- | ---- |
| session_id | String | AI 서버 메모리 세션 |

Request Body 없음.

**Response `200 OK`**

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

| 필드 | 타입 | DB 컬럼 | 설명 |
| ---- | ---- | ------- | ---- |
| matched_policy_id | Integer | `matched_policy.matched_policy_id` | 저장된 매칭 결과 ID |
| policy_id | Integer | `matched_policy.policy_id` | 매칭된 제도 ID |
| match_group | String | `matched_policy.match_group` | `적합`, `확인_불가` 중 하나 |

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 401 | `로그인이 필요합니다.` | 인증 실패 |
| 404 | `존재하지 않는 세션입니다.` | `session_id` 없음 또는 만료 |

### 챗봇 진행 상태 조회

로그인한 유저의 챗봇 진행 상태를 조회합니다. 대화 본문은 AI 서버 메모리에만 있으므로 반환하지 않습니다.

**Request**

`GET /api/v1/chat/state`

Request Body 없음.

**Response `200 OK`**

```json
{
  "conversation_state_id": 3,
  "carer_id": 1,
  "current_phase": 1,
  "active_policy_id": 16,
  "updated_at": "2026-07-21T15:49:07+09:00"
}
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 401 | `로그인이 필요합니다.` | 인증 실패 |
| 404 | `진행 중인 대화 상태가 없습니다.` | 저장된 상태 없음 |

### 챗봇 진행 상태 초기화

사용자가 2단계 챗봇 진단을 처음부터 다시 시작할 때 진행 상태를 초기화합니다.

**Request**

`DELETE /api/v1/chat/state`

Request Body 없음.

**Response `200 OK`**

```json
{
  "message": "챗봇 진행 상태가 초기화되었습니다."
}
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 401 | `로그인이 필요합니다.` | 인증 실패 |

## 기준 데이터 관리

### 기관 목록 조회

제도 운영 기관 목록을 조회합니다.

**Request**

`GET /api/web/agencies`

Request Body 없음.

**Response `200 OK`**

```json
[
  {
    "agency_id": 3,
    "agency_name": "서울시복지재단"
  }
]
```

### 기관 상세 조회

기관과 연결된 제도 목록을 조회합니다.

**Request**

`GET /api/web/agencies/{agency_id}`

| Path Variable | 타입 | 설명 |
| ------------- | ---- | ---- |
| agency_id | Integer | 조회할 기관 ID |

**Response `200 OK`**

```json
{
  "agency_id": 3,
  "agency_name": "서울시복지재단",
  "policies": [
    {
      "policy_id": 16,
      "policy_name": "가족돌봄청년 후원연계사업"
    }
  ]
}
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 404 | `존재하지 않는 기관입니다.` | 해당 `agency_id` 없음 |

### 제도 유형 목록 조회

관심 유형 선택, 제도 필터, 제도 상세의 유형 표시에서 사용합니다.

**Request**

`GET /api/web/policy-types`

Request Body 없음.

**Response `200 OK`**

```json
[
  {
    "policy_type_id": 1,
    "type_name": "생계·주거 지원"
  },
  {
    "policy_type_id": 2,
    "type_name": "돌봄·가사 지원"
  }
]
```

### 서류 목록 조회

서류 선택, 서류 이력 등록, 투두 연결에 사용할 서류 마스터 목록을 조회합니다.

**Request**

`GET /api/web/documents`

Request Body 없음.

**Response `200 OK`**

```json
[
  {
    "document_id": 1,
    "document_name": "가족관계증명서",
    "issuers": [
      {
        "document_issuer_id": 1,
        "issuer_name": "정부24",
        "issuer_site": "https://..."
      }
    ]
  }
]
```

### 서류 상세 조회

서류 발급처와 연결된 제도 목록을 조회합니다.

**Request**

`GET /api/web/documents/{document_id}`

| Path Variable | 타입 | 설명 |
| ------------- | ---- | ---- |
| document_id | Integer | 조회할 서류 ID |

**Response `200 OK`**

```json
{
  "document_id": 1,
  "document_name": "가족관계증명서",
  "issuers": [
    {
      "document_issuer_id": 1,
      "issuer_name": "정부24",
      "issuer_site": "https://..."
    }
  ],
  "policies": [
    {
      "policy_id": 5,
      "policy_name": "가족돌봄청년 자기돌봄비 지원"
    }
  ]
}
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 404 | `존재하지 않는 서류입니다.` | 해당 `document_id` 없음 |

## 관심 유형 관리

### 내 관심 유형 조회

로그인한 유저의 관심 제도 유형을 조회합니다.

**Request**

`GET /api/web/users/me/interest-policy-types`

Request Body 없음.

**Response `200 OK`**

```json
[
  {
    "interest_policy_type_id": 11,
    "policy_type_id": 1,
    "type_name": "생계·주거 지원"
  },
  {
    "interest_policy_type_id": 12,
    "policy_type_id": 3,
    "type_name": "의료·건강 지원"
  }
]
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 401 | `로그인이 필요합니다.` | 인증 실패 |

### 내 관심 유형 수정

로그인한 유저의 관심 제도 유형을 전체 교체합니다.

**Request**

`PATCH /api/web/users/me/interest-policy-types`

```json
{
  "interest_policy_type_ids": [1, 3]
}
```

| 필드 | 타입 | 필수 | DB 컬럼/테이블 | 설명 |
| ---- | ---- | ---- | -------------- | ---- |
| interest_policy_type_ids | Integer[] | Y | `interest_policy_types.policy_type_id` | 새 관심 유형 ID 목록 |

**Response `200 OK`**

```json
{
  "message": "관심 유형이 수정되었습니다.",
  "interest_policy_type_ids": [1, 3]
}
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 400 | `관심 제도 유형을 1개 이상 선택해주세요.` | 빈 목록 |
| 401 | `로그인이 필요합니다.` | 인증 실패 |
| 404 | `존재하지 않는 제도 유형입니다.` | 정책 유형 ID 없음 |

## 제도 관리

### 정책 응답 공통 필드

정책 목록, 카드, 상세 응답은 목적에 따라 일부 필드만 내려줄 수 있지만 필드명은 아래 기준을 사용합니다.

```json
{
  "policy_id": 16,
  "policy_name": "가족돌봄청년 후원연계사업",
  "agency_id": 3,
  "agency_name": "서울시복지재단",
  "support_period": "최대 12개월 / 240만원 (생애 1회)",
  "cost": "본인부담 없음",
  "summary": "가족돌봄청년에게 돌봄·생계 부담 완화를 지원합니다.",
  "application_method": "온라인 신청",
  "duration": null,
  "notes": "세부 조건은 공고문을 확인해 주세요.",
  "deadline_type": "고정일",
  "deadline_date_raw": "2025.9.12",
  "application_deadline": "2025-09-12T00:00:00+09:00",
  "result_note": "개별 안내",
  "link": "https://...",
  "contact": "02-1234-5678",
  "application_region": "서울시",
  "schedule_type": "상시",
  "age_min": 9,
  "age_max": 39,
  "exception_age": null,
  "income_criteria": "중위소득 100% 이하",
  "qualification_text": "서울시 거주 가족돌봄청년",
  "support_target": "가족을 돌보는 9~39세 청년",
  "duplication_restriction": "유사 사업 중복 지원 제한",
  "original_notice": "공고 원문 요약",
  "last_checked_at": "2026-07-21T00:00:00+09:00",
  "info_reference_year": 2026,
  "is_lifetime_limit_once": true,
  "result_date": "2026-08-15T00:00:00+09:00",
  "category": "YOUNG_CARER",
  "policy_types": [
    {
      "policy_type_id": 2,
      "type_name": "돌봄·가사 지원"
    }
  ]
}
```

### 제도 목록 조회

전체 제도 목록을 조회합니다. 관리자성 전체 목록이 아니라 웹에서 검색/필터에 사용할 읽기 전용 목록입니다.

**Request**

`GET /api/web/policies?category=YOUNG_CARER&policy_type_ids=1,2&agency_id=3&keyword=돌봄`

| Query | 타입 | 필수 | DB 컬럼/테이블 | 설명 |
| ----- | ---- | ---- | -------------- | ---- |
| category | String | N | `policies.category` | 제도 카테고리 |
| policy_type_ids | String | N | `connect_policy_policy_types.policy_type_id` | 제도 유형 ID 쉼표 목록 |
| agency_id | Integer | N | `policies.agency_id` | 기관 ID |
| keyword | String | N | `policies.policy_name`, `policies.summary` | 검색어 |

**Response `200 OK`**

```json
[
  {
    "policy_id": 16,
    "policy_name": "가족돌봄청년 후원연계사업",
    "agency_id": 3,
    "agency_name": "서울시복지재단",
    "summary": "가족돌봄청년에게 필요한 돌봄·생계 부담 완화 지원입니다.",
    "support_period": "최대 12개월 / 240만원 (생애 1회)",
    "application_deadline": "2025-09-12T00:00:00+09:00",
    "category": "YOUNG_CARER",
    "policy_types": [
      {
        "policy_type_id": 2,
        "type_name": "돌봄·가사 지원"
      }
    ]
  }
]
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 400 | `값이 올바르지 않습니다.` | Query 형식 오류 |

### 대안 복지 조회

가족돌봄청년에 해당하지 않거나, 회원가입/로그인을 나중에 하기로 한 사용자에게 관심 유형에 맞는 대안 복지 목록을 보여줍니다.

- 서버는 `category`를 `GENERAL`로 조회합니다.
- 로그인 여부와 무관하게 동일한 응답을 반환합니다.
- 저장 여부, 알림, 투두 같은 유저별 상태값은 포함하지 않습니다.

**Request**

`GET /api/web/policies/alternatives?interest_policy_type_ids=1,2`

| Query | 타입 | 필수 | DB 컬럼/테이블 | 설명 |
| ----- | ---- | ---- | -------------- | ---- |
| interest_policy_type_ids | String | Y | `policy_types.policy_type_id` | 관심 유형 ID를 쉼표로 구분한 값 |

**Response `200 OK`**

```json
[
  {
    "policy_id": 12,
    "policy_name": "보건복지부 일상돌봄서비스",
    "agency_id": 1,
    "agency_name": "보건복지부",
    "summary": "전국 공통 신청 가능",
    "support_period": "상시",
    "application_deadline": null,
    "link": "https://...",
    "category": "GENERAL",
    "policy_types": [
      {
        "policy_type_id": 2,
        "type_name": "돌봄·가사 지원"
      }
    ]
  }
]
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 400 | `조회 조건 (interest_policy_type_ids) 이 필요합니다.` | `interest_policy_type_ids` 누락 |
| 400 | `값이 올바르지 않습니다.` | `interest_policy_type_ids` 형식 오류 |

### ID 기반 제도 카드 조회

챗봇 매칭 직후 카드 렌더링에 필요한 제도 정보를 ID 목록으로 조회합니다.

- `support_period`는 파싱하지 않고 그대로 출력합니다.
- 챗봇 문서의 `field`는 `policy_types[].type_name`으로 대체합니다.

**Request**

`GET /api/v1/policies?ids=2,7,16`

| Query | 타입 | 필수 | 설명 |
| ----- | ---- | ---- | ---- |
| ids | String | Y | `policy_id`를 쉼표로 구분한 값 |

**Response `200 OK`**

```json
{
  "policies": [
    {
      "policy_id": 16,
      "policy_name": "가족돌봄청년 후원연계사업",
      "agency_id": 3,
      "agency_name": "서울시복지재단",
      "summary": "가족돌봄청년에게 필요한 돌봄·생계 부담 완화 지원입니다.",
      "support_period": "최대 12개월 / 240만원 (생애 1회)",
      "deadline_type": "고정일",
      "deadline_date_raw": "2025.9.12",
      "application_deadline": "2025-09-12T00:00:00+09:00",
      "link": "https://...",
      "category": "YOUNG_CARER",
      "policy_types": [
        {
          "policy_type_id": 2,
          "type_name": "돌봄·가사 지원"
        }
      ]
    }
  ]
}
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 400 | `조회 조건 (ids) 이 필요합니다.` | `ids` 누락 |
| 400 | `값이 올바르지 않습니다.` | `ids` 형식 오류 |

### 제도번역기

화면에 떠 있는 제도 하나를 AI가 쉬운 말로 풀어서 설명합니다.

- 1차 버전은 고정된 풀이 텍스트 하나만 반환합니다.
- `explanation`은 마크다운이나 특수 포맷 없는 순수 텍스트입니다.
- 호출마다 AI가 새로 생성하므로 응답까지 1~3초 정도 소요될 수 있습니다.
- 같은 `policy_id`를 여러 번 호출해도 문구가 조금씩 달라질 수 있습니다. 1차 버전은 캐싱하지 않습니다.

**Request**

`POST /api/v1/policies/{policy_id}/translate`

| Path Variable | 타입 | 설명 |
| ------------- | ---- | ---- |
| policy_id | Integer | 풀이를 원하는 제도 ID |

Request Body 없음. 빈 body 또는 `{}` 전송.

**Response `200 OK`**

```json
{
  "policy_id": 16,
  "explanation": "이 제도는 만 19세부터 39세까지의 청년이라면 누구나 신청할 수 있는 문화생활 지원 사업이에요. 소득 조건 없이 신청 가능하고, 연 1회 지원금을 문화 콘텐츠(공연, 전시, 도서 등) 구매에 쓸 수 있어요. 별도 서류 없이 온라인 신청만으로 접수돼요."
}
```

| 필드 | 타입 | 설명 |
| ---- | ---- | ---- |
| policy_id | Integer | 요청한 제도 ID |
| explanation | String | AI가 풀어 쓴 제도 설명. 3~5문장 |

**Errors**

`404 Not Found`

```json
{
  "error": "POLICY_NOT_FOUND",
  "message": "해당 제도를 찾을 수 없습니다."
}
```

## 맞춤 지원 제도 관리

### 맞춤 지원 제도 목록 조회

로그인한 유저의 2단계 챗봇 매칭 결과를 바탕으로 가족돌봄청년 전용 제도 목록을 유형별로 그룹핑해 조회합니다.

- 파라미터 없이 로그인한 유저 기준으로 조회합니다.
- 매칭된 제도가 있는 유형만 응답에 포함합니다.
- 매칭된 제도가 없으면 `[]`를 반환합니다.
- 웹은 같은 `policy_id`를 여러 유형 그룹에 중복 표시하지 않습니다.
- 제도 하나가 여러 유형을 가질 경우 백엔드는 대표 유형 그룹 1곳에만 포함합니다. 대표 유형은 유저의 `interest_policy_types` 순서를 우선하고, 해당하지 않으면 가장 작은 `policy_type_id`를 사용합니다.
- 카드에서 제도의 전체 유형이 필요하면 각 정책의 `policy_types` 배열을 사용합니다.
- `match_group`은 한글 문자열 `적합`, `확인_불가` 그대로 내려줍니다. 웹은 이를 배지로 구분 표시하고, 백엔드는 같은 그룹 안에서 `적합`을 먼저, `확인_불가`를 뒤에 정렬합니다.
- `matched_policy`에는 `적합`, `확인_불가`만 저장합니다. 이를 위해 `matched_policy.match_group` 컬럼 추가가 필요합니다.

**Request**

`GET /api/web/policies/matched`

Request Body 없음.

**Response `200 OK`**

```json
[
  {
    "policy_type_id": 2,
    "type_name": "돌봄·가사 지원",
    "policies": [
      {
        "matched_policy_id": 101,
        "policy_id": 12,
        "match_group": "적합",
        "was_benefited": false,
        "policy_name": "보건복지부 일상돌봄서비스",
        "agency_id": 1,
        "agency_name": "보건복지부",
        "summary": "전국 공통 신청 가능",
        "support_period": "상시",
        "application_deadline": null,
        "link": "https://...",
        "policy_types": [
          {
            "policy_type_id": 2,
            "type_name": "돌봄·가사 지원"
          },
          {
            "policy_type_id": 3,
            "type_name": "의료·건강 지원"
          }
        ]
      }
    ]
  }
]
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 401 | `로그인이 필요합니다.` | 인증 실패 |

### 매칭 제도 수혜 여부 수정

추천된 제도를 이미 이용했거나 이용하지 않았는지 기록합니다.

**Request**

`PATCH /api/web/users/me/matched-policies/{matched_policy_id}`

| Path Variable | 타입 | 설명 |
| ------------- | ---- | ---- |
| matched_policy_id | Integer | 수정할 매칭 제도 ID |

```json
{
  "was_benefited": true
}
```

| 필드 | 타입 | 필수 | DB 컬럼 | 설명 |
| ---- | ---- | ---- | ------- | ---- |
| was_benefited | Boolean | Y | `matched_policy.was_benefited` | 수혜 여부 |

**Response `200 OK`**

```json
{
  "matched_policy_id": 101,
  "was_benefited": true,
  "message": "수혜 여부가 수정되었습니다."
}
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 400 | `값이 누락되었습니다.` | `was_benefited` 누락 |
| 401 | `로그인이 필요합니다.` | 인증 실패 |
| 404 | `존재하지 않는 매칭 제도입니다.` | 매칭 제도 없음 또는 본인 소유 아님 |

### 제도 상세 조회

제도 상세 화면에 필요한 정보를 `policy_id`로 조회합니다.

**Request**

`GET /api/web/policies/{policy_id}`

| Path Variable | 타입 | 설명 |
| ------------- | ---- | ---- |
| policy_id | Integer | 조회할 제도 ID |

**Response `200 OK`**

```json
{
  "policy_id": 5,
  "policy_name": "가족돌봄청년 자기돌봄비 지원",
  "agency_id": 3,
  "agency_name": "서울시",
  "support_period": "2026.01.23 ~ 2026.01.28",
  "cost": "본인부담 없음",
  "summary": "서울시 가족돌봄청년 대상 자기돌봄비 지원",
  "application_method": "서울복지포털에서 온라인 신청",
  "duration": null,
  "notes": "예산 소진 시 조기 종료될 수 있습니다.",
  "deadline_type": "고정일",
  "deadline_date_raw": "2026.01.28",
  "application_deadline": "2026-01-28T00:00:00+09:00",
  "result_note": "개별 문자 안내",
  "link": "https://...",
  "contact": "02-1234-5678",
  "application_region": "서울시",
  "schedule_type": "모집형",
  "age_min": 9,
  "age_max": 39,
  "exception_age": null,
  "income_criteria": "중위소득 100% 이하",
  "qualification_text": "서울시 거주 가족돌봄청년",
  "support_target": "가족을 돌보는 청년",
  "duplication_restriction": "유사 사업 중복 지원 제한",
  "original_notice": "공고 원문 요약",
  "last_checked_at": "2026-07-21T00:00:00+09:00",
  "info_reference_year": 2026,
  "is_lifetime_limit_once": true,
  "result_date": "2026-08-15T00:00:00+09:00",
  "category": "YOUNG_CARER",
  "policy_types": [
    {
      "policy_type_id": 2,
      "type_name": "돌봄·가사 지원"
    }
  ],
  "documents": [
    {
      "document_id": 1,
      "document_name": "가족관계증명서",
      "issuers": [
        {
          "document_issuer_id": 1,
          "issuer_name": "정부24",
          "issuer_site": "https://..."
        }
      ]
    }
  ]
}
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 404 | `존재하지 않는 제도입니다.` | 해당 `policy_id` 없음 |

## 저장 제도 관리

### 제도 저장

제도 상세 화면의 `[마감일 알림 받기]` 버튼을 눌렀을 때 제도를 저장합니다.

- 저장 성공 시 서버가 해당 제도의 필요 서류를 조회해 `todos`를 자동 생성합니다.

**Request**

`POST /api/web/users/me/saved-policies`

```json
{
  "policy_id": 5
}
```

| 필드 | 타입 | 필수 | DB 컬럼 | 설명 |
| ---- | ---- | ---- | ------- | ---- |
| policy_id | Integer | Y | `saved_policies.policy_id` | 저장할 제도 ID |

**Response `200 OK`**

```json
{
  "saved_policy_id": 42,
  "policy_id": 5,
  "message": "제도가 저장되었습니다."
}
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 400 | `값이 누락되었습니다.` | `policy_id` 누락 |
| 401 | `로그인이 필요합니다.` | 인증 실패 |
| 404 | `존재하지 않는 제도입니다.` | 해당 `policy_id` 없음 |
| 409 | `이미 저장한 제도입니다.` | 이미 저장한 제도 |

### 저장한 제도 목록 조회

로그인한 유저가 저장한 제도 목록을 조회합니다. 웹의 `내가 선택한 제도` 섹션에서 사용합니다.

- 웹 저장 목록은 카드 표시와 상세 진입에 필요한 제도 정보만 반환합니다.
- `saved_policies.applied`, `todos.is_checked`는 앱 투두/캘린더 전용 흐름에서만 사용하므로 웹 응답에 포함하지 않습니다.

**Request**

`GET /api/web/users/me/saved-policies`

Request Body 없음.

**Response `200 OK`**

```json
[
  {
    "saved_policy_id": 42,
    "policy_id": 5,
    "policy_name": "가족돌봄청년 자기돌봄비 지원",
    "agency_id": 3,
    "agency_name": "서울시",
    "summary": "서울시 청년 대상",
    "support_period": "2026.01.23 ~ 2026.01.28",
    "application_deadline": "2026-01-28T00:00:00+09:00",
    "link": "https://...",
    "policy_types": [
      {
        "policy_type_id": 2,
        "type_name": "돌봄·가사 지원"
      }
    ],
    "documents": [
      {
        "document_id": 1,
        "document_name": "가족관계증명서",
        "issuers": [
          {
            "document_issuer_id": 1,
            "issuer_name": "정부24",
            "issuer_site": "https://..."
          }
        ]
      }
    ]
  }
]
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 401 | `로그인이 필요합니다.` | 인증 실패 |

### 제도 저장 취소

저장한 제도를 취소합니다. `saved_policy_id`가 로그인한 본인의 저장 항목인지 확인 후 삭제합니다.

- 저장 취소 시 해당 저장 항목에 연결된 `todos`, `notifications`도 함께 삭제합니다.

**Request**

`DELETE /api/web/users/me/saved-policies/{saved_policy_id}`

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

## 서류 이력 관리

### 내 서류 이력 조회

로그인한 유저가 이미 발급했거나 보유한다고 말한 서류 이력을 조회합니다.

**Request**

`GET /api/web/users/me/document-history`

Request Body 없음.

**Response `200 OK`**

```json
[
  {
    "history_id": 7,
    "carer_id": 1,
    "document_id": 1,
    "document_name": "가족관계증명서",
    "policy_id": 5,
    "policy_name": "가족돌봄청년 자기돌봄비 지원",
    "issued_date": "2026-07-01T00:00:00+09:00",
    "valid_until": "2026-10-01",
    "direct_utter": true,
    "confirmed_by_user": true,
    "created_at": "2026-07-21T15:49:07+09:00"
  }
]
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 401 | `로그인이 필요합니다.` | 인증 실패 |

### 서류 이력 저장

챗봇 또는 서류 확인 화면에서 유저가 보유 서류를 직접 말하거나 확인했을 때 저장합니다.

**Request**

`POST /api/web/users/me/document-history`

```json
{
  "document_id": 1,
  "policy_id": 5,
  "issued_date": "2026-07-01T00:00:00+09:00",
  "valid_until": "2026-10-01",
  "direct_utter": true,
  "confirmed_by_user": true
}
```

| 필드 | 타입 | 필수 | DB 컬럼 | 설명 |
| ---- | ---- | ---- | ------- | ---- |
| document_id | Integer | Y | `user_document_history.document_id` | 서류 ID |
| policy_id | Integer | Y | `user_document_history.policy_id` | 관련 제도 ID |
| issued_date | String | N | `user_document_history.issued_date` | 발급일 |
| valid_until | String | N | `user_document_history.valid_until` | 유효기간 또는 사용자 표현 |
| direct_utter | Boolean | N | `user_document_history.direct_utter` | 사용자가 직접 말했는지 여부 |
| confirmed_by_user | Boolean | N | `user_document_history.confirmed_by_user` | 사용자 확인 여부 |

**Response `201 Created`**

```json
{
  "history_id": 7,
  "message": "서류 이력이 저장되었습니다."
}
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 400 | `값이 누락되었습니다.` | `document_id` 또는 `policy_id` 누락 |
| 401 | `로그인이 필요합니다.` | 인증 실패 |
| 404 | `존재하지 않는 서류입니다.` | 해당 `document_id` 없음 |
| 404 | `존재하지 않는 제도입니다.` | 해당 `policy_id` 없음 |

### 서류 이력 수정

저장된 서류 이력의 발급일, 유효기간, 확인 여부를 수정합니다.

**Request**

`PATCH /api/web/users/me/document-history/{history_id}`

| Path Variable | 타입 | 설명 |
| ------------- | ---- | ---- |
| history_id | Integer | 수정할 서류 이력 ID |

```json
{
  "issued_date": "2026-07-02T00:00:00+09:00",
  "valid_until": "2026-10-02",
  "confirmed_by_user": true
}
```

**Response `200 OK`**

```json
{
  "history_id": 7,
  "message": "서류 이력이 수정되었습니다."
}
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 400 | `값이 올바르지 않습니다.` | 타입 또는 날짜 형식 오류 |
| 401 | `로그인이 필요합니다.` | 인증 실패 |
| 404 | `존재하지 않는 서류 이력입니다.` | 이력 없음 또는 본인 소유 아님 |

### 서류 이력 삭제

잘못 저장된 서류 이력을 삭제합니다.

**Request**

`DELETE /api/web/users/me/document-history/{history_id}`

| Path Variable | 타입 | 설명 |
| ------------- | ---- | ---- |
| history_id | Integer | 삭제할 서류 이력 ID |

**Response `200 OK`**

```json
{
  "message": "서류 이력이 삭제되었습니다."
}
```

**Errors**

| Status | message | 조건 |
| ------ | ------- | ---- |
| 401 | `로그인이 필요합니다.` | 인증 실패 |
| 404 | `존재하지 않는 서류 이력입니다.` | 이력 없음 또는 본인 소유 아님 |
