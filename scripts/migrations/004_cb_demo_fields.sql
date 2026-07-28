-- ---------------------------------------------------------------------------
-- 004. 데모용 신청 일정 + AI 추정 필요서류
-- ---------------------------------------------------------------------------
-- 경고 — 이 마이그레이션이 만드는 두 축은 성격이 정반대다
--
--   신청 일정 3종        : 근거가 아예 없는 '지어낸' 값이다 (데모 전용).
--   required_documents_ai: 근거에서 뽑거나 추정한 값이다 (정확도에 편차가 있다).
--
--   둘 다 003의 income_pct_max와 반대 방향이다. 003은 "근거가 없으면 값을
--   만들어내지 않는다"였다. 여기는 데모 화면을 채우는 것이 목적이라 그 원칙을
--   의도적으로 깬다. 그래서 지어낸 값에는 is_demo_deadline을, 추정값에는
--   required_documents_source를 붙여 출처를 DB 안에 같이 적어둔다.
--
--   실시간 챗봇 답변 경로(app/cb/nodes.py, explain.py, prompts/*.md)는 이
--   컬럼들을 읽지 않는다. 읽게 되는 순간 "지어내지 않는다" 원칙이 무너진다.
--   경계는 scripts/check_prompt_isolation.py가 검사한다.
--
-- 무엇을 만드는가
--   cb.cb_institutions에 컬럼 7개. 새 테이블도, public 접근도, FK도 없다.
--
-- 적용 이력
--   2026-07-28 운영 적용 완료. 이 파일은 그때의 스냅샷이고 더 고치지 않는다.
--   이후 추가된 컬럼은 005_cb_apply_guide.sql에 있다.
--
-- 신청 기간의 '끝'에 컬럼을 따로 두지 않는 이유
--   apply_period_end는 apply_deadline과 항상 같은 값이다. 두 벌로 저장하면
--   한쪽만 고쳤을 때 조용히 어긋난다. 응답에서 파생시킨다:
--     apply_period_end := apply_deadline
--
-- 되돌리기
--   scripts/migrations/004_cb_demo_fields_rollback.sql
--   (데모가 끝나면 되돌리는 것이 기본이다. 특히 날짜 3종은 남겨둘 이유가
--    없다 — 실제 일정이 생기면 그때 별도 컬럼으로 받는다.)
-- ---------------------------------------------------------------------------

ALTER TABLE cb.cb_institutions
  -- 'YYYY-MM-DD' 문자열. DATE가 아니라 TEXT인 것은 화면 계약을 그대로 담기
  -- 위해서다. 대신 형식이 어긋난 값은 CHECK로 막는다 — 프론트가 문자열을
  -- 그대로 파싱하므로 형식이 곧 계약이다. ('상시' 같은 값이 못 들어온다.)
  ADD COLUMN IF NOT EXISTS apply_deadline TEXT
    CHECK (apply_deadline IS NULL OR apply_deadline ~ '^\d{4}-\d{2}-\d{2}$'),

  ADD COLUMN IF NOT EXISTS apply_period_start TEXT
    CHECK (apply_period_start IS NULL OR apply_period_start ~ '^\d{4}-\d{2}-\d{2}$'),

  ADD COLUMN IF NOT EXISTS result_announcement_date TEXT
    CHECK (result_announcement_date IS NULL
           OR result_announcement_date ~ '^\d{4}-\d{2}-\d{2}$'),

  -- TRUE인 행의 날짜 3종은 전부 '지어낸 값'이다. 같은 배치가 같은 근거(없음)로
  -- 만들었으므로 플래그를 나누지 않는다 — --clear 한 번에 셋이 함께 지워진다.
  ADD COLUMN IF NOT EXISTS is_demo_deadline BOOLEAN NOT NULL DEFAULT FALSE,

  -- 서류 객체 배열. 문자열 배열이 아니다:
  --   [{"name": "가족관계증명서",
  --     "url": "https://efamily.scourt.go.kr",
  --     "url_type": "certificate_issuance"}]
  -- url은 null일 수 있다 (신분증·통장 사본·진단서처럼 발급 URL이 없는 서류).
  -- 빈 배열 []은 "근거를 못 찾았다", NULL은 "아직 배치를 안 돌렸다"이다.
  ADD COLUMN IF NOT EXISTS required_documents_ai JSONB,

  ADD COLUMN IF NOT EXISTS required_documents_generated_at TIMESTAMPTZ,

  -- 무엇을 근거로 뽑았는지. 내부 참고용이다 (프론트 경고 배지 용도가 아니다).
  --   basfrm           복지로가 준 실제 서식 파일명에서 추출 (가장 신뢰)
  --   source_text      제도 원문에 적힌 서류 문구에서 추출
  --   web_search       원문에 근거가 없어 웹에서 찾아 보강 (추정 섞임)
  --   generic_fallback 어디에도 근거가 없어 신분증+신청서만 넣음 (최소 채움)
  --   none             배치가 아무것도 만들지 못함 (LLM 실패 등). 예비값이다.
  ADD COLUMN IF NOT EXISTS required_documents_source TEXT
    CHECK (required_documents_source IS NULL
           OR required_documents_source IN
              ('basfrm', 'source_text', 'web_search', 'generic_fallback', 'none'));

COMMENT ON COLUMN cb.cb_institutions.apply_deadline IS
  '신청 마감일 ''YYYY-MM-DD''. 데모용으로 지어낸 값일 수 있다 — is_demo_deadline을 '
  '반드시 함께 읽어라. 응답의 apply_period_end가 곧 이 값이다. '
  'support_cycle=''수시''인 제도는 마감 개념이 없어 NULL이며, 화면에서 '
  '"상시 접수"로 렌더링한다.';

COMMENT ON COLUMN cb.cb_institutions.apply_period_start IS
  '신청 시작일 ''YYYY-MM-DD''. 항상 apply_deadline보다 앞선다(최소 7일). '
  '데모용 생성 규칙상 "시작 전인데 이미 마감" 상태는 산술적으로 발생할 수 없다.';

COMMENT ON COLUMN cb.cb_institutions.result_announcement_date IS
  '결과 발표일 ''YYYY-MM-DD''. 항상 apply_deadline보다 7~21일 뒤다.';

COMMENT ON COLUMN cb.cb_institutions.is_demo_deadline IS
  'TRUE면 apply_deadline/apply_period_start/result_announcement_date가 모두 '
  '데모용으로 생성된 값이다. 실제 일정이 아니다.';

COMMENT ON COLUMN cb.cb_institutions.required_documents_ai IS
  'LLM이 뽑은 필요서류 객체 배열 [{name, url, url_type}]. url_type은 '
  'form_download(서식 다운로드) / certificate_issuance(증명서 발급처) / '
  'info_page(안내 페이지). []는 "근거 없음", NULL은 "배치 미실행". '
  '실시간 챗봇 답변 경로는 이 컬럼을 읽지 않는다.';

COMMENT ON COLUMN cb.cb_institutions.required_documents_source IS
  'required_documents_ai의 근거. 내부 참고용이며 프론트 노출용이 아니다.';

-- 데모 값만 골라 지우거나 재생성 대상을 고를 때 쓴다.
CREATE INDEX IF NOT EXISTS cb_inst_is_demo_deadline
  ON cb.cb_institutions (is_demo_deadline) WHERE is_demo_deadline;

CREATE INDEX IF NOT EXISTS cb_inst_req_docs_pending
  ON cb.cb_institutions (serv_id) WHERE required_documents_ai IS NULL;
