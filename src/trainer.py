# ============================================================
# trainer.py - Isolation Forest 모델 재학습 프로세스
# cron: 0 2 * * 0 python3 /opt/batch_monitor/src/trainer.py >> /var/log/trainer.log 2>&1
# ============================================================

import sys
import os
import numpy as np
import pandas as pd
from datetime import datetime
import oracledb
import joblib
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (
    DB_USER, DB_PASSWORD, DB_DSN,
    MODEL_DIR, LOG_DIR, TRAIN_HISTORY_DAYS, MIN_SAMPLE_COUNT, REGR_ID
)
from log_utils import setup_logger
from freq_utils import classify_frequency
from sql.trainer_sql import (
    GET_EXCLUDED_FILE_IDS,
    GET_TRAINING_DATA,
    UPSERT_FREQ_MST,
)

log = setup_logger('trainer', LOG_DIR)


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
# 학습 데이터 조회 (과거 180일)
# ============================================================
def get_training_data(conn):
    with conn.cursor() as cur:
        cur.execute(GET_TRAINING_DATA, days=TRAIN_HISTORY_DAYS)
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
# Isolation Forest 학습
# ============================================================
def train_model(file_df):
    """
    피처: [arrival_sec, tot_rec_cnt, send_rec_cnt, weekday, is_month_end]
    StandardScaler 정규화 후 IsolationForest 학습
    """
    feature_cols = ['arrival_sec', 'tot_rec_cnt', 'send_rec_cnt', 'weekday', 'is_month_end']
    X = file_df[feature_cols].values

    scaler   = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    iso = IsolationForest(
        n_estimators=100,
        contamination=0.05,   # 5% 이상치 가정
        random_state=42,
        n_jobs=-1,
    )
    iso.fit(X_scaled)
    return iso, scaler


# ============================================================
# BAT_FILE_FREQ_MST UPSERT (MAIN)
# ============================================================
def upsert_freq_mst(conn, file_id, freq_type, median_gap, std_gap, sample_cnt, file_df):
    with conn.cursor() as cur:
        cur.execute(UPSERT_FREQ_MST, {
            'file_id':     file_id,
            'freq_type':   freq_type,
            'median_gap':  round(median_gap, 4),
            'std_gap':     round(std_gap, 4),
            'round_gap':   round(median_gap),
            'sample_cnt':  sample_cnt,
            'win_days':    TRAIN_HISTORY_DAYS,
            'analysis_st': file_df['reg_dt'].min().to_pydatetime(),
            'analysis_ed': file_df['reg_dt'].max().to_pydatetime(),
            'regr_id':     REGR_ID,
        })
    conn.commit()


# ============================================================
# 모델 저장
# ============================================================
def save_models(file_id, iso, scaler):
    os.makedirs(MODEL_DIR, exist_ok=True)
    iso_path    = os.path.join(MODEL_DIR, f"{file_id}_iso.pkl")
    scaler_path = os.path.join(MODEL_DIR, f"{file_id}_scaler.pkl")
    joblib.dump(iso,    iso_path)
    joblib.dump(scaler, scaler_path)
    return iso_path, scaler_path


# ============================================================
# main
# ============================================================
def main():
    log.info("===== trainer.py 시작 =====")
    start_time = datetime.now()

    try:
        conn = get_connection()
    except Exception as e:
        log.error(f"DB 연결 실패: {e}")
        sys.exit(1)

    try:
        excluded = get_excluded_file_ids(conn)
        log.info(f"제외 FILE_ID: {len(excluded)}건")

        train_df = get_training_data(conn)
        if train_df.empty:
            log.info("학습 데이터 없음. 종료.")
            return

        file_ids = [fid for fid in train_df['file_id'].unique() if fid not in excluded]
        log.info(f"학습 대상 FILE_ID: {len(file_ids)}건")

        success_cnt = 0
        skip_cnt    = 0
        fail_cnt    = 0

        for file_id in file_ids:
            try:
                file_df = train_df[train_df['file_id'] == file_id].copy()

                if len(file_df) < MIN_SAMPLE_COUNT:
                    log.info(f"{file_id}: 샘플 부족 ({len(file_df)}건) → 스킵")
                    skip_cnt += 1
                    continue

                iso, scaler = train_model(file_df)
                iso_path, _ = save_models(file_id, iso, scaler)

                freq_type, median_gap, std_gap = classify_frequency(file_df)
                upsert_freq_mst(conn, file_id, freq_type, median_gap, std_gap,
                                len(file_df), file_df)

                log.info(f"{file_id}: 학습 완료 (샘플={len(file_df)}건, "
                         f"주기={freq_type}) → {iso_path}")
                success_cnt += 1

            except Exception as e:
                log.error(f"{file_id}: 학습 실패 - {e}")
                fail_cnt += 1

        elapsed = int((datetime.now() - start_time).total_seconds())
        log.info(
            f"===== trainer.py 완료: "
            f"성공={success_cnt}, 스킵={skip_cnt}, 실패={fail_cnt}, "
            f"소요={elapsed}초 ====="
        )

    finally:
        conn.close()


if __name__ == '__main__':
    main()
