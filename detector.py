# ============================================================
# detector.py - 배치 파일 미수신 감지 프로세스
# cron: */10 * * * * python3 /opt/batch_monitor/detector.py >> /var/log/detector.log 2>&1
# ============================================================

import sys
import os
import logging
import numpy as np
import pandas as pd
from datetime import datetime
import oracledb
import joblib
import requests

sys.path.insert(0, '/opt/batch_monitor')
from config import (
    DB_USER, DB_PASSWORD, DB_DSN, MBRSH_PGM_ID,
    MODEL_DIR, OLLAMA_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT,
    HISTORY_DAYS, MIN_SAMPLE_COUNT, REGR_ID
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
log = logging.getLogger(__name__)


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
        cur.execute("SELECT FILE_ID FROM BAT_MNTLST_EXC WHERE USE_YN = 'Y'")
        return {row[0] for row in cur.fetchall()}


# ============================================================
# 과거 90일 수신 이력 조회
# ============================================================
def get_historical_data(conn):
    sql = """
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
    with conn.cursor() as cur:
        cur.execute(sql, days=HISTORY_DAYS)
        rows = cur.fetchall()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=['file_id', 'reg_dt', 'tot_rec_cnt', 'send_rec_cnt'])
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
    sql = """
        SELECT COUNT(*)
        FROM   BAT_ALARM_HIS
        WHERE  FILE_ID = :file_id
          AND  TRUNC(ALARM_DT) = TRUNC(SYSDATE)
    """
    with conn.cursor() as cur:
        cur.execute(sql, file_id=file_id)
        return cur.fetchone()[0] > 0


# ============================================================
# 수신 주기 분류
# ============================================================
def classify_frequency(file_df):
    """
    median_gap 기준:
      1일        → DAILY
      6~8일      → WEEKLY
      25~35일    → MONTHLY
      std > 50%  → IRREGULAR  (알람 제외)
      그 외      → EVERY_{n}_DAYS
    """
    dates = sorted(file_df['arrival_date'].unique())
    if len(dates) < 2:
        return "IRREGULAR", 0, 0

    gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
    median_gap = np.median(gaps)
    std_gap    = np.std(gaps)
    gap        = round(median_gap)

    if gap == 0:
        return "IRREGULAR", median_gap, std_gap

    if std_gap > median_gap * 0.5:
        freq_type = "IRREGULAR"
    elif gap == 1:
        freq_type = "DAILY"
    elif gap in (6, 7, 8):
        freq_type = "WEEKLY"
    elif 25 <= gap <= 35:
        freq_type = "MONTHLY"
    else:
        freq_type = f"EVERY_{gap}_DAYS"

    return freq_type, median_gap, std_gap


# ============================================================
# 도착 window 계산 (5th / 50th / 95th percentile)
# ============================================================
def sec_to_hms(sec):
    sec = int(sec)
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


def calc_arrival_window(file_df, today):
    """
    컨텍스트 필터: 같은 요일 + 월말여부 동일한 날만 사용
    sample_cnt < MIN_SAMPLE_COUNT 이면 None 반환 (알람 제외)
    """
    today_weekday     = today.weekday()
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
# Isolation Forest anomaly score 계산
# ============================================================
def get_anomaly_score(file_id, file_df, today, now):
    iso_path    = os.path.join(MODEL_DIR, f"{file_id}_iso.pkl")
    scaler_path = os.path.join(MODEL_DIR, f"{file_id}_scaler.pkl")

    if not (os.path.exists(iso_path) and os.path.exists(scaler_path)):
        log.warning(f"{file_id}: 모델 파일 없음 → 기본값 -0.5 사용")
        return -0.5

    try:
        iso    = joblib.load(iso_path)
        scaler = joblib.load(scaler_path)

        arrival_sec  = now.hour * 3600 + now.minute * 60 + now.second
        tot_rec_cnt  = float(file_df['tot_rec_cnt'].median())
        send_rec_cnt = float(file_df['send_rec_cnt'].median())
        weekday      = today.weekday()
        is_month_end = 1 if today.day >= 25 else 0

        X = np.array([[arrival_sec, tot_rec_cnt, send_rec_cnt, weekday, is_month_end]])
        score = iso.score_samples(scaler.transform(X))[0]
        return round(float(score), 4)

    except Exception as e:
        log.error(f"{file_id}: anomaly score 계산 실패 - {e}")
        return -0.5


# ============================================================
# Ollama EXAONE 한국어 알람 메시지 생성
# ============================================================
def generate_alarm_message(file_id, freq_type, window, check_time,
                            delay_min, anomaly_score, today):
    is_month_end = today.day >= 25
    prompt = (
        f"다음 배치 파일 미수신 상황에 대한 한국어 알람 메시지를 3~4문장으로 작성하세요.\n"
        f"마지막 문장은 반드시 \"즉시 확인이 필요합니다.\"로 끝내세요.\n\n"
        f"- 파일ID: {file_id}\n"
        f"- 수신 주기: {freq_type}\n"
        f"- 예상 도착 범위: {window['exp_min']} ~ {window['exp_max']} (중앙값: {window['exp_med']})\n"
        f"- 현재 시각: {check_time}\n"
        f"- 지연 시간: {delay_min}분\n"
        f"- 이상 점수: {anomaly_score:.4f} (음수일수록 이상)\n"
        f"- 월말 여부: {'예' if is_month_end else '아니오'}\n\n"
        f"알람 메시지:"
    )

    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=OLLAMA_TIMEOUT,
        )
        if resp.status_code == 200:
            msg = resp.json().get('response', '').strip()
            if msg:
                return msg
    except Exception as e:
        log.warning(f"{file_id}: LLM 호출 실패 ({e}) → fallback 메시지 사용")

    # fallback 템플릿 (알람은 반드시 발송)
    return (
        f"[배치 파일 미수신 알람] 파일ID {file_id}의 배치 파일이 "
        f"예정 마감 시각({window['exp_max']})을 {delay_min}분 초과하여 도착하지 않았습니다. "
        f"수신 주기는 {freq_type}이며 이상 점수는 {anomaly_score:.4f}입니다. "
        f"즉시 확인이 필요합니다."
    )


# ============================================================
# BAT_ALARM_HIS INSERT
# ============================================================
def insert_alarm(conn, file_id, freq_type, window, check_time,
                 delay_min, anomaly_score, alarm_msg, now):
    sql = """
        INSERT INTO BAT_ALARM_HIS (
            MBRSH_PGM_ID, FILE_ID,    ALARM_ID,
            ALARM_DT,     FREQUENCY_TYPE,
            EXP_MIN_TIME, EXP_MED_TIME, EXP_MAX_TIME,
            CHECK_TIME,   DELAY_MIN,    ANOMALY_SCORE,
            ALARM_MSG,    SEND_STS,
            REGR_ID,      REG_DT
        ) VALUES (
            :mbrsh,     :file_id,   SEQ_BAT_ALARM_HIS.NEXTVAL,
            :alarm_dt,  :freq_type,
            :exp_min,   :exp_med,   :exp_max,
            :chk_time,  :delay_min, :anomaly_score,
            :alarm_msg, '0',
            :regr_id,   SYSDATE
        )
    """
    with conn.cursor() as cur:
        cur.execute(sql, {
            'mbrsh':         MBRSH_PGM_ID,
            'file_id':       file_id,
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
    log.info(f"{file_id}: 알람 INSERT 완료 (delay={delay_min}분, score={anomaly_score})")


# ============================================================
# main
# ============================================================
def main():
    log.info("===== detector.py 시작 =====")
    now        = datetime.now()
    today      = now.date()
    check_time = now.strftime("%H:%M:%S")

    try:
        conn = get_connection()
    except Exception as e:
        log.error(f"DB 연결 실패: {e}")
        sys.exit(1)

    try:
        excluded = get_excluded_file_ids(conn)
        log.info(f"제외 FILE_ID: {len(excluded)}건")

        hist_df = get_historical_data(conn)
        if hist_df.empty:
            log.info("과거 수신 데이터 없음. 종료.")
            return

        file_ids = [fid for fid in hist_df['file_id'].unique() if fid not in excluded]
        log.info(f"모니터링 대상 FILE_ID: {len(file_ids)}건")

        alarm_cnt = 0
        for file_id in file_ids:
            try:
                file_df = hist_df[hist_df['file_id'] == file_id].copy()

                # 1. 오늘 이미 도착했으면 스킵
                if has_arrived_today(file_df, today):
                    continue

                # 2. 주기 분류 → IRREGULAR 스킵
                freq_type, median_gap, std_gap = classify_frequency(file_df)
                if freq_type == "IRREGULAR":
                    continue

                # 3. 오늘 이미 알람이 있으면 스킵 (중복 방지)
                if has_alarm_today(conn, file_id):
                    continue

                # 4. 도착 window 계산 → 샘플 부족 스킵
                window = calc_arrival_window(file_df, today)
                if window is None:
                    continue

                # 5. deadline(95th) 미초과 → 아직 기다림
                if not is_past_deadline(window['exp_max'], now):
                    continue

                # 6. 지연 분 계산
                delay_min = calc_delay_min(window['exp_max'], now)

                # 7. Isolation Forest anomaly score
                anomaly_score = get_anomaly_score(file_id, file_df, today, now)

                # 8. LLM 한국어 알람 메시지 생성
                alarm_msg = generate_alarm_message(
                    file_id, freq_type, window, check_time,
                    delay_min, anomaly_score, today
                )

                # 9. BAT_ALARM_HIS INSERT
                insert_alarm(
                    conn, file_id, freq_type, window, check_time,
                    delay_min, anomaly_score, alarm_msg, now
                )
                alarm_cnt += 1

            except Exception as e:
                log.error(f"{file_id}: 처리 중 오류 - {e}")
                continue

        log.info(f"===== detector.py 완료: {alarm_cnt}건 알람 생성 =====")

    finally:
        conn.close()


if __name__ == '__main__':
    main()
