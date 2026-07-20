"""
sender.py 전용 SQL 상수 모음.

[상수 목록]
  GET_PENDING_ALARMS
      BAT_ALARM_HIS에서 SEND_STS='0'(전송 대기) 알람을 발생시간 순으로 조회.
      sender가 5분마다 이 목록을 가져와 COM_MAILQUE_MST에 적재한다.

  INSERT_MAILQUE_MST
      COM_MAILQUE_MST INSERT. 실제 컬럼 구성은 직접 작성.
      bind 파라미터: :contents (BAT_ALARM_HIS.ALARM_MSG 원문)
      필요 시 sender.py의 insert_mailqueue() 호출부에서 bind를 추가로 넘기면 됨.

  UPDATE_SUCCESS
      COM_MAILQUE_MST INSERT 성공 시 SEND_STS='1'로 갱신.
      TGT_FILE_PATH(생성된 txt 파일 경로)와 SEND_DT(처리 일시)를 함께 기록.

  UPDATE_FAILURE
      INSERT 실패 시 SEND_STS='9'로 갱신.
      ERR_MSG에 에러 내용을 저장 (1,000자 이내 잘라서 저장).
      실패 알람은 자동 재시도 없이 수동 조치 대상이 된다.
"""

GET_PENDING_ALARMS = """
    SELECT ALARM_ID, FILE_ID, ALARM_MSG
    FROM   BAT_ALARM_HIS
    WHERE  SEND_STS = '0'
    ORDER  BY ALARM_DT
"""

# TODO: COM_MAILQUE_MST 실제 컬럼 구성에 맞게 작성
# 현재 bind는 :contents (ALARM_MSG) 하나만 연결되어 있음
INSERT_MAILQUE_MST = """
    INSERT INTO COM_MAILQUE_MST (
         MID
       , SUBID
       , TID
       , SNAME
       , SMAIL
       , RPOS
       , QUERY
       , CTNPOS
       , SUBJECT
       , CONTENTS
       , CDATE
       , SDATE
       , STATUS
       , CHARSET
       , ISSECURE
    ) VALUES (
          COM_MAILQUE_MST_SEQ.NEXTVAL
        , 0
        , 'ADSMON'
        , 'NXMEML'
        , 'NXMILE@okcashbag.com'
        , '1'
        , 'SELECT ''skcc08798@gmail.com'' AS RMAIL, ''skcc08798@gmail.com'' AS RNAME FROM DUAL'
        , '0'
        , '(운영자_bp) 배치파일모니터링'
        , :contents
        , SYSDATE
        , SYSDATE
        , '0'
        , 0
        , '0'
    )
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
