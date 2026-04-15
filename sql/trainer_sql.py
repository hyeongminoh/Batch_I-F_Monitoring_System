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
