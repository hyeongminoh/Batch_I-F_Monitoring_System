# ============================================================
# sender.py - 슬랙 알람 전송 프로세스
# cron: */5 * * * * python3 /opt/batch_monitor/src/sender.py >> /var/log/sender.log 2>&1
# ============================================================

import sys
import os
import subprocess
from datetime import datetime
import oracledb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (
    DB_USER, DB_PASSWORD, DB_DSN,
    SLACK_CHANNEL, SLACK_SCRIPT, ALARM_DIR, LOG_DIR, REGR_ID
)
from log_utils import setup_logger
from sql.sender_sql import (
    GET_PENDING_ALARMS,
    UPDATE_SUCCESS,
    UPDATE_FAILURE,
)

log = setup_logger('sender', LOG_DIR)


# ============================================================
# DB 연결
# ============================================================
def get_connection():
    return oracledb.connect(user=DB_USER, password=DB_PASSWORD, dsn=DB_DSN)


# ============================================================
# 전송 대기 알람 조회 (SEND_STS='0')
# ============================================================
def get_pending_alarms(conn):
    with conn.cursor() as cur:
        cur.execute(GET_PENDING_ALARMS)
        return cur.fetchall()


# ============================================================
# 알람 txt 파일 생성
# ============================================================
def create_alarm_file(file_id, alarm_msg, now):
    os.makedirs(ALARM_DIR, exist_ok=True)
    ts       = now.strftime("%Y%m%d_%H%M%S")
    filename = f"ALARM_{file_id}_{ts}.txt"
    filepath = os.path.join(ALARM_DIR, filename)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(alarm_msg)

    return filepath


# ============================================================
# mon_slack.sh 실행
# ============================================================
def send_slack(filepath):
    cmd    = ['sh', SLACK_SCRIPT, SLACK_CHANNEL, filepath]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(
            f"mon_slack.sh 실패 (rc={result.returncode}): {result.stderr.strip()}"
        )


# ============================================================
# 전송 성공 처리 → SEND_STS='1'
# ============================================================
def update_success(conn, alarm_id, file_path, now):
    with conn.cursor() as cur:
        cur.execute(UPDATE_SUCCESS, {
            'send_dt':   now,
            'file_path': file_path,
            'updr_id':   REGR_ID,
            'alarm_id':  alarm_id,
        })
    conn.commit()


# ============================================================
# 전송 실패 처리 → SEND_STS='9'
# ============================================================
def update_failure(conn, alarm_id, err_msg):
    with conn.cursor() as cur:
        cur.execute(UPDATE_FAILURE, {
            'err_msg':  str(err_msg)[:1000],
            'updr_id':  REGR_ID,
            'alarm_id': alarm_id,
        })
    conn.commit()


# ============================================================
# main
# ============================================================
def main():
    log.info("===== sender.py 시작 =====")

    try:
        conn = get_connection()
    except Exception as e:
        log.error(f"DB 연결 실패: {e}")
        sys.exit(1)

    try:
        pending = get_pending_alarms(conn)
        log.info(f"전송 대기 알람: {len(pending)}건")

        success_cnt = 0
        fail_cnt    = 0

        for alarm_id, file_id, alarm_msg in pending:
            now = datetime.now()
            try:
                # 1. 알람 txt 파일 생성
                filepath = create_alarm_file(file_id, alarm_msg, now)
                log.info(f"[{alarm_id}] {file_id}: 파일 생성 → {filepath}")

                # 2. 슬랙 전송 (mon_slack.sh 미설치 환경에서는 주석 처리)
                # send_slack(filepath)

                # 3. 성공 처리
                update_success(conn, alarm_id, filepath, now)
                log.info(f"[{alarm_id}] {file_id}: 슬랙 전송 완료")
                success_cnt += 1

            except Exception as e:
                err_msg = str(e)
                log.error(f"[{alarm_id}] {file_id}: 전송 실패 - {err_msg}")
                update_failure(conn, alarm_id, err_msg)
                fail_cnt += 1

        log.info(f"===== sender.py 완료: 성공={success_cnt}, 실패={fail_cnt} =====")

    finally:
        conn.close()


if __name__ == '__main__':
    main()
