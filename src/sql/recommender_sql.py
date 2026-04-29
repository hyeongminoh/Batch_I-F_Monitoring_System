"""
recommender.py 에서 사용하는 SQL 모음
"""

# USE_YN IN ('Y','P') : 이미 제외 중이거나 추천 대기인 FILE_ID는 재추천 제외
GET_MANAGED_FILE_IDS = """
    SELECT FILE_ID
    FROM BAT_MNTLST_EXC
    WHERE USE_YN IN ('Y', 'P')
"""

GET_ANALYSIS_DATA = """
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

INSERT_RECOMMENDATION = """
    INSERT INTO BAT_MNTLST_EXC (
        MBRSH_PGM_ID, FILE_ID, EXCL_RSN, USE_YN,
        REGR_ID, REG_DT
    ) VALUES (
        :mbrsh, :file_id, :excl_rsn, 'P',
        :regr_id, SYSDATE
    )
"""
