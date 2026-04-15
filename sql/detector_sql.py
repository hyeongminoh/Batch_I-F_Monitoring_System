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
        MBRSH_PGM_ID, FILE_ID,    ALARM_ID,
        ALARM_DT,     FREQUENCY_TYPE,
        EXP_MIN_TIME, EXP_MED_TIME, EXP_MAX_TIME,
        CHECK_TIME,   DELAY_MIN,    ANOMALY_SCORE,
        ALARM_MSG,    SEND_STS,
        REGR_ID,      REG_DT
    ) VALUES (
        :mbrsh,     :file_id,   SEQ_BAT_ALARM_HIS.NEXTVAL,
        :alarm_dt,  :freq_type,
        :exp_min,   :exp_med,   :exp_max,
        :chk_time,  :delay_min, :anomaly_score,
        :alarm_msg, '0',
        :regr_id,   SYSDATE
    )
"""
