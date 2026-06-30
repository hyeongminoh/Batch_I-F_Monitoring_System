# ============================================================
# detector_detail.py - 개선된 배치 파일 미수신 감지 프로세스
# cron: */10 * * * * python3 {설치경로}/src/detector_detail.py
# ============================================================
"""
detector.py와 동일한 탐지 로직(M 알람 / V 알람)을 수행하되,
대규모 파일(약 4,000개 이상) 환경에서의 성능 문제를 4가지 방식으로 개선한 버전.

[detector.py 대비 개선사항]

  1. 오늘 수신 예정 파일만 모수 추출 (get_expected_file_ids)
     BAT_FILE_FREQ_MST의 주기 프로필을 기반으로 오늘 탐지 대상을 사전 필터링.
     - DAILY       : 항상 포함
     - WEEKLY      : 항상 포함 (요일 필터는 window 계산 시 적용)
     - MONTHLY     : DOM_PATTERN anchor ± tolerance 범위에 오늘 날짜 포함 시만
     - EVERY_N_DAYS: 동일
     - IRREGULAR   : 항상 제외
     → 4,000개 중 MONTHLY·EVERY_N_DAYS 파일 대부분이 탈락해 처리 대상 감소.

  2. 예정 파일만 90일 이력 조회 (get_historical_data)
     COM_BATFILE_TRN 조회 시 expected 파일 FILE_ID만 IN 절로 필터.
     Oracle IN 절 상한(1000개) 대비 안전하게 900개씩 chunk 처리.
     → 전체 이력 로드 대비 I/O 및 메모리 대폭 절감.

  3. 오늘 알람 1회 배치 로드 (load_alarms_today)
     detector.py: 파일마다 HAS_ALARM_TODAY 쿼리 (최대 8,000회/run)
     detector_detail.py: run 시작 시 GET_ALARMS_TODAY 1회 조회 → set 보관.
     has_alarm_today()는 DB 대신 set O(1) 조회로 대체.
     INSERT 후 alarms_today.add()로 메모리 갱신 → run 내 중복 방지 유지.

  4. groupby 사전 인덱싱 (grouped dict)
     detector.py: 루프마다 hist_df[hist_df['file_id'] == file_id] 전체 스캔
     detector_detail.py: run 시작 시 groupby로 dict 생성 → O(1) 조회.

[탐지 로직]
  detector.py와 동일. 자세한 설명은 detector.py 모듈 docstring 참조.

[DB 쿼리 횟수 비교 (파일 4,000개 기준)]
  detector.py      : ~8,001회/run
  detector_detail.py: ~4회/run (제외 조회 1 + 프로필 1 + 이력 조회 N + 알람 1)
"""

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
    USE_LLM, OLLAMA_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT,
    HISTORY_DAYS, MIN_SAMPLE_COUNT, REGR_ID,
    VOLUME_ZSCORE_THRESHOLD,
)
import llm as llm_module
from log_utils import setup_logger
from freq_utils import classify_frequency, sec_to_hms, detect_dom_pattern
from sql.detector_sql import (
    GET_EXCLUDED_FILE_IDS,
    INSERT_ALARM,
    GET_FREQ_MST,
    UPSERT_FREQ_MST_FB,
)
from sql.detector_detail_sql import (
    GET_HISTORICAL_DATA_FILTERED,
    GET_ALARMS_TODAY,
)

log = setup_logger('detector_detail', LOG_DIR)

WEEKDAY_KO = ['월', '화', '수', '목', '금', '토', '일']


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
# BAT_FILE_FREQ_MST 전체 로드 (run 시작 시 1회)
# ============================================================
def load_freq_mst(conn):
    with conn.cursor() as cur:
        cur.execute(GET_FREQ_MST)
        rows = cur.fetchall()
    result = {}
    for file_id, effective_src, freq_type, median_gap, std_gap, round_gap, dom_pattern in rows:
        result[file_id] = {
            'effective_src': effective_src,
            'freq_type':     freq_type or 'IRREGULAR',
            'median_gap':    float(median_gap or 0),
            'std_gap':       float(std_gap or 0),
            'round_gap':     int(round_gap or 0),
            'dom_pattern':   dom_pattern,
        }
    return result


# ============================================================
# 오늘 수신 예정 파일 모수 추출
# ============================================================
def get_expected_file_ids(freq_mst, excluded, today):
    """
    BAT_FILE_FREQ_MST 기반으로 오늘 수신 예정 FILE_ID 추출.

    DAILY       : 항상 포함
    WEEKLY      : 항상 포함 (실제 요일 필터는 calc_arrival_window 단계에서 적용)
    MONTHLY     : DOM_PATTERN anchor ± tolerance 범위에 오늘 day가 포함될 때
    EVERY_N_DAYS: 동일
    IRREGULAR   : 항상 제외
    """
    stats = {
        'DAILY':        {'total': 0, 'expected': 0},
        'WEEKLY':       {'total': 0, 'expected': 0},
        'MONTHLY':      {'total': 0, 'expected': 0},
        'EVERY_N_DAYS': {'total': 0, 'expected': 0},
        'IRREGULAR':    {'total': 0, 'expected': 0},
    }
    expected = []

    for file_id, profile in freq_mst.items():
        if file_id in excluded:
            continue

        freq_type   = profile['freq_type']
        round_gap   = profile['round_gap']
        dom_pattern = profile['dom_pattern']

        if freq_type == 'DAILY':
            stat_key = 'DAILY'
        elif freq_type == 'WEEKLY':
            stat_key = 'WEEKLY'
        elif freq_type == 'MONTHLY':
            stat_key = 'MONTHLY'
        elif freq_type.startswith('EVERY_'):
            stat_key = 'EVERY_N_DAYS'
        else:
            stat_key = 'IRREGULAR'

        stats[stat_key]['total'] += 1

        if freq_type == 'IRREGULAR':
            continue

        if freq_type in ('DAILY', 'WEEKLY'):
            expected.append(file_id)
            stats[stat_key]['expected'] += 1
            continue

        # MONTHLY / EVERY_N_DAYS: anchor ± tolerance
        tolerance = max(2, round_gap // 5)
        if dom_pattern:
            anchor_days = [int(d) for d in dom_pattern.split(',')]
            closest     = min(anchor_days, key=lambda d: abs(d - today.day))
            is_expected = abs(closest - today.day) <= tolerance
        else:
            is_expected = True  # DOM_PATTERN 미탐지 시 보수적으로 포함

        if is_expected:
            expected.append(file_id)
            stats[stat_key]['expected'] += 1

    return expected, stats


# ============================================================
# 모수 추출 결과 로그
# ============================================================
def log_pool_summary(stats, expected, today):
    total_reg = sum(s['total'] for s in stats.values())
    notes = {
        'DAILY':        '매일',
        'WEEKLY':       '요일 필터 미적용 (window 계산 시 적용)',
        'MONTHLY':      f'anchor ± tolerance, day={today.day}',
        'EVERY_N_DAYS': f'anchor ± tolerance, day={today.day}',
        'IRREGULAR':    '탐지 제외',
    }
    log.info(f"[모수 추출] {today.strftime('%Y-%m-%d')} "
             f"({WEEKDAY_KO[today.weekday()]}요일, day={today.day})")
    log.info(f"  {'FREQ_TYPE':<16} {'등록':>6}  {'오늘 대상':>9}  비고")
    log.info(f"  {'-'*62}")
    for ft in ['DAILY', 'WEEKLY', 'MONTHLY', 'EVERY_N_DAYS', 'IRREGULAR']:
        s       = stats[ft]
        exp_str = f"{s['expected']}건" if ft != 'IRREGULAR' else '-'
        log.info(f"  {ft:<16} {s['total']:>5}건  {exp_str:>8}  {notes[ft]}")
    log.info(f"  {'-'*62}")
    pct = len(expected) / total_reg * 100 if total_reg else 0
    log.info(f"  {'합계':<16} {total_reg:>5}건  {len(expected):>8}건  ({pct:.1f}%)")


# ============================================================
# 오늘 발생 알람 전체 1회 로드
# 파일별 개별 쿼리(HAS_ALARM_TODAY) 대체 → set O(1) 조회
# ============================================================
def load_alarms_today(conn):
    """반환: {('FILE_ID', 'M'), ('FILE_ID', 'V'), ...}"""
    with conn.cursor() as cur:
        cur.execute(GET_ALARMS_TODAY)
        return {(row[0], row[1]) for row in cur.fetchall()}


def has_alarm_today(alarms_today, file_id, alarm_type):
    return (file_id, alarm_type) in alarms_today


# ============================================================
# 오늘 수신 예정 파일의 90일 이력 조회 (expected 파일만)
# Oracle IN 절 1000개 제한 → 900개씩 chunk 처리
# ============================================================
def get_historical_data(conn, expected_file_ids):
    if not expected_file_ids:
        return pd.DataFrame()

    all_rows   = []
    chunk_size = 900

    for i in range(0, len(expected_file_ids), chunk_size):
        chunk        = expected_file_ids[i:i + chunk_size]
        placeholders = ', '.join(f':id{j}' for j in range(len(chunk)))
        params       = {f'id{j}': fid for j, fid in enumerate(chunk)}
        params['days'] = HISTORY_DAYS

        sql = GET_HISTORICAL_DATA_FILTERED.format(placeholders=placeholders)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            all_rows.extend(cur.fetchall())

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows,
                      columns=['file_id', 'file_nm', 'reg_dt',
                               'tot_rec_cnt', 'send_rec_cnt'])
    df['reg_dt']       = pd.to_datetime(df['reg_dt'])
    df['arrival_date'] = df['reg_dt'].dt.date
    df['arrival_sec']  = (df['reg_dt'].dt.hour * 3600
                          + df['reg_dt'].dt.minute * 60
                          + df['reg_dt'].dt.second)
    df['weekday']      = df['reg_dt'].dt.weekday
    df['is_month_end'] = (df['reg_dt'].dt.day >= 25).astype(int)
    df['day_of_month'] = df['reg_dt'].dt.day
    return df


# ============================================================
# 오늘 도착 여부 확인
# ============================================================
def has_arrived_today(file_df, today):
    return (file_df['arrival_date'] == today).any()


# ============================================================
# 공통 컨텍스트 필터
# ============================================================
def filter_by_context(file_df, today, freq_type, round_gap, dom_pattern):
    if freq_type == 'MONTHLY' or freq_type.startswith('EVERY_'):
        tolerance = max(2, round_gap // 5)
        dom = file_df['reg_dt'].dt.day
        if dom_pattern:
            anchor_days = [int(d) for d in dom_pattern.split(',')]
            closest     = min(anchor_days, key=lambda d: abs(d - today.day))
            filtered    = file_df[(dom - closest).abs() <= tolerance]
            filter_desc = f"날짜(anchor={closest}±{tolerance}일, 패턴={dom_pattern})"
        else:
            filtered    = file_df[(dom - today.day).abs() <= tolerance]
            filter_desc = f"날짜(±{tolerance}일)"
    else:
        today_weekday      = today.weekday()
        today_is_month_end = 1 if today.day >= 25 else 0
        mask        = ((file_df['weekday'] == today_weekday) &
                       (file_df['is_month_end'] == today_is_month_end))
        filtered    = file_df[mask]
        filter_desc = "요일/월말"
    return filtered, filter_desc


# ============================================================
# 도착 window 계산 (5th / 50th / 95th percentile)
# ============================================================
def calc_arrival_window(file_df, today, freq_type='', round_gap=0, dom_pattern=None):
    filtered, filter_desc = filter_by_context(file_df, today, freq_type, round_gap, dom_pattern)
    if len(filtered) < MIN_SAMPLE_COUNT:
        return None, filter_desc
    arr = filtered['arrival_sec'].values
    return {
        'exp_min':    sec_to_hms(np.percentile(arr, 5)),
        'exp_med':    sec_to_hms(np.percentile(arr, 50)),
        'exp_max':    sec_to_hms(np.percentile(arr, 95)),
        'sample_cnt': len(filtered),
    }, filter_desc


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
# BAT_FILE_FREQ_MST FB 기록 (freq_mst 미등록 FILE_ID 한정)
# ============================================================
def upsert_freq_mst_fb(conn, file_id, freq_type, median_gap, std_gap, dom_pattern, file_df):
    with conn.cursor() as cur:
        cur.execute(UPSERT_FREQ_MST_FB, {
            'mbrsh':       MBRSH_PGM_ID,
            'file_id':     file_id,
            'freq_type':   freq_type,
            'median_gap':  round(median_gap, 4),
            'std_gap':     round(std_gap, 4),
            'round_gap':   round(median_gap),
            'dom_pattern': dom_pattern,
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
        day_of_month = today.day

        log.info(f"  [{file_id}] 모델 입력 피처: "
                 f"arrival_sec={arrival_sec}, tot_rec={tot_rec_cnt:.0f}, "
                 f"send_rec={send_rec_cnt:.0f}, weekday={weekday}, "
                 f"month_end={is_month_end}, dom={day_of_month}")

        X     = np.array([[arrival_sec, tot_rec_cnt, send_rec_cnt,
                           weekday, is_month_end, day_of_month]])
        score = round(float(iso.score_samples(scaler.transform(X))[0]), 4)

        log.info(f"  [{file_id}] anomaly score = {score} "
                 f"({'정상 범위' if score > -0.5 else '이상 의심'}, 음수일수록 이상)")
        return score

    except Exception as e:
        log.error(f"  [{file_id}] anomaly score 계산 실패 - {e}")
        return -0.5


# ============================================================
# M 알람 메시지 생성 (fallback + LLM 비교)
# ============================================================
def build_fallback_message(file_id, freq_type, window, delay_min):
    return (
        f"[배치 미수신 알람] {file_id}\n"
        f"마감: {window['exp_max']} / 지연: {delay_min}분 / 주기: {freq_type}\n"
        f"즉시 확인이 필요합니다."
    )


def save_compare_file(directory, file_id, ts, message):
    os.makedirs(directory, exist_ok=True)
    filepath = os.path.join(directory, f"ALARM_{file_id}_{ts}.txt")
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(message)
    return filepath


def generate_alarm_message(file_id, freq_type, window, check_time,
                            delay_min, anomaly_score, today, ts):
    fallback_msg  = build_fallback_message(file_id, freq_type, window, delay_min)
    fallback_path = save_compare_file(ALARM_DIR_FALLBACK, file_id, ts, fallback_msg)

    llm_msg, llm_ok = None, False
    if USE_LLM:
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
# BAT_ALARM_HIS INSERT (M · V 공통)
# 삽입 후 alarms_today set 갱신 → run 내 중복 방지
# ============================================================
def insert_alarm(conn, alarms_today, file_id, file_nm, freq_type,
                 window, check_time, delay_min, anomaly_score,
                 alarm_msg, now, alarm_type='M'):
    with conn.cursor() as cur:
        cur.execute(INSERT_ALARM, {
            'mbrsh':         MBRSH_PGM_ID,
            'file_id':       file_id,
            'file_nm':       file_nm,
            'alarm_dt':      now,
            'alarm_type':    alarm_type,
            'freq_type':     freq_type,
            'exp_min':       window['exp_min'] if window else None,
            'exp_med':       window['exp_med'] if window else None,
            'exp_max':       window['exp_max'] if window else None,
            'chk_time':      check_time,
            'delay_min':     delay_min,
            'anomaly_score': anomaly_score,
            'alarm_msg':     alarm_msg[:2000],
            'regr_id':       REGR_ID,
        })
    conn.commit()
    alarms_today.add((file_id, alarm_type))  # 메모리 동기화 (run 중 중복 방지)
    log.info(f"  [{file_id}] BAT_ALARM_HIS INSERT 완료 (TYPE={alarm_type})")


# ============================================================
# V 알람 fallback 메시지
# ============================================================
def build_volume_fallback_message(file_id, today_cnt, hist_median, hist_std, z_score):
    deviation_pct = ((today_cnt - hist_median) / hist_median * 100) if hist_median > 0 else 0
    direction     = "초과" if today_cnt > hist_median else "미달"
    return (
        f"[배치 건수 이상 알람] {file_id}\n"
        f"금일 수신건수: {today_cnt:,.0f}건 "
        f"(예상 중앙값: {hist_median:,.0f}건, 편차: {z_score:.1f}σ, {deviation_pct:+.1f}% {direction})\n"
        f"즉시 확인이 필요합니다."
    )


# ============================================================
# V 알람 탐지 (건수 이상)
# ============================================================
def check_volume_anomaly(conn, alarms_today, file_id, file_nm, file_df,
                          freq_type, round_gap, dom_pattern, today, now):
    if has_alarm_today(alarms_today, file_id, 'M'):
        log.info(f"  [{file_id}] V SKIP → 오늘 M 알람 존재, V 알람 억제")
        return False

    if has_alarm_today(alarms_today, file_id, 'V'):
        log.info(f"  [{file_id}] V SKIP → 오늘 이미 V 알람 발송됨")
        return False

    today_rows = file_df[file_df['arrival_date'] == today]
    if today_rows.empty:
        return False
    today_cnt = float(today_rows['tot_rec_cnt'].iloc[-1])

    hist = file_df[file_df['arrival_date'] != today]
    hist_filtered, filter_desc = filter_by_context(hist, today, freq_type, round_gap, dom_pattern)

    if len(hist_filtered) < MIN_SAMPLE_COUNT:
        log.info(f"  [{file_id}] V SKIP → {filter_desc} 건수 비교 샘플 부족")
        return False

    hist_counts = hist_filtered['tot_rec_cnt'].values.astype(float)
    hist_median = float(np.median(hist_counts))
    hist_std    = float(np.std(hist_counts))

    if hist_std == 0:
        z_score = 0.0 if today_cnt == hist_median else 99.0
    else:
        z_score = abs(today_cnt - hist_median) / hist_std

    log.info(f"  [{file_id}] 건수: 오늘={today_cnt:,.0f}, 중앙={hist_median:,.0f}, "
             f"std={hist_std:,.0f}, z={z_score:.2f} (임계값={VOLUME_ZSCORE_THRESHOLD})")

    if z_score <= VOLUME_ZSCORE_THRESHOLD:
        log.info(f"  [{file_id}] 건수 정상")
        return False

    log.info(f"  [{file_id}] 건수 이상 감지 → V 알람 발동")
    alarm_msg = build_volume_fallback_message(file_id, today_cnt, hist_median, hist_std, z_score)
    log.info(f"  [{file_id}] V 알람 메시지:\n{alarm_msg}")

    insert_alarm(
        conn, alarms_today, file_id, file_nm, freq_type,
        window=None, check_time=now.strftime("%H:%M:%S"),
        delay_min=None, anomaly_score=round(z_score, 4),
        alarm_msg=alarm_msg, now=now, alarm_type='V',
    )
    log.info("-" * 60)
    return True


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
    log.info(f"  [RUN START] detector_detail.py  {now.strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("▼" * 60)

    try:
        conn = get_connection()
        log.info("DB 연결 성공")
    except Exception as e:
        log.error(f"DB 연결 실패: {e}")
        sys.exit(1)

    alarm_cnt     = 0
    vol_alarm_cnt = 0

    try:
        # ── 1. 제외 FILE_ID 로드 ──────────────────────────
        excluded = get_excluded_file_ids(conn)
        log.info(f"제외 FILE_ID: {len(excluded)}건")

        # ── 2. 전체 주기 프로필 로드 ─────────────────────
        freq_mst = load_freq_mst(conn)
        t_cnt    = sum(1 for v in freq_mst.values() if v['effective_src'] == 'T')
        d_cnt    = sum(1 for v in freq_mst.values() if v['effective_src'] == 'D')
        log.info(f"BAT_FILE_FREQ_MST 로드: {len(freq_mst)}건 (T={t_cnt}, D={d_cnt})")

        # ── 3. 오늘 수신 예정 파일 모수 추출 ─────────────
        expected, stats = get_expected_file_ids(freq_mst, excluded, today)
        log_pool_summary(stats, expected, today)

        if not expected:
            log.info("오늘 수신 예정 파일 없음. 종료.")
            return

        # ── 4. 예정 파일만 90일 이력 조회 ────────────────
        log.info(f"이력 조회 시작 (대상 {len(expected)}건, {HISTORY_DAYS}일)")
        hist_df = get_historical_data(conn, expected)
        log.info(f"이력 조회 완료: {len(hist_df):,}행")

        if hist_df.empty:
            log.info("이력 데이터 없음. 종료.")
            return

        # ── 5. file_id 기준 사전 그룹핑 ──────────────────
        grouped = {
            fid: grp.reset_index(drop=True)
            for fid, grp in hist_df.groupby('file_id')
        }
        log.info(f"그룹핑 완료: {len(grouped)}개 FILE_ID")

        # ── 6. 오늘 알람 현황 1회 로드 ───────────────────
        alarms_today = load_alarms_today(conn)
        m_cnt = sum(1 for _, t in alarms_today if t == 'M')
        v_cnt = sum(1 for _, t in alarms_today if t == 'V')
        log.info(f"오늘 알람 현황: {len(alarms_today)}건 (M={m_cnt}, V={v_cnt})")
        log.info("-" * 60)

        # ── 7. 파일별 탐지 ────────────────────────────────
        for file_id in expected:
            try:
                log.info(f"[{file_id}] 점검 시작")

                file_df = grouped.get(file_id)
                if file_df is None or file_df.empty:
                    log.info(f"  [{file_id}] SKIP → 90일 이력 없음")
                    continue

                file_nm = str(file_df['file_nm'].iloc[-1]) \
                    if pd.notna(file_df['file_nm'].iloc[-1]) else file_id

                # 1. 수신 주기 결정 (freq_mst 우선, 없으면 직접 계산)
                profile = freq_mst.get(file_id)
                if profile:
                    freq_type   = profile['freq_type']
                    median_gap  = profile['median_gap']
                    std_gap     = profile['std_gap']
                    round_gap   = profile['round_gap']
                    dom_pattern = profile['dom_pattern']
                    dom_info    = f", 월중패턴={dom_pattern}" if dom_pattern else ""
                    log.info(f"  [{file_id}] 수신 주기 (MST/{profile['effective_src']}): "
                             f"{freq_type} (median={median_gap:.1f}일, std={std_gap:.1f}일{dom_info})")
                else:
                    freq_type, median_gap, std_gap = classify_frequency(file_df)
                    round_gap   = round(median_gap)
                    dom_pattern = detect_dom_pattern(file_df, freq_type, round_gap)
                    dom_info    = f", 월중패턴={dom_pattern}" if dom_pattern else ""
                    log.info(f"  [{file_id}] 수신 주기 (계산): {freq_type} "
                             f"(median={median_gap:.1f}일, std={std_gap:.1f}일{dom_info})")
                    try:
                        upsert_freq_mst_fb(conn, file_id, freq_type, median_gap,
                                           std_gap, dom_pattern, file_df)
                        log.info(f"  [{file_id}] BAT_FILE_FREQ_MST FB 기록 완료")
                    except Exception as fb_e:
                        log.warning(f"  [{file_id}] BAT_FILE_FREQ_MST FB 기록 실패(무시): {fb_e}")

                if freq_type == 'IRREGULAR':
                    log.info(f"  [{file_id}] SKIP → IRREGULAR (불규칙 수신 파일)")
                    continue

                # 2. 도착 여부 분기
                if has_arrived_today(file_df, today):
                    # ── V 알람 ──────────────────────────────
                    log.info(f"  [{file_id}] 오늘 수신 완료 → 건수 이상 체크(V)")
                    if check_volume_anomaly(conn, alarms_today, file_id, file_nm, file_df,
                                            freq_type, round_gap, dom_pattern, today, now):
                        vol_alarm_cnt += 1
                    continue

                # ── M 알람 ──────────────────────────────────
                # 3. 중복 방지
                if has_alarm_today(alarms_today, file_id, 'M'):
                    log.info(f"  [{file_id}] SKIP → 오늘 이미 M 알람 발송됨 (중복 방지)")
                    continue

                # 4. 도착 window 계산
                window, filter_desc = calc_arrival_window(
                    file_df, today, freq_type, round_gap, dom_pattern
                )
                if window is None:
                    log.info(f"  [{file_id}] SKIP → {filter_desc} 조건 샘플 부족 "
                             f"(최소 {MIN_SAMPLE_COUNT}건 필요, 오늘 수신 예정일 아닐 가능성)")
                    continue
                log.info(f"  [{file_id}] 도착 window [{filter_desc} 필터]: "
                         f"{window['exp_min']} ~ {window['exp_max']} "
                         f"(중앙값={window['exp_med']}, 샘플={window['sample_cnt']}건)")

                # 5. deadline 미초과
                if not is_past_deadline(window['exp_max'], now):
                    log.info(f"  [{file_id}] SKIP → deadline({window['exp_max']}) 미초과, "
                             f"현재 {check_time}")
                    continue
                log.info(f"  [{file_id}] deadline({window['exp_max']}) 초과 확인 → M 알람 발동")

                # 6. 지연 분
                delay_min = calc_delay_min(window['exp_max'], now)
                log.info(f"  [{file_id}] 지연 시간: {delay_min}분")

                # 7. Isolation Forest anomaly score
                anomaly_score = get_anomaly_score(file_id, file_df, today, now)

                # 8. 알람 메시지 생성
                alarm_msg = generate_alarm_message(
                    file_id, freq_type, window, check_time,
                    delay_min, anomaly_score, today, ts
                )
                log.info(f"  [{file_id}] 알람 메시지:\n{alarm_msg}")

                # 9. BAT_ALARM_HIS INSERT (M)
                insert_alarm(
                    conn, alarms_today, file_id, file_nm, freq_type,
                    window, check_time, delay_min, anomaly_score,
                    alarm_msg, now, alarm_type='M'
                )
                alarm_cnt += 1
                log.info("-" * 60)

            except Exception as e:
                log.error(f"[{file_id}] 처리 중 오류 - {e}")
                continue

    finally:
        conn.close()

    elapsed = int((datetime.now() - now).total_seconds())
    log.info("▲" * 60)
    log.info(f"  [RUN END  ] detector_detail.py  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
             f"  |  M알람 {alarm_cnt}건  |  V알람 {vol_alarm_cnt}건  |  소요 {elapsed}초")
    log.info("▲" * 60)
    log.info("")


if __name__ == '__main__':
    main()
