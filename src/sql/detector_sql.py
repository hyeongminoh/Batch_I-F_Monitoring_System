"""
detector.py 전용 SQL 상수 모음.

[상수 목록]
  GET_EXCLUDED_FILE_IDS   : BAT_MNTLST_EXC에서 USE_YN='Y' 제외 파일 조회
  GET_HISTORICAL_DATA     : COM_BATFILE_TRN에서 모든 파일의 최근 N일 수신 이력 조회
  HAS_ALARM_TODAY         : 특정 파일의 오늘 알람 발생 여부 확인 (파일별 개별 쿼리)
  INSERT_ALARM            : BAT_ALARM_HIS에 M/V 알람 신규 등록
  GET_FREQ_MST            : BAT_FILE_FREQ_MST에서 수신 주기 프로필 전체 조회
                            EFFECTIVE_SRC 기준으로 MAIN(T) 또는 FB(D) 컬럼 선택 반환
  UPSERT_FREQ_MST_FB      : BAT_FILE_FREQ_MST 미등록 파일의 fallback 주기를 FB_* 컬럼에 기록
                            MAIN_FREQ_TYPE이 이미 있으면(trainer 분석 완료) UPDATE 건너뜀

[detector_detail.py 와의 차이]
  - GET_HISTORICAL_DATA: FILE_ID 필터 없이 전체 조회 (detector_detail_sql.py는 IN 절 필터)
  - HAS_ALARM_TODAY: 파일별 개별 쿼리 (detector_detail_sql.py는 GET_ALARMS_TODAY로 대체)
"""

GET_EXCLUDED_FILE_IDS = """
    SELECT FILE_ID
    FROM BAT_MNTLST_EXC
    WHERE USE_YN = 'Y'
"""

GET_HISTORICAL_DATA = """
    SELECT FILE_ID,
           FILE_NM,
           REG_DT,
           NVL(TOT_REC_CNT, 0)  AS TOT_REC_CNT,
           NVL(SEND_REC_CNT, 0) AS SEND_REC_CNT
    FROM   COM_BATFILE_TRN
    WHERE  TRANS_RCV_FG = 'R'
      AND  STS_CD = '3'
      AND  REG_DT >= SYSDATE - :days
    ORDER  BY FILE_ID, REG_DT
"""

HAS_ALARM_TODAY = """
    SELECT COUNT(*)
    FROM   BAT_ALARM_HIS
    WHERE  FILE_ID    = :file_id
      AND  ALARM_TYPE = :alarm_type
      AND  PROC_DY    = TO_CHAR(SYSDATE, 'YYYYMMDD')
"""

INSERT_ALARM = """
    INSERT INTO BAT_ALARM_HIS (
        MBRSH_PGM_ID, PROC_DY,
        FILE_ID,      FILE_NM,      ALARM_ID,
        ALARM_DT,     ALARM_TYPE,   FREQUENCY_TYPE,
        EXP_MIN_TIME, EXP_MED_TIME, EXP_MAX_TIME,
        CHECK_TIME,   DELAY_MIN,    ANOMALY_SCORE,
        ALARM_MSG,    SEND_STS,
        REGR_ID,      REG_DT,       UPDR_ID,  UPD_DT
    ) VALUES (
        :mbrsh,     TO_CHAR(SYSDATE, 'YYYYMMDD'),
        :file_id,   :file_nm,   SEQ_BAT_ALARM_HIS.NEXTVAL,
        :alarm_dt,  :alarm_type, :freq_type,
        :exp_min,   :exp_med,    :exp_max,
        :chk_time,  :delay_min,  :anomaly_score,
        :alarm_msg, '0',
        :regr_id,   SYSDATE,     :regr_id,  SYSDATE
    )
"""

# ICS_WRKDAY_MST에서 영업일 목록 조회 (BUSINESS_DAY 파일 판별·비영업일 스킵용)
# ICS_DATE가 VARCHAR2(8) YYYYMMDD 형태이므로 문자열 비교
GET_BUSINESS_DAYS = """
    SELECT ICS_DATE
    FROM   ICS_WRKDAY_MST
    WHERE  MBRSH_PGM_ID = 'A'
      AND  WORK_YN      = 'Y'
      AND  ICS_DATE    >= TO_CHAR(SYSDATE - :days, 'YYYYMMDD')
    ORDER  BY ICS_DATE
"""

# 전체 FILE_ID의 유효 주기 프로필을 한 번에 로드.
# EFFECTIVE_SRC 기준으로 MAIN(T) 또는 FB(D) 컬럼을 선택해 반환.
GET_FREQ_MST = """
    SELECT FILE_ID,
           EFFECTIVE_SRC,
           CASE WHEN EFFECTIVE_SRC = 'T' THEN MAIN_FREQ_TYPE    ELSE FB_FREQ_TYPE    END AS FREQ_TYPE,
           CASE WHEN EFFECTIVE_SRC = 'T' THEN MAIN_MEDIAN_GAP   ELSE FB_MEDIAN_GAP   END AS MEDIAN_GAP,
           CASE WHEN EFFECTIVE_SRC = 'T' THEN MAIN_STD_GAP      ELSE FB_STD_GAP      END AS STD_GAP,
           CASE WHEN EFFECTIVE_SRC = 'T' THEN MAIN_ROUND_GAP    ELSE FB_ROUND_GAP    END AS ROUND_GAP,
           CASE WHEN EFFECTIVE_SRC = 'T' THEN MAIN_DOM_PATTERN  ELSE FB_DOM_PATTERN  END AS DOM_PATTERN
    FROM BAT_FILE_FREQ_MST
    WHERE EFFECTIVE_SRC IS NOT NULL
"""

# detector가 BAT_FILE_FREQ_MST에 없는 FILE_ID의 주기를 임시 기록(fallback).
# MAIN_FREQ_TYPE IS NOT NULL(trainer 데이터 존재)이면 UPDATE를 건너뜀.
UPSERT_FREQ_MST_FB = """
    MERGE INTO BAT_FILE_FREQ_MST dst
    USING DUAL
    ON (dst.FILE_ID = :file_id)
    WHEN MATCHED THEN
        UPDATE SET
            FB_FREQ_TYPE     = :freq_type,
            FB_MEDIAN_GAP    = :median_gap,
            FB_STD_GAP       = :std_gap,
            FB_ROUND_GAP     = :round_gap,
            FB_DOM_PATTERN   = :dom_pattern,
            FB_SAMPLE_CNT    = :sample_cnt,
            FB_WIN_DAYS      = :win_days,
            FB_ANALYSIS_ST   = :analysis_st,
            FB_ANALYSIS_ED   = :analysis_ed,
            FB_UPD_DT        = SYSDATE,
            FB_REGR_ID       = :regr_id,
            EFFECTIVE_SRC    = 'D',
            EFFECTIVE_UPD_DT = SYSDATE,
            UPDR_ID          = :regr_id,
            UPD_DT           = SYSDATE
        WHERE MAIN_FREQ_TYPE IS NULL
    WHEN NOT MATCHED THEN
        INSERT (
            MBRSH_PGM_ID, FILE_ID,
            FB_FREQ_TYPE, FB_MEDIAN_GAP, FB_STD_GAP, FB_ROUND_GAP,
            FB_DOM_PATTERN, FB_SAMPLE_CNT, FB_WIN_DAYS,
            FB_ANALYSIS_ST, FB_ANALYSIS_ED,
            FB_UPD_DT, FB_REGR_ID,
            EFFECTIVE_SRC, EFFECTIVE_UPD_DT,
            REGR_ID, REG_DT, UPDR_ID, UPD_DT
        ) VALUES (
            :mbrsh, :file_id,
            :freq_type, :median_gap, :std_gap, :round_gap,
            :dom_pattern, :sample_cnt, :win_days,
            :analysis_st, :analysis_ed,
            SYSDATE, :regr_id,
            'D', SYSDATE,
            :regr_id, SYSDATE, :regr_id, SYSDATE
        )
"""
