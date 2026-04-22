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

# ── 4. BAT_ALARM_HIS 알람 이력 ───────────────────────────────────────────────
run_query(
    "BAT_ALARM_HIS - 전체",
    "SELECT ALARM_ID, FILE_ID, ALARM_DT, SEND_STS, DELAY_MIN FROM BAT_ALARM_HIS ORDER BY ALARM_DT DESC"
)

# ── 5. 시퀀스 현재 값 확인 ───────────────────────────────────────────────────
run_query(
    "SEQ_BAT_ALARM_HIS - NEXTVAL 확인",
    "SELECT SEQ_BAT_ALARM_HIS.NEXTVAL FROM DUAL"
)

# ── 6. detector SQL 핵심 로직 미리보기 ───────────────────────────────────────
run_query(
    "detector 미리보기 - 오늘 미도착 FILE_ID 후보",
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

cur.close()
conn.close()
log.info("=" * 60)
log.info("test_db.py 완료")
log.info("=" * 60)
