-- ---------------------------------------------------------------------------
-- 003. 장애 중증도 / 소득 구간 구조화
-- ---------------------------------------------------------------------------
-- 왜 필요한가
--   지금까지 장애 정도와 소득 기준은 target_detail·select_criteria 원문
--   텍스트에만 있었다. eligibility.py가 문자열 부분일치로 다루고 있어서
--   '제외'는 되지만 '순위 가산'은 할 수 없었다.
--
--   실측(856건):
--     장애 언급 263건 중 구체적 유형 명시는 26건(10%)뿐이라 '유형'은 실효성이
--     낮다. 반면 중증도 명시는 41건(16%)이고, 자격이 실제로 갈리는 지점이다.
--     소득 언급 329건 중 "중위소득 O%"가 직접 적힌 것은 103건(31%)이고,
--     나머지 226건은 '차상위계층'처럼 카테고리명으로만 적혀 있다.
--
-- 무엇을 만드는가
--   cb.cb_institutions에 컬럼 2개. 새 테이블도, public 접근도, FK도 없다.
--
-- 설계 원칙 (중요)
--   기존 conditions/denied_conditions('장애등록'이 있다/없다)는 그대로 둔다.
--   disability_severity는 '있다/없다'가 아니라 '정도'라는 별개의 축이다.
--   두 축을 합치면 "장애는 있는데 중증도는 모름"이라는 중간 상태를 표현할 수
--   없게 되고, 그 상태가 198건(장애 언급 건의 75%)으로 가장 흔하다.
--
--   income_pct_max는 NULL을 허용한다. 원문에 근거가 없으면 값을 만들어내지
--   않는다 — 잘못된 %는 자격 판정을 조용히 왜곡시키고, 그게 값이 없는 것보다
--   훨씬 위험하다. NULL은 '제한 없음'이 아니라 '모름'이며, 랭킹 코드는
--   NULL을 감점 근거로 쓰지 않는다.
--
-- 되돌리기
--   ALTER TABLE cb.cb_institutions
--     DROP COLUMN disability_severity,
--     DROP COLUMN income_pct_max;
-- ---------------------------------------------------------------------------

ALTER TABLE cb.cb_institutions
  ADD COLUMN IF NOT EXISTS disability_severity TEXT
    CHECK (disability_severity IN ('severe', 'mild', 'unknown')),
  ADD COLUMN IF NOT EXISTS income_pct_max INTEGER;

COMMENT ON COLUMN cb.cb_institutions.disability_severity IS
  '제도가 요구하는 장애 정도. severe=심한(구 1~3급) / mild=심하지 않은(구 4~6급) '
  '/ unknown=장애를 언급하지만 정도는 명시하지 않음. '
  'NULL은 백필 전이거나 장애를 아예 언급하지 않는 제도다. '
  'conditions의 장애등록 여부와는 별개 축이다 (003 마이그레이션 주석 참고).';

COMMENT ON COLUMN cb.cb_institutions.income_pct_max IS
  '"기준중위소득 O% 이하"의 O값. 원문 regex 또는 법정 카테고리 사전(생계급여 32 / '
  '의료급여 40 / 주거급여 48 / 교육급여 50 / 차상위 50)으로 채운다. '
  'NULL은 "소득 제한 없음"이 아니라 "원문에 근거 없음"이다 — 감점 근거로 쓰지 않는다.';

-- 랭킹에서 두 컬럼을 함께 읽는다. 856행이라 인덱스 없이도 밀리초 단위지만,
-- 값이 있는 행만 부분 인덱스로 잡아두면 백필 리포트 쿼리가 가벼워진다.
CREATE INDEX IF NOT EXISTS cb_inst_income_pct
  ON cb.cb_institutions (income_pct_max) WHERE income_pct_max IS NOT NULL;

CREATE INDEX IF NOT EXISTS cb_inst_disability_severity
  ON cb.cb_institutions (disability_severity) WHERE disability_severity IS NOT NULL;
