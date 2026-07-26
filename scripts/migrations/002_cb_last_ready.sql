-- ---------------------------------------------------------------------------
-- 002. 마지막으로 결과까지 마친 대화 기록
-- ---------------------------------------------------------------------------
-- 왜 필요한가
--   프론트는 새로고침이나 재로그인 뒤에 '이 사용자가 상담을 이미 마쳤는가'를
--   알아야 결과 화면으로 보낼지 상담 화면으로 보낼지 정한다. 기존 챗봇은
--   public.carers.diagnosis_completed로 그 판단을 했는데, cb는 carers에 쓰지
--   않는다(격리 원칙). 그래서 cb가 스스로 답할 수 있어야 한다.
--
--   대화 상태 자체는 LangGraph checkpointer에 있지만 user_id로 찾을 수 있는
--   색인이 없다. 여기에 마지막 완료 thread_id만 적어 둔다.
--
-- 무엇을 만드는가
--   cb.cb_user_profile에 컬럼 2개를 추가한다. 새 테이블도, public 스키마
--   접근도, FK도 없다.
--
-- 되돌리기
--   ALTER TABLE cb.cb_user_profile
--     DROP COLUMN last_ready_thread_id,
--     DROP COLUMN last_ready_at;
-- ---------------------------------------------------------------------------

ALTER TABLE cb.cb_user_profile
  ADD COLUMN IF NOT EXISTS last_ready_thread_id TEXT,
  ADD COLUMN IF NOT EXISTS last_ready_at        TIMESTAMPTZ;

COMMENT ON COLUMN cb.cb_user_profile.last_ready_thread_id IS
  '마지막으로 phase=ready에 도달한 대화의 thread_id. '
  'GET /api/v1/cb/threads/latest가 이 값으로 결과를 복원한다. '
  '대화 본문과 결과 카드는 여기 저장하지 않는다 — checkpointer가 단일 소스다.';
