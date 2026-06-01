"""
sender.py 전용 SQL 상수 모음.

[상수 목록]
  GET_PENDING_ALARMS
      BAT_ALARM_HIS에서 SEND_STS='0'(전송 대기) 알람을 발생시간 순으로 조회.
      sender가 5분마다 이 목록을 가져와 슬랙으로 발송한다.

  UPDATE_SUCCESS
      슬랙 전송 성공 시 SEND_STS='1'로 갱신.
      TGT_FILE_PATH(전송된 txt 파일 경로)와 SEND_DT(전송 일시)를 함께 기록.

  UPDATE_FAILURE
      슬랙 전송 실패 시 SEND_STS='9'로 갱신.
      ERR_MSG에 에러 내용을 저장 (1,000자 이내 잘라서 저장).
      실패 알람은 자동 재시도 없이 수동 조치 대상이 된다.
"""

GET_PENDING_ALARMS = """
    SELECT ALARM_ID, FILE_ID, ALARM_MSG
    FROM   BAT_ALARM_HIS
    WHERE  SEND_STS = '0'
    ORDER  BY ALARM_DT
"""

UPDATE_SUCCESS = """
    UPDATE BAT_ALARM_HIS
    SET    SEND_STS      = '1',
           SEND_DT       = :send_dt,
           TGT_FILE_PATH = :file_path,
           UPDR_ID       = :updr_id,
           UPD_DT        = SYSDATE
    WHERE  ALARM_ID = :alarm_id
"""

UPDATE_FAILURE = """
    UPDATE BAT_ALARM_HIS
    SET    SEND_STS = '9',
           ERR_MSG  = :err_msg,
           UPDR_ID  = :updr_id,
           UPD_DT   = SYSDATE
    WHERE  ALARM_ID = :alarm_id
"""
