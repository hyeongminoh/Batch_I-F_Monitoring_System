# ============================================================
# detector.py - 배치 파일 미수신 감지 프로세스
# cron: */10 * * * * python3 /opt/batch_monitor/src/detector.py >> /var/log/detector.log 2>&1
# ============================================================

import sys
import os
import numpy as np
import pandas as pd
from datetime import datetime
import oracledb
import joblib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (
    DB_USER, DB_PASSWORD, DB_DSN, MBRSH_PGM_ID,
    MODEL_DIR, LOG_DIR, ALARM_DIR_FALLBACK, ALARM_DIR_LLM,
    OLLAMA_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT,
    HISTORY_DAYS, MIN_SAMPLE_COUNT, REGR_ID
)
import llm as llm_module
from log_utils import setup_logger
from freq_utils import classify_frequency, sec_to_hms
from sql.detector_sql import (
    GET_EXCLUDED_FILE_IDS,
    GET_HISTORICAL_DATA,
    HAS_ALARM_TODAY,
    INSERT_ALARM,
    GET_FREQ_MST,
    UPSERT_FREQ_MST_FB,
)

log = setup_logger('detector', LOG_DIR)


# ============================================================
# DB 연결
# ============================================================
def get_connection():
    return oracledb.connect(user=DB_USER, password=DB_PASSWORD, dsn=DB_DSN)


# ============================================================
# 제외 FILE_ID 조회
# ============================================================
def get_excluded_file_ids(conn):
    with conn.cursor() as cur:
        cur.execute(GET_EXCLUDED_FILE_IDS)
        return {row[0] for row in cur.fetchall()}


# ============================================================
# 과거 90일 수신 이력 조회
# ============================================================
def get_historical_data(conn):
    with conn.cursor() as cur:
        cur.execute(GET_HISTORICAL_DATA, days=HISTORY_DAYS)
        rows = cur.fetchall()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=['file_id', 'file_nm', 'reg_dt', 'tot_rec_cnt', 'send_rec_cnt'])
    df['reg_dt']       = pd.to_datetime(df['reg_dt'])
    df['arrival_date'] = df['reg_dt'].dt.date
    df['arrival_sec']  = (df['reg_dt'].dt.hour * 3600
                          + df['reg_dt'].dt.minute * 60
                          + df['reg_dt'].dt.second)
    df['weekday']      = df['reg_dt'].dt.weekday        # 0=월 ~ 6=일
    df['is_month_end'] = (df['reg_dt'].dt.day >= 25).astype(int)
    return df


# ============================================================
# 오늘 도착 여부 확인
# ============================================================
def has_arrived_today(file_df, today):
    return (file_df['arrival_date'] == today).any()


# ============================================================
# 오늘 이미 알람 발생 여부 확인
# ============================================================
def has_alarm_today(conn, file_id):
    with conn.cursor() as cur:
        cur.execute(HAS_ALARM_TODAY, file_id=file_id)
        return cur.fetchone()[0] > 0


# ============================================================
# 도착 window 계산 (5th / 50th / 95th percentile)
# ============================================================
def calc_arrival_window(file_df, today):
    today_weekday      = today.weekday()
    today_is_month_end = 1 if today.day >= 25 else 0

    mask     = ((file_df['weekday'] == today_weekday) &
                (file_df['is_month_end'] == today_is_month_end))
    filtered = file_df[mask]
    sample_cnt = len(filtered)

    if sample_cnt < MIN_SAMPLE_COUNT:
        return None

    arr = filtered['arrival_sec'].values
    return {
        'exp_min':    sec_to_hms(np.percentile(arr, 5)),
        'exp_med':    sec_to_hms(np.percentile(arr, 50)),
        'exp_max':    sec_to_hms(np.percentile(arr, 95)),
        'sample_cnt': sample_cnt,
    }


# ============================================================
# deadline(95th) 초과 여부 & 지연 분 계산
# ============================================================
def is_past_deadline(exp_max_time, now):
    return now.strftime("%H:%M:%S") > exp_max_time


def calc_delay_min(exp_max_time, now):
    h, m, s  = map(int, exp_max_time.split(':'))
    deadline = h * 3600 + m * 60 + s
    now_sec  = now.hour * 3600 + now.minute * 60 + now.second
    return max(0, (now_sec - deadline) // 60)


# ============================================================
# BAT_FILE_FREQ_MST 전체 로드 (run 시작 시 1회)
# ============================================================
def load_freq_mst(conn):
    with conn.cursor() as cur:
        cur.execute(GET_FREQ_MST)
        rows = cur.fetchall()
    result = {}
    for file_id, effective_src, freq_type, median_gap, std_gap, round_gap in rows:
        result[file_id] = {
            'effective_src': effective_src,
            'freq_type':     freq_type or 'IRREGULAR',
            'median_gap':    float(median_gap or 0),
            'std_gap':       float(std_gap or 0),
        }
    return result


# ============================================================
# BAT_FILE_FREQ_MST FB 기록 (trainer 미실행 FILE_ID 한정)
# ============================================================
def upsert_freq_mst_fb(conn, file_id, freq_type, median_gap, std_gap, file_df):
    with conn.cursor() as cur:
        cur.execute(UPSERT_FREQ_MST_FB, {
            'file_id':     file_id,
            'freq_type':   freq_type,
            'median_gap':  round(median_gap, 4),
            'std_gap':     round(std_gap, 4),
            'round_gap':   round(median_gap),
            'sample_cnt':  len(file_df),
            'win_days':    HISTORY_DAYS,
            'analysis_st': file_df['reg_dt'].min().to_pydatetime(),
            'analysis_ed': file_df['reg_dt'].max().to_pydatetime(),
            'regr_id':     REGR_ID,
        })
    conn.commit()


# ============================================================
# Isolation Forest anomaly score 계산
# ============================================================
def get_anomaly_score(file_id, file_df, today, now):
    iso_path    = os.path.join(MODEL_DIR, f"{file_id}_iso.pkl")
    scaler_path = os.path.join(MODEL_DIR, f"{file_id}_scaler.pkl")

    if not (os.path.exists(iso_path) and os.path.exists(scaler_path)):
        log.warning(f"  [{file_id}] 모델 파일 없음 → 기본값 -0.5 사용")
        return -0.5

    try:
        iso    = joblib.load(iso_path)
        scaler = joblib.load(scaler_path)

        arrival_sec  = now.hour * 3600 + now.minute * 60 + now.second
        tot_rec_cnt  = float(file_df['tot_rec_cnt'].median())
        send_rec_cnt = float(file_df['send_rec_cnt'].median())
        weekday      = today.weekday()
        is_month_end = 1 if today.day >= 25 else 0

        log.info(f"  [{file_id}] 모델 입력 피처: "
                 f"arrival_sec={arrival_sec}, tot_rec={tot_rec_cnt:.0f}, "
                 f"send_rec={send_rec_cnt:.0f}, weekday={weekday}, month_end={is_month_end}")

        X = np.array([[arrival_sec, tot_rec_cnt, send_rec_cnt, weekday, is_month_end]])
        score = iso.score_samples(scaler.transform(X))[0]
        score = round(float(score), 4)

        log.info(f"  [{file_id}] anomaly score = {score} "
                 f"({'정상 범위' if score > -0.5 else '이상 의심'}, 음수일수록 이상)")
        return score

    except Exception as e:
        log.error(f"  [{file_id}] anomaly score 계산 실패 - {e}")
        return -0.5


# ============================================================
# fallback 템플릿 메시지 생성
# ============================================================
def build_fallback_message(file_id, freq_type, window, delay_min):
    return (
        f"[배치 미수신 알람] {file_id}\n"
        f"마감: {window['exp_max']} / 지연: {delay_min}분 / 주기: {freq_type}\n"
        f"즉시 확인이 필요합니다."
    )


# ============================================================
# 비교용 파일 저장 (fallback/, llm/ 디렉토리)
# ============================================================
def save_compare_file(directory, file_id, ts, message):
    os.makedirs(directory, exist_ok=True)
    filepath = os.path.join(directory, f"ALARM_{file_id}_{ts}.txt")
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(message)
    return filepath


# ============================================================
# 두 메시지 생성 후 로그 비교 출력, 최종 메시지 반환
# ============================================================
def generate_alarm_message(file_id, freq_type, window, check_time,
                            delay_min, anomaly_score, today, ts):
    fallback_msg = build_fallback_message(file_id, freq_type, window, delay_min)
    fallback_path = save_compare_file(ALARM_DIR_FALLBACK, file_id, ts, fallback_msg)

    llm_msg, llm_ok = llm_module.generate(
        file_id, freq_type, window, check_time, delay_min, anomaly_score, today,
        OLLAMA_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT,
    )

    log.info(f"  [{file_id}] ── 메시지 비교 ──────────────────────")
    log.info(f"  [{file_id}] [fallback] → {fallback_path}\n{fallback_msg}")
    if llm_ok:
        llm_path = save_compare_file(ALARM_DIR_LLM, file_id, ts, llm_msg)
        log.info(f"  [{file_id}] [LLM] → {llm_path}\n{llm_msg}")
        log.info(f"  [{file_id}] → LLM 메시지 사용")
    else:
        log.info(f"  [{file_id}] [LLM] 사용 불가 (Ollama 미실행 or 오류)")
        log.info(f"  [{file_id}] → fallback 메시지 사용")
    log.info(f"  [{file_id}] ─────────────────────────────────────")

    return llm_msg if llm_ok else fallback_msg


# ============================================================
# BAT_ALARM_HIS INSERT
# ============================================================
def insert_alarm(conn, file_id, file_nm, freq_type, window, check_time,
                 delay_min, anomaly_score, alarm_msg, now):
    with conn.cursor() as cur:
        cur.execute(INSERT_ALARM, {
            'mbrsh':         MBRSH_PGM_ID,
            'file_id':       file_id,
            'file_nm':       file_nm,
            'alarm_dt':      now,
            'freq_type':     freq_type,
            'exp_min':       window['exp_min'],
            'exp_med':       window['exp_med'],
            'exp_max':       window['exp_max'],
            'chk_time':      check_time,
            'delay_min':     delay_min,
            'anomaly_score': anomaly_score,
            'alarm_msg':     alarm_msg[:2000],
            'regr_id':       REGR_ID,
        })
    conn.commit()
    log.info(f"  [{file_id}] BAT_ALARM_HIS INSERT 완료")


# ============================================================
# main
# ============================================================
def main():
    now        = datetime.now()
    today      = now.date()
    check_time = now.strftime("%H:%M:%S")
    ts         = now.strftime("%Y%m%d_%H%M%S")

    log.info("")
    log.info("▼" * 60)
    log.info(f"  [RUN START] detector.py  {now.strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("▼" * 60)

    try:
        conn = get_connection()
        log.info("DB 연결 성공")
    except Exception as e:
        log.error(f"DB 연결 실패: {e}")
        sys.exit(1)

    try:
        excluded = get_excluded_file_ids(conn)
        log.info(f"제외 FILE_ID: {sorted(excluded)} ({len(excluded)}건)")

        hist_df = get_historical_data(conn)
        if hist_df.empty:
            log.info("과거 수신 데이터 없음. 종료.")
            return

        freq_mst = load_freq_mst(conn)
        log.info(f"BAT_FILE_FREQ_MST 로드: {len(freq_mst)}건 "
                 f"(T={sum(1 for v in freq_mst.values() if v['effective_src'] == 'T')}, "
                 f"D={sum(1 for v in freq_mst.values() if v['effective_src'] == 'D')})")

        file_ids = [fid for fid in hist_df['file_id'].unique() if fid not in excluded]
        log.info(f"모니터링 대상 FILE_ID: {len(file_ids)}건")
        log.info("-" * 60)

        alarm_cnt = 0
        for file_id in file_ids:
            try:
                log.info(f"[{file_id}] 점검 시작")
                file_df = hist_df[hist_df['file_id'] == file_id].copy()
                file_nm = file_df['file_nm'].iloc[-1]  # 가장 최근 수신 파일명

                # 1. 오늘 이미 도착했으면 스킵
                if has_arrived_today(file_df, today):
                    log.info(f"  [{file_id}] SKIP → 오늘 이미 수신 완료")
                    continue

                # 2. 수신 주기 분류
                #    BAT_FILE_FREQ_MST(T=trainer/D=detector) 우선,
                #    미등록 FILE_ID는 직접 계산 후 FB로 기록
                profile = freq_mst.get(file_id)
                if profile:
                    freq_type  = profile['freq_type']
                    median_gap = profile['median_gap']
                    std_gap    = profile['std_gap']
                    log.info(f"  [{file_id}] 수신 주기 (MST/{profile['effective_src']}): "
                             f"{freq_type} (median={median_gap:.1f}일, std={std_gap:.1f}일)")
                else:
                    freq_type, median_gap, std_gap = classify_frequency(file_df)
                    log.info(f"  [{file_id}] 수신 주기 (계산): {freq_type} "
                             f"(median={median_gap:.1f}일, std={std_gap:.1f}일)")
                    try:
                        upsert_freq_mst_fb(conn, file_id, freq_type, median_gap,
                                           std_gap, file_df)
                        log.info(f"  [{file_id}] BAT_FILE_FREQ_MST FB 기록 완료")
                    except Exception as fb_e:
                        log.warning(f"  [{file_id}] BAT_FILE_FREQ_MST FB 기록 실패(무시): {fb_e}")

                if freq_type == "IRREGULAR":
                    log.info(f"  [{file_id}] SKIP → IRREGULAR (불규칙 수신 파일)")
                    continue

                # 3. 오늘 이미 알람이 있으면 스킵 (중복 방지)
                if has_alarm_today(conn, file_id):
                    log.info(f"  [{file_id}] SKIP → 오늘 이미 알람 발송됨 (중복 방지)")
                    continue

                # 4. 도착 window 계산 → 샘플 부족 스킵
                window = calc_arrival_window(file_df, today)
                if window is None:
                    log.info(f"  [{file_id}] SKIP → 동일 요일/월말 조건 샘플 부족 "
                             f"(최소 {MIN_SAMPLE_COUNT}건 필요)")
                    continue
                log.info(f"  [{file_id}] 도착 window: "
                         f"{window['exp_min']} ~ {window['exp_max']} "
                         f"(중앙값={window['exp_med']}, 샘플={window['sample_cnt']}건)")

                # 5. deadline(95th) 미초과 → 아직 기다림
                if not is_past_deadline(window['exp_max'], now):
                    log.info(f"  [{file_id}] SKIP → deadline({window['exp_max']}) 미초과, "
                             f"현재 {check_time}")
                    continue
                log.info(f"  [{file_id}] deadline({window['exp_max']}) 초과 확인 → 알람 발동")

                # 6. 지연 분 계산
                delay_min = calc_delay_min(window['exp_max'], now)
                log.info(f"  [{file_id}] 지연 시간: {delay_min}분")

                # 7. Isolation Forest anomaly score
                anomaly_score = get_anomaly_score(file_id, file_df, today, now)

                # 8. LLM 한국어 알람 메시지 생성
                alarm_msg = generate_alarm_message(
                    file_id, freq_type, window, check_time,
                    delay_min, anomaly_score, today, ts
                )
                log.info(f"  [{file_id}] 알람 메시지:\n{alarm_msg}")

                # 9. BAT_ALARM_HIS INSERT
                insert_alarm(
                    conn, file_id, file_nm, freq_type, window, check_time,
                    delay_min, anomaly_score, alarm_msg, now
                )
                alarm_cnt += 1
                log.info("-" * 60)

            except Exception as e:
                log.error(f"[{file_id}] 처리 중 오류 - {e}")
                continue

        elapsed = int((datetime.now() - now).total_seconds())
        log.info("▲" * 60)
        log.info(f"  [RUN END  ] detector.py  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                 f"  |  알람 {alarm_cnt}건  |  소요 {elapsed}초")
        log.info("▲" * 60)
        log.info("")

    finally:
        conn.close()


if __name__ == '__main__':
    main()
