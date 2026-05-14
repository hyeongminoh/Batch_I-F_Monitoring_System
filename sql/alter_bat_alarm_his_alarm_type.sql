-- ============================================================
-- BAT_ALARM_HIS ALARM_TYPE 컬럼 추가
-- M = 미수신 알람 (파일이 deadline 초과까지 미도착)
-- V = 건수 이상 알람 (파일 도착했으나 TOT_REC_CNT Z-score 초과)
-- ============================================================

ALTER TABLE BAT_ALARM_HIS ADD (
    ALARM_TYPE CHAR(1)
);

COMMENT ON COLUMN BAT_ALARM_HIS.ALARM_TYPE IS
'알람 유형. M=미수신(파일 미도착), V=건수이상(도착했으나 TOT_REC_CNT 이상). NULL은 기존 레코드(M으로 간주).';
