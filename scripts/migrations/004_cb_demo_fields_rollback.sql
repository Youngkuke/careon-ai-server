-- ---------------------------------------------------------------------------
-- 004 되돌리기. 데모가 끝나면 이걸 적용한다.
-- ---------------------------------------------------------------------------
-- 컬럼을 통째로 지우기 전에, 데이터만 지우고 싶다면 아래를 쓴다:
--
--   UPDATE cb.cb_institutions
--      SET apply_deadline = NULL, apply_period_start = NULL,
--          result_announcement_date = NULL, is_demo_deadline = FALSE
--    WHERE is_demo_deadline;
--
--   UPDATE cb.cb_institutions
--      SET required_documents_ai = NULL,
--          required_documents_generated_at = NULL,
--          required_documents_source = NULL;
--
-- 005(apply_guide_*)를 먼저 되돌린 뒤에 이걸 적용한다.
-- ---------------------------------------------------------------------------

DROP INDEX IF EXISTS cb.cb_inst_is_demo_deadline;
DROP INDEX IF EXISTS cb.cb_inst_req_docs_pending;

ALTER TABLE cb.cb_institutions
  DROP COLUMN IF EXISTS apply_deadline,
  DROP COLUMN IF EXISTS apply_period_start,
  DROP COLUMN IF EXISTS result_announcement_date,
  DROP COLUMN IF EXISTS is_demo_deadline,
  DROP COLUMN IF EXISTS required_documents_ai,
  DROP COLUMN IF EXISTS required_documents_generated_at,
  DROP COLUMN IF EXISTS required_documents_source;
