# ============================================================
# sender.py - 메일큐(COM_MAILQUE_MST) 적재 프로세스
# cron: */5 * * * * python3 /opt/batch_monitor/src/sender.py >> /var/log/sender.log 2>&1
# ============================================================
"""
5분마다 실행되어 BAT_ALARM_HIS의 미전송 알람(SEND_STS='0')을 COM_MAILQUE_MST에 적재한다.
detector가 탐지·저장한 알람을 메일큐로 넘겨 실제 발송은 메일큐 처리기가 담당.

[처리 흐름]
  1. BAT_ALARM_HIS에서 SEND_STS='0'인 알람 조회 (발생시간 오름차순)
  2. 알람별:
     a. DB 원문(ALARM_MSG) → {ALARM_DIR}/ALARM_{FILE_ID}_{ts}_src.txt 저장 (항상)
     b. USE_LLM=1이면 LLM(EXAONE)으로 문구 재작성
        성공 → {ALARM_DIR_LLM}/..._llm.txt 저장
        실패 → _src.txt 그대로 사용
     c. COM_MAILQUE_MST INSERT (CONTENTS = ALARM_MSG)
     d. 성공: SEND_STS='1', TGT_FILE_PATH·SEND_DT 기록
        실패: SEND_STS='9', ERR_MSG 기록

[파일 저장 구조]
  {ALARM_DIR}/
  ├── ALARM_{FILE_ID}_{ts}_src.txt   ← DB 원문 (항상 생성)
  └── llm/
      └── ALARM_{FILE_ID}_{ts}_llm.txt  ← LLM 재작성본 (성공 시만)

[처리 실패 시]
  SEND_STS='9'로 기록하고 다음 run에서 재시도하지 않는다.
  (재시도가 필요하다면 SEND_STS를 수동으로 '0'으로 되돌린다)
"""

import sys
import os
from datetime import datetime
import oracledb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (
    DB_USER, DB_PASSWORD, DB_DSN,
    ALARM_DIR, ALARM_DIR_LLM, LOG_DIR, REGR_ID,
    USE_LLM, OLLAMA_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT,
)
import llm as llm_module
from log_utils import setup_logger
from sql.sender_sql import (
    GET_PENDING_ALARMS,
    INSERT_MAILQUE_MST,
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
    반환: (기록용 대표 경로, src 경로, llm 경로 또는 None)
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
            log.info(f"  [{file_id}] LLM 재작성 생성 → {llm_path}")
        else:
            log.info(f"  [{file_id}] LLM 스킵/실패 → DB 원문 파일 사용 ({src_path})")

    # TGT_FILE_PATH에 기록할 대표 경로 (llm 있으면 llm, 없으면 src)
    tgt_path = llm_path if llm_path else src_path

    return tgt_path, src_path, llm_path


# ============================================================
# COM_MAILQUE_MST INSERT
# ============================================================
def insert_mailqueue(conn, file_id, alarm_msg):
    # file_id는 현재 INSERT_MAILQUE_MST에서 미사용.
    # COM_MAILQUE_MST 컬럼 확정 후 sql/sender_sql.py의 SQL과 여기 bind를 함께 맞출 것.
    with conn.cursor() as cur:
        cur.execute(INSERT_MAILQUE_MST, {
            'contents': alarm_msg,
        })


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
                # 1. DB 원문 txt + (옵션) LLM txt 병행 생성
                tgt_path, src_path, llm_path = prepare_alarm_files(
                    file_id, alarm_msg, now
                )
                log.info(
                    f"[{alarm_id}] {file_id}: src={src_path}"
                    + (f", llm={llm_path}" if llm_path else "")
                )

                # 2. COM_MAILQUE_MST INSERT
                insert_mailqueue(conn, file_id, alarm_msg)

                # 3. 성공 처리
                update_success(conn, alarm_id, tgt_path, now)
                log.info(f"[{alarm_id}] {file_id}: 메일큐 적재 완료")
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
