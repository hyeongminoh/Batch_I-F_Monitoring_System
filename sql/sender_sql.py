"""
sender.py 에서 사용하는 SQL 모음
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
