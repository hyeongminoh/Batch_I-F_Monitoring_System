"""
detector.py 에서 사용하는 SQL 모음
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
    WHERE  FILE_ID = :file_id
      AND  TRUNC(ALARM_DT) = TRUNC(SYSDATE)
"""

INSERT_ALARM = """
    INSERT INTO BAT_ALARM_HIS (
        MBRSH_PGM_ID, FILE_ID,    FILE_NM,    ALARM_ID,
        ALARM_DT,     FREQUENCY_TYPE,
        EXP_MIN_TIME, EXP_MED_TIME, EXP_MAX_TIME,
        CHECK_TIME,   DELAY_MIN,    ANOMALY_SCORE,
        ALARM_MSG,    SEND_STS,
        REGR_ID,      REG_DT
    ) VALUES (
        :mbrsh,     :file_id,   :file_nm,   SEQ_BAT_ALARM_HIS.NEXTVAL,
        :alarm_dt,  :freq_type,
        :exp_min,   :exp_med,   :exp_max,
        :chk_time,  :delay_min, :anomaly_score,
        :alarm_msg, '0',
        :regr_id,   SYSDATE
    )
"""

# 전체 FILE_ID의 유효 주기 프로필을 한 번에 로드.
# EFFECTIVE_SRC 기준으로 MAIN(T) 또는 FB(D) 컬럼을 선택해 반환.
GET_FREQ_MST = """
    SELECT FILE_ID,
           EFFECTIVE_SRC,
           CASE WHEN EFFECTIVE_SRC = 'T' THEN MAIN_FREQ_TYPE ELSE FB_FREQ_TYPE END AS FREQ_TYPE,
           CASE WHEN EFFECTIVE_SRC = 'T' THEN MAIN_MEDIAN_GAP ELSE FB_MEDIAN_GAP END AS MEDIAN_GAP,
           CASE WHEN EFFECTIVE_SRC = 'T' THEN MAIN_STD_GAP ELSE FB_STD_GAP END AS STD_GAP,
           CASE WHEN EFFECTIVE_SRC = 'T' THEN MAIN_ROUND_GAP ELSE FB_ROUND_GAP END AS ROUND_GAP
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
            FILE_ID,
            FB_FREQ_TYPE, FB_MEDIAN_GAP, FB_STD_GAP, FB_ROUND_GAP,
            FB_SAMPLE_CNT, FB_WIN_DAYS, FB_ANALYSIS_ST, FB_ANALYSIS_ED,
            FB_UPD_DT, FB_REGR_ID,
            EFFECTIVE_SRC, EFFECTIVE_UPD_DT,
            REGR_ID, REG_DT, UPD_DT
        ) VALUES (
            :file_id,
            :freq_type, :median_gap, :std_gap, :round_gap,
            :sample_cnt, :win_days, :analysis_st, :analysis_ed,
            SYSDATE, :regr_id,
            'D', SYSDATE,
            :regr_id, SYSDATE, SYSDATE
        )
"""
