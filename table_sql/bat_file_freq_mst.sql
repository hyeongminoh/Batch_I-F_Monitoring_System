-- ============================================================
-- BAT_FILE_FREQ_MST
-- 배치파일별 유입 주기 프로필
--
-- 갱신 주체별 컬럼 분리
--   MAIN_* : trainer가 180일 분석 결과를 기록 (매주 일요일 02:00)
--   FB_*   : detector가 90일 분석으로 임시 기록 (MAIN_FREQ_TYPE IS NULL 인 FILE_ID 한정)
--
-- EFFECTIVE_SRC 결정 규칙 (애플리케이션 책임)
--   MAIN_FREQ_TYPE IS NOT NULL  → 'T' (trainer 우선)
--   MAIN_FREQ_TYPE IS NULL      → 'D' (detector fallback)
--
-- 인덱스: PK(FILE_ID)만 사용
-- ============================================================

CREATE TABLE BAT_FILE_FREQ_MST (
    FILE_ID             VARCHAR2(10)    NOT NULL,   -- COM_BATFILE_TRN.FILE_ID

    -- ── [MAIN] trainer 분석 결과 ──────────────────────────
    -- trainer 실행 시 UPSERT. 항상 EFFECTIVE_SRC='T' 로 설정.
    MAIN_FREQ_TYPE      VARCHAR2(40),               -- DAILY/WEEKLY/MONTHLY/IRREGULAR/EVERY_N_DAYS
    MAIN_MEDIAN_GAP     NUMBER(12,4),               -- 연속 수신일 간격 중앙값(일)
    MAIN_STD_GAP        NUMBER(12,4),               -- 연속 수신일 간격 표준편차(일)
    MAIN_ROUND_GAP      NUMBER(10,0),               -- ROUND(MAIN_MEDIAN_GAP). 분류 기준값
    MAIN_SAMPLE_CNT     NUMBER(10),                 -- 분석에 사용된 수신 건수
    MAIN_WIN_DAYS       NUMBER(10)  DEFAULT 180,    -- 분석 윈도우 (고정 180)
    MAIN_ANALYSIS_ST    DATE,                       -- 분석 데이터 최솟값(REG_DT)
    MAIN_ANALYSIS_ED    DATE,                       -- 분석 데이터 최댓값(REG_DT)
    MAIN_UPD_DT         DATE,                       -- trainer 마지막 갱신 일시
    MAIN_REGR_ID        VARCHAR2(8),                -- trainer 등록자 ID

    -- ── [FB] detector fallback 분석 결과 ─────────────────
    -- MAIN_FREQ_TYPE IS NULL 인 경우에만 detector가 기록.
    -- trainer 실행 후에도 FB_* 컬럼은 보존(덮어쓰지 않음).
    FB_FREQ_TYPE        VARCHAR2(40),
    FB_MEDIAN_GAP       NUMBER(12,4),
    FB_STD_GAP          NUMBER(12,4),
    FB_ROUND_GAP        NUMBER(10,0),
    FB_SAMPLE_CNT       NUMBER(10),
    FB_WIN_DAYS         NUMBER(10)  DEFAULT 90,     -- 분석 윈도우 (고정 90)
    FB_ANALYSIS_ST      DATE,
    FB_ANALYSIS_ED      DATE,
    FB_UPD_DT           DATE,                       -- detector 마지막 fallback 일시
    FB_REGR_ID          VARCHAR2(8),

    -- ── [EFFECTIVE] 알람·메시지에 사용 중인 출처 ─────────
    -- T=trainer(MAIN_*), D=detector(FB_*)
    EFFECTIVE_SRC       CHAR(1),
    EFFECTIVE_UPD_DT    DATE,

    -- ── 공통 감사 컬럼 ────────────────────────────────────
    REGR_ID             VARCHAR2(8),
    REG_DT              DATE DEFAULT SYSDATE    NOT NULL,
    UPDR_ID             VARCHAR2(8),
    UPD_DT              DATE DEFAULT SYSDATE    NOT NULL
);

ALTER TABLE BAT_FILE_FREQ_MST
    ADD CONSTRAINT PK_BAT_FILE_FREQ_MST PRIMARY KEY (FILE_ID);

COMMENT ON TABLE BAT_FILE_FREQ_MST IS
'배치파일(FILE_ID)별 유입 주기 프로필. MAIN_*=trainer(180일), FB_*=detector fallback(90일). EFFECTIVE_SRC로 현재 사용 출처 구분.';

COMMENT ON COLUMN BAT_FILE_FREQ_MST.FILE_ID IS
'배치파일 식별자. COM_BATFILE_TRN.FILE_ID와 동일.';

-- MAIN
COMMENT ON COLUMN BAT_FILE_FREQ_MST.MAIN_FREQ_TYPE IS
'trainer 분석 결과 주기 유형. DAILY/WEEKLY/MONTHLY/IRREGULAR/EVERY_N_DAYS.';
COMMENT ON COLUMN BAT_FILE_FREQ_MST.MAIN_MEDIAN_GAP IS
'trainer: 연속 수신일 간격(일) 중앙값.';
COMMENT ON COLUMN BAT_FILE_FREQ_MST.MAIN_STD_GAP IS
'trainer: 연속 수신일 간격(일) 표준편차.';
COMMENT ON COLUMN BAT_FILE_FREQ_MST.MAIN_ROUND_GAP IS
'trainer: ROUND(MAIN_MEDIAN_GAP). 분류 규칙의 기준값으로 사용.';
COMMENT ON COLUMN BAT_FILE_FREQ_MST.MAIN_SAMPLE_CNT IS
'trainer: 분석에 사용된 수신 건수.';
COMMENT ON COLUMN BAT_FILE_FREQ_MST.MAIN_WIN_DAYS IS
'trainer: 분석 윈도우 일수. 고정 180.';
COMMENT ON COLUMN BAT_FILE_FREQ_MST.MAIN_ANALYSIS_ST IS
'trainer: 분석 포함 데이터의 최소 수신 시점(REG_DT).';
COMMENT ON COLUMN BAT_FILE_FREQ_MST.MAIN_ANALYSIS_ED IS
'trainer: 분석 포함 데이터의 최대 수신 시점(REG_DT).';
COMMENT ON COLUMN BAT_FILE_FREQ_MST.MAIN_UPD_DT IS
'trainer에 의한 마지막 갱신 일시. NULL이면 trainer 미실행.';
COMMENT ON COLUMN BAT_FILE_FREQ_MST.MAIN_REGR_ID IS
'trainer 실행 등록자 ID.';

-- FB
COMMENT ON COLUMN BAT_FILE_FREQ_MST.FB_FREQ_TYPE IS
'detector fallback 분석 결과 주기 유형.';
COMMENT ON COLUMN BAT_FILE_FREQ_MST.FB_MEDIAN_GAP IS
'detector fallback: 연속 수신일 간격(일) 중앙값.';
COMMENT ON COLUMN BAT_FILE_FREQ_MST.FB_STD_GAP IS
'detector fallback: 연속 수신일 간격(일) 표준편차.';
COMMENT ON COLUMN BAT_FILE_FREQ_MST.FB_ROUND_GAP IS
'detector fallback: ROUND(FB_MEDIAN_GAP).';
COMMENT ON COLUMN BAT_FILE_FREQ_MST.FB_SAMPLE_CNT IS
'detector fallback: 분석에 사용된 수신 건수.';
COMMENT ON COLUMN BAT_FILE_FREQ_MST.FB_WIN_DAYS IS
'detector fallback: 분석 윈도우 일수. 고정 90.';
COMMENT ON COLUMN BAT_FILE_FREQ_MST.FB_ANALYSIS_ST IS
'detector fallback: 분석 포함 데이터의 최소 수신 시점(REG_DT).';
COMMENT ON COLUMN BAT_FILE_FREQ_MST.FB_ANALYSIS_ED IS
'detector fallback: 분석 포함 데이터의 최대 수신 시점(REG_DT).';
COMMENT ON COLUMN BAT_FILE_FREQ_MST.FB_UPD_DT IS
'detector에 의한 마지막 fallback 갱신 일시.';
COMMENT ON COLUMN BAT_FILE_FREQ_MST.FB_REGR_ID IS
'detector fallback 등록자 ID.';

-- EFFECTIVE
COMMENT ON COLUMN BAT_FILE_FREQ_MST.EFFECTIVE_SRC IS
'알람·메시지 생성에 사용 중인 출처. T=trainer(MAIN_*), D=detector(FB_*). MAIN_FREQ_TYPE IS NOT NULL이면 항상 T.';
COMMENT ON COLUMN BAT_FILE_FREQ_MST.EFFECTIVE_UPD_DT IS
'EFFECTIVE_SRC가 마지막으로 변경된 일시.';
