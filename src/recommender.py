# ============================================================
# recommender.py - 모니터링 제외 파일 자동 추천 프로세스
# cron: 0 3 * * 1 python3 /opt/batch_monitor/src/recommender.py >> /var/log/recommender.log 2>&1
#
# 동작:
#   1. 90일 이력으로 IRREGULAR 또는 샘플 부족 FILE_ID 탐지
#   2. LLM(EXAONE)으로 한국어 제외 사유 생성
#   3. BAT_MNTLST_EXC에 USE_YN='P'(추천대기)로 INSERT
#      → 담당자가 'Y'(제외) 또는 'N'(유지)으로 최종 결정
# ============================================================

import sys
import os
import requests
import pandas as pd
from datetime import datetime
import oracledb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (
    DB_USER, DB_PASSWORD, DB_DSN, MBRSH_PGM_ID,
    LOG_DIR, HISTORY_DAYS, MIN_SAMPLE_COUNT, REGR_ID,
    USE_LLM, OLLAMA_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT,
)
from log_utils import setup_logger
from freq_utils import classify_frequency
from sql.recommender_sql import (
    GET_MANAGED_FILE_IDS,
    GET_ANALYSIS_DATA,
    INSERT_RECOMMENDATION,
)

log = setup_logger('recommender', LOG_DIR)

REASON_IRREGULAR  = 'IRREGULAR'
REASON_LOW_SAMPLE = 'LOW_SAMPLE'


# ============================================================
# DB 연결
# ============================================================
def get_connection():
    return oracledb.connect(user=DB_USER, password=DB_PASSWORD, dsn=DB_DSN)


# ============================================================
# 이미 관리 중인 FILE_ID 조회 (Y=제외, P=추천대기)
# ============================================================
def get_managed_file_ids(conn):
    with conn.cursor() as cur:
        cur.execute(GET_MANAGED_FILE_IDS)
        return {row[0] for row in cur.fetchall()}


# ============================================================
# 분석용 이력 데이터 조회
# ============================================================
def get_analysis_data(conn):
    with conn.cursor() as cur:
        cur.execute(GET_ANALYSIS_DATA, days=HISTORY_DAYS)
        rows = cur.fetchall()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=['file_id', 'reg_dt', 'tot_rec_cnt', 'send_rec_cnt'])
    df['reg_dt']       = pd.to_datetime(df['reg_dt'])
    df['arrival_date'] = df['reg_dt'].dt.date
    return df


# ============================================================
# 제외 추천 후보 탐지
# ============================================================
def detect_candidates(hist_df, managed):
    candidates = []
    for file_id in hist_df['file_id'].unique():
        if file_id in managed:
            continue

        file_df    = hist_df[hist_df['file_id'] == file_id]
        sample_cnt = len(file_df)

        if sample_cnt < MIN_SAMPLE_COUNT:
            candidates.append({
                'file_id':    file_id,
                'reason':     REASON_LOW_SAMPLE,
                'sample_cnt': sample_cnt,
                'median_gap': 0.0,
                'std_gap':    0.0,
            })
            continue

        freq_type, median_gap, std_gap = classify_frequency(file_df)
        if freq_type == 'IRREGULAR':
            candidates.append({
                'file_id':    file_id,
                'reason':     REASON_IRREGULAR,
                'sample_cnt': sample_cnt,
                'median_gap': median_gap,
                'std_gap':    std_gap,
            })

    return candidates


# ============================================================
# fallback 제외 사유 (LLM 없을 때)
# ============================================================
def build_fallback_reason(c):
    if c['reason'] == REASON_IRREGULAR:
        ratio = (c['std_gap'] / c['median_gap'] * 100) if c['median_gap'] > 0 else 0
        return (
            f"최근 {HISTORY_DAYS}일 분석 결과, 수신 간격 표준편차({c['std_gap']:.1f}일)가 "
            f"중앙값({c['median_gap']:.1f}일)의 {ratio:.0f}%를 초과하여 "
            f"불규칙 수신 패턴이 감지됩니다. 정기 모니터링 제외를 권장합니다."
        )
    return (
        f"최근 {HISTORY_DAYS}일간 수신 건수가 {c['sample_cnt']}건으로 "
        f"패턴 분석에 필요한 최소 샘플({MIN_SAMPLE_COUNT}건)에 미달합니다. "
        f"정기 모니터링 제외를 권장합니다."
    )


# ============================================================
# LLM 제외 사유 생성
# ============================================================
def build_llm_prompt(c):
    reason_kor = '불규칙 수신' if c['reason'] == REASON_IRREGULAR else '샘플 부족'
    if c['reason'] == REASON_IRREGULAR:
        detail = (f"수신 간격 중앙값 {c['median_gap']:.1f}일, "
                  f"표준편차 {c['std_gap']:.1f}일")
    else:
        detail = f"분석 기간 수신 건수 {c['sample_cnt']}건"

    return (
        f"배치 파일 모니터링 제외 추천 사유를 한국어로 2~3문장으로 작성하세요.\n\n"
        f"- 파일ID: {c['file_id']}\n"
        f"- 분류 결과: {reason_kor}\n"
        f"- 분석 기간: 최근 {HISTORY_DAYS}일\n"
        f"- {detail}\n\n"
        f"제외 추천 사유:"
    )


def generate_reason(c):
    if not USE_LLM:
        return build_fallback_reason(c)

    try:
        log.info(f"  [{c['file_id']}] LLM 사유 생성 중 ({OLLAMA_MODEL}) ...")
        resp = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": build_llm_prompt(c), "stream": False},
            timeout=OLLAMA_TIMEOUT,
        )
        if resp.status_code == 200:
            msg = resp.json().get('response', '').strip()
            if msg:
                return msg
        log.warning(f"  [{c['file_id']}] LLM 응답 비정상 → fallback 사유 사용")
    except Exception as e:
        log.warning(f"  [{c['file_id']}] LLM 호출 실패 → fallback 사유 사용: {e}")

    return build_fallback_reason(c)


# ============================================================
# BAT_MNTLST_EXC INSERT (USE_YN='P')
# ============================================================
def insert_recommendation(conn, c, excl_rsn):
    with conn.cursor() as cur:
        cur.execute(INSERT_RECOMMENDATION, {
            'mbrsh':    MBRSH_PGM_ID,
            'file_id':  c['file_id'],
            'excl_rsn': excl_rsn[:200],
            'regr_id':  REGR_ID,
        })
    conn.commit()


# ============================================================
# main
# ============================================================
def main():
    log.info("===== recommender.py 시작 =====")
    start_time = datetime.now()

    try:
        conn = get_connection()
        log.info("DB 연결 성공")
    except Exception as e:
        log.error(f"DB 연결 실패: {e}")
        sys.exit(1)

    try:
        managed = get_managed_file_ids(conn)
        log.info(f"기 관리 FILE_ID: {len(managed)}건 (Y=제외중, P=추천대기)")

        hist_df = get_analysis_data(conn)
        if hist_df.empty:
            log.info("분석 데이터 없음. 종료.")
            return

        candidates = detect_candidates(hist_df, managed)
        log.info(f"제외 추천 후보: {len(candidates)}건")

        if not candidates:
            log.info("추천 대상 없음. 종료.")
            return

        rec_cnt  = 0
        fail_cnt = 0

        for c in candidates:
            try:
                log.info(f"[{c['file_id']}] 처리 시작 (원인: {c['reason']})")
                excl_rsn = generate_reason(c)
                insert_recommendation(conn, c, excl_rsn)
                log.info(f"[{c['file_id']}] 추천 등록 완료 (USE_YN='P')\n  사유: {excl_rsn}")
                rec_cnt += 1
            except Exception as e:
                log.error(f"[{c['file_id']}] 처리 실패: {e}")
                fail_cnt += 1

        elapsed = int((datetime.now() - start_time).total_seconds())
        log.info(
            f"===== recommender.py 완료: "
            f"추천={rec_cnt}, 실패={fail_cnt}, 소요={elapsed}초 ====="
        )

    finally:
        conn.close()


if __name__ == '__main__':
    main()
