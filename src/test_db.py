"""
DB 연동 테스트 스크립트
- 접속 확인
- 각 테이블 샘플 데이터 조회
- detector/sender/trainer SQL 기본 동작 확인
"""
import sys
import traceback

# ── config 로드 ───────────────────────────────────────────────────────────────
try:
    import config
except Exception as e:
    print(f"[FAIL] config 로드 실패: {e}")
    sys.exit(1)

from log_utils import setup_logger
log = setup_logger('test_db', config.LOG_DIR)

log.info("=" * 60)
log.info("test_db.py 시작")
log.info("=" * 60)
log.info(f"DSN : {config.DB_DSN}")
log.info(f"USER: {config.DB_USER}")
log.info(f"VOLUME_ZSCORE_THRESHOLD: {config.VOLUME_ZSCORE_THRESHOLD}")

# ── oracledb 임포트 ──────────────────────────────────────────────────────────
try:
    import oracledb
    log.info(f"oracledb {oracledb.__version__} 임포트 완료")
except ImportError:
    log.error("oracledb 미설치 → pip install oracledb")
    sys.exit(1)

# ── DB 접속 ──────────────────────────────────────────────────────────────────
try:
    conn = oracledb.connect(
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        dsn=config.DB_DSN,
    )
    log.info(f"DB 접속 성공 (Oracle {conn.version})")
except Exception as e:
    log.error(f"DB 접속 실패: {e}")
    sys.exit(1)

cur = conn.cursor()

def run_query(label, sql, params=None):
    """쿼리 실행 후 결과 로깅. 실패해도 종료하지 않음."""
    try:
        cur.execute(sql, params or {})
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        log.info(f"── {label} ({len(rows)}건) ──")
        if rows:
            log.info("  " + " | ".join(cols))
            log.info("  " + "-" * 80)
            for r in rows[:5]:
                log.info("  " + " | ".join(str(v) for v in r))
            if len(rows) > 5:
                log.info(f"  ... (이하 {len(rows)-5}건 생략)")
        else:
            log.info("  (데이터 없음)")
    except Exception as e:
        log.error(f"[FAIL] {label}: {e}")
        log.debug(traceback.format_exc())

# ── 1. COM_BATFILE_TRN 수신 완료 건수 ────────────────────────────────────────
run_query(
    "COM_BATFILE_TRN - 최근 7일 수신 완료",
    """
    SELECT FILE_ID, FILE_NM, REG_DT, STS_CD
    FROM   COM_BATFILE_TRN
    WHERE  TRANS_RCV_FG = 'R'
      AND  STS_CD = '3'
      AND  REG_DT >= SYSDATE - 7
    ORDER  BY REG_DT DESC
    FETCH FIRST 10 ROWS ONLY
    """
)

# ── 2. FILE_ID별 수신 건수 요약 ──────────────────────────────────────────────
run_query(
    "COM_BATFILE_TRN - FILE_ID별 90일 수신 건수",
    """
    SELECT FILE_ID, COUNT(*) AS CNT,
           MIN(REG_DT) AS FIRST_RCV, MAX(REG_DT) AS LAST_RCV
    FROM   COM_BATFILE_TRN
    WHERE  TRANS_RCV_FG = 'R'
      AND  STS_CD = '3'
      AND  REG_DT >= SYSDATE - 90
    GROUP  BY FILE_ID
    ORDER  BY CNT DESC
    """
)

# ── 3. BAT_MNTLST_EXC 제외 목록 ─────────────────────────────────────────────
run_query(
    "BAT_MNTLST_EXC - 전체",
    "SELECT * FROM BAT_MNTLST_EXC ORDER BY USE_YN, FILE_ID"
)

# ── 4. BAT_ALARM_HIS 알람 이력 (ALARM_TYPE 포함) ─────────────────────────────
run_query(
    "BAT_ALARM_HIS - 전체 (M/V 구분)",
    """
    SELECT ALARM_ID, FILE_ID, ALARM_DT, ALARM_TYPE,
           SEND_STS, DELAY_MIN, ANOMALY_SCORE
    FROM   BAT_ALARM_HIS
    ORDER  BY ALARM_DT DESC
    FETCH FIRST 10 ROWS ONLY
    """
)

# ── 5. BAT_ALARM_HIS ALARM_TYPE별 집계 ───────────────────────────────────────
run_query(
    "BAT_ALARM_HIS - ALARM_TYPE별 집계",
    """
    SELECT ALARM_TYPE, COUNT(*) AS CNT,
           SUM(CASE WHEN SEND_STS = '1' THEN 1 ELSE 0 END) AS SENT,
           SUM(CASE WHEN SEND_STS = '9' THEN 1 ELSE 0 END) AS FAILED
    FROM   BAT_ALARM_HIS
    GROUP  BY ALARM_TYPE
    ORDER  BY ALARM_TYPE
    """
)

# ── 6. 시퀀스 현재 값 확인 ───────────────────────────────────────────────────
run_query(
    "SEQ_BAT_ALARM_HIS - NEXTVAL 확인",
    "SELECT SEQ_BAT_ALARM_HIS.NEXTVAL FROM DUAL"
)

# ── 7. BAT_FILE_FREQ_MST 주기 프로필 ─────────────────────────────────────────
run_query(
    "BAT_FILE_FREQ_MST - 전체 (EFFECTIVE_SRC / DOM_PATTERN 포함)",
    """
    SELECT FILE_ID, EFFECTIVE_SRC,
           CASE WHEN EFFECTIVE_SRC = 'T' THEN MAIN_FREQ_TYPE ELSE FB_FREQ_TYPE END AS FREQ_TYPE,
           CASE WHEN EFFECTIVE_SRC = 'T' THEN MAIN_ROUND_GAP  ELSE FB_ROUND_GAP  END AS ROUND_GAP,
           CASE WHEN EFFECTIVE_SRC = 'T' THEN MAIN_DOM_PATTERN ELSE FB_DOM_PATTERN END AS DOM_PATTERN,
           CASE WHEN EFFECTIVE_SRC = 'T' THEN MAIN_SAMPLE_CNT ELSE FB_SAMPLE_CNT END AS SAMPLE_CNT
    FROM   BAT_FILE_FREQ_MST
    ORDER  BY EFFECTIVE_SRC, FILE_ID
    """
)

# ── 8. BAT_FILE_FREQ_MST FREQ_TYPE별 집계 ────────────────────────────────────
run_query(
    "BAT_FILE_FREQ_MST - FREQ_TYPE별 집계",
    """
    SELECT EFFECTIVE_SRC,
           CASE WHEN EFFECTIVE_SRC = 'T' THEN MAIN_FREQ_TYPE ELSE FB_FREQ_TYPE END AS FREQ_TYPE,
           COUNT(*) AS CNT,
           SUM(CASE WHEN CASE WHEN EFFECTIVE_SRC = 'T'
                              THEN MAIN_DOM_PATTERN ELSE FB_DOM_PATTERN END
                    IS NOT NULL THEN 1 ELSE 0 END) AS HAS_DOM_PATTERN
    FROM   BAT_FILE_FREQ_MST
    GROUP  BY EFFECTIVE_SRC,
              CASE WHEN EFFECTIVE_SRC = 'T' THEN MAIN_FREQ_TYPE ELSE FB_FREQ_TYPE END
    ORDER  BY EFFECTIVE_SRC, FREQ_TYPE
    """
)

# ── 9. detector 미리보기 - 오늘 미도착 FILE_ID 후보 (M 알람 대상) ─────────────
run_query(
    "detector 미리보기 - 오늘 미도착 FILE_ID 후보 (M 알람)",
    """
    SELECT DISTINCT t.FILE_ID
    FROM   COM_BATFILE_TRN t
    WHERE  t.TRANS_RCV_FG = 'R'
      AND  t.STS_CD = '3'
      AND  t.REG_DT >= SYSDATE - 90
      AND  t.FILE_ID NOT IN (
               SELECT FILE_ID FROM BAT_MNTLST_EXC WHERE USE_YN = 'Y'
           )
      AND  t.FILE_ID NOT IN (
               SELECT FILE_ID
               FROM   COM_BATFILE_TRN
               WHERE  TRANS_RCV_FG = 'R'
                 AND  STS_CD = '3'
                 AND  TRUNC(REG_DT) = TRUNC(SYSDATE)
           )
    ORDER  BY t.FILE_ID
    """
)

# ── 10. detector 미리보기 - 오늘 도착 FILE_ID + 건수 현황 (V 알람 후보) ────────
run_query(
    "detector 미리보기 - 오늘 도착 FILE_ID 건수 현황 (V 알람 후보)",
    """
    SELECT t.FILE_ID,
           t.TOT_REC_CNT AS TODAY_CNT,
           h.MEDIAN_CNT,
           ROUND(ABS(t.TOT_REC_CNT - h.MEDIAN_CNT)
                 / NULLIF(h.STD_CNT, 0), 2) AS Z_SCORE
    FROM (
        SELECT FILE_ID, TOT_REC_CNT
        FROM   COM_BATFILE_TRN
        WHERE  TRANS_RCV_FG = 'R'
          AND  STS_CD = '3'
          AND  TRUNC(REG_DT) = TRUNC(SYSDATE)
    ) t
    JOIN (
        SELECT FILE_ID,
               MEDIAN(TOT_REC_CNT)  AS MEDIAN_CNT,
               STDDEV(TOT_REC_CNT)  AS STD_CNT
        FROM   COM_BATFILE_TRN
        WHERE  TRANS_RCV_FG = 'R'
          AND  STS_CD = '3'
          AND  TRUNC(REG_DT) != TRUNC(SYSDATE)
          AND  REG_DT >= SYSDATE - 90
        GROUP  BY FILE_ID
    ) h ON t.FILE_ID = h.FILE_ID
    WHERE t.FILE_ID NOT IN (
        SELECT FILE_ID FROM BAT_MNTLST_EXC WHERE USE_YN = 'Y'
    )
    ORDER  BY Z_SCORE DESC NULLS LAST
    """
)

cur.close()
conn.close()
log.info("=" * 60)
log.info("test_db.py 완료")
log.info("=" * 60)
