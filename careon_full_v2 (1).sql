-- ============================================================
-- CareOn PostgreSQL 스키마 v2
-- 변경사항(2026-07-21 논의 반영):
--  1) category ENUM(GENERAL/YOUNG_CARER) 제거, policy_type_id 단일 FK 제거
--     -> policy_types(4유형 고정) + connect_policy_policy_types(다대다) 로 교체
--  2) policies.deadline_date_raw TEXT 컬럼 추가 (원문 보존, application_deadline은 파싱 가능한 것만)
--  3) result_date 대신 result_note(VARCHAR 255)에 결과발표 원문 저장
--  4) required_documents 원문을 documents/connect_policy_documents 테이블로 행 분리
--  5) is_lifetime_limit_once BOOLEAN: '생애' 문구 감지해 자동 채움
--  6) policies.external_ref VARCHAR(20) 추가: 원본 institution_id(INST-XXXX) 보존
-- ============================================================

-- ---------- ENUM 타입 ----------
CREATE TYPE notification_type_enum AS ENUM ('DEADLINE_D7','DEADLINE_D3','DEADLINE_D1','RESULT_DDAY');

-- ---------- 1. agencies ----------
CREATE TABLE agencies (
  agency_id   SERIAL PRIMARY KEY,
  agency_name VARCHAR(100)
);

-- ---------- 2. policy_types (4유형 고정값) ----------
CREATE TABLE policy_types (
  policy_type_id SERIAL PRIMARY KEY,
  type_name      VARCHAR(50)
);

-- ---------- 3. documents ----------
CREATE TABLE documents (
  document_id   SERIAL PRIMARY KEY,
  document_name VARCHAR(100)
);

-- ---------- 4. document_issuers ----------
CREATE TABLE document_issuers (
  document_issuer_id SERIAL PRIMARY KEY,
  issuer_name         VARCHAR(100),
  issuer_site         VARCHAR(500)
);

-- ---------- 5. document_issues ----------
CREATE TABLE document_issues (
  document_issue_id  SERIAL PRIMARY KEY,
  document_id        INT NOT NULL REFERENCES documents(document_id),
  document_issuer_id  INT NOT NULL REFERENCES document_issuers(document_issuer_id)
);

-- ---------- 6. policies (수정됨: category/policy_type_id 제거, 신규 컬럼 추가) ----------
CREATE TABLE policies (
  policy_id               SERIAL PRIMARY KEY,
  external_ref            VARCHAR(20),               -- 원본 institution_id (예: INST-0001)
  policy_name             VARCHAR(255),
  agency_id               INT NOT NULL REFERENCES agencies(agency_id),
  support_period          VARCHAR(100),
  cost                    VARCHAR(100),              -- self_pay_amount
  summary                 VARCHAR(255),
  application_method      VARCHAR(255),
  duration                VARCHAR(100),
  notes                   TEXT,
  deadline_type           VARCHAR(50),
  deadline_date_raw       TEXT,                      -- 원문 그대로 보존 (신규)
  application_deadline    TIMESTAMP,                 -- 파싱 가능한 경우만 채움
  result_note             VARCHAR(255),              -- 결과발표 원문 (result_date 대신 사용)
  link                    VARCHAR(500),
  contact                 VARCHAR(100),
  application_region      VARCHAR(30),
  schedule_type           VARCHAR(20),
  age_min                 INT,
  age_max                 INT,
  exception_age           VARCHAR(100),
  income_criteria         VARCHAR(100),
  qualification_text      TEXT,
  support_target          TEXT,
  duplication_restriction TEXT,
  original_notice         TEXT,
  last_checked_at         TIMESTAMP,
  info_reference_year     INT,
  is_lifetime_limit_once  BOOLEAN
);

-- ---------- 7. connect_policy_policy_types (신규, 다대다) ----------
CREATE TABLE connect_policy_policy_types (
  connect_policy_policy_type_id SERIAL PRIMARY KEY,
  policy_id      INT NOT NULL REFERENCES policies(policy_id),
  policy_type_id INT NOT NULL REFERENCES policy_types(policy_type_id)
);

-- ---------- 8. connect_policy_documents ----------
CREATE TABLE connect_policy_documents (
  connect_policy_document_id SERIAL PRIMARY KEY,
  policy_id   INT NOT NULL REFERENCES policies(policy_id),
  document_id INT NOT NULL REFERENCES documents(document_id)
);

-- ---------- 9. carers ----------
CREATE TABLE carers (
  carer_id                        SERIAL PRIMARY KEY,
  name                            VARCHAR(50),
  email                           VARCHAR(255),
  password                        VARCHAR(255),
  region                          VARCHAR(20),
  terms_agreed                    BOOLEAN,
  install_prompt_count            INT,
  app_installed                   BOOLEAN,
  notification_enabled            BOOLEAN,
  reset_token                     VARCHAR(255),
  reset_token_expires_at          TIMESTAMP,
  diagnosis_completed             BOOLEAN,
  refresh_token                   VARCHAR(255),
  refresh_token_expires_at        TIMESTAMP,
  age                             INT,
  household_members_count         INT,
  cared_count                     INT,
  know_housing_type               VARCHAR(30),
  housing_type                    VARCHAR(50),
  biggest_burden_type             VARCHAR(100),
  burden_type_reason_summary      VARCHAR(500),
  daily_care_hours_self           VARCHAR(30),
  daily_care_hours_household      VARCHAR(30),
  has_backup_caregiver            BOOLEAN,
  backup_caregiver_relation       VARCHAR(50),
  medical_burden_level            VARCHAR(10),
  is_student                      BOOLEAN,
  has_income_activity             BOOLEAN,
  income_value_status             VARCHAR(50),
  income_assessment_criteria      VARCHAR(50),
  income_value                    INT,
  median_income_ratio             VARCHAR(30),
  income_variability_type         VARCHAR(50),
  income_related_utterance        TEXT,
  has_basic_livelihood_support        BOOLEAN,
  has_basic_livelihood_support_source  BOOLEAN,
  has_cha_sang_wi                 BOOLEAN,
  has_cha_sang_wi_source          BOOLEAN,
  military_service_status         VARCHAR(30),
  military_service_extension_years INT,
  housing_deposit                 INT,
  housing_monthly_rent            INT,
  household_asset_value           INT,
  vehicle_value                   INT,
  financial_detail_status         VARCHAR(30),
  created_at                      TIMESTAMP DEFAULT now(),
  updated_at                      TIMESTAMP DEFAULT now()
);

-- ---------- 10. cared ----------
CREATE TABLE cared (
  cared_id           SERIAL PRIMARY KEY,
  carer_id           INT NOT NULL REFERENCES carers(carer_id),
  cared_relation     VARCHAR(30),
  age                INT,
  condition_summary  TEXT,
  severity_level     VARCHAR(20),
  created_at         TIMESTAMP DEFAULT now(),
  updated_at         TIMESTAMP DEFAULT now()
);

-- ---------- 11. saved_policies ----------
CREATE TABLE saved_policies (
  saved_policy_id SERIAL PRIMARY KEY,
  carer_id        INT NOT NULL REFERENCES carers(carer_id),
  policy_id       INT NOT NULL REFERENCES policies(policy_id)
);

-- ---------- 12. todos ----------
CREATE TABLE todos (
  todo_id         SERIAL PRIMARY KEY,
  saved_policy_id INT NOT NULL REFERENCES saved_policies(saved_policy_id),
  document_id     INT NOT NULL REFERENCES documents(document_id),
  is_checked      BOOLEAN
);

-- ---------- 13. notifications ----------
CREATE TABLE notifications (
  notification_id   SERIAL PRIMARY KEY,
  saved_policy_id   INT NOT NULL REFERENCES saved_policies(saved_policy_id),
  notification_type notification_type_enum,
  sent_at           TIMESTAMP,
  is_read           BOOLEAN
);

-- ---------- 14. matched_policy ----------
CREATE TABLE matched_policy (
  matched_policy_id SERIAL PRIMARY KEY,
  carer_id          INT NOT NULL REFERENCES carers(carer_id),
  policy_id         INT NOT NULL REFERENCES policies(policy_id),
  was_benefited     BOOLEAN,
  created_at        TIMESTAMP DEFAULT now(),
  updated_at        TIMESTAMP DEFAULT now()
);

-- ---------- 15. interest_policy_types ----------
CREATE TABLE interest_policy_types (
  interest_policy_type_id SERIAL PRIMARY KEY,
  carer_id       INT NOT NULL REFERENCES carers(carer_id),
  policy_type_id INT NOT NULL REFERENCES policy_types(policy_type_id)
);

-- ---------- 16. user_document_history ----------
CREATE TABLE user_document_history (
  history_id       SERIAL PRIMARY KEY,
  carer_id         INT NOT NULL REFERENCES carers(carer_id),
  document_id      INT NOT NULL REFERENCES documents(document_id),
  policy_id        INT NOT NULL REFERENCES policies(policy_id),
  issued_date      TIMESTAMP,
  valid_until      VARCHAR(30),
  direct_utter     BOOLEAN,
  confirmed_by_user BOOLEAN,
  created_at       TIMESTAMP DEFAULT now()
);

-- ---------- 17. user_conversation_state ----------
CREATE TABLE user_conversation_state (
  conversation_state_id SERIAL PRIMARY KEY,
  current_phase   INT,
  active_policy_id INT NOT NULL REFERENCES policies(policy_id),
  updated_at      TIMESTAMP,
  carer_id        INT NOT NULL REFERENCES carers(carer_id)
);

-- ---------- 18. carer_income_signal ----------
CREATE TABLE carer_income_signal (
  signal_id               SERIAL PRIMARY KEY,
  carer_id                INT NOT NULL REFERENCES carers(carer_id),
  signal_type             VARCHAR(100),
  raw_value               TEXT,
  parsed_value            INT,
  source                  VARCHAR(50),
  confidence              VARCHAR(20),
  contradicts_signal_id   INT,
  contradiction_resolved  BOOLEAN,
  created_at              TIMESTAMP DEFAULT now()
);
-- ============================================================
-- CareOn 57개 제도 실데이터 INSERT
-- ============================================================

-- ---------- policy_types (4유형 고정) ----------
INSERT INTO policy_types (policy_type_id, type_name) VALUES (1, '돌봄가사');
INSERT INTO policy_types (policy_type_id, type_name) VALUES (2, '의료건강');
INSERT INTO policy_types (policy_type_id, type_name) VALUES (3, '심리청년특화');
INSERT INTO policy_types (policy_type_id, type_name) VALUES (4, '생계주거');

-- ---------- agencies (고유 기관 53개) ----------
INSERT INTO agencies (agency_id, agency_name) VALUES (1, '각 자치구 동주민센터 / 통합돌봄과 등');
INSERT INTO agencies (agency_id, agency_name) VALUES (2, '각 자치구 복지 부서');
INSERT INTO agencies (agency_id, agency_name) VALUES (3, '강남구 정신건강복지센터');
INSERT INTO agencies (agency_id, agency_name) VALUES (4, '강남구청 일자리정책과');
INSERT INTO agencies (agency_id, agency_name) VALUES (5, '강동구청 일자리정책과 청년정책팀');
INSERT INTO agencies (agency_id, agency_name) VALUES (6, '강북구청');
INSERT INTO agencies (agency_id, agency_name) VALUES (7, '강북구청 일자리청년과 청년정책팀');
INSERT INTO agencies (agency_id, agency_name) VALUES (8, '강서구청 일자리정책과 청년지원팀');
INSERT INTO agencies (agency_id, agency_name) VALUES (9, '관악구청');
INSERT INTO agencies (agency_id, agency_name) VALUES (10, '관악구청 청년정책과');
INSERT INTO agencies (agency_id, agency_name) VALUES (11, '광진구 일자리청년과');
INSERT INTO agencies (agency_id, agency_name) VALUES (12, '광진구 주택과');
INSERT INTO agencies (agency_id, agency_name) VALUES (13, '광진구1인가구지원센터');
INSERT INTO agencies (agency_id, agency_name) VALUES (14, '구로구청 일자리지원과');
INSERT INTO agencies (agency_id, agency_name) VALUES (15, '국토교통부 (접수: 각 자치구 동주민센터)');
INSERT INTO agencies (agency_id, agency_name) VALUES (16, '국토교통부 / 각 자치구청');
INSERT INTO agencies (agency_id, agency_name) VALUES (17, '금천구청');
INSERT INTO agencies (agency_id, agency_name) VALUES (18, '금천구청 일자리청년과 청년동행팀');
INSERT INTO agencies (agency_id, agency_name) VALUES (19, '노원구청');
INSERT INTO agencies (agency_id, agency_name) VALUES (20, '도봉구청');
INSERT INTO agencies (agency_id, agency_name) VALUES (21, '동대문구청 청년정책고용과');
INSERT INTO agencies (agency_id, agency_name) VALUES (22, '동작구청 청년청소년과');
INSERT INTO agencies (agency_id, agency_name) VALUES (23, '마포구청');
INSERT INTO agencies (agency_id, agency_name) VALUES (24, '서대문구 청년정책과');
INSERT INTO agencies (agency_id, agency_name) VALUES (25, '서울시 (관할 동주민센터 및 보건소)');
INSERT INTO agencies (agency_id, agency_name) VALUES (26, '서울시 (서울주택도시개발공사 청년월세지원센터)');
INSERT INTO agencies (agency_id, agency_name) VALUES (27, '서울시 / 각 자치구');
INSERT INTO agencies (agency_id, agency_name) VALUES (28, '서울시 돌봄고독정책관 1인가구지원과');
INSERT INTO agencies (agency_id, agency_name) VALUES (29, '서울시 돌봄복지과');
INSERT INTO agencies (agency_id, agency_name) VALUES (30, '서울시 문화정책과');
INSERT INTO agencies (agency_id, agency_name) VALUES (31, '서울시 미래청년기획관 청년사업담당관');
INSERT INTO agencies (agency_id, agency_name) VALUES (32, '서울시 복지정책과');
INSERT INTO agencies (agency_id, agency_name) VALUES (33, '서울시 주택정책과');
INSERT INTO agencies (agency_id, agency_name) VALUES (34, '서울시복지재단');
INSERT INTO agencies (agency_id, agency_name) VALUES (35, '서울청년기지개센터');
INSERT INTO agencies (agency_id, agency_name) VALUES (36, '서울청년센터 금천 청춘삘딩');
INSERT INTO agencies (agency_id, agency_name) VALUES (37, '서울특별시 미래청년기획관 청년사업담당관');
INSERT INTO agencies (agency_id, agency_name) VALUES (38, '서초구 아동청년과');
INSERT INTO agencies (agency_id, agency_name) VALUES (39, '서초구청 사회복지과');
INSERT INTO agencies (agency_id, agency_name) VALUES (40, '성동구청');
INSERT INTO agencies (agency_id, agency_name) VALUES (41, '성동구청 아동청년과');
INSERT INTO agencies (agency_id, agency_name) VALUES (42, '성북구청 일자리정책과 청년지원팀');
INSERT INTO agencies (agency_id, agency_name) VALUES (43, '송파구청');
INSERT INTO agencies (agency_id, agency_name) VALUES (44, '양천구청');
INSERT INTO agencies (agency_id, agency_name) VALUES (45, '영등포구청');
INSERT INTO agencies (agency_id, agency_name) VALUES (46, '용산구 일자리정책담당관');
INSERT INTO agencies (agency_id, agency_name) VALUES (47, '은평구');
INSERT INTO agencies (agency_id, agency_name) VALUES (48, '은평구청 가족정책과');
INSERT INTO agencies (agency_id, agency_name) VALUES (49, '은평구청 청장년희망과 청년미래팀');
INSERT INTO agencies (agency_id, agency_name) VALUES (50, '종로구청 일자리정책과');
INSERT INTO agencies (agency_id, agency_name) VALUES (51, '중구청');
INSERT INTO agencies (agency_id, agency_name) VALUES (52, '중랑구청 지역경제과 청년지원팀');
INSERT INTO agencies (agency_id, agency_name) VALUES (53, '한국산업인력공단');

-- ---------- documents (고유 서류명 100개) ----------
INSERT INTO documents (document_id, document_name) VALUES (1, '(맞벌이) 재직증명서 또는 건강보험납입증명서');
INSERT INTO documents (document_id, document_name) VALUES (2, '(해당 시) 가족관계증명서');
INSERT INTO documents (document_id, document_name) VALUES (3, '(해당시) 근로계약서');
INSERT INTO documents (document_id, document_name) VALUES (4, '(해당시) 돌봄가족에 대한 경제적 지원 증명서류');
INSERT INTO documents (document_id, document_name) VALUES (5, '(해당시) 한부모가족증명서');
INSERT INTO documents (document_id, document_name) VALUES (6, '가입신청서');
INSERT INTO documents (document_id, document_name) VALUES (7, '가족관계증명서');
INSERT INTO documents (document_id, document_name) VALUES (8, '가족관계증명서(부모기준)');
INSERT INTO documents (document_id, document_name) VALUES (9, '개인정보 동의서');
INSERT INTO documents (document_id, document_name) VALUES (10, '개인정보 수집·이용 동의서');
INSERT INTO documents (document_id, document_name) VALUES (11, '개인정보 처리 동의서');
INSERT INTO documents (document_id, document_name) VALUES (12, '건강검진확인서 등');
INSERT INTO documents (document_id, document_name) VALUES (13, '건강보험료 납부확인서(문화힐링비)');
INSERT INTO documents (document_id, document_name) VALUES (14, '건강보험료납부확인서 (무료 이용자만)');
INSERT INTO documents (document_id, document_name) VALUES (15, '건강보험자격득실확인서');
INSERT INTO documents (document_id, document_name) VALUES (16, '건강보험자격확인서');
INSERT INTO documents (document_id, document_name) VALUES (17, '건보료 납부확인서');
INSERT INTO documents (document_id, document_name) VALUES (18, '결제 영수증');
INSERT INTO documents (document_id, document_name) VALUES (19, '결제 영수증 등');
INSERT INTO documents (document_id, document_name) VALUES (20, '결제영수증 등');
INSERT INTO documents (document_id, document_name) VALUES (21, '결제영수증(매출전표)');
INSERT INTO documents (document_id, document_name) VALUES (22, '경제활동 증명서 등 중 1개)');
INSERT INTO documents (document_id, document_name) VALUES (23, '고용보험 피보험 이력내역서');
INSERT INTO documents (document_id, document_name) VALUES (24, '고용보험내역서');
INSERT INTO documents (document_id, document_name) VALUES (25, '고용임금확인서 (지정 양식)');
INSERT INTO documents (document_id, document_name) VALUES (26, '공고문 참조');
INSERT INTO documents (document_id, document_name) VALUES (27, '공공·민간기관 추천서');
INSERT INTO documents (document_id, document_name) VALUES (28, '근로계약서 등)');
INSERT INTO documents (document_id, document_name) VALUES (29, '근로계약서(해당시)');
INSERT INTO documents (document_id, document_name) VALUES (30, '근로활동/소득신고서');
INSERT INTO documents (document_id, document_name) VALUES (31, '기초수급');
INSERT INTO documents (document_id, document_name) VALUES (32, '단기근로 증빙서류(해당자)');
INSERT INTO documents (document_id, document_name) VALUES (33, '단기근로계약서(해당시)');
INSERT INTO documents (document_id, document_name) VALUES (34, '대학 재학증명서 또는 초본');
INSERT INTO documents (document_id, document_name) VALUES (35, '돌봄공백 시 진단서·소견서 등');
INSERT INTO documents (document_id, document_name) VALUES (36, '돌봄필요 증명서(진단서');
INSERT INTO documents (document_id, document_name) VALUES (37, '동의서');
INSERT INTO documents (document_id, document_name) VALUES (38, '등본');
INSERT INTO documents (document_id, document_name) VALUES (39, '미취업 상태 확인용 사실증명 등');
INSERT INTO documents (document_id, document_name) VALUES (40, '별도 서류 제출 불필요 (공공마이데이터 동의 처리)');
INSERT INTO documents (document_id, document_name) VALUES (41, '병적증명서 또는 주민등록초본 (모든 서류 원본 스캔본 업로드)');
INSERT INTO documents (document_id, document_name) VALUES (42, '보증료 납부 영수증 등');
INSERT INTO documents (document_id, document_name) VALUES (43, '보호종료확인서 등');
INSERT INTO documents (document_id, document_name) VALUES (44, '본인 명의 통장사본');
INSERT INTO documents (document_id, document_name) VALUES (45, '부정수급방지확약서');
INSERT INTO documents (document_id, document_name) VALUES (46, '사실증명');
INSERT INTO documents (document_id, document_name) VALUES (47, '사실증명 등 (주민번호 뒷자리 가림)');
INSERT INTO documents (document_id, document_name) VALUES (48, '사실증명(사업자등록사실여부)');
INSERT INTO documents (document_id, document_name) VALUES (49, '사업자등록증 등');
INSERT INTO documents (document_id, document_name) VALUES (50, '사회보장급여 신청(변경)서');
INSERT INTO documents (document_id, document_name) VALUES (51, '성적표');
INSERT INTO documents (document_id, document_name) VALUES (52, '시험 응시 및 결제 영수증');
INSERT INTO documents (document_id, document_name) VALUES (53, '신분증');
INSERT INTO documents (document_id, document_name) VALUES (54, '신청서');
INSERT INTO documents (document_id, document_name) VALUES (55, '신청서 및 증빙서류 (공무원 시험 등 자격증 미발급 시험 제외)');
INSERT INTO documents (document_id, document_name) VALUES (56, '신청서 양식');
INSERT INTO documents (document_id, document_name) VALUES (57, '실업 증명 등 위기사유 증빙 서류');
INSERT INTO documents (document_id, document_name) VALUES (58, '영수증');
INSERT INTO documents (document_id, document_name) VALUES (59, '월세 입금 내역서 등');
INSERT INTO documents (document_id, document_name) VALUES (60, '은빛SOL라이프 신청서(행정정보제공 동의)');
INSERT INTO documents (document_id, document_name) VALUES (61, '응시 증빙(응시확인서/성적표)');
INSERT INTO documents (document_id, document_name) VALUES (62, '응시료 지원신청서 외');
INSERT INTO documents (document_id, document_name) VALUES (63, '응시확인서');
INSERT INTO documents (document_id, document_name) VALUES (64, '응시확인서 또는 성적표');
INSERT INTO documents (document_id, document_name) VALUES (65, '의사소견서 등)');
INSERT INTO documents (document_id, document_name) VALUES (66, '이력서/응시 증빙');
INSERT INTO documents (document_id, document_name) VALUES (67, '이사비용 견적서 및 영수증(이체확인증)');
INSERT INTO documents (document_id, document_name) VALUES (68, '이사업체 사업자등록증 사본');
INSERT INTO documents (document_id, document_name) VALUES (69, '이사진행 증빙사진');
INSERT INTO documents (document_id, document_name) VALUES (70, '임대차계약서');
INSERT INTO documents (document_id, document_name) VALUES (71, '임대차계약서 등 (PDF 1개로 병합 제출)');
INSERT INTO documents (document_id, document_name) VALUES (72, '임대차계약서 사본');
INSERT INTO documents (document_id, document_name) VALUES (73, '임신확인서');
INSERT INTO documents (document_id, document_name) VALUES (74, '입·퇴원 및 질병명 표기 의료기관 발급 서류(소견서');
INSERT INTO documents (document_id, document_name) VALUES (75, '자기돌봄비 신청서(돌봄상황기술)');
INSERT INTO documents (document_id, document_name) VALUES (76, '장애인등록증');
INSERT INTO documents (document_id, document_name) VALUES (77, '전세보증금반환 보증서');
INSERT INTO documents (document_id, document_name) VALUES (78, '전세사기피해 결정문 등');
INSERT INTO documents (document_id, document_name) VALUES (79, '제출서류 없음');
INSERT INTO documents (document_id, document_name) VALUES (80, '주민등록등본');
INSERT INTO documents (document_id, document_name) VALUES (81, '주민등록등본 등 필요 서류');
INSERT INTO documents (document_id, document_name) VALUES (82, '주민등록초본');
INSERT INTO documents (document_id, document_name) VALUES (83, '중개보수/이사비 지출증빙 서류(영수증)');
INSERT INTO documents (document_id, document_name) VALUES (84, '증빙서류 일체');
INSERT INTO documents (document_id, document_name) VALUES (85, '증빙서류(진단서·소견서');
INSERT INTO documents (document_id, document_name) VALUES (86, '지방세 미과세 증명서');
INSERT INTO documents (document_id, document_name) VALUES (87, '지원신청서');
INSERT INTO documents (document_id, document_name) VALUES (88, '지출증빙 영수증 등');
INSERT INTO documents (document_id, document_name) VALUES (89, '진단서');
INSERT INTO documents (document_id, document_name) VALUES (90, '진단서 등)');
INSERT INTO documents (document_id, document_name) VALUES (91, '차상위 증명서');
INSERT INTO documents (document_id, document_name) VALUES (92, '초본');
INSERT INTO documents (document_id, document_name) VALUES (93, '최근 3개월간 월세 이체 내역서(임대인 계좌확인)');
INSERT INTO documents (document_id, document_name) VALUES (94, '최종학력 졸업증명서(또는 수료증)');
INSERT INTO documents (document_id, document_name) VALUES (95, '통장사본');
INSERT INTO documents (document_id, document_name) VALUES (96, '통장사본 등');
INSERT INTO documents (document_id, document_name) VALUES (97, '필수 제출서류 업로드 (초본');
INSERT INTO documents (document_id, document_name) VALUES (98, '한부모가족증명서');
INSERT INTO documents (document_id, document_name) VALUES (99, '행정정보공동이용동의서');
INSERT INTO documents (document_id, document_name) VALUES (100, '확정일자 날인 임대차계약서 전체 사본 1부');

-- ---------- policies (57건) ----------
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (1, 'INST-0001', '가족돌봄청년 후원연계사업 (초록우산, 기아대책 등)', 34, '최대 3개월 기준 산출 (연말까지 집행 완료)', NULL, '경제적 위기가정의 가족돌봄청년에게 보육(보호자 간병비), 교육, 의료(치료비), 주거 항목별 1개 선택 시 최대 200~500만원 지원', '가족돌봄청년 사례관리기관 담당자가 이메일로 대리 수행 접수', '출처 1은 연령 9~24세 및 2025.8.27~9.12 접수로 명시. 출처 55(성북구 공고)는 초록우산 9~24세, 기아대책 9~34세 및 2024년 접수일정으로 명시. 시기 및 기관별로 연령과 지원 한도가 상이함.', '고정일', '2025.9.12. (출처 1 공고 기준)', '2025-09-12 00:00:00', NULL, 'https://welfare.seoul.kr/web/contents/communication1-1.do?schM=view&id=27069&schBcid=swf_news', '''서울시가족돌봄청년지원 WAY'' 카카오톡 문의 (02-6353-0337)', NULL, '정기', 9, 34, NULL, '무관', '경제적 위기 가정 (서울복지포털 가족돌봄정보 등록 완료자 필수)', '장애, 질병 등이 있는 가족을 돌보는 서울시 가족돌봄청(소)년 (자녀돌봄, 배우자돌봄 제외)', '타기관 또는 초록우산 동일내용 중복수혜 시 감액 또는 제외 (기아대책과 초록우산 동시 신청 불가)', NULL, '2026-07-18', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (2, 'INST-0002', '서울시 가족돌봄청소년·청년 자기돌봄비 지원 사업', 32, '최대 6개월 (또는 8개월)', NULL, '가족을 돌보느라 자신의 삶을 돌볼 여유가 부족한 청년에게 자기개발, 건강관리, 상담 등에 사용할 수 있는 자기돌봄비를 월 30만원(고부담형 40만원) 전용통장으로 지급', '서울복지포털 온라인 신청 (단, 9~13세는 주소지 관할 구청 방문 접수)', '출처 2는 신청기간 26.5.6~5.26 및 지급 6개월로 안내. 반면 출처 9는 신청기간 26.3.16~4.6 및 지급 최대 8개월로 안내. 회차 및 공고에 따라 상이함.', '고정일', '2026.5.26. 18:00 (출처 2 기준)', '2026-05-26 18:00:00', '6월 중', 'https://opcl.kr/policy-details/zXg3_J0BGedfapFa8Iru/', '안심돌봄 120 / 1668-0120', NULL, '정기', 9, 39, '제대군인의 경우 최대 3년 이내 지원 상한연령 가산 (군 복무기간 반영)', '가구합산 기준 중위소득%', '신청일 기준 전월 건강보험료 부과액 기준 중위소득 150% 이하 가구. 기초생활수급자(생계·주거·의료·교육 급여) 및 차상위계층은 신청 불가.', '장애, 정신 및 신체의 질병 문제를 가진 가족(민법 779조)을 돌보는 자. 신청자와 돌봄대상가족이 모두 서울시 내 동일 세대 구성. (자녀돌봄 제외)', '서울시 디딤돌 소득 사업 참여자', 'https://wis.eseoul.go.kr/rest/file/download/4c3686a9d2e043159eedb748499da711/4', '2026-07-18', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (3, 'INST-0003', '일상돌봄서비스', 1, '기본서비스 6개월~12개월 (재판정 가능), 특화서비스 1년 (총 3년까지 가능)', '수급자 및 차상위 전액 지원(면제), 소득구간(120% 이하 10%, 120~160% 25%, 160% 초과 100%)에 따라 본인부담금 차등 발생', '돌봄이 필요한 청장년 및 가족돌봄청년에게 재가돌봄·가사 지원(기본서비스)과 병원동행, 식사·영양관리, 심리지원, 독립생활 지원(특화서비스) 바우처 제공', '복지로 온라인 신청 또는 주소지 동주민센터 방문, 전화, 팩스 신청', '출처 8은 서비스별 재판정 2회 명시, 출처 10은 재판정 5회 명시. 구로구(출처 7)는 제공기관 공고. 서대문구(출처 53) 상시모집 공고 존재.', '상시', '예산 소진 시 종료', NULL, '구청 개별 통지', 'https://www.socialservice.or.kr:444/user/htmlEditor/view2.do?p_sn=82', '주민등록상 주소지 읍·면·동 행정복지센터', NULL, '상시', 13, 64, '가족돌봄청년은 13세~39세, 돌봄필요 청·중장년은 13~64세', '복합조건', '소득수준에 무관하게 누구나 이용 가능 (단, 소득 수준별로 본인부담금 부과율만 달라짐)', '질병, 부상, 고립 등으로 일상생활이 불가능한 청·중장년, 자립준비청년, 보호연장아동, 질병 등을 앓는 가족을 돌보는 가족돌봄청년 (부모, 조부모, 배우자, 형제자매 돌봄. 자녀 돌봄 제외)', '타 공적 돌봄서비스(장기요양, 가사간병, 장애인활동지원, 보훈재가복지 등) 이용자는 일상돌봄 중 특화형(D형)만 이용 가능', NULL, '2026-07-18', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (4, 'INST-0004', '서울형 가사서비스 지원 사업', 27, '바우처 지급 배정일 ~ 지급 연도의 11월 30일까지', NULL, '임산부, 맞벌이, 다자녀 가정의 가사노동 부담 경감을 위해 1가정당 연 70만원을 신용/체크카드 바우처 형태로 지원', '탄생육아 몽땅정보통(umppa.seoul.go.kr) 온라인 신청', '출처 14, 15는 중위소득 180% 이하, 출처 16은 중위소득 150% 이하로 상이함. 가족돌봄공백(본인 또는 가족의 질병, 장애) 가구는 우선 선정 지원됨. 임신부는 임신 3개월~출산 후 1년 이내.', '예산소진시마감', '2026. 10. 31. (예산 소진 시 조기마감)', '2026-10-31 00:00:00', '선정 시 개별 문자 통보', 'https://umppa.seoul.go.kr/hmpg/sprt/bzin/bzmgComtDetail.do?biz_mng_no=9F04398B4B3648348729DB5796A4DC39', '탄생육아 몽땅정보통 / 해당 자치구 가족정책과', NULL, '정기', NULL, NULL, NULL, '가구합산 기준 중위소득%', '신청일 기준 서울 거주 중위소득 180% 이하 (정부 행복이음시스템 소득조회 실시. 노인장기요양보험료 제외 건강보험료 본인부담금 납부액 기준)', '12세 이하 자녀 양육 맞벌이, 임신 3개월~출산 후 1년 이내 임산부, 18세 이하 2자녀 이상(12세 이하 1명 필수 포함) 다자녀 가정', NULL, NULL, '2026-07-18', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (5, 'INST-0005', '서울시 청년수당', 37, '매월 50만원 (최대 6개월)', NULL, '서울 거주 미취업 또는 단기 근로 청년에게 진로 탐색 및 구직 활동 지원을 위한 수당 월 50만원을 최장 6개월간 지급하고 멘토링, 특강 등 지원', '청년몽땅정보통(youth.seoul.go.kr) 온라인 접수 (방문 및 우편 불가)', '출처마다 일정 상이(출처 19는 3월, 출처 21은 2025년 6월, 출처 23은 2026년 2차 추가모집 5.27~5.29 명시). 2차 모집 기준으로 작성.', '고정일', '2026. 5. 29. 16:00 (2차 추가모집 기준)', '2026-05-29 16:00:00', NULL, 'https://youth.seoul.go.kr/infoData/plcyInfo/view.do?sprtInfoId=&plcyBizId=V202600005&key=2309150002', '청년수당 콜센터 1566-3344, 다산콜센터 02-120', NULL, '정기', 19, 34, '제대군인의 경우 병역법 등에 따른 복무기간에 비례해 최대 3년 연장 지원', '개인소득 기준 중위소득%', '신청 전월 기준 건강보험료 월 부과액 기준 중위소득 150% 이하', '서울시 주민등록상 거주하며 최종학력(고교, 대학, 대학원) 졸업자(수료, 제적, 중퇴, 졸업예정자 포함) 중 미취업자 (고용보험 미가입자). 단, 주 30시간 이하 또는 3개월 이하 단기근로자는 신청 가능.', '서울시 청년월세지원 사업, 서울시 희망두배 청년통장, 고용노동부 국민취업지원제도 1·2유형 참여자', NULL, '2026-07-18', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (6, 'INST-0006', '희망두배 청년통장', 29, '24개월(2년) 또는 36개월(3년)', '월 15만원 저축', '서울시 거주 근로 청년이 2년 또는 3년 동안 매월 15만원을 저축하면, 서울시가 동일 금액인 15만원을 매월 매칭 적립하여 두 배 이상의 목돈(최대 1,080만원)으로 돌려주는 제도', '서울시 자산형성지원사업 홈페이지(account.welfare.seoul.kr) 온라인 신청 (PC만 가능)', '선정 후 의무사항: 적립기간의 50% 이상 근로, 재무 금융교육 연 1회 이수 필수.', '고정일', '2026. 6. 19. 18:00', '2026-06-19 18:00:00', '2026. 11. 3. (예정)', 'https://account.welfare.seoul.kr/web/contents/noticeMatch.lp?currentPageNo=1&srchItemType=&srchItemSub=&srchType=A&srchVal=&srchAct=view&boardCd=20260526080515790176', '자산형성사업 콜센터 1688-1453', NULL, '정기', 18, 34, '제대 군인은 복무기간만큼 상향 연장되어 최대 만 39세까지 신청 가능', '복합조건', '본인 근로소득 세전 월평균 255만원 이하. 부양의무자(부모 또는 배우자) 소득이 연 1억 원 미만이며 재산이 9억 원 미만.', '주민등록상 서울시에 거주하며 공고일 기준 최근 1년 내 3개월 이상 근로했거나 현재 3개월 이상 근로 중인 자 (외국인, 재외국민 신청 불가)', '타 자산형성지원사업 기 수혜자, 서울시 청년수당, 가족돌봄청년 자기돌봄비 지원사업 참여자', 'https://account.welfare.seoul.kr/web/contents/noticeMatch.lp?currentPageNo=1&srchItemType=&srchItemSub=&srchType=A&srchVal=&srchAct=view&boardCd=20260526080515790176', '2026-07-18', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (7, 'INST-0007', '서울시 청년월세지원 사업', 26, '최대 12개월 / 240만원 (생애 1회)', NULL, '주거비 부담 완화를 위해 서울시에 월세로 거주하는 19~39세 무주택 청년가구에게 월 최대 20만원 이하의 월세를 최장 12개월간 지원', '서울주거포털(housing.seoul.go.kr) 온라인 신청 및 접수', '출처 34(26년 모집공고, 5.6~5.19), 출처 32(25년 공고, 6.11~6.24). 2026년 공고 기준으로 작성. 구간별 전산 무작위 추첨제 시행.', '고정일', '2026. 5. 19. 18:00', '2026-05-19 18:00:00', '7월 초 심사결과 통보, 7월 말 최종 발표', 'https://housing.seoul.go.kr/site/main/content/sh01_060513', 'SH 청년월세지원센터 1833-2030, 다산콜센터 02-120', NULL, '정기', 19, 39, '의무복무 제대군인 청년 대상, 군 복무기간 고려 최대 3살(42세)까지 지원 연령 상한 연장', '가구합산 기준 중위소득%', '기준 중위소득 48% 초과 ~ 150% 이하. 임차보증금 8천만원 이하 및 월세 60만원 이하 거주. (월세 60만원 초과 시, 보증금월세환산액(환산율 4.5% 적용)과 월세액 합산 90만원 이하면 신청 가능). 일반재산 1억 3천만원 이하, 차량가액 2,500만원 미만.', '주민등록상 서울시 거주 청년 1인 가구, 한부모가족, 전세사기피해자 1인가구, 무자녀 청년 신혼부부, 청년안심주택(민간임대) 거주자. 무주택자 한정.', '서울시 청년월세 기수혜자, 국토부 청년월세 특별지원 수혜자, 자치구 청년월세 수혜자, 기초생활수급자, 공공임대주택 거주자', 'https://housing.seoul.go.kr/site/main/file/download/uu/da39f9221c574254a6c8bc52327495f5', '2026-07-18', 2026, TRUE);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (8, 'INST-0008', '청년월세 한시 특별지원 (국토교통부)', 15, '월 20만원씩 최대 24개월(회) 간 지원 (생애 1회)', NULL, '원가구(부모 등)와 분리되어 거주하는 저소득 무주택 청년층의 주거비 부담 경감을 위해 월 최대 20만원씩 최장 24개월간 월세를 지원', '복지로(bokjiro.go.kr) 온라인 신청 또는 주소지 관할 동주민센터 방문 신청', '서울시 청년월세와 달리, 원가구 소득 및 재산 기준을 동시 적용하며 지원 기간이 최대 24개월임. 신청기간 2026.3.30~5.29.', '고정일', '2026. 5. 29. 16:00', '2026-05-29 16:00:00', '2026. 9. 14. (예정)', 'https://www.bokjiro.go.kr/ssis-tbu/twataa/wlfareInfo/moveTWAT52011M.do?wlfareInfoId=WLF00004661', '국토교통부 콜센터 1599-0001', NULL, '상시', 19, 34, NULL, '복합조건', '청년독립가구 기준 중위소득 60% 이하 & 총재산가액 1.22억원 이하. 원가구(본인+부모) 기준 중위소득 100% 이하 & 재산가액 4.7억원 이하.', '부모님과 별도 거주하는 무주택 청년 (기혼자, 30세 이상, 미혼부/모 등은 원가구 소득 미고려 인정 가능)', '국토부 또는 지자체 시행 청년월세 수혜 중인 자, 공공임대주택 거주자, 2촌 이내 혈족 주택 임차자', NULL, '2026-07-18', 2026, TRUE);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (9, 'INST-0009', '서울시 청년 마음건강 지원사업', 31, '최대 6회기 1:1 심층 심리상담 (마음 상태별 사후 관리 프로그램 연계)', '무료', '우울·불안 등 정서적 어려움을 겪는 청년에게 간이정신진단(MMPI), 기질검사(TCI) 등 진단을 통한 맞춤형 1:1 심층 심리상담을 기본 6회기 무상 제공', '청년몽땅정보통(youth.seoul.go.kr) 온라인 신청', '모집 기수별 상이 (출처 38은 1차 1.26~2.2, 출처 39/40은 2차 3월 말, 출처 37은 3차 7.20~7.23). 신청 시 별도 제출 서류는 없음.', '회차형', '3차 기준 2026. 7. 23. 17:00 (조기마감 시 신청 불가)', NULL, '회차별 신청마감 후 청년몽땅정보통 공지', 'https://news.seoul.go.kr/gov/archives/579367', '서울시 다산콜센터 02-120', NULL, '회차형', 19, 39, '의무복무 제대군인의 경우 군 복무기간에 따라 최대 3년 연장 지원', '무관', NULL, '신청일 기준 서울시에 거주하며 심리지원이 필요한 청년 (소득 등 제한 없음)', NULL, 'https://www.seoul.go.kr/news/news_notice.do?nttNo=461542', '2026-07-18', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (10, 'INST-0010', '서울시 고립·은둔청년 지원사업', 35, NULL, NULL, '사회적 고립 상태나 은둔 성향을 보이는 청년의 일상 복귀와 사회 진입을 위해 권역별 센터 배정 후 심리상담, 사회기술훈련, 자조모임, 멘토링 등을 통합 밀착 지원', '청년몽땅정보통 온라인 접수', '부모 및 가족을 대상으로 하는 ''고립·은둔 청년 지킴이 양성교육''도 별도 운영함.', '상시', NULL, NULL, '사업 신청일로부터 2주 이내 사전척도 안내 후 권역센터 배정 연락', 'https://siryc.or.kr/pages/2026-business', '서울청년기지개센터 권역협력팀 02-2179-5452', NULL, '상시', 19, 39, NULL, '무관', NULL, '서울 거주 만 19~39세 고립·은둔 성향 청년 누구나', NULL, 'https://siryc.or.kr/posts/2xt2dkw', '2026-07-18', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (11, 'INST-0011', '서울형 유급병가 지원사업 (서울형 입원 생활비 지원)', 25, '연간 최대 14일 지원 (입원 13일, 공단 일반건강검진 1일)', NULL, '아파서 입원하거나 일반건강검진을 받아 근로소득을 잃는 저소득 지역가입자에게 생활 임금(2026년 기준 1일 96,960원)을 현금으로 지원', '온라인(sickleave.seoul.go.kr) 신청 또는 주소지 관할 보건소·동주민센터 방문, 팩스, 우편 접수', '출처 60~65. 2026년 소득기준 완화. 입원 연계 외래진료 최대 3일 포함 가능.', '상시', '퇴원일 또는 1차 공단 일반건강검진일로부터 180일 이내 신청', NULL, '신청일로부터 30일 이내 지급 (특별사유 발생 시 60일)', 'https://sickleave.seoul.go.kr/', '거주지 관할 보건소 및 동주민센터', NULL, '상시', NULL, NULL, NULL, '가구합산 기준 중위소득%', '신청인 가구 소득 중위소득 100% 이하 및 일반재산 4억원 이하. 입원(검진)일 기준 1개월 전부터 서울시민. 기간 내 국민건강보험 지역가입자 자격 유지. 입원 전월 포함 3개월간 24일 이상 근로(사업자의 경우 45일 이상).', '미용, 성형, 출산 등을 제외한 질병·부상 치료 목적으로 병원에 입원하거나 공단 일반건강검진을 받은 근로자 및 개인사업자', '국민기초생활보장 생계급여, 서울형 기초보장, 국가/서울형 긴급복지지원, 실업급여, 산재보험급여 수급자', NULL, '2026-07-18', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (12, 'INST-0012', '청년 부동산 중개보수 및 이사비 지원사업', 31, '최대 40만원 지급 (생애 1회)', NULL, '주거 이전 잦은 청년층 부담 경감을 위해 개인용달, 포장이사, 중개수수료 등에 실제로 지출된 비용을 합산하여 최대 40만원 실비 지급', '청년몽땅정보통(youth.seoul.go.kr) 온라인 신청', '총 8,000명 모집 (상반기 4,000명, 하반기 4,000명). 상/하반기 신청 기한 상이함 (출처 71 4월 기준, 출처 134는 25년 8월 기준 명시).', '고정일', '2026. 4. 14. 18:00 (상반기 기준)', '2026-04-14 18:00:00', '7월 말 지원금 지급', 'https://soco.seoul.go.kr/coHouse/cmmn/file/fileDown.do?atchFileId=12817e11dea24565ae8f7a84b98175db&fileSn=1', '전담콜센터 1877-9358', NULL, '정기', 19, 39, NULL, '가구합산 기준 중위소득%', '가구당 기준 중위소득 150% 이하, 신청자 무주택자. 임차보증금과 (월세액x100)의 합계인 거래금액이 2억원 이하인 임차 거주자', '2024.1.1. 이후 서울시로 전입 또는 서울 내 이사 완료 청년 (신청자 본인이 세대주 및 임차인). 동거인 있어도 무방.', '부모 소유 주택 임차, 생계·의료·주거급여 기초생활수급자, 타 기관에서 중개보수 및 이사비 지원받은 사람', 'https://soco.seoul.go.kr/youth/bbs/BMSR00013/view.do?boardId=6486&menuNo=400018', '2026-07-18', 2026, TRUE);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (13, 'INST-0013', '청년 임차보증금 이자지원 사업', 33, '계약기간 내 회당 6개월~2년, 대출기간 합산 최대 8년까지 연장 가능', '대출 적용금리에서 서울시 지원금리(기본 2.0% + 우대금리 최대 1.0%) 제외한 잔여 금리 본인 부담 (최저 1.0%는 본인 부담)', '임차보증금(전월세) 대출 시 융자 최대 2억원 한도 내에서 대출 금리의 최대 연 3.0%를 서울시가 이자 지원', '서울주거포털에서 추천서 발급 온라인 신청 후, 하나은행 모바일앱(하나원큐) 또는 지점에서 대출 신청', '2026년 소득기준이 4천만원에서 5천만원으로 상향 조정됨.', '상시', NULL, NULL, NULL, 'https://housing.seoul.go.kr/site/main/content/sh01_040901', '서울시 주택정책과 02-2133-7026, 하나은행 전담 콜센터 1599-2222', NULL, '상시', 19, 39, '만 40세가 되는 날이 포함된 임대차 계약 종료일까지만 지원', '개인소득 기준', '본인 연소득 5천만원 이하(기혼자는 부부합산 연소득 6천만원 이하). 무주택 세대주 또는 예비세대주.', '서울시 거주 목돈 마련 어려운 무주택 청년 (한부모가족 청년 부/모, 자립준비청년은 추가 1.0% 우대금리 적용)', '주거급여수급자(조건부 가능), 자녀출산 무주택가구 주거비 지원 등 유사 임차보증금 지원사업 수혜자', 'https://housing.seoul.go.kr/site/main/file/download/uu/0446e94f40c545dc85ed15a133939e86', '2026-07-18', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (14, 'INST-0014', '전세보증금반환보증 보증료 지원사업', 16, '보증료 최대 40만원 실비 지급 (생애 1회)', NULL, '깡통전세 예방을 위해 전세보증금 반환보증 보험에 가입한 임차인에게 기 납부한 보증료를 최대 40만원 전액 실비 환급 지원', '정부24 온라인 신청 또는 주소지 관할 구청 방문 신청', '2025.3.31 이후 보증보험 가입자부터 한도가 40만원으로 상향됨.', '상시', NULL, NULL, '접수 후 30일 이내 안내', 'https://housing.seoul.go.kr/site/main/content/sh01_061030', '국토교통부 콜센터 1599-0001', NULL, '상시', 19, 39, NULL, '개인소득 기준', '본인 연소득 5천만원 이하(청년 기준), 임차보증금 3억원 이하 주거용 주택 거주 무주택자', '서울시에 주민등록을 두고 HUG, HF, SGI의 전세금반환보증에 가입한 자', '민간임대주택 등록임대사업자 거주 임차인, 법인 임차인, 동일 보증료 지원사업 기수혜자', NULL, '2026-07-18', 2026, TRUE);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (15, 'INST-0015', '서울시 1인가구 건강동행·마음동행·이사동행 서비스', 28, '건강동행 월 최대 10회, 연 200시간 한도 (중위소득 100% 이하는 연 48회 무료 지원)', '건강동행 기준 시간당 6,000원(30분 초과 시 3,000원 추가). 교통비는 이용자 부담.', '1인가구를 위해 기존 병원안심동행서비스를 확장, 병원·재활 진료 과정 전반을 동행매니저가 지원(건강)하며, 심리상담 연계(마음) 및 이사 시 행정절차 점검(이사) 등을 통합 지원', '서울1인가구포털(1in.seoul.go.kr) 온라인 신청 또는 콜센터 전화', '만 12세 이하 초등학생은 보호자 동반 시 이용 가능. 신청시 대중교통으로만 이동 가능.', '상시', NULL, NULL, '당일 접수 시 3시간 내 배정 후 출동', 'https://news.seoul.go.kr/welfare/archives/537252', '일인친구 콜센터 1533-1179', NULL, '상시', NULL, NULL, NULL, '복합조건', '서비스 이용에 대한 소득 및 자산 요건은 없으나 무료 이용을 위해서는 중위소득 100% 이하 증빙 필요', '거동이 불편하거나 보호자 동행이 필요한 서울 거주 1인가구 및 다인가구 시민', NULL, NULL, '2026-07-18', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (16, 'INST-0016', '서울청년문화패스', 30, '선정일 ~ 이듬해 3월 31일', '티켓 7만원 초과 시 차액 자부담 (뮤지컬은 지원기간 내 1회만 결제 가능)', '사회초년생의 문화예술 관람 접근성 제고를 위해 연극, 뮤지컬, 전시 등에 사용할 수 있는 연간 20만원 한도 문화바우처 카드 지급', '청년몽땅정보통 온라인 접수 및 신한은행 전용카드(체크카드) 비대면 발급', '전용 사이트에서 결제해야 하며, 예매 건당 7만원 이내로만 포인트 사용이 가능하고 초과분은 자비 부담.', '예산소진시마감', NULL, NULL, NULL, 'https://youth.seoul.go.kr/infoData/plcyInfo/view.do?key=2309150002&plcyBizId=V202600004', '콜센터 1533-3427', NULL, '상시', 21, 23, '의무복무 제대군인의 경우 최대 3년(26세까지) 연령 상향 지원', '가구합산 기준 중위소득%', '신청일 기준 건강보험료 본인부담금 기준 중위소득 150% 이하', '서울에 주민등록이 되어 있는 21~23세 청년', '과거년도 기 선정자 (생애 최초 신청자만 가능)', 'https://youth.seoul.go.kr/resource/file/conts005_1.pdf', '2026-07-18', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (17, 'INST-0017', '은평형 청년월세 지원사업', 49, '최대 12개월 (월 10만원 지급)', NULL, '은평구에 거주하는 무주택 1인가구 청년을 위해 시비 및 국비 지원 조건에 해당하지 않는 대상자 약 70명을 선발해 월 10만원씩 최장 12개월 월세 지원', '은평구청 누리집 고시공고 내 안내', '서울시 청년월세사업과 별개로 지자체 재원으로 운영되는 보완적 성격의 지원.', '예산소진시마감', NULL, NULL, NULL, 'http://www.xn--z92b13l34dhpao4wv9k.com/news_gisa/gisa_view.htm?gisa_category=01180400&gisa_idx=676444&date_y=2026&date_m=01', '은평구청 청년미래팀 02-351-6885', '은평구', '정기', 19, 39, NULL, '중위소득%', '기준 중위소득 150% 이하, 임차보증금 8천만원 이하, 월세 60만원 이하 주택 거주. 재산 1억원 이하.', '은평구에 실거주하는 무주택 1인가구 청년', NULL, NULL, '2026-07-18', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (18, 'INST-0018', '은평구 전입 1인가구 생활지원 <은빛SOL라이프>', 48, '실물 박스 1회 수령', NULL, '타 지역에서 은평구로 이주한 1인가구의 초기 정착을 지원하고 고독사를 방지하기 위해 공구, 생활, 응급 세트 중 택 1 가능한 ''웰컴행복박스'' 제공', '온라인 신청 또는 동주민센터 방문 신청', '청년층은 분기별 모집(3, 6, 9, 11월), 중장년층은 연중 상시 신청을 받음.', '회차형', '2026. 11. 30. (분기별 예산 소진 시 마감)', '2026-11-30 00:00:00', NULL, 'https://www.shinailbo.co.kr/news/articleView.html?idxno=5028060', '은평구 가족정책과 02-351-6193', '은평구', '회차형', 19, 64, '1962년생 ~ 2007년생', '무관', NULL, '2026년 1월 이후 은평구로 전입한 1인가구', NULL, NULL, '2026-07-18', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (19, 'INST-0019', '은평구 안전돌봄서비스 <안녕, 은빛SOL메이트>', 47, '앱 지속 사용 가능', NULL, '휴대폰 미사용 위기 감지 시 자동 알림을 발송하는 안부확인 모니터링 시스템. 출석, 식사기록 등 건강 미션 완수 시 은평사랑상품권으로 전환 가능한 포인트(최대 5만) 지급', '사업 참여 신청 후 구청 승인, 앱 다운로드 가입', NULL, '상시', '2026년 6월 (상반기 기준 모집)', NULL, NULL, 'https://play.google.com/store/apps/details?id=com.mills.sme20.solmate&hl=ko', '앱 내 고객센터', '은평구', '상시', 19, 64, NULL, '중위소득%', '기준 중위소득 150% 이하', '은평구에 주민등록이 되어 있는 고립 위험도 있는 청·중장년 1인가구', NULL, NULL, '2026-07-18', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (20, 'INST-0020', '광진구 청년문화생활바우처', 11, '카드 발급월 ~ 당해 11월 30일까지 사용', NULL, '서울시 청년문화패스의 연령 한계(23세)를 넘어, 24~29세 청년들에게 문화예술·체육·취미 등에 사용할 수 있는 연 10만 원의 전용 바우처 카드 지급', '광진구청 홈페이지 > 참여소통 > 구민의견/참여 > 온라인접수', '모집 규모 500명이며 초과 시 건강보험료 평균 납부액 낮은 순으로 우선 선발.', '회차형', '2026. 7. 15. (2차 모집 기준)', '2026-07-15 00:00:00', NULL, 'https://www.gwangjin.go.kr/health/bbs/B0000001/view.do?nttId=6613243&menuNo=300240', '광진구 청년정책팀 02-450-7048', '광진구', '회차형', 24, 29, NULL, '가구합산 기준 중위소득%', '가구 기준 중위소득 120% 이하', '신청일 기준 광진구에 1년 이상 주민등록을 유지하고 거주 중인 청년', '문화누리카드 수혜자(수급자, 차상위), 기타 유사 서비스 수혜자', NULL, '2026-07-18', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (21, 'INST-0021', '광진구 1인가구 이사지원서비스 <광진인(IN)>', 13, '실비 최대 30만원 지원 (1회)', NULL, '타 지역에서 전입하거나 관내 이주하는 청년 및 1인가구를 대상으로 실제 소요된 이사비(트럭 대여, 용달 등)를 최대 30만원까지 계좌로 후불 실비 지원하며, 이사 기념 침구 세트 증정', '광진1인가구플랫폼 앱 신청 후, 이메일(1lifegj@naver.com)로 증빙서류 제출', '현금 결제 시 현금영수증 필히 제출. 이사 전후 사진이나 작업 사진(4장) 증빙 필요.', '상시', '매월 상시 접수', NULL, '선정 후 익월 초 개별 안내', 'https://youth.seoul.go.kr/infoData/sprtInfo/view.do?sprtInfoId=70421&key=2309130006', '광진구1인가구지원센터 02-465-0336', '광진구', '상시', NULL, NULL, NULL, '개인소득 기준', '건강보험료 본인부담금 기준 중위소득 150% 이하. 전세보증금 1억 이하 또는 보증금 8천만/월세 60만 이하 주택 거주.', '해당 월에 이사 또는 전입하는 광진구 거주 1인가구 (''광진1인가구플랫폼'' 등록 회원)', '기초생활수급자 대상 제외, 타 기관 관련 이사비용 지원사업 중복 불가', NULL, '2026-07-18', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (22, 'INST-0022', '광진형 청년 월세 지원사업', 12, '최대 24개월', NULL, '국토부나 서울시 정책 혜택을 받지 못하는 광진구 청년 1인가구 150명을 선발해 월 20만원 한도 내에서 최장 24개월간 월세를 구비로 지원', '광진구청 누리집 온라인 신청', '서울시 청년월세가 12개월 지원인데 반해, 광진형은 최대 24개월(480만원)을 지원하는 것이 차이점.', '고정일', '8. 21. (공고 기준 상이)', NULL, '9월 이후', 'https://www.thevoiceofus.co.kr/news/article.html?no=16396', '광진구청 주택과 02-450-9752', '광진구', '정기', 19, 39, NULL, '중위소득%', '건강보험료 납부액 기준 중위소득 150% 이하, 일반재산 1억 3,000만원 이하. 보증금 8,000만원 이하 및 월 임대료 60만원 이하.', '광진구에 거주하는 무주택 청년 1인가구', '국토교통부·서울시 청년 월세 지원사업과 중복 수혜 불가 (중복 신청은 가능하나 수급은 1개만)', NULL, '2026-07-18', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (23, 'INST-0023', '동작구 청년 월세 임차료 등 주거안정 지원사업', 22, '최대 12개월 (월세 기준) / 대출이자는 일시금 1회 지급', NULL, '동작구 청년 1인가구 및 신혼부부의 주거 부담을 줄이기 위해 전세보증금 대출이자(최대 150~200만원 일시금) 또는 월세(월 최대 20~30만원 실비)를 지원', '동작통합예약사이트(dongjak.go.kr/yeyak) 온라인 신청', '전산 추첨 방식으로 총 250명(월세 150명, 대출이자 100명) 선발.', '고정일', '2025. 5. 23. 18:00 (2025년 기준)', '2025-05-23 18:00:00', NULL, 'https://youthjob.dongjak.go.kr/noryangjin/board/qe74yhgjenaa/contents/details.do?mId=1399&boardContentsNo=993', '동작구청 청년청소년과 02-820-9306', '동작구', '정기', 19, 39, NULL, '무관', NULL, '동작구 내 전월세로 거주하는 1인 가구 청년 또는 혼인신고를 완료한 신혼부부 청년', NULL, NULL, '2026-07-18', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (24, 'INST-0024', '서대문구 미취업 청년 어학·자격증 응시료 지원 사업', 24, '생애 1회 최대 10만원', '응시료 중 10만원 초과 비용', '구직 비용 완화를 위하여, 당해 연도 응시한 어학 시험이나 각종 국가자격증 시험의 응시료를 생애 1회, 최대 10만 원 한도로 실비 환급', '이메일 제출 (sdmyouth@sdm.go.kr)', '선착순 1,000명 지원이며, 예산 조기 소진 시 마감. 이메일 접수만 가능함.', '예산소진시마감', '2026. 12. 10. (예산소진 시 조기종료)', '2026-12-10 00:00:00', NULL, 'https://www.sdm.go.kr/news/news/notice.do?mode=view&sdmBoardSeq=308789', '서대문구 청년정책팀 02-3140-8096', '서대문구', '상시', 19, 39, NULL, '무관', NULL, '신청일 기준 서대문구에 주민등록을 둔 미취업 청년', NULL, 'https://www.sdm.go.kr/news/news/notice.do?mode=view&sdmBoardSeq=308789', '2026-07-18', 2026, TRUE);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (25, 'INST-0025', '광진구 청년 어학·자격시험 응시료 지원사업', 11, '해당 연도 내 실비 지급', '지원 한도(15만원) 초과금', '취업 준비를 하는 광진구 청년을 대상으로 한국사, 국가기술자격, 국가공인 민간자격, 어학시험 등의 응시료를 연 최대 15만원 한도 내에서 횟수 제한 없이 실비 지원', '이메일 신청 (gjyouth@gwangjin.go.kr)', '지원금 한도가 대부분 자치구는 10만원이나 광진구는 15만원임.', '상시', NULL, NULL, '문자 통지', 'https://gwangjin.newstool.co.kr/pdf/gwangjin_202604.pdf', '일자리청년과 02-450-7068', '광진구', '상시', 19, 39, NULL, '무관', NULL, '광진구민 청년 (미취업자)', NULL, 'https://www.gwangjin.go.kr/portal/bbs/B0000001/view.do?menuNo=200190&nttId=6561067', '2026-07-18', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (26, 'INST-0026', '서초구 청년 자격증 응시료 지원사업', 38, '생애 1회 실비 지급', NULL, '어학, 국가전문자격 등 923종의 자격증 시험 응시를 완료한 미취업 청년들에게 응시 비용을 실비 지급하는 제도', '서초구청 누리집 내 지정 게시판 온라인 접수', '분기별 접수 진행. (1분기 3월, 3분기 9월 등). 운전면허시험 지원 불가. 이미 응시한 시험만 신청 가능.', '회차형', '분기별 접수 마감 (3분기 9.1~9.30)', NULL, NULL, 'https://www.seocho.go.kr/site/seocho/ex/online/OnlineListF.do?ocIdx=testfee', '아동청년과 (게시물 참조)', '서초구', '회차형', 19, 39, '의무복무 제대군인 군복무 기간만큼 연령 상한 연장 (최대 3년)', '무관', '2026.1.1. 이전 거주지 및 미취업자 조건 (단기근로자는 근로계약서 첨부 시 인정)', '서초구 거주 미취업 청년', '타 지자체 응시료 사업, 서울 청년수당, 국민취업지원제도 참여자', NULL, '2026-07-18', 2026, TRUE);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (27, 'INST-0027', '서울형 긴급복지 지원제도', 2, '원칙 1회 지원 (필요 시 연장)', NULL, '주소득자의 사망, 실직, 화재 등 돌발적인 위기 사유로 생계 유지가 불가능해진 벼랑 끝 가구에게 긴급 생계비(1인가구 약 78만원), 의료비, 주거비 등을 신속 지원', '해당 동 주민센터 방문 신청', NULL, '상시', NULL, NULL, '상황에 따라 즉각 심사 및 통보', 'https://news.seoul.go.kr/welfare/archives/48196', '동주민센터 등 긴급복지 상담', NULL, '상시', NULL, NULL, NULL, '가구합산 기준 중위소득%', '기준 중위소득 100% 이하, 재산 4억 900만원 이하, 금융재산 1,000만원 이하 (2026년 기준)', '가족으로부터 방임, 유기, 실직, 중한 질병 등 갑작스러운 위기사유로 생계곤란에 빠진 가구', NULL, NULL, '2026-07-18', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (28, 'INST-0028', '건강한 마음으로 삶을 즐겁게! 강남구민을 위한 심리상담 서비스', 3, '8회기', NULL, '우울증 및 정서적 어려움을 겪는 구민에게 8회기 심리상담 서비스 제공', '전화 문의 및 신청', NULL, '상시', NULL, NULL, NULL, 'https://youth.seoul.go.kr/infoData/sprtInfo/view.do?key=2309130006&sprtInfoId=50327', '02-2226-0344', '강남구', '상시', NULL, NULL, NULL, '무관', '강남구 관내 거주자', '심리상담이 필요한 자', NULL, NULL, '2026-07-19', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (29, 'INST-0029', '가사간병 방문지원 사업', 39, '월 24시간(A형) 또는 월 27시간', '소득수준 및 이용시간에 따라 차등 지원 (시간당 19,000원 기준 중 본인부담금 발생, 수급자/차상위는 최소화)', '가사 및 간병이 필요한 취약계층에게 방문 인력을 통해 신체수발, 건강 및 가사 지원, 일상생활 지원 제공', '동주민센터 방문신청', '서울 전역 대상의 국가사업이나 자치구 사회복지과 통해 접수됨', '상시', NULL, NULL, NULL, 'https://www.seocho.go.kr/site/seocho/04/10402060400002015070710.jsp', '02-2155-6659', '서초구', '상시', NULL, 64, NULL, '중위소득 70% 이하 (가구합산)', '65세 미만의 기준중위소득 70% 이하 계층', '장애정도가 심한 장애인, 6개월 이상 치료를 요하는 중증질환자, 희귀난치성 질환자, 조손/소년소녀가정, 장기입원 퇴원자', '장애인 활동지원서비스, 노인맞춤돌봄서비스, 노인장기요양보험급여 수급자, 보장시설 입소자, 의료기관 입원자', NULL, '2026-07-19', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (30, 'INST-0030', '도봉구 미취업 청년 어학·자격증 응시료 지원사업', 20, '1인 최대 10만 원 실비 지원 (횟수 제한 없음, 한도 내 복수 신청 가능)', NULL, '도봉구 거주 미취업 청년 대상 어학 및 자격증 시험 응시료 실비 지원', '온라인 신청', '원문 상 2026년 1분기 기준 정보', '회차형', '2026-02-27 (1분기)', '2026-02-27 00:00:00', NULL, 'https://www.toeicstory.co.kr/2538', NULL, '도봉구', '정기', NULL, NULL, NULL, '무관', NULL, '미취업 청년', NULL, NULL, '2026-07-19', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (31, 'INST-0031', '성북구 미취업청년 자격증 및 어학시험 응시료 지원', 42, '1인당 10만원 이내 실비지원 (한도 내 횟수 제한 없이 분할 신청 가능)', NULL, '어학, 한국사, 국가공인자격시험 당해연도 실제 응시료 실비 환급', '성북구청 홈페이지 온라인 신청', '자동차운전면허 제외', '회차형', '매월 1~10일 (예산 소진 시 조기종료)', NULL, '매월 25일 부분선정 개별 문자 통보', 'https://www.sb.go.kr/yeyak/contents.do?key=6602', '02-2241-3996', '성북구', '상시', 19, 39, '1986년~2007년 출생자', '무관', '2026.1.1 이전부터 신청일까지 계속 성북구에 주민등록, 신청일 현재 미취업 및 사업자미등록', '미취업 청년 (3개월 이하 또는 주 30시간 이하 단기근로자 지원 가능)', NULL, NULL, '2026-07-19', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (32, 'INST-0032', '중구 청년 자격증 등 응시료 지원', 51, '1인당 최대 8만원 한도, 연 1회', NULL, '어학 및 자격증 등 시험 응시료 실비 지원', '온라인 신청', '2026년 공고 기준', '예산소진시마감', NULL, NULL, NULL, 'https://www.junggu.seoul.kr/content.do?cmsid=16597', NULL, '중구', '상시', 19, 39, NULL, '무관', '2026.1.1 이후 실시한 어학, 국가자격 등 시험 응시자', '미취업 청년 및 사업자 등록 사실 없는 자', NULL, NULL, '2026-07-19', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (33, 'INST-0033', '노원구 미취업청년 어학, 자격증 응시료 지원', 19, '1인 생애 10만원 이내', NULL, '자격증 응시료 실비 생애 10만원 이내 지원', '이메일 제출 또는 방문접수', NULL, '고정일', '2026-12-10', '2026-12-10 00:00:00', NULL, 'https://www.toeicstory.co.kr/2538', NULL, '노원구', '상시', NULL, NULL, NULL, '무관', NULL, '미취업 청년', NULL, NULL, '2026-07-19', 2026, TRUE);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (34, 'INST-0034', '용산구 청년 국가 자격증 및 어학시험 응시료 지원사업', 46, '당해연도 1인 최대 10만원 지원', NULL, '국가공인민간자격 99종 포함한 국가자격증 및 어학시험 응시료 지원', '이메일(ysyouth@yongsan.go.kr) 접수', '운전면허시험 지원 불가', '회차형', '매월 1일~10일 (12월까지, 예산 소진 시 조기 종료)', NULL, NULL, 'https://www.smyc.kr/program/?bmode=view&idx=147065878', '02-2199-4524', '용산구', '상시', 19, 39, NULL, '무관', '1개월 이상 용산구 거주, 신청일 기준 미취업', '미취업 청년', NULL, NULL, '2026-07-19', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (35, 'INST-0035', '송파구 청년 어학, 자격시험 응시료 지원', 43, '1인당 최대 10만원 실비지원 (생애 1회)', NULL, '송파구 거주 청년 대상 어학 및 자격시험 응시료 실비지원', '온라인 신청', NULL, '예산소진시마감', '2026-12-31', '2026-12-31 00:00:00', NULL, 'https://www.toeicstory.co.kr/2538', NULL, '송파구', '상시', NULL, NULL, NULL, '무관', NULL, '미취업 청년', NULL, NULL, '2026-07-19', 2026, TRUE);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (36, 'INST-0036', '강서구 미취업청년 자격증 응시료 지원사업', 8, '최대 10만원 실비 지원 (생애 1회)', NULL, '어학시험 및 국가자격시험 등 응시료 실비 지원 (300명 지원)', '이메일 접수 (gsyouth@gangseo.seoul.kr)', NULL, '예산소진시마감', '2026-11-30', '2026-11-30 00:00:00', NULL, 'https://youth.seoul.go.kr/infoData/sprtInfo/view.do?key=2309130006&sprtInfoId=69799', '02-2600-6775', '강서구', '상시', 19, 39, NULL, '무관', '2026.1.1부터 신청일 현재까지 주민등록상 강서구 거주 중인 자', '미취업자 및 사업자등록 사실이 없는 청년', '정부 및 타 지자체 동일·유사 사업 수혜를 받지 않은 자', NULL, '2026-07-19', 2026, TRUE);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (37, 'INST-0037', '종로구 미취업청년 자격시험 응시료 지원사업', 50, '1인 최대 합산 10만원, 연 1회 신청 가능', NULL, '종로구 미취업 청년을 위한 공인어학, 한국사, 국가기술 등 시험 응시료 실비 지원 (100명 모집)', '종로구 홈페이지 온라인 신청', NULL, '예산소진시마감', '2026-11-30', '2026-11-30 00:00:00', NULL, 'https://app.jongno.go.kr/main/edu/intergrate/788', '02-2148-2312', '종로구', '상시', 19, 39, NULL, '무관', '2026.1.1 이후부터 신청일까지 계속 주민등록 유지', '미취업 청년', '국민취업지원제도, 청년수당 수혜자 선정 제외', NULL, '2026-07-19', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (38, 'INST-0038', '중랑구 미취업청년 자격증 응시료 지원사업', 52, '1인 생애 10만원 이내', NULL, '어학, 한국사, 국가공인 자격증 응시료 실비 지원을 통한 경제적 부담 완화', '이메일(jnyouth2274@jn.go.kr) 또는 방문 신청', '경찰청 주관 자동차운전면허 제외', '예산소진시마감', '2026-12-10 (매월 20일까지 신청)', '2026-12-10 00:00:00', '월말 선정 통보', 'https://youth.seoul.go.kr/infoData/plcyInfo/view.do?sprtInfoId=&plcyBizId=20260415005400212750&key=2309150002', '02-2094-2274', '중랑구', '상시', 19, 39, '1987.1.1~2007.12.31 출생자', '무관', '주민등록상 중랑구 거주', '미취업 청년 (근로계약 3개월 이하/주 26시간 이하 단기근로자, 동행일자리 등 참여자 가능)', '생애 10만원 기지원자, 2026년 서울 청년수당, 국민취업지원제도, 타자치구 유사사업 참여자', NULL, '2026-07-19', 2026, TRUE);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (39, 'INST-0039', '강동구 미취업 청년 어학 및 자격시험 응시료 지원', 5, '1인당 최대 10만원 실비지원 (생애 1회)', NULL, '강동구 미취업 청년 500명에게 어학, 자격시험 응시료 실비 지원', '강동구청 홈페이지 온라인 접수 후 이메일(exam_fee@gangdong.go.kr) 서류 제출', '운전면허시험 응시료 제외', '회차형', '매월 10일까지 (예산 소진 시 조기종료)', NULL, '매월 26~28일 개별 문자 통보', 'https://www.gangdong.go.kr/web/newportal/contents/gdp_005_008_014', '02-3425-5825', '강동구', '상시', 19, 39, NULL, '무관', '신청일 기준 강동구 거주', '미취업 및 사업자등록 사실이 없는 청년 (주30시간 이하/3개월 이하 근로자, 동행일자리 등 정부 일자리 참여자 포함)', '서울시 청년수당, 국민취업지원제도, 타지자체 응시료지원사업 중복 불가', NULL, '2026-07-19', 2026, TRUE);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (40, 'INST-0040', '금천형 취업성공키트', 18, '1인당 최대 50만원 (자격시험 30만 + 취업도전 10만 + 문화힐링 10만 한도 내 생애 1회)', '문화힐링비는 실비의 10% 본인 부담 (90% 지원)', '자격시험 준비비(응시료, 수강료, 교재비), 취업도전비(면접, 헤어메이크업 등), 문화힐링비(OTT, 도서, 영화)를 합산하여 최대 50만원 지원', '금천구청 홈페이지 온라인 신청 후 이메일(gckit@geumcheon.go.kr) 제출', '항목별로 건수 제한 없이 여러 건 합산 가능', '예산소진시마감', '2026-11-30', '2026-11-30 00:00:00', NULL, 'https://www.geumcheon.go.kr/portal/testFeeContents.do?key=4343', '02-2627-2588', '금천구', '상시', 19, 39, '문화힐링비는 만 24세 이상 39세 이하만 해당', '복합조건 (기본소득 무관, 단 문화힐링비는 기준중위소득 150% 미만)', '비용지출 이후 신청일까지 금천구 계속 거주', '미취업/미창업 청년 (주 26시간 이하 또는 3개월 이하 단기근로자 미취업 간주)', NULL, NULL, '2026-07-19', 2026, TRUE);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (41, 'INST-0041', '구로구 청년 어학 및 국가 자격증 응시료 지원 사업', 14, NULL, NULL, '청년 능력개발 및 구직활동 지원을 위한 어학 및 자격시험 응시료 지원', '신청서식 작성 후 제출', NULL, '상시', NULL, NULL, NULL, 'https://www.guro.go.kr/www/selectBbsNttView.do?bbsNo=662&key=1790&nttNo=228017', NULL, '구로구', '상시', 19, 39, NULL, '무관', NULL, '구직 미취업 청년', NULL, NULL, '2026-07-19', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (42, 'INST-0042', '강남구 미취업 청년 어학·자격시험 응시료 지원사업', 4, '1인당 최대 20만원 실비 지원', NULL, '강남구 거주 취업준비 청년 대상 어학 및 국가자격증 시험 응시료 최대 20만원 지원', '이메일 신청 (gnyouth@gangnam.go.kr)', '타 자치구 대비 높은 한도(20만원)', '예산소진시마감', '2026-12-10', '2026-12-10 00:00:00', '서류 보완 후 개별 통보', 'https://www.gangnam.go.kr/board/B_000001/1076200/view.do?mid=ID05_040101', '02-3423-5598', '강남구', '상시', 19, 39, NULL, '무관', '강남구 거주', '미취업 청년', NULL, NULL, '2026-07-19', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (43, 'INST-0043', '영등포구 청년 국가자격시험 응시료 지원 사업', 45, '연 10만원 이내 (횟수 제한 없음)', NULL, '어학시험, 한국사, 국가자격시험 응시료를 연 10만원 이내 지원 (약 1,000명)', '영등포구청 홈페이지 온라인 신청 또는 이메일(ydpjob@ydp.go.kr) 접수', '자동차운전면허 제외', '예산소진시마감', '2026-12-10 (매월 말일까지 접수, 12월만 10일까지)', '2026-12-10 00:00:00', '익월 12일경 확인 및 문자 알림', 'https://www.ydp.go.kr/www/contents.do?key=5998', NULL, '영등포구', '상시', 19, 39, '1987년~2007년 출생자', '무관', '신청일 기준 1개월 이상 영등포구에 주민등록 유지', '미취업자 및 사업자 미등록자 (3개월/주30시간 이하 단기근로, 정부일자리 참여자 포함)', '정부, 타지자체 유사사업, 국민취업지원제도, 서울시 청년수당 당해 연도 중복 불가', NULL, '2026-07-19', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (44, 'INST-0044', '강북구 청년 어학·자격시험 응시료 지원사업', 7, '연 1회 1인당 최대 10만원 한도 통합 신청', NULL, '강북구 거주 미취업 청년 및 강북구 소재 대학 재·휴학생에게 시험 응시료 최대 10만원 지원', '이메일(21sjy@gangbuk.go.kr) 또는 방문 접수', '타구 거주자라도 관내 대학생이면 지원 가능', '예산소진시마감', '2026-12-11 (분기별 접수)', '2026-12-11 00:00:00', '4, 7, 10, 12월 25일경 개별 문자 통보', 'https://snsgangbuk.com/sub/notice.html?type=view&bsNo=2714&page=1', '02-901-2647', '강북구', '상시', 19, 39, NULL, '무관', '강북구에 거주 중이거나 강북구 소재 대학에 재(휴)학 중', '미취업 청년', NULL, NULL, '2026-07-19', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (45, 'INST-0045', '성동구 청년 취업성공 어학·자격시험 응시료 지원', 40, NULL, NULL, '성동구 미취업 청년의 취업 역량 강화를 위한 응시료 지원', NULL, NULL, '상시', NULL, NULL, NULL, 'https://www.munhwa.com/article/11561350', NULL, '성동구', '상시', NULL, NULL, NULL, '무관', NULL, '미취업 청년', NULL, NULL, '2026-07-19', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (46, 'INST-0046', '양천구 청년 국가자격시험 응시료 지원 사업', 44, '생애 1회, 최대 20만원 한도', NULL, '어학, 한국사 및 880여 종 국가자격증 시험 응시료를 최대 20만원 지원하여 취업 준비 부담 해소', '양천구청 홈페이지 온라인 신청 및 업로드', '1,100명 지원 예정. 이전 10만원 지원받은 자도 차액 10만원 추가 신청 가능', '회차형', '매월 10일까지', NULL, '매월 25일 개인별 문자 발송', 'https://youth.seoul.go.kr/infoData/plcyInfo/view.do?key=2309150002&plcyBizId=20240418005400200014', NULL, '양천구', '상시', 19, 39, '의무복무 제대군인 최대 3년 상한 연장 (만 42세까지)', '무관', '2026.1.1 이전부터 신청일까지 계속 양천구에 주민등록 유지', '미취업자 및 사업자미등록자 (3개월/주26시간 이하 단기근로, 공공근로자 가능)', '타 지자체 등 유사 지원 수혜 건', NULL, '2026-07-19', 2026, TRUE);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (47, 'INST-0047', '마포구 청년 취업 준비 비용 지원사업', 23, NULL, NULL, '마포구 청년의 구직활동 촉진을 위해 취업 준비 비용 지원', '마포구 홈페이지 고시공고 참고', '공고문 참고 요망', '예산소진시마감', '2026-12-11', '2026-12-11 00:00:00', NULL, 'https://nk-rdw.tistory.com/m/6762', NULL, '마포구', '상시', NULL, NULL, NULL, '무관', '마포구 거주', '취업 준비 청년', NULL, NULL, '2026-07-19', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (48, 'INST-0048', '관악구 청년 어학·자격시험 응시료 지원', 10, '1인당 연 1회 최대 10만원 실비 지원', NULL, '어학, 국가기술 등 자격시험 응시료를 연 1회 지원. 예산 부족 시 거주기간 등 고려 선발', '관악구청 홈페이지 온라인 신청', '경찰청 주관 운전면허 제외', '회차형', '매월 1일~10일 18:00 (9월부터 재개 예정, 예산소진 시 마감)', NULL, '매월 25일경 문자 발송', 'https://www.gwanak.go.kr/site/gwanak/ex/reservation/re00403.do?riType=A&riIdx=RI002804', '02-879-5932', '관악구', '상시', 19, 39, NULL, '무관', '신청일 기준 주민등록상 관악구 거주', '미취업 및 사업자 미등록 청년 (단기근로 가능)', '서울시 청년수당, 국민취업지원제도, 타 지자체 응시료 지원사업', NULL, '2026-07-19', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (49, 'INST-0049', '성동구 전입 1인가구 청년 지원사업 생필품 구매', 41, '20만 원 한도 (생애 1회)', '20만원 초과 비용', '타 시군구에서 전입한 청년 1인 가구에게 식료품, 주방욕실용품 등 생필품 구매 영수증 인증 시 20만 원 실비 지급', '성동구청 홈페이지 온라인 접수', '선구매 후지원. 온라인 구매 영수증 포함', '회차형', '매월 1~10일 접수 (11월까지)', NULL, NULL, 'https://youth.seoul.go.kr/infoData/sprtInfo/view.do?key=2309130006&sprtInfoId=67149', NULL, '성동구', '상시', 19, 39, NULL, '중위소득 120% 이하', '2026.1.1 이후 성동구 전입 및 신청일 기준 3개월 이상 거주, 신청자 본인 무주택', '1인 가구 독립 세대 구성자', '외국인, 재외국민, 타인/가족 명의 임대차계약 거주자 제외', NULL, '2026-07-19', 2026, TRUE);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (50, 'INST-0050', '성동구 청년 월세 지원 사업', 40, '월 최대 20만원 (1년)', NULL, '주거비 부담 완화를 위해 40명의 청년 세대주에게 연 최대 240만 원의 월세 지원', '성동구청 누리집 참조', NULL, '고정일', NULL, NULL, NULL, 'https://www.sgilbo.kr/ko-kr/articles/53167', NULL, '성동구', '정기', 19, 39, NULL, '중위소득 150% 이하', '임차보증금 8천만 원 이하, 월 임차료 70만 원 이하, 일반재산 1억 3,000만 원 이하', '부모와 따로 거주하는 무주택 청년 세대주', NULL, NULL, '2026-07-19', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (51, 'INST-0051', '관악형 청년 월세 지원사업', 9, '최대 12개월 (생애 1회)', NULL, '관악구 장기 거주자 및 저소득 청년 50명을 선발해 월 20만원 지원', '관악구 홈페이지 온라인 신청', '선착순 아님, 배점 기준 추첨', '고정일', '2026-04-13 18:00', '2026-04-13 18:00:00', '2026-05', 'https://www.gwanak.go.kr/site/gwanak/ex/reservation/re00403.do?riIdx=RI003274&riType=A', '02-879-5921', '관악구', '정기', 19, 39, NULL, '무관 (소득 수준이 배점에 반영됨)', '임차보증금 8천만 원 이하, 월세 60만 원 이하 (보증금 환산액 합계 90만 원 이하 가능)', '관악구 주민등록 및 월세 거주 무주택 청년 1인가구 또는 신혼부부 가구', '국토부, 서울시, 타 지자체 월세 지원, 기초생활수급자 제외', NULL, '2026-07-19', 2026, TRUE);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (52, 'INST-0052', '관악구 청년 주거환경개선비 지원', 9, NULL, NULL, '관악구 청년 가구 대상 도배, 장판 등 주거환경 개선 비용 선착순 지원 (100명)', '온라인 선착순 접수', NULL, '고정일', '2026-03-31', '2026-03-31 00:00:00', '서류 심사 후 요건 충족 시 선정', 'https://www.gwanak.go.kr/site/gwanak/ex/reservation/re00403.do?riIdx=RI003225&riType=A', '02-879-5921', '관악구', '정기', NULL, NULL, NULL, '무관', NULL, '청년', NULL, NULL, '2026-07-19', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (53, 'INST-0053', '금천구 하계 청년 아르바이트', 17, '약 4주간 근무 (7월 중)', NULL, '여름방학 기간 구청, 보건소, 복지관 등에서 4주간 행정 및 현장 보조 업무 수행 (100명)', '금천구청 누리집 온라인 접수', '1996~2007년생 대상 (대학생 한정 아님)', '고정일', '2026-06-12', '2026-06-12 00:00:00', '2026-06-16 (공개추첨)', 'https://www.gcinnews.com/news/articleView.html?idxno=14359', NULL, '금천구', '정기', 19, 30, '1996년생 이후 출생자', '무관', '공고일 기준 금천구 주민등록', '청년 (대학생, 고졸자, 취준생 무관)', '최근 2년 이내 참여 이력자 제외', NULL, '2026-07-19', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (54, 'INST-0054', '강북구 청년 아르바이트', 6, '실근무 20일', NULL, '관내 공공기관에서 20일간 보조 업무를 수행하며 소득 창출 (72명 선발)', '온라인 접수', '일 51,600원 및 중식비 9,000원 별도 지급', '고정일', '2026-06-10', '2026-06-10 00:00:00', '2026-06-12 17:00', 'https://www.gangbuk.go.kr:18000/portal/bbs/B0000266/list.do?menuNo=200694', NULL, '강북구', '정기', 19, 39, NULL, '무관 (특별선발 시 수급자, 차상위 우대)', '접수 시작일 기준 강북구 주민등록', '청년', '최근 2년(''24년 여름~''26년 겨울) 근무자 제외', NULL, '2026-07-19', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (55, 'INST-0055', '동대문구 청년취업 자격취득 활동 지원사업', 21, '최대 20만원 한도', NULL, '최소 10만원 이상 증빙된 자격증 시험 응시료를 최대 20만원까지 지역상품권으로 환급', '이메일 제출', '2026년부터 상/하반기 분할 운영, 2년마다 1회 지원 가능', '예산소진시마감', '2026-06-30 (상반기)', '2026-06-30 00:00:00', NULL, 'https://www.ddm.go.kr/www/selectBbsNttView.do?key=198&bbsNo=38&nttNo=180877', 'ddm2030@ddm.go.kr', '동대문구', '정기', 19, 39, NULL, '무관', '신청일 기준 동대문구 거주, 고용보험 미가입자', '미취업 청년 (단기근로자, 대학/휴학생 가능)', '국민취업지원제도, 서울시 청년수당 수혜자 불가', NULL, '2026-07-19', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (56, 'INST-0056', '청년 국가기술자격시험 응시료 지원사업', 53, '1인당 연간 3회 한도', '응시료의 50%', '한국산업인력공단 시행 국가기술자격시험 원서 접수 시 응시료 50% 즉시 감면', '온라인 원서접수 시 자동 적용', '자치구 사업과 별도로 작동하는 국가 지원망', '예산소진시마감', NULL, NULL, NULL, 'https://job.gg.go.kr/jobSprt/detail.do?seq=2372', '1644-8000', NULL, '상시', NULL, 34, NULL, '무관', NULL, '만 34세 이하 청년', NULL, NULL, '2026-07-19', 2026, NULL);
INSERT INTO policies (policy_id, external_ref, policy_name, agency_id, support_period, cost, summary, application_method, notes, deadline_type, deadline_date_raw, application_deadline, result_note, link, contact, application_region, schedule_type, age_min, age_max, exception_age, income_criteria, qualification_text, support_target, duplication_restriction, original_notice, last_checked_at, info_reference_year, is_lifetime_limit_once) VALUES (57, 'INST-0057', '금천구 청년도전지원사업', 36, '전체 프로그램 이수 시 최대 350만원 지급', NULL, '고립, 은둔, 구직단념 등 취약 청년을 대상으로 사례관리, 진로탐색, 힐링 프로그램을 제공하고 이수 시 수당 지급', '홈페이지(https://www.youthblg.org) 온라인 신청', '가족돌봄청년에 준하는 자립준비청년, 고립청년 대상 심리·경제 결합 지원', '상시', NULL, NULL, NULL, 'https://linkareer.com/activity/309882', '010-4377-0597', '금천구', '상시', 18, 39, NULL, '무관', '6개월 이상 취/창업 및 교육 이력 없는 구직단념청년 등', '구직단념청년, 자립준비청년, 시설 입퇴소 청년, 북한이탈청년', NULL, NULL, '2026-07-19', 2026, NULL);

SELECT setval('policies_policy_id_seq', 57);

-- ---------- connect_policy_policy_types (다대다 매핑) ----------
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (1, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (1, 2);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (1, 1);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (2, 3);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (2, 2);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (3, 1);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (3, 3);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (3, 2);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (4, 1);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (5, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (5, 3);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (6, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (7, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (8, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (9, 3);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (10, 3);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (11, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (11, 2);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (12, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (13, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (14, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (15, 2);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (15, 1);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (15, 3);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (16, 3);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (17, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (18, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (19, 3);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (19, 1);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (20, 3);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (21, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (22, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (23, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (24, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (25, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (26, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (27, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (27, 2);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (28, 3);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (29, 1);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (30, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (30, 3);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (31, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (31, 3);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (32, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (32, 3);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (33, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (33, 3);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (34, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (34, 3);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (35, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (35, 3);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (36, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (36, 3);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (37, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (37, 3);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (38, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (38, 3);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (39, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (39, 3);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (40, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (40, 3);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (41, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (41, 3);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (42, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (42, 3);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (43, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (43, 3);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (44, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (44, 3);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (45, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (45, 3);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (46, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (46, 3);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (47, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (47, 3);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (48, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (48, 3);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (49, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (50, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (51, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (52, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (53, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (54, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (55, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (55, 3);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (56, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (56, 3);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (57, 4);
INSERT INTO connect_policy_policy_types (policy_id, policy_type_id) VALUES (57, 3);

-- ---------- connect_policy_documents (필요서류 행 분리 매핑) ----------
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (1, 56);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (1, 84);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (2, 75);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (2, 80);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (2, 10);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (2, 7);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (2, 36);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (2, 65);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (2, 4);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (3, 50);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (3, 85);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (3, 27);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (3, 22);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (3, 53);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (4, 80);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (4, 73);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (4, 7);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (4, 76);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (4, 1);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (4, 49);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (4, 35);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (5, 94);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (5, 3);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (5, 41);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (6, 6);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (6, 25);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (6, 81);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (7, 87);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (7, 9);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (7, 100);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (7, 93);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (7, 7);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (7, 5);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (7, 78);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (8, 70);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (8, 59);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (9, 79);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (11, 54);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (11, 30);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (11, 74);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (11, 90);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (11, 12);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (12, 80);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (12, 7);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (12, 72);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (12, 83);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (12, 96);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (13, 80);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (13, 2);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (13, 98);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (13, 43);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (14, 77);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (14, 42);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (15, 31);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (15, 91);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (15, 14);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (16, 40);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (18, 60);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (21, 38);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (21, 70);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (21, 17);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (21, 67);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (21, 68);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (21, 95);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (21, 69);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (24, 54);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (24, 11);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (24, 45);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (24, 52);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (25, 54);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (25, 48);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (26, 63);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (26, 20);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (27, 89);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (27, 57);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (27, 96);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (31, 64);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (31, 18);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (31, 44);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (31, 15);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (31, 48);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (31, 99);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (31, 32);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (32, 39);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (37, 26);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (38, 54);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (38, 61);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (38, 21);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (38, 95);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (38, 82);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (38, 23);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (38, 33);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (39, 54);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (39, 37);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (39, 92);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (39, 24);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (39, 46);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (39, 63);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (39, 58);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (39, 95);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (40, 13);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (40, 66);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (40, 19);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (41, 62);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (43, 97);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (43, 28);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (44, 54);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (44, 34);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (44, 16);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (44, 46);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (44, 51);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (44, 58);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (44, 95);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (44, 29);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (46, 82);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (46, 24);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (46, 47);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (49, 8);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (49, 70);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (49, 86);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (49, 88);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (51, 71);
INSERT INTO connect_policy_documents (policy_id, document_id) VALUES (55, 55);
