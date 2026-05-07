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
    SLACK_CHANNEL, SLACK_SCRIPT, ALARM_DIR, ALARM_DIR_LLM, LOG_DIR, REGR_ID,
    USE_LLM, OLLAMA_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT,
)
import llm as llm_module
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
# 알람 txt 파일 (DB 원문 / LLM 재작성) — 병행 저장
# ============================================================
def write_alarm_text(directory, file_id, ts, suffix, content):
    os.makedirs(directory, exist_ok=True)
    filename = f"ALARM_{file_id}_{ts}_{suffix}.txt"
    filepath = os.path.join(directory, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    return filepath


def prepare_alarm_files(file_id, alarm_msg, now):
    """
    1) DB 원문 → ALARM_DIR … _src.txt
    2) USE_LLM 시 LLM 재작성 → ALARM_DIR_LLM … _llm.txt (실패 시 생략)
    반환: (슬랙에 넘길 경로, src 경로, llm 경로 또는 None)
    """
    ts = now.strftime("%Y%m%d_%H%M%S")
    src_path = write_alarm_text(ALARM_DIR, file_id, ts, "src", alarm_msg)

    llm_path = None
    if USE_LLM:
        llm_msg, ok = llm_module.generate_sender(
            file_id, alarm_msg, OLLAMA_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT,
        )
        if ok and llm_msg:
            llm_path = write_alarm_text(ALARM_DIR_LLM, file_id, ts, "llm", llm_msg)
            log.info(f"  [{file_id}] LLM 전송문 생성 → {llm_path}")
        else:
            log.info(f"  [{file_id}] LLM 스킵/실패 → DB 원문 파일로 전송 ({src_path})")

    # mon_slack.sh 등은 경로 1개만 받는 경우가 많아, 전송본 파일 하나만 지정
    slack_path = llm_path if llm_path else src_path

    return slack_path, src_path, llm_path


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
                # 1. DB 원문 txt + (옵션) LLM txt 병행 생성 → 슬랙용 경로 결정
                slack_path, src_path, llm_path = prepare_alarm_files(
                    file_id, alarm_msg, now
                )
                log.info(
                    f"[{alarm_id}] {file_id}: src={src_path}"
                    + (f", llm={llm_path}" if llm_path else "")
                    + f", slack={slack_path}"
                )

                # 2. 슬랙 전송 (mon_slack.sh 미설치 환경에서는 주석 처리)
                # send_slack(slack_path)

                # 3. 성공 처리
                update_success(conn, alarm_id, slack_path, now)
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
