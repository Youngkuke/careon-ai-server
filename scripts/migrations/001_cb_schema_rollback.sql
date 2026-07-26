-- ============================================================================
-- CareOn 챗봇 검색엔진 — 001 롤백
--
-- cb 스키마 안에 있는 모든 것을 제거한다:
--   cb_institutions / cb_user_profile / cb_saved_institutions / cb_sync_runs
--   LangGraph checkpointer 4개 테이블 (checkpoints 등)
--   cb.touch_updated_at() 함수, pgvector 확장, 모든 인덱스/트리거/제약
--
-- public 스키마의 기존 23개 테이블은 이 스크립트의 영향을 전혀 받지 않는다.
-- cb 스키마가 public의 어떤 객체도 참조하지 않으므로 CASCADE가 밖으로 번지지 않는다.
-- ============================================================================

DROP SCHEMA IF EXISTS cb CASCADE;
