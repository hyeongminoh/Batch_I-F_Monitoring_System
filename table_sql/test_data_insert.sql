-- ================================================================
-- COM_BATFILE_TRN 테스트 데이터 (trainer + detector 공통)
--
-- 애플리케이션 조회 조건 (src/sql/*_sql.py):
--   TRANS_RCV_FG = 'R'
--   STS_CD       = '3'
--   REG_DT      >= SYSDATE - :days   (detector 90일 / trainer 180일)
--
-- detector가 "아무 것도 안 함"으로 보일 때 흔한 원인:
--   1) 오늘(TRUNC(SYSDATE)) 해당 FILE_ID 수신 행이 이미 있음 → 미수신 알람 안 남
--   2) 동일 요일 + 월말여부(day>=25) 필터 후 샘플 < MIN_SAMPLE_COUNT(기본 3)
--   3) 주기 IRREGULAR 로 분류됨
--   4) BAT_MNTLST_EXC 에 제외 등록됨
--
-- 아래 스크립트는 위를 피하도록 날짜·건수를 맞춤.
-- (재실행 시 중복 방지: 필요하면 상단 DELETE 블록 주석 해제)
-- ================================================================

-- 재실행 초기화 (선택)
-- DELETE FROM COM_BATFILE_TRN WHERE REGR_ID = 'TEST';
-- DELETE FROM BAT_MNTLST_EXC WHERE REGR_ID = 'TEST' AND FILE_ID = '3520AB3901';
-- COMMIT;

-- ================================================================
-- A. 일단위 1500BIL906 — 180일치 (trainer 180일 윈도우 충족)
--    마지막 수신일 = 어제 (오늘 미수신 시나리오)
-- ================================================================
INSERT INTO COM_BATFILE_TRN (
    MBRSH_PGM_ID, FILE_ID, FILE_NM,
    TRANS_RCV_FG, STS_CD,
    TOT_REC_CNT, SEND_REC_CNT,
    REGR_ID, REG_DT
)
SELECT
    'A',
    '1500BIL906',
    '1500BIL906.DLY.' || TO_CHAR(dt, 'YYYYMMDD') || '.dat',
    'R',
    '3',
    ROUND(DBMS_RANDOM.VALUE(8000, 12000)),
    ROUND(DBMS_RANDOM.VALUE(7500, 11500)),
    'TEST',
    dt + 9 / 24 + ROUND(DBMS_RANDOM.VALUE(-10, 10)) / 1440
FROM (
    SELECT TRUNC(SYSDATE) - LEVEL AS dt
    FROM   DUAL
    CONNECT BY LEVEL <= 180
)
WHERE dt < TRUNC(SYSDATE);   -- 오늘 행 제외 → 미수신 가정

-- detector window 보강: "오늘과 같은 요일" + "오늘과 같은 월말구간(day>=25 여부)"
-- 에 해당하는 과거 일자만 골라 최소 15건 (샘플 3건 이상 확실히 확보)
INSERT INTO COM_BATFILE_TRN (
    MBRSH_PGM_ID, FILE_ID, FILE_NM,
    TRANS_RCV_FG, STS_CD,
    TOT_REC_CNT, SEND_REC_CNT,
    REGR_ID, REG_DT
)
SELECT
    'A',
    '1500BIL906',
    '1500BIL906.WIN.' || TO_CHAR(d, 'YYYYMMDD') || '.dat',
    'R',
    '3',
    ROUND(DBMS_RANDOM.VALUE(8000, 12000)),
    ROUND(DBMS_RANDOM.VALUE(7500, 11500)),
    'TEST',
    d + 9 / 24 + ROUND(DBMS_RANDOM.VALUE(-5, 5)) / 1440
FROM (
    SELECT d
    FROM (
        SELECT TRUNC(SYSDATE) - LEVEL AS d
        FROM   DUAL
        CONNECT BY LEVEL <= 400
    ) cand,
    (
        SELECT TRUNC(SYSDATE) AS td FROM DUAL
    ) p
    WHERE cand.d < p.td
      AND TO_CHAR(cand.d, 'DY', 'NLS_DATE_LANGUAGE=AMERICAN')
          = TO_CHAR(p.td, 'DY', 'NLS_DATE_LANGUAGE=AMERICAN')
      AND CASE WHEN TO_NUMBER(TO_CHAR(cand.d, 'DD')) >= 25 THEN 1 ELSE 0 END
          = CASE WHEN TO_NUMBER(TO_CHAR((SELECT TRUNC(SYSDATE) FROM DUAL), 'DD')) >= 25
                 THEN 1 ELSE 0 END
    ORDER BY d DESC
)
WHERE ROWNUM <= 15;

-- ================================================================
-- B. 주단위 2200MCT215 — 26주 (매주 목요일 09:30 전후)
--    TRUNC(SYSDATE,'IW') = 해당 주 월요일 00:00 → +3일 = 목요일 자정
--    오늘이 목요일이면 thursday_dt < TRUNC(SYSDATE) 로 이번 주 목요일 행 제외 → 미수신 시나리오
-- ================================================================
INSERT INTO COM_BATFILE_TRN (
    MBRSH_PGM_ID, FILE_ID, FILE_NM,
    TRANS_RCV_FG, STS_CD,
    TOT_REC_CNT, SEND_REC_CNT,
    REGR_ID, REG_DT
)
SELECT
    'A',
    '2200MCT215',
    '2200MCT215.WK.' || TO_CHAR(thursday_dt, 'YYYYMMDD') || '.dat',
    'R',
    '3',
    ROUND(DBMS_RANDOM.VALUE(3000, 7000)),
    ROUND(DBMS_RANDOM.VALUE(2800, 6800)),
    'TEST',
    -- 목요일 09:30 기준 ±15분
    thursday_dt + 9 / 24 + 30 / 1440 + ROUND(DBMS_RANDOM.VALUE(-15, 15)) / 1440
FROM (
    SELECT TRUNC(SYSDATE, 'IW') - (LEVEL - 1) * 7 + 3 AS thursday_dt
    FROM   DUAL
    CONNECT BY LEVEL <= 26
)
WHERE thursday_dt < TRUNC(SYSDATE);

-- ================================================================
-- C. 월단위 3500MM7902 — 매월 7일·17일 새벽 3시 전후 (과거만)
--    매월 1일(TRUNC(MM)) 기준 +6일=7일, +16일=17일
--    도착: 해당일 03:00 ±20분
-- ================================================================
INSERT INTO COM_BATFILE_TRN (
    MBRSH_PGM_ID, FILE_ID, FILE_NM,
    TRANS_RCV_FG, STS_CD,
    TOT_REC_CNT, SEND_REC_CNT,
    REGR_ID, REG_DT
)
SELECT
    'A',
    '3500MM7902',
    '3500MM7902.MO.' || TO_CHAR(target_dt, 'YYYYMMDD') || '.dat',
    'R',
    '3',
    ROUND(DBMS_RANDOM.VALUE(50000, 100000)),
    ROUND(DBMS_RANDOM.VALUE(48000, 98000)),
    'TEST',
    target_dt + 3 / 24 + ROUND(DBMS_RANDOM.VALUE(-20, 20)) / 1440
FROM (
    SELECT ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -LEVEL) + 6 AS target_dt
    FROM   DUAL
    CONNECT BY LEVEL <= 24
    UNION ALL
    SELECT ADD_MONTHS(TRUNC(SYSDATE, 'MM'), -LEVEL) + 16 AS target_dt
    FROM   DUAL
    CONNECT BY LEVEL <= 24
)
WHERE target_dt < TRUNC(SYSDATE);

-- ================================================================
-- D. 제외 후보 3520AB3901 — 불규칙 30건 + BAT_MNTLST_EXC 등록
-- ================================================================
INSERT INTO COM_BATFILE_TRN (
    MBRSH_PGM_ID, FILE_ID, FILE_NM,
    TRANS_RCV_FG, STS_CD,
    TOT_REC_CNT, SEND_REC_CNT,
    REGR_ID, REG_DT
)
SELECT
    'A',
    '3520AB3901',
    '3520AB3901.RND.' || TO_CHAR(TRUNC(SYSDATE) - ROUND(DBMS_RANDOM.VALUE(1, 180)), 'YYYYMMDD') || '.dat',
    'R',
    '3',
    ROUND(DBMS_RANDOM.VALUE(100, 5000)),
    ROUND(DBMS_RANDOM.VALUE(100, 5000)),
    'TEST',
    TRUNC(SYSDATE) - ROUND(DBMS_RANDOM.VALUE(1, 180))
        + ROUND(DBMS_RANDOM.VALUE(8, 18)) / 24
        + ROUND(DBMS_RANDOM.VALUE(0, 59)) / 1440
FROM DUAL
CONNECT BY LEVEL <= 30;

MERGE INTO BAT_MNTLST_EXC dst
USING (SELECT 'A' AS MBRSH_PGM_ID, '3520AB3901' AS FILE_ID FROM DUAL) src
ON (dst.MBRSH_PGM_ID = src.MBRSH_PGM_ID AND dst.FILE_ID = src.FILE_ID)
WHEN MATCHED THEN
    UPDATE SET
        EXCL_RSN = '비정기 랜덤 유입 파일, 모니터링 불필요',
        USE_YN   = 'Y',
        UPDR_ID  = 'TEST',
        UPD_DT   = SYSDATE
WHEN NOT MATCHED THEN
    INSERT (MBRSH_PGM_ID, FILE_ID, EXCL_RSN, USE_YN, REGR_ID, REG_DT)
    VALUES ('A', '3520AB3901', '비정기 랜덤 유입 파일, 모니터링 불필요', 'Y', 'TEST', SYSDATE);

-- ================================================================
-- E. 샘플 부족 9999TEST01 — 2건만 (trainer 스킵 / detector window 부족 가능)
-- ================================================================
INSERT INTO COM_BATFILE_TRN (
    MBRSH_PGM_ID, FILE_ID, FILE_NM,
    TRANS_RCV_FG, STS_CD,
    TOT_REC_CNT, SEND_REC_CNT,
    REGR_ID, REG_DT
)
SELECT 'A',
       '9999TEST01',
       '9999TEST01.' || TO_CHAR(SYSDATE - LEVEL * 7, 'YYYYMMDD') || '.dat',
       'R',
       '3',
       1000,
       900,
       'TEST',
       TRUNC(SYSDATE) - LEVEL * 7 + 10 / 24
FROM DUAL
CONNECT BY LEVEL <= 2;

COMMIT;

-- ================================================================
-- 검증용 (앱과 동일 조건)
-- ================================================================
-- detector 90일
/*
SELECT FILE_ID, COUNT(*) cnt
FROM   COM_BATFILE_TRN
WHERE  TRANS_RCV_FG = 'R'
  AND  STS_CD = '3'
  AND  REG_DT >= SYSDATE - 90
GROUP BY FILE_ID
ORDER BY FILE_ID;
*/

-- trainer 180일
/*
SELECT FILE_ID, COUNT(*) cnt
FROM   COM_BATFILE_TRN
WHERE  TRANS_RCV_FG = 'R'
  AND  STS_CD = '3'
  AND  REG_DT >= SYSDATE - 180
GROUP BY FILE_ID
ORDER BY FILE_ID;
*/
