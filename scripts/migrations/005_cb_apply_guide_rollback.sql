-- ---------------------------------------------------------------------------
-- 005 되돌리기.
-- ---------------------------------------------------------------------------
-- 004보다 먼저 되돌린다. 004 롤백은 004가 만든 컬럼만 지우므로 순서를
-- 지키지 않으면 apply_guide_* 2개가 남는다.
--
-- 컬럼을 지우지 않고 값만 비우려면:
--
--   UPDATE cb.cb_institutions
--      SET apply_guide_easy = NULL, apply_guide_generated_at = NULL;
-- ---------------------------------------------------------------------------

DROP INDEX IF EXISTS cb.cb_inst_apply_guide_pending;

ALTER TABLE cb.cb_institutions
  DROP COLUMN IF EXISTS apply_guide_easy,
  DROP COLUMN IF EXISTS apply_guide_generated_at;
