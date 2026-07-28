-- ---------------------------------------------------------------------------
-- 005. 신청 방법 쉬운 말 가이드 (apply_guide_easy)
-- ---------------------------------------------------------------------------
-- 왜 004가 아니라 새 파일인가
--   004는 2026-07-28에 운영에 적용됐다. 적용된 마이그레이션은 그 시점의
--   스냅샷으로 두고 고치지 않는다 — 파일을 고치면 "적용된 것"과 "파일에 적힌
--   것"이 갈라져서, 나중에 스키마를 처음부터 재구성할 때 실제 운영과 다른
--   결과가 나온다.
--
-- 무엇을 만드는가
--   cb.cb_institutions에 컬럼 2개. 새 테이블도, public 접근도, FK도 없다.
--
-- 이 파일은 컬럼만 만든다
--   값을 채우는 배치(프롬프트 + backfill 스크립트)는 여기서 만들지 않는다.
--   프론트 쪽에서 별도로 진행한다. 실제로 값이 채워지는 순서는
--   required_documents_ai 백필이 끝난 뒤다.
--
-- 배치를 만드는 사람에게
--   1) 이 컬럼은 '데모용으로 지어낸 값'이 아니라 '원문을 쉽게 푼 값'이다.
--      apply_method 원문에 없는 절차를 새로 만들어 넣으면 안 된다.
--      app/cb/prompts/explain.md의 쉬운 말 설명과 같은 성격이고,
--      004의 apply_deadline(지어낸 값)과는 성격이 다르다.
--   2) 빈 결과는 빈 문자열이 아니라 NULL로 저장한다. 두 상태를 섞으면
--      재실행 대상을 고를 수 없다 (NULL = 미생성).
--   3) 실시간 답변 경로는 이 컬럼을 읽지 않는다. 배치 프롬프트는
--      app/cb/prompts_batch/에 두고, 경계는
--      scripts/check_prompt_isolation.py가 검사한다.
--      새 컬럼을 그 검사의 BATCH_COLUMNS에 추가해야 한다.
--
-- 되돌리기
--   scripts/migrations/005_cb_apply_guide_rollback.sql
-- ---------------------------------------------------------------------------

ALTER TABLE cb.cb_institutions
  -- 신청 방법을 16세 수준으로 풀어 쓴 가이드. apply_method 원문을 대체하지
  -- 않고 옆에 붙는다.
  --
  -- CHECK을 걸지 않는다. 자유 서술 텍스트라 형식을 제약할 근거가 없고,
  -- 길이 상한을 두면 절차가 긴 제도에서 조용히 잘린다.
  ADD COLUMN IF NOT EXISTS apply_guide_easy TEXT,

  ADD COLUMN IF NOT EXISTS apply_guide_generated_at TIMESTAMPTZ;

COMMENT ON COLUMN cb.cb_institutions.apply_guide_easy IS
  '신청 방법을 16세 수준으로 풀어 쓴 가이드. apply_method 원문을 대체하지 않고 '
  '옆에 붙는다. 원문에 없는 절차를 만들어 넣지 않는다 — 004의 데모용 날짜와 '
  '달리 이 값은 원문을 푼 것이다. NULL은 아직 생성하지 않은 상태이며, '
  '빈 문자열은 쓰지 않는다.';

COMMENT ON COLUMN cb.cb_institutions.apply_guide_generated_at IS
  'apply_guide_easy를 생성한 시각. NULL이면 미생성이다.';

-- 재생성 대상(아직 안 만든 행)을 고를 때 쓴다. 004의
-- cb_inst_req_docs_pending와 같은 목적이다.
CREATE INDEX IF NOT EXISTS cb_inst_apply_guide_pending
  ON cb.cb_institutions (serv_id) WHERE apply_guide_easy IS NULL;
