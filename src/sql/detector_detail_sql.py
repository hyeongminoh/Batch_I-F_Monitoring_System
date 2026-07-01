"""
detector_detail.py 전용 SQL 상수 모음.
detector_sql.py와 다른 부분만 여기에 정의하며, 나머지는 detector_sql.py를 공유한다.

[상수 목록]
  GET_HISTORICAL_DATA_FILTERED
      오늘 수신 예정 파일(expected)의 90일 이력만 조회.
      IN 절의 bind 변수 목록({placeholders})은 호출 측에서 동적으로 치환한다.
      Oracle IN 절 상한(1000개) 대비 900개씩 chunk 분할 처리.
      detector_sql.py의 GET_HISTORICAL_DATA(전체 조회)를 대체.

  GET_ALARMS_TODAY
      오늘 발생한 모든 알람을 run 시작 시 1회만 조회.
      반환값을 {(FILE_ID, ALARM_TYPE)} set으로 변환해 O(1) 조회로 활용.
      detector_sql.py의 HAS_ALARM_TODAY(파일별 개별 쿼리, 최대 8,000회)를 대체.
"""

# 오늘 수신 예정 파일의 90일 이력 조회.
# IN 절은 호출 측에서 {placeholders} 에 ':id0, :id1, ...' 형태로 치환.
GET_HISTORICAL_DATA_FILTERED = """
    SELECT FILE_ID,
           FILE_NM,
           REG_DT,
           NVL(TOT_REC_CNT, 0)  AS TOT_REC_CNT,
           NVL(SEND_REC_CNT, 0) AS SEND_REC_CNT
    FROM   COM_BATFILE_TRN
    WHERE  TRANS_RCV_FG = 'R'
      AND  STS_CD       = '3'
      AND  REG_DT      >= SYSDATE - :days
      AND  FILE_ID      IN ({placeholders})
    ORDER  BY FILE_ID, REG_DT
"""

# 오늘 발생한 알람 전체 조회.
# run 시작 시 1회만 실행, {(file_id, alarm_type)} set 으로 변환해 O(1) 조회.
# detector_sql.py 의 HAS_ALARM_TODAY (파일별 개별 쿼리) 를 대체.
GET_ALARMS_TODAY = """
    SELECT FILE_ID,
           ALARM_TYPE
    FROM   BAT_ALARM_HIS
    WHERE  TRUNC(ALARM_DT) = TRUNC(SYSDATE)
"""
