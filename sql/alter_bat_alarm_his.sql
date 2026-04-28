-- ============================================================
-- BAT_ALARM_HIS - FILE_NM 컬럼 추가
-- COM_BATFILE_TRN.FILE_NM 과 동일한 타입 (VARCHAR2(40))
-- 형식: FILE_ID.YYYYMMDD.HHMMSS (예: EB140402.20260403.145707)
-- ============================================================

ALTER TABLE BAT_ALARM_HIS
    ADD FILE_NM VARCHAR2(40);

COMMENT ON COLUMN BAT_ALARM_HIS.FILE_NM IS
'알람 발생 시점 기준 해당 FILE_ID의 가장 최근 수신 파일명. COM_BATFILE_TRN.FILE_NM과 동일.';
