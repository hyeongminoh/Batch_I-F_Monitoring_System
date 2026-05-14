-- ============================================================
-- BAT_FILE_FREQ_MST DOM_PATTERN 컬럼 추가
-- EVERY_N_DAYS / MONTHLY 파일의 월중 수신일 패턴 저장
--   예) 매월 5일·15일 수신 → "5,15"
--       매월 1일·10일·20일 수신 → "1,10,20"
-- ============================================================

ALTER TABLE BAT_FILE_FREQ_MST ADD (
    MAIN_DOM_PATTERN    VARCHAR2(200),
    FB_DOM_PATTERN      VARCHAR2(200)
);

COMMENT ON COLUMN BAT_FILE_FREQ_MST.MAIN_DOM_PATTERN IS
'trainer: 월중 도착일 패턴. EVERY_N_DAYS/MONTHLY 파일에서 탐지. 쉼표 구분 day_of_month 목록 (예: "5,15"). DAILY/WEEKLY/IRREGULAR는 NULL.';

COMMENT ON COLUMN BAT_FILE_FREQ_MST.FB_DOM_PATTERN IS
'detector fallback: 월중 도착일 패턴. EVERY_N_DAYS/MONTHLY 파일에서 탐지.';
