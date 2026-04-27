"""
trainer.py 에서 사용하는 SQL 모음
"""

GET_EXCLUDED_FILE_IDS = """
    SELECT FILE_ID
    FROM BAT_MNTLST_EXC
    WHERE USE_YN = 'Y'
"""

GET_TRAINING_DATA = """
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

# trainer가 학습 완료 후 BAT_FILE_FREQ_MST MAIN_* 컬럼을 갱신한다.
# EFFECTIVE_SRC는 항상 'T'로 설정 (trainer 우선).
UPSERT_FREQ_MST = """
    MERGE INTO BAT_FILE_FREQ_MST dst
    USING DUAL
    ON (dst.FILE_ID = :file_id)
    WHEN MATCHED THEN
        UPDATE SET
            MAIN_FREQ_TYPE   = :freq_type,
            MAIN_MEDIAN_GAP  = :median_gap,
            MAIN_STD_GAP     = :std_gap,
            MAIN_ROUND_GAP   = :round_gap,
            MAIN_SAMPLE_CNT  = :sample_cnt,
            MAIN_WIN_DAYS    = :win_days,
            MAIN_ANALYSIS_ST = :analysis_st,
            MAIN_ANALYSIS_ED = :analysis_ed,
            MAIN_UPD_DT      = SYSDATE,
            MAIN_REGR_ID     = :regr_id,
            EFFECTIVE_SRC    = 'T',
            EFFECTIVE_UPD_DT = SYSDATE,
            UPDR_ID          = :regr_id,
            UPD_DT           = SYSDATE
    WHEN NOT MATCHED THEN
        INSERT (
            FILE_ID,
            MAIN_FREQ_TYPE, MAIN_MEDIAN_GAP, MAIN_STD_GAP, MAIN_ROUND_GAP,
            MAIN_SAMPLE_CNT, MAIN_WIN_DAYS, MAIN_ANALYSIS_ST, MAIN_ANALYSIS_ED,
            MAIN_UPD_DT, MAIN_REGR_ID,
            EFFECTIVE_SRC, EFFECTIVE_UPD_DT,
            REGR_ID, REG_DT, UPD_DT
        ) VALUES (
            :file_id,
            :freq_type, :median_gap, :std_gap, :round_gap,
            :sample_cnt, :win_days, :analysis_st, :analysis_ed,
            SYSDATE, :regr_id,
            'T', SYSDATE,
            :regr_id, SYSDATE, SYSDATE
        )
"""
