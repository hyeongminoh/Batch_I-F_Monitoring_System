# ============================================================
# trainer.py - Isolation Forest 모델 재학습 프로세스
# cron: 0 2 * * 0 python3 /opt/batch_monitor/src/trainer.py >> /var/log/trainer.log 2>&1
# ============================================================
"""
매주 일요일 02:00에 실행되어 FILE_ID별 이상 탐지 모델을 재학습하고
수신 주기 프로필(BAT_FILE_FREQ_MST)을 갱신하는 주간 배치 프로세스.

[처리 흐름]
  1. BAT_MNTLST_EXC에서 USE_YN='Y' 제외 FILE_ID 조회
  2. COM_BATFILE_TRN에서 최근 180일 수신 이력 조회
  3. FILE_ID별로 아래 작업 수행:
     a. 샘플 부족(MIN_SAMPLE_COUNT 미만) → 스킵
     b. Isolation Forest 학습 (피처 6개)
        → {MODEL_DIR}/{FILE_ID}_iso.pkl, _scaler.pkl 저장
     c. 수신 주기 분류 (classify_frequency)
        → DAILY / WEEKLY / MONTHLY / EVERY_N_DAYS / IRREGULAR
     d. 월중 수신일 패턴 탐지 (detect_dom_pattern)
        → EVERY_N_DAYS / MONTHLY 파일만. 예: "5,15"
     e. BAT_FILE_FREQ_MST MAIN_* 컬럼 UPSERT (EFFECTIVE_SRC='T')

[Isolation Forest 피처 (6개)]
  arrival_sec   : 하루 중 도착 시각(초). 비정상 시간대 탐지.
  tot_rec_cnt   : 전체 레코드 수. 건수 급변 탐지.
  send_rec_cnt  : 전송 레코드 수. 전송률 이상 탐지.
  weekday       : 요일(0=월~6=일). 요일별 패턴 학습.
  is_month_end  : 월말 여부(day≥25이면 1). 월말 특이 패턴 반영.
  day_of_month  : 월중 일(1~31). EVERY_N_DAYS 날짜 패턴 인식.

[산출물]
  - {MODEL_DIR}/{FILE_ID}_iso.pkl    : 학습된 IsolationForest 모델
  - {MODEL_DIR}/{FILE_ID}_scaler.pkl : StandardScaler (피처 정규화용)
  - BAT_FILE_FREQ_MST MAIN_* 컬럼   : detector가 사용하는 주기 프로필

[주의]
  기존 .pkl 파일은 이번 trainer 실행 완료 전까지 detector가 계속 사용한다.
  배포 후 첫 trainer 실행 전까지 피처 수 불일치로 anomaly score가 -0.5
  fallback 처리될 수 있다.
"""

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
    MBRSH_PGM_ID, MODEL_DIR, LOG_DIR, TRAIN_HISTORY_DAYS, MIN_SAMPLE_COUNT, REGR_ID
)
from log_utils import setup_logger
from freq_utils import classify_frequency, detect_dom_pattern
from sql.trainer_sql import (
    GET_EXCLUDED_FILE_IDS,
    GET_TRAINING_DATA,
    UPSERT_FREQ_MST,
    GET_BUSINESS_DAYS,
)

log = setup_logger('trainer', LOG_DIR)


# ============================================================
# DB 연결
# ============================================================
def get_connection():
    return oracledb.connect(user=DB_USER, password=DB_PASSWORD, dsn=DB_DSN)


# ============================================================
# 영업일 캘린더 로드 (ICS_WRKDAY_MST, run 시작 시 1회)
# ============================================================
def get_business_days(conn):
    from datetime import date as date_type
    with conn.cursor() as cur:
        cur.execute(GET_BUSINESS_DAYS, days=TRAIN_HISTORY_DAYS)
        rows = cur.fetchall()
    return {date_type(int(r[0][:4]), int(r[0][4:6]), int(r[0][6:8])) for r in rows}


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
    df['day_of_month'] = df['reg_dt'].dt.day
    return df


# ============================================================
# Isolation Forest 학습
# ============================================================
def train_model(file_df):
    """
    피처: [arrival_sec, tot_rec_cnt, send_rec_cnt, weekday, is_month_end, day_of_month]
    StandardScaler 정규화 후 IsolationForest 학습
    """
    feature_cols = ['arrival_sec', 'tot_rec_cnt', 'send_rec_cnt', 'weekday', 'is_month_end', 'day_of_month']
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
def upsert_freq_mst(conn, file_id, freq_type, median_gap, std_gap, sample_cnt,
                    dom_pattern, file_df):
    with conn.cursor() as cur:
        cur.execute(UPSERT_FREQ_MST, {
            'mbrsh':       MBRSH_PGM_ID,
            'file_id':     file_id,
            'freq_type':   freq_type,
            'median_gap':  round(median_gap, 4),
            'std_gap':     round(std_gap, 4),
            'round_gap':   round(median_gap),
            'sample_cnt':  sample_cnt,
            'dom_pattern': dom_pattern,
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

        biz_days = get_business_days(conn)
        log.info(f"영업일 캘린더 로드: {len(biz_days)}건"
                 + (f" ({min(biz_days)} ~ {max(biz_days)})" if biz_days else ""))

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

                freq_type, median_gap, std_gap = classify_frequency(file_df, biz_days)
                round_gap   = round(median_gap)
                dom_pattern = detect_dom_pattern(file_df, freq_type, round_gap)
                upsert_freq_mst(conn, file_id, freq_type, median_gap, std_gap,
                                len(file_df), dom_pattern, file_df)

                dom_info = f", 월중패턴={dom_pattern}" if dom_pattern else ""
                log.info(f"{file_id}: 학습 완료 (샘플={len(file_df)}건, "
                         f"주기={freq_type}{dom_info}) → {iso_path}")
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
