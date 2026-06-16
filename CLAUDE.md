# 배치 파일 모니터링 시스템

## 프로젝트 개요
망분리 온프레미스 리눅스 서버에서 동작하는 배치 파일 미수신 탐지 및 슬랙 알람 시스템.
외부 인터넷 통신 없이 완전 로컬에서 동작한다.

## 아키텍처
2개의 독립 운영 프로세스 + 2개의 주간 학습·분석 프로세스로 구성

### detector.py (cron 10분마다)
1. COM_BATFILE_TRN 조회 (과거 90일 이력)
2. BAT_MNTLST_EXC에서 제외 FILE_ID 필터링 (USE_YN='Y')
3. BAT_FILE_FREQ_MST 전체 로드 (run 시작 시 1회)
4. FILE_ID별 수신 주기 결정 (도착 여부 분기 전 공통 수행)
   - BAT_FILE_FREQ_MST에 있으면 저장된 FREQ_TYPE 사용 (T=trainer 우선, D=detector fallback)
   - 미등록 FILE_ID는 90일 이력으로 직접 계산 → BAT_FILE_FREQ_MST에 FB(D)로 기록
5. 오늘 도착 여부에 따라 분기
   - **도착** → V 알람: TOT_REC_CNT Z-score 탐지 (M 알람 존재 시 억제)
   - **미도착** → M 알람: 95th percentile deadline 초과 체크
6. (M 알람) Isolation Forest로 anomaly score 계산 (.pkl 모델 로드)
7. (M 알람) fallback 템플릿 / Ollama EXAONE 2.4B 한국어 알람 메시지 생성 (양쪽 비교 저장)
8. BAT_ALARM_HIS INSERT (SEND_STS='0', ALARM_TYPE='M' or 'V')

### sender.py (cron 5분마다)
1. BAT_ALARM_HIS에서 SEND_STS='0' 조회
2. DB 원문 → {ALARM_DIR}/ALARM_{FILE_ID}_{YYYYMMDD_HHMMSS}_src.txt 저장 (항상)
3. USE_LLM=1 이면 LLM으로 슬랙용 문구 재작성
   - 성공 → {ALARM_DIR_LLM}/..._llm.txt 생성, 슬랙 전송본으로 사용
   - 실패 → _src.txt 로 전송
4. mon_slack.sh 실행
5. 성공 → SEND_STS='1', TGT_FILE_PATH·SEND_DT 기록
6. 실패 → SEND_STS='9', ERR_MSG 기록

### trainer.py (cron 매주 일요일 02:00)
1. 과거 180일 데이터로 FILE_ID별 Isolation Forest 재학습 (피처 6개: arrival_sec, tot_rec_cnt, send_rec_cnt, weekday, is_month_end, day_of_month)
2. BAT_MNTLST_EXC USE_YN='Y' 파일은 학습에서도 제외
3. {MODEL_DIR}/{FILE_ID}_iso.pkl 저장
4. {MODEL_DIR}/{FILE_ID}_scaler.pkl 저장
5. 학습 완료 후 수신 주기 분류 + DOM_PATTERN 탐지 → BAT_FILE_FREQ_MST MAIN(T) 컬럼 UPSERT

### recommender.py (cron 매주 월요일 03:00)
1. 90일 이력으로 IRREGULAR 또는 샘플 부족 FILE_ID 탐지
   - 이미 USE_YN IN ('Y', 'P')인 FILE_ID는 재추천 제외
2. LLM(EXAONE)으로 한국어 제외 사유 생성 (USE_LLM=0이면 fallback 템플릿 사용)
3. BAT_MNTLST_EXC INSERT (USE_YN='P' 추천대기)
   → 담당자가 'Y'(제외) 또는 'N'(유지)으로 최종 결정

## cron 설정
```
*/10 * * * * python3 {설치경로}/src/detector.py
*/5  * * * * python3 {설치경로}/src/sender.py
0 2  * * 0   python3 {설치경로}/src/trainer.py
0 3  * * 1   python3 {설치경로}/src/recommender.py
```
로그는 각 스크립트가 {LOG_DIR}에 날짜별 파일로 직접 기록 (log_utils.py 사용)

## DB 정보
- DB: Oracle
- 드라이버: oracledb 3.3 (Thin mode, Instant Client 불필요)
- 연결 방식: oracledb.connect(user=, password=, dsn=)

## 테이블 정의

### COM_BATFILE_TRN (기존 테이블 - 배치파일 전송 트랜잭션)
```sql
CREATE TABLE COM_BATFILE_TRN (
    MBRSH_PGM_ID    VARCHAR2(1)     NOT NULL,  -- 멤버쉽프로그램ID
    FILE_ID         VARCHAR2(10)    NOT NULL,  -- 파일ID (고정 식별자, 핵심 키)
    FILE_NM         VARCHAR2(40),              -- 파일명 (FILE_ID.YYYYMMDD.HHMMSS 형식)
    TRANS_RCV_FG    VARCHAR2(1),               -- 송수신구분 (R=수신)
    TRANS_ORGAN_CD  VARCHAR2(4),               -- 송신기관코드
    RCV_ORGAN_CD    VARCHAR2(4),               -- 수신기관코드
    STS_CD          VARCHAR2(1),               -- 상태코드 (3=수신완료)
    WORK_STS        VARCHAR2(1),               -- 작업상태
    SEND_FR_DT      DATE,                      -- 전송시작일시
    SEND_TO_DT      DATE,                      -- 전송종료일시
    FILE_SZ         NUMBER(13),                -- 파일크기
    TOT_REC_CNT     NUMBER(13),                -- 전체레코드수
    SEND_REC_CNT    NUMBER(13),                -- 전송레코드수
    MSG             VARCHAR2(100),             -- 메시지
    REGR_ID         VARCHAR2(8),               -- 등록자ID
    REG_DT          DATE,                      -- 등록일시 (파일 유입시간, 핵심)
    UPDR_ID         VARCHAR2(8),               -- 변경자ID
    UPD_DT          DATE,                      -- 변경일시
    FILE_NAME       VARCHAR2(40),              -- 파일명
    RCV_DY          VARCHAR2(8)                -- 수신일자
);
```
- 필터 조건: TRANS_RCV_FG = 'R' AND STS_CD = '3'
- 도착시간 기준 컬럼: REG_DT
- FILE_NM 구조: FILE_ID.YYYYMMDD.HHMMSS (예: EB140402.20260403.145707)
- FILE_ID는 날짜와 무관하게 항상 고정

### BAT_MNTLST_EXC (신규 - 모니터링 제외 파일 관리)
```sql
CREATE TABLE BAT_MNTLST_EXC (
    MBRSH_PGM_ID    VARCHAR2(1)     NOT NULL,  -- 멤버쉽프로그램ID
    FILE_ID         VARCHAR2(10)    NOT NULL,  -- 제외할 파일ID
    EXCL_RSN        VARCHAR2(200),             -- 제외사유
    USE_YN          CHAR(1),                   -- 제외여부 Y=제외중 N=해제 P=추천대기
    REGR_ID         VARCHAR2(8),               -- 등록자ID
    REG_DT          DATE,                      -- 등록일시
    UPDR_ID         VARCHAR2(8),               -- 변경자ID
    UPD_DT          DATE                       -- 변경일시
);
```
- USE_YN 흐름: P(추천대기, recommender INSERT) → Y(제외중) or N(해제)
- USE_YN='Y' 인 FILE_ID만 감지·학습 대상에서 제외 (P는 아직 제외 대상 아님)
- 삭제 없이 USE_YN으로 상태 관리 (이력 보존)

### BAT_ALARM_HIS (신규 - 알람 이력 및 중복 방지)
```sql
CREATE TABLE BAT_ALARM_HIS (
    MBRSH_PGM_ID    VARCHAR2(1)     NOT NULL,  -- 멤버쉽프로그램ID         ┐
    PROC_DY         VARCHAR2(8)     NOT NULL,  -- 처리일자 YYYYMMDD         │
    FILE_ID         VARCHAR2(10)    NOT NULL,  -- 파일ID                    ├─ PK
    FILE_NM         VARCHAR2(500)   NOT NULL,  -- 파일명                    │
    ALARM_ID        NUMBER          NOT NULL,  -- 알람ID (SEQ 채번)         ┘
    ALARM_DT        DATE,                      -- 알람발생일시
    ALARM_TYPE      CHAR(1),                   -- 알람유형 M=미수신 V=건수이상
    FREQUENCY_TYPE  VARCHAR2(20),              -- 배치수신주기
    EXP_MIN_TIME    VARCHAR2(8),               -- 예상도착 최솟값 HH24:MI:SS (5th)
    EXP_MED_TIME    VARCHAR2(8),               -- 예상도착 중앙값 HH24:MI:SS (50th)
    EXP_MAX_TIME    VARCHAR2(8),               -- 예상도착 최댓값 HH24:MI:SS (95th, deadline)
    CHECK_TIME      VARCHAR2(8),               -- 알람발생시각 HH24:MI:SS
    DELAY_MIN       NUMBER(5),                 -- deadline 초과 분수
    ANOMALY_SCORE   NUMBER(10,4),              -- Isolation Forest score (음수일수록 이상)
    ALARM_MSG       VARCHAR2(2000),            -- EXAONE 생성 한국어 알람 메시지
    TGT_FILE_PATH   VARCHAR2(500),             -- 알람 txt 파일 경로
    SEND_STS        VARCHAR2(1),               -- 전송상태 0=대기 1=완료 9=실패
    SEND_DT         DATE,                      -- 슬랙 전송완료일시
    ERR_MSG         VARCHAR2(1000),            -- 전송실패 에러메시지
    REGR_ID         VARCHAR2(8),               -- 등록자ID
    REG_DT          DATE,                      -- 등록일시
    UPDR_ID         VARCHAR2(8),               -- 변경자ID
    UPD_DT          DATE                       -- 변경일시
);

ALTER TABLE BAT_ALARM_HIS
    ADD CONSTRAINT PK_BAT_ALARM_HIS
    PRIMARY KEY (MBRSH_PGM_ID, PROC_DY, FILE_ID, FILE_NM, ALARM_ID);
```
- ALARM_ID 채번: SEQ_BAT_ALARM_HIS 시퀀스 사용
- ALARM_TYPE 흐름: M=미수신(deadline 초과), V=건수이상(TOT_REC_CNT Z-score 초과)
  - M 알람: EXP_MIN/MED/MAX_TIME, DELAY_MIN, ANOMALY_SCORE 모두 기록
  - V 알람: ANOMALY_SCORE에 Z-score 저장, EXP_*·DELAY_MIN은 NULL
- SEND_STS 흐름: 0(대기) → 1(완료) or 9(실패)
- 인덱스: ALARM_DT / (FILE_ID, ALARM_DT) / (SEND_STS, ALARM_DT)

### BAT_FILE_FREQ_MST (신규 - 배치파일별 유입 주기 프로필)
```sql
CREATE TABLE BAT_FILE_FREQ_MST (
    FILE_ID             VARCHAR2(10)    NOT NULL,   -- COM_BATFILE_TRN.FILE_ID

    -- [MAIN] trainer 분석 결과 (180일)
    MAIN_FREQ_TYPE      VARCHAR2(40),               -- DAILY/WEEKLY/MONTHLY/IRREGULAR/EVERY_N_DAYS
    MAIN_MEDIAN_GAP     NUMBER(12,4),               -- 연속 수신일 간격 중앙값(일)
    MAIN_STD_GAP        NUMBER(12,4),               -- 연속 수신일 간격 표준편차(일)
    MAIN_ROUND_GAP      NUMBER(10,0),               -- ROUND(MAIN_MEDIAN_GAP). 분류 기준값
    MAIN_SAMPLE_CNT     NUMBER(10),                 -- 분석에 사용된 수신 건수
    MAIN_WIN_DAYS       NUMBER(10)  DEFAULT 180,    -- 분석 윈도우 (고정 180)
    MAIN_ANALYSIS_ST    DATE,                       -- 분석 데이터 최솟값(REG_DT)
    MAIN_ANALYSIS_ED    DATE,                       -- 분석 데이터 최댓값(REG_DT)
    MAIN_UPD_DT         DATE,                       -- trainer 마지막 갱신 일시
    MAIN_REGR_ID        VARCHAR2(8),                -- trainer 등록자 ID

    -- [FB] detector fallback 분석 결과 (90일)
    FB_FREQ_TYPE        VARCHAR2(40),
    FB_MEDIAN_GAP       NUMBER(12,4),
    FB_STD_GAP          NUMBER(12,4),
    FB_ROUND_GAP        NUMBER(10,0),
    FB_SAMPLE_CNT       NUMBER(10),
    FB_WIN_DAYS         NUMBER(10)  DEFAULT 90,     -- 분석 윈도우 (고정 90)
    FB_ANALYSIS_ST      DATE,
    FB_ANALYSIS_ED      DATE,
    FB_UPD_DT           DATE,                       -- detector 마지막 fallback 일시
    FB_REGR_ID          VARCHAR2(8),

    -- [DOM_PATTERN] 월중 수신일 패턴 (EVERY_N_DAYS / MONTHLY 파일만)
    MAIN_DOM_PATTERN    VARCHAR2(200),              -- trainer 탐지. 예: "5,15" (매월 5일·15일)
    FB_DOM_PATTERN      VARCHAR2(200),              -- detector fallback 탐지

    -- [EFFECTIVE] 알람·메시지에 사용 중인 출처
    EFFECTIVE_SRC       CHAR(1),                    -- T=trainer(MAIN_*), D=detector(FB_*)
    EFFECTIVE_UPD_DT    DATE,

    -- 공통 감사 컬럼
    REGR_ID             VARCHAR2(8),
    REG_DT              DATE DEFAULT SYSDATE    NOT NULL,
    UPDR_ID             VARCHAR2(8),
    UPD_DT              DATE DEFAULT SYSDATE    NOT NULL,

    CONSTRAINT PK_BAT_FILE_FREQ_MST PRIMARY KEY (FILE_ID),
    CONSTRAINT CK_FREQ_MST_EFF_SRC
        CHECK (EFFECTIVE_SRC IS NULL OR EFFECTIVE_SRC IN ('T', 'D'))
);
```
- MAIN_* : trainer가 매주 일요일 학습 후 갱신 (180일 분석). EFFECTIVE_SRC='T'로 고정
- FB_* : detector가 BAT_FILE_FREQ_MST 미등록 FILE_ID에 한해 임시 기록 (90일 분석). EFFECTIVE_SRC='D'
- MAIN_FREQ_TYPE IS NOT NULL이면 항상 T 우선. trainer 실행 후에도 FB_* 컬럼은 보존
- EFFECTIVE_SRC 결정: MAIN_FREQ_TYPE IS NOT NULL → 'T', NULL → 'D'
- DOM_PATTERN: EVERY_N_DAYS / MONTHLY 파일에서만 탐지·저장. DAILY·WEEKLY·IRREGULAR는 NULL
  - detector의 도착 window 필터를 ±tolerance 근사값 대신 anchor day 기반 정확 필터로 개선

## 핵심 로직

### 주기 분류 — freq_utils.classify_frequency()
```python
# biz_days(ICS_WRKDAY_MST 영업일 set) 전달 시 BUSINESS_DAY를 먼저 판별
# 조건: ① 모든 도착일이 영업일 ② 영업일 순서 기준 연속 간격이 모두 1
if biz_days and all_on_biz and all(biz_gap == 1):  → BUSINESS_DAY

# 달력 간격 기준 분류 (BUSINESS_DAY 조건 불충족 시)
gap = round(median_gap)
if gap == 0:                    → IRREGULAR
elif std_gap > median_gap * 0.5:→ IRREGULAR  # 알람 제외 대상
elif gap == 1:                  → DAILY
elif gap in (6, 7, 8):          → WEEKLY
elif 25 <= gap <= 35:           → MONTHLY
else:                           → EVERY_{gap}_DAYS
```
- trainer와 detector 양쪽에서 공통 사용 (freq_utils.py)
- detector는 BAT_FILE_FREQ_MST 미등록 시에만 호출
- biz_days: ICS_WRKDAY_MST WORK_YN='Y' (MBRSH_PGM_ID='A') 날짜 set, run 시작 시 1회 로드

### 월중 수신일 패턴 탐지 — freq_utils.detect_dom_pattern()
```python
# EVERY_N_DAYS / MONTHLY 파일에서만 동작
# DAILY·WEEKLY·BUSINESS_DAY·IRREGULAR는 None 반환
# 1. 전체 수신 레코드의 day_of_month 빈도 집계
# 2. round_gap // 2 를 최소 간격으로 탐욕적 클러스터링
# 3. 각 클러스터의 최빈 day를 anchor로 선택
# 4. 기대 클러스터 수(=30 // round_gap) 초과 시 빈도 낮은 클러스터 제거
# 반환: "5,15" 형태 문자열 (없으면 None)
```
- trainer와 detector 양쪽에서 공통 사용
- 탐지 결과는 BAT_FILE_FREQ_MST MAIN_DOM_PATTERN / FB_DOM_PATTERN에 저장
- detector의 calc_arrival_window에서 anchor day 기반 정확 필터로 활용

### BAT_FILE_FREQ_MST 갱신 흐름
```
trainer (매주)  → MAIN_* UPSERT + EFFECTIVE_SRC='T'
detector (10분) → run 시작 시 전체 로드(1회 쿼리)
                → 미등록 FILE_ID만 FB_* UPSERT + EFFECTIVE_SRC='D'
                → MAIN_FREQ_TYPE IS NOT NULL이면 FB UPDATE 건너뜀
```

### 컨텍스트 필터 — filter_by_context() (공통)
도착 window 계산(M 알람)과 건수 Z-score 탐지(V 알람)가 동일한 필터 로직을 공유합니다.

| FREQ_TYPE | 필터 기준 |
|---|---|
| DAILY / WEEKLY / BUSINESS_DAY | 요일 + 월말여부(day≥25) |
| MONTHLY / EVERY_N_DAYS | day_of_month ± max(2, round_gap//5)일, DOM_PATTERN anchor 기반 |

BUSINESS_DAY 파일은 detector에서 오늘이 비영업일(ICS_WRKDAY_MST WORK_YN='N')이면 알람 처리 전 SKIP합니다.

### 도착 window 계산 (M 알람용)
- 위 컨텍스트 필터 적용 후 도착 시각(arrival_sec) percentile 계산
- sample_cnt < 3 이면 "오늘 수신 예정일 아님"으로 판단하여 M 알람 제외
- 5th percentile  → EXP_MIN_TIME
- 50th percentile → EXP_MED_TIME
- 95th percentile → EXP_MAX_TIME (deadline 기준)
- window는 매 run마다 90일 이력으로 재계산 (컨텍스트 의존적이라 캐싱 불가)

### 알람 발동 조건

**M 알람 (미수신)** — 모두 만족해야 발동
1. BAT_MNTLST_EXC USE_YN='Y' 가 아닐 것
2. FREQUENCY_TYPE이 IRREGULAR가 아닐 것
3. 오늘 해당 FILE_ID가 수신되지 않았을 것
4. 오늘 이미 M 알람이 없을 것 (중복 방지)
5. 현재 시각이 EXP_MAX_TIME(95th)을 초과했을 것

**V 알람 (건수 이상)** — 모두 만족해야 발동
1. BAT_MNTLST_EXC USE_YN='Y' 가 아닐 것
2. FREQUENCY_TYPE이 IRREGULAR가 아닐 것
3. 오늘 해당 FILE_ID가 수신됐을 것
4. 오늘 M 알람이 없을 것 (M 발생 시 V 억제)
5. 오늘 이미 V 알람이 없을 것 (중복 방지)
6. TOT_REC_CNT Z-score > VOLUME_ZSCORE_THRESHOLD (기본 3.0)
   - Z-score = |금일건수 - 역사적중앙값| / 역사적표준편차 (동일 컨텍스트 필터 적용, 오늘 제외)
   - hist_std == 0 이면: 금일건수 ≠ 중앙값일 때 z_score = 99.0으로 처리

### Isolation Forest 피처
```python
features = [
    arrival_sec,    # REG_DT 기준 하루 중 도착 시각(초)
    tot_rec_cnt,    # TOT_REC_CNT
    send_rec_cnt,   # SEND_REC_CNT
    weekday,        # 요일 (0=월 ~ 6=일)
    is_month_end,   # 월말여부 (day >= 25 이면 1)
    day_of_month,   # REG_DT 기준 일(1~31) — EVERY_N_DAYS/MONTHLY 파일의 날짜 패턴 인식
]
```
- trainer와 detector 양쪽에서 동일한 6개 피처 사용 (일관성 필수)
- 기존 5개 피처로 학습된 .pkl 파일은 배포 후 첫 trainer 실행 전까지 오류 → anomaly score -0.5 fallback 처리됨

## LLM 설정
- 모델: EXAONE 2.4B (Ollama 로컬)
- 엔드포인트: http://localhost:11434/api/generate
- USE_LLM=0 으로 비활성화 가능 (비활성화 시 fallback 메시지 자동 사용)
- LLM 실패 시에도 fallback 메시지로 알람 반드시 발송

### llm.py 함수 구성
- `generate()` — detector 용: 미수신 감지 시 한국어 알람 메시지 생성, BAT_ALARM_HIS.ALARM_MSG에 저장
- `generate_sender()` — sender 용: DB 저장 원문을 슬랙 전송용으로 재작성

### detector 프롬프트 입력값
```
파일ID, 수신 주기(FREQ_TYPE), 예상 도착 window(min/med/max),
현재 시각, 지연 분수, anomaly score, 월말여부
→ 한국어 알람 메시지 생성 (마지막 문장: "즉시 확인이 필요합니다.")
```

## 슬랙 알람
- 명령어: sh {SLACK_SCRIPT} {SLACK_CHANNEL} {txt파일경로}
- 파일 생성 주체: sender.py
- DB 원문: {ALARM_DIR}/ALARM_{FILE_ID}_{YYYYMMDD_HHMMSS}_src.txt (항상 생성)
- LLM 재작성: {ALARM_DIR_LLM}/ALARM_{FILE_ID}_{YYYYMMDD_HHMMSS}_llm.txt (LLM 성공 시)
- 전송 파일: llm 성공 시 _llm.txt, 실패 시 _src.txt
- detector 비교용 fallback: {ALARM_DIR}/fallback/ALARM_{FILE_ID}_{YYYYMMDD_HHMMSS}.txt

## 설치된 패키지 (추가 설치 불필요)
- Python 3.9.18
- oracledb 3.3
- pandas
- numpy
- scikit-learn
- joblib
- requests

## 반입 필요 파일 (망분리 환경)
- ollama-linux-amd64 (~50MB)
- EXAONE-3.5-2.4B-Instruct-Q4_K_M.gguf (~1.6GB)

## 파일/디렉토리 구조
```
{설치경로}/
├── .env                  ← 민감 정보 (git 제외)
├── .env.docker           ← Docker 환경용 .env
├── .env.example          ← 환경변수 템플릿
├── .dockerignore
├── .gitignore
├── requirements.txt
├── CLAUDE.md
├── README.md
├── docker_run.txt        ← docker 실행 명령어 메모
├── src/                  ← Python 소스 전체
│   ├── config.py         ← DB접속정보, 경로, 설정값 (.env에서 로드)
│   ├── freq_utils.py     ← 공통 주기 분류 유틸 (classify_frequency, detect_dom_pattern, sec_to_hms)
│   ├── detector.py       ← 감지 프로세스
│   ├── sender.py         ← 전송 프로세스
│   ├── trainer.py        ← 모델 재학습 + 주기 프로필 갱신
│   ├── recommender.py    ← 모니터링 제외 파일 자동 추천 (USE_YN='P')
│   ├── llm.py            ← Ollama EXAONE 메시지 생성 (generate / generate_sender)
│   ├── log_utils.py      ← 공통 로그 설정 (날짜별 파일)
│   ├── test_db.py        ← DB 연동 테스트 스크립트
│   └── sql/
│       ├── __init__.py
│       ├── detector_sql.py     ← GET_EXCLUDED_FILE_IDS, GET_HISTORICAL_DATA,
│       │                          HAS_ALARM_TODAY, INSERT_ALARM,
│       │                          GET_FREQ_MST, UPSERT_FREQ_MST_FB
│       ├── sender_sql.py       ← GET_PENDING_ALARMS, UPDATE_SUCCESS, UPDATE_FAILURE
│       ├── trainer_sql.py      ← GET_EXCLUDED_FILE_IDS, GET_TRAINING_DATA, UPSERT_FREQ_MST
│       └── recommender_sql.py  ← GET_MANAGED_FILE_IDS, GET_ANALYSIS_DATA,
│                                  INSERT_RECOMMENDATION
├── docker/               ← Docker 관련 파일
│   ├── Dockerfile
│   └── docker-compose.yml
├── table_sql/            ← DB DDL (테이블·시퀀스 생성 스크립트)
│   ├── bat_alarm_his.sql
│   ├── bat_file_freq_mst.sql
│   ├── bat_mntlsth_exc.sql
│   ├── seq_bat_alarm_his.sql
│   └── test_data_insert.sql  ← 테스트 데이터 INSERT
└── sql/                  ← DB 변경 DDL
    ├── alter_bat_alarm_his.sql
    ├── alter_bat_alarm_his_alarm_type.sql  ← ALARM_TYPE 컬럼 추가 (M/V 구분)
    └── alter_bat_file_freq_mst.sql         ← MAIN_DOM_PATTERN, FB_DOM_PATTERN 컬럼 추가

{MODEL_DIR}/              ← 기본: {BASE_DATA_DIR}/models
├── {FILE_ID}_iso.pkl
└── {FILE_ID}_scaler.pkl

{ALARM_DIR}/              ← 기본: {BASE_DATA_DIR}/batch_alarms
├── ALARM_{FILE_ID}_{YYYYMMDD_HHMMSS}_src.txt  ← DB 원문 (sender.py, 항상 생성)
├── fallback/
│   └── ALARM_{FILE_ID}_{YYYYMMDD_HHMMSS}.txt  ← detector fallback 템플릿 비교용
└── llm/
    └── ALARM_{FILE_ID}_{YYYYMMDD_HHMMSS}_llm.txt  ← sender LLM 재작성 슬랙 전송본
```

## 시퀀스
```sql
CREATE SEQUENCE SEQ_BAT_ALARM_HIS
START WITH 1
INCREMENT BY 1
NOCACHE
NOCYCLE;
```
