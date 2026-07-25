-- ============================================================================
-- CareOn 챗봇 검색엔진 — 격리 스키마 (001)
--
-- 이 마이그레이션은 기존 public 스키마의 23개 테이블을 일절 건드리지 않는다.
-- FK 참조도, JOIN도, 읽기도 하지 않는다. 유일한 연결점은 user_id 컬럼에
-- carer_id '값'을 복사해 넣는 것뿐이며, 이는 FK가 아니다.
--
-- 롤백:  scripts/migrations/001_cb_schema_rollback.sql
--        (DROP SCHEMA cb CASCADE 한 줄로 이 파일이 만든 모든 것이 사라진다.
--         pgvector 확장도 cb 스키마 안에 설치하므로 함께 정리된다.)
--
-- 대상 DB: PostgreSQL 17.6 (Supabase). pgvector 0.8.2 사용 가능 확인 완료.
--
-- ⚠️ 앱 연결은 반드시 search_path=cb 로 열어야 한다.
--    pgvector를 cb 스키마에 설치하므로 '<=>' 연산자도 cb 안에 만들어진다.
--    연산자는 타입과 달리 스키마 한정 표기(cb.vector)로 해결되지 않고
--    search_path로만 해석된다. 걸지 않으면 다음 오류가 난다:
--      operator does not exist: cb.vector <=> cb.vector
--    DSN 예: postgresql://...?options=-csearch_path%3Dcb
--    (LangGraph checkpointer도 같은 설정을 요구하므로 어차피 필요하다.)
--    search_path에 public을 넣지 않는다 — 기존 테이블 오접근을 막는 안전장치가 된다.
--    carers.region 1회 복사만 public.carers 로 명시적 한정해서 읽는다.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS cb;

-- pgvector를 cb 스키마 안에 설치한다. public/extensions 스키마를 오염시키지 않고,
-- DROP SCHEMA cb CASCADE 롤백 범위 안에 확장까지 포함시키기 위해서다.
-- 주의: vector가 이미 다른 스키마에 설치돼 있으면 IF NOT EXISTS가 그냥 건너뛰고
--       WITH SCHEMA는 무시된다. 이 DB에는 미설치 상태임을 확인했다.
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA cb;


-- ---------------------------------------------------------------------------
-- 공통: updated_at 자동 갱신
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION cb.touch_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;


-- ---------------------------------------------------------------------------
-- 1. cb_institutions — 제도 원본 저장소 + 임베딩
--
-- 공공데이터포털 2종 API가 유일한 소스다.
--   중앙부처복지서비스 461건 + 지자체복지서비스(서울) 395건 = 856건
-- 기존 public.policies(64건)는 이 작업에서 사용하지 않는다.
-- ---------------------------------------------------------------------------
CREATE TABLE cb.cb_institutions (
  serv_id             TEXT PRIMARY KEY,          -- 예: WLF00006688. 두 API 공통 식별자.
  source              TEXT NOT NULL CHECK (source IN ('central', 'local')),

  serv_nm             TEXT NOT NULL,             -- 제도명
  serv_dgst           TEXT,                      -- 한 줄 요약 (servDgst)

  -- --- 복지로 3종 필터 -------------------------------------------------------
  -- API 원본이 한글 라벨로 직접 내려주는 값이다 (LLM 추론이 아니다).
  -- 두 API의 필드명/구분자/표기가 다르므로 수집 시 아래 정규 라벨로 통일한다:
  --   중앙 lifeArray / trgterIndvdlArray / intrsThemaArray   (구분자 ",")
  --   지자체 lifeNmArray / trgterIndvdlNmArray / intrsThemaNmArray (구분자 ", ")
  --   생애주기는 '임신 · 출산'(공백 있음), 관심주제는 '임신·출산'(공백 없음)으로
  --   같은 API 안에서도 표기가 흔들린다 → '임신·출산'으로 정규화한다.
  --
  -- CHECK <@ 로 어휘를 DB가 강제한다. 값을 임의로 지어낼 수 없다.
  life_cycle_tags     TEXT[] NOT NULL DEFAULT '{}' CHECK (
    life_cycle_tags <@ ARRAY[
      '임신·출산','영유아','아동','청소년','청년','중장년','노년'
    ]::TEXT[]),

  household_tags      TEXT[] NOT NULL DEFAULT '{}' CHECK (
    household_tags <@ ARRAY[
      '저소득','장애인','한부모·조손','다자녀','다문화·탈북민','보훈대상자'
    ]::TEXT[]),

  theme_tags          TEXT[] NOT NULL DEFAULT '{}' CHECK (
    theme_tags <@ ARRAY[
      '신체건강','정신건강','생활지원','주거','일자리','문화·여가','안전·위기',
      '임신·출산','보육','교육','입양·위탁','보호·돌봄','서민금융','법률','에너지'
    ]::TEXT[]),

  -- 태그 출처. 원본에 태그가 비어 있는 건만 LLM으로 보완하고 'llm'으로 표시한다.
  tags_source         TEXT NOT NULL DEFAULT 'api' CHECK (tags_source IN ('api', 'llm')),

  -- --- 지역 ------------------------------------------------------------------
  ctpv_nm             TEXT,                      -- 시도명 (중앙부처는 NULL)
  sgg_nm              TEXT,                      -- 원본 sggNm 원문 보존

  -- region_scope 판정은 sggNm 빈값 여부가 아니라 25개 자치구 화이트리스트로 한다.
  -- 미분류 값(빈값, '-', '서울특별시교육청' 등)은 전부 metro로 흡수(fail-open)해서
  -- 어느 그룹에도 안 잡혀 검색에서 통째로 누락되는 건이 생기지 않게 한다.
  --   national 461 / metro 58 (본청 49 + 교육청 8 + '-' 1) / district 337
  region_scope        TEXT NOT NULL CHECK (region_scope IN ('national', 'metro', 'district')),

  -- 검색 필터용 단일 스칼라 키. 3그룹 합집합을 = ANY() 한 줄로 표현하기 위한 것.
  --   WHERE region_key = ANY(ARRAY['national','metro','강동구'])
  region_key          TEXT GENERATED ALWAYS AS (
    CASE WHEN region_scope = 'district' THEN sgg_nm ELSE region_scope END
  ) STORED,

  -- --- 상세 원문 (임베딩 소스 + translate 노드 입력) ---------------------------
  --
  -- ⚠️ 두 API의 결측 패턴이 다르다. 실측(각 12건 상세조회) 결과:
  --
  --   양쪽 다 채워짐 : target_detail, select_criteria, service_content,
  --                    serv_nm, serv_dgst, jur_org_nm, support_cycle, provision_type
  --   중앙만 채워짐  : criteria_year(crtrYr), contact(rprsCtadr)
  --   지자체만 채워짐: enforce_begin_ymd, enforce_end_ymd, origin_modified_ymd,
  --                    apply_method_nm(aplyMtdNm)
  --   apply_method   : 중앙은 applmetList[] 건당 평균 5개(신청/조사/결정/지급/사후관리
  --                    기관 단계별)를 병합. 지자체는 aplyMtdCn 단일 텍스트이고
  --                    약 33%가 비어 있다(12건 중 8건만 내용 있음).
  --
  -- translate 노드 원칙: 비어 있는 필드는 프롬프트에 아예 넣지 않는다.
  --   빈 문자열이나 "없음"을 넣으면 LLM이 "신청방법이 없습니다" 같은 잘못된 단정을
  --   만든다. 없는 섹션은 언급 없이 생략하고, 필요하면 detail_link(복지로 원문)로
  --   유도한다. 지자체 신청방법 결측 건은 jur_org_nm + extra_info.inqpl_ctadr(문의처)로
  --   안내한다.
  target_detail       TEXT,   -- 지원대상  중앙 tgtrDtlCn / 지자체 sprtTrgtCn
  select_criteria     TEXT,   -- 선정기준  slctCritCn (공통)
  service_content     TEXT,   -- 서비스내용 alwServCn (공통)
  apply_method        TEXT,   -- 신청방법  중앙 applmetList[] 병합 / 지자체 aplyMtdCn

  -- 복지로 화면의 "추가정보" 탭에 해당하는 4개 리스트를 정규화해서 담는다.
  -- 지자체 제도는 복지로 UI에 탭이 없지만, API에는 데이터가 있다
  -- (baslawList 12/12, basfrmList 11/12, inqplCtadrList 12/12, inqplHmpgReldList 2/12).
  -- 복지로 UI의 렌더링 선택을 우리 데이터 모델의 기준으로 삼지 않는다.
  --   {"baslaw": [...],        근거법령
  --    "basfrm": [...],        서식·구비서류
  --    "inqpl_ctadr": [...],   문의처 연락처
  --    "inqpl_hmpg": [...]}    관련 사이트
  extra_info          JSONB NOT NULL DEFAULT '{}'::JSONB,

  -- --- 부가 정보 -------------------------------------------------------------
  jur_org_nm          TEXT,   -- 소관부처·담당부서 (jurMnofNm / bizChrDeptNm)
  support_cycle       TEXT,   -- sprtCycNm
  provision_type      TEXT,   -- srvPvsnNm
  apply_method_nm     TEXT,   -- aplyMtdNm (지자체)
  detail_link         TEXT,   -- 복지로 상세 URL (servDtlLink)
  -- 중앙은 rprsCtadr를 그대로. 지자체는 이 필드가 없으므로
  -- extra_info.inqpl_ctadr의 첫 항목을 대표 연락처로 파생해서 채운다.
  contact             TEXT,
  criteria_year       INT,    -- crtrYr (중앙)
  enforce_begin_ymd   TEXT,   -- enfcBgngYmd (지자체, YYYYMMDD 문자열 원문)
  enforce_end_ymd     TEXT,   -- enfcEndYmd  (99991231 = 종료일 없음)
  origin_modified_ymd TEXT,   -- lastModYmd

  -- API 스키마가 바뀌어도 재수집 없이 복구할 수 있도록 원문을 통째로 보존한다.
  raw_list            JSONB,
  raw_detail          JSONB,

  -- --- 임베딩 / 신선도 --------------------------------------------------------
  embedding           cb.vector(1536),   -- OpenAI text-embedding-3-small
  embedding_model     TEXT,
  content_hash        TEXT,              -- 임베딩 대상 텍스트 해시 → 변경분만 재임베딩
  embedded_at         TIMESTAMPTZ,

  -- 대화 중 "확정 후보만 개별 재조회" 판단 기준.
  last_fetched_at     TIMESTAMPTZ NOT NULL,
  detail_fetched_at   TIMESTAMPTZ,

  -- 원본 API에서 사라진 건은 지우지 않고 내린다 (저장한 유저의 참조가 깨지지 않게).
  is_active           BOOLEAN NOT NULL DEFAULT TRUE,

  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 3종 필터는 배열 교집합(&&)으로 걸리므로 GIN.
CREATE INDEX cb_inst_life_gin  ON cb.cb_institutions USING GIN (life_cycle_tags);
CREATE INDEX cb_inst_hh_gin    ON cb.cb_institutions USING GIN (household_tags);
CREATE INDEX cb_inst_theme_gin ON cb.cb_institutions USING GIN (theme_tags);

-- 지역 3그룹 합집합 필터용.
CREATE INDEX cb_inst_region_key ON cb.cb_institutions (region_key) WHERE is_active;

-- 배치 수집이 "재임베딩 대상"을 찾을 때 쓴다.
CREATE INDEX cb_inst_embed_todo ON cb.cb_institutions (embedded_at NULLS FIRST, updated_at);

-- 대화 중 신선도 확인용.
CREATE INDEX cb_inst_fetched ON cb.cb_institutions (last_fetched_at) WHERE is_active;

-- ⚠️ 벡터 ANN 인덱스(HNSW/IVFFlat)는 의도적으로 만들지 않는다.
--    856건 × 1536차원 = 약 5MB로, exact 스캔이 밀리초 단위이고 recall 100%다.
--    ANN 인덱스는 근사라 이 규모에서는 정확도만 떨어뜨린다. 또 우리 쿼리는 항상
--    지역+태그 hard filter가 선행하므로 '필터링된 ANN' 누락 문제도 피해야 한다.
--    수만 건 규모가 되면 그때 HNSW를 검토한다.

CREATE TRIGGER cb_inst_touch BEFORE UPDATE ON cb.cb_institutions
  FOR EACH ROW EXECUTE FUNCTION cb.touch_updated_at();


-- ---------------------------------------------------------------------------
-- 2. cb_user_profile — 유저 프로필 (독립 저장)
-- ---------------------------------------------------------------------------
CREATE TABLE cb.cb_user_profile (
  -- JWT access_token의 sub 값 = public.carers.carer_id 값.
  -- FK가 아니다. 로그인 주체를 식별하기 위해 같은 값을 독립적으로 보관할 뿐이다.
  user_id             BIGINT PRIMARY KEY,

  region_sgg          TEXT,          -- 예: '강동구'
  region_copied_at    TIMESTAMPTZ,
  region_source       TEXT NOT NULL DEFAULT 'carers_onetime_copy',

  -- 자가진단 결과 등 복사분. 구조를 미리 못박지 않기 위해 JSONB로 둔다.
  -- 챗봇이 대화 중 필요한 것을 그때그때 되묻는 방식이라 고정 컬럼을 만들지 않는다.
  diagnosis_snapshot  JSONB NOT NULL DEFAULT '{}'::JSONB,

  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TRIGGER cb_user_profile_touch BEFORE UPDATE ON cb.cb_user_profile
  FOR EACH ROW EXECUTE FUNCTION cb.touch_updated_at();

COMMENT ON COLUMN cb.cb_user_profile.region_sgg IS
  'EXCEPTION: public.carers.region에서 1회 복사. 2026-07-26 승인. '
  'cb_user_profile 행이 없을 때만 읽고, 이후로는 carers를 다시 조회하지 않는다. '
  '이것이 public 스키마에 대한 유일하게 허용된 접근이다.';

-- 대화로 누적되는 3종 필터 태그는 여기 저장하지 않는다.
-- LangGraph checkpointer의 State가 단일 소스다 (이중 저장하면 어긋난다).


-- ---------------------------------------------------------------------------
-- 3. cb_saved_institutions — 유저가 저장한 제도
-- ---------------------------------------------------------------------------
CREATE TABLE cb.cb_saved_institutions (
  saved_id      BIGSERIAL PRIMARY KEY,
  user_id       BIGINT NOT NULL,              -- FK 아님 (위와 동일)
  serv_id       TEXT NOT NULL REFERENCES cb.cb_institutions(serv_id) ON DELETE CASCADE,

  -- 'exact' = 유사도 임계값 이상, 'maybe' = 임계값 미만이지만 3종 필터는 통과
  match_bucket  TEXT CHECK (match_bucket IN ('exact', 'maybe')),
  similarity    REAL,
  match_reason  TEXT,                          -- AI가 제시한 매칭 근거
  translated    TEXT,                          -- translate 노드 결과 캐시

  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, serv_id)
);

CREATE INDEX cb_saved_user ON cb.cb_saved_institutions (user_id, created_at DESC);


-- ---------------------------------------------------------------------------
-- 4. cb_sync_runs — 배치 수집 이력
-- ---------------------------------------------------------------------------
CREATE TABLE cb.cb_sync_runs (
  run_id          BIGSERIAL PRIMARY KEY,
  source          TEXT NOT NULL CHECK (source IN ('central', 'local')),
  status          TEXT NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'success', 'failed')),
  fetched_count   INT NOT NULL DEFAULT 0,
  inserted_count  INT NOT NULL DEFAULT 0,
  updated_count   INT NOT NULL DEFAULT 0,
  embedded_count  INT NOT NULL DEFAULT 0,

  -- data.go.kr은 동시 요청 시 resultCode=99 UNKNOWN_ERROR + totalCount=0을
  -- 간헐적으로 반환한다. 수집기는 resultCode를 반드시 검사하고 재시도해야 하며,
  -- 끝내 실패한 건은 여기에 남긴다. totalCount만 믿으면 데이터가 조용히 누락된다.
  error_text      TEXT,

  started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at     TIMESTAMPTZ
);

CREATE INDEX cb_sync_recent ON cb.cb_sync_runs (source, started_at DESC);


-- ---------------------------------------------------------------------------
-- 5. LangGraph checkpointer (이 파일에서 만들지 않음)
--
-- langgraph PostgresSaver.setup()이 아래 4개를 자동 생성한다:
--   checkpoints, checkpoint_blobs, checkpoint_writes, checkpoint_migrations
-- 연결 시 search_path=cb 로 고정해서 public이 아니라 cb 안에 만들어지게 한다.
-- 이 4개만 cb_ prefix가 없는데, 라이브러리가 이름을 강제하기 때문이다.
-- cb 스키마 안에 있으므로 롤백 범위는 동일하다.
--
-- thread_id = carer_id → 재로그인해도 대화가 이어진다.
-- 별도 대화 테이블은 만들지 않는다 (기존 public.user_conversation_state와 무관).
-- ---------------------------------------------------------------------------

COMMENT ON SCHEMA cb IS
  'CareOn 챗봇 검색엔진 전용. public 스키마의 기존 테이블을 참조하지 않는다. '
  '롤백: DROP SCHEMA cb CASCADE;';
