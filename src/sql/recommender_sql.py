"""
recommender.py 전용 SQL 상수 모음.

[상수 목록]
  GET_MANAGED_FILE_IDS
      BAT_MNTLST_EXC에서 USE_YN IN ('Y','P') 파일 조회.
      이미 제외 확정('Y') 또는 추천 대기('P') 상태인 파일은 재추천하지 않는다.

  GET_ANALYSIS_DATA
      COM_BATFILE_TRN에서 최근 90일 수신 이력 조회.
      recommender는 이 데이터로 classify_frequency()를 실행해
      IRREGULAR 또는 샘플 부족 파일을 탐지한다.

  INSERT_RECOMMENDATION
      탐지된 후보를 BAT_MNTLST_EXC에 USE_YN='P'(추천대기)로 등록.
      담당자 검토 후 'Y'(제외) 또는 'N'(유지)으로 최종 결정된다.
      이미 관리 중인 FILE_ID는 GET_MANAGED_FILE_IDS로 사전 필터링하므로
      중복 INSERT 발생 가능성이 낮지만, 운영 환경에서는 PK/UK 제약 확인 권장.
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
        REGR_ID, REG_DT, UPDR_ID, UPD_DT
    ) VALUES (
        :mbrsh, :file_id, :excl_rsn, 'P',
        :regr_id, SYSDATE, :regr_id, SYSDATE
    )
"""
