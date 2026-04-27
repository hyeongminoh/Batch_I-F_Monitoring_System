# 배치 파일 모니터링 시스템

## 프로젝트 개요
망분리 온프레미스 리눅스 서버에서 동작하는 배치 파일 미수신 탐지 및 슬랙 알람 시스템.
외부 인터넷 통신 없이 완전 로컬에서 동작한다.

## 아키텍처
2개의 독립 프로세스 + 1개의 별도 학습 프로세스로 구성

### detector.py (cron 10분마다)
1. COM_BATFILE_TRN 조회 (과거 90일 이력)
2. BAT_MNTLST_EXC에서 제외 FILE_ID 필터링 (USE_YN='Y')
3. BAT_FILE_FREQ_MST 전체 로드 (run 시작 시 1회)
4. FILE_ID별 수신 주기 결정
   - BAT_FILE_FREQ_MST에 있으면 저장된 FREQ_TYPE 사용 (T=trainer 우선, D=detector fallback)
   - 미등록 FILE_ID는 90일 이력으로 직접 계산 → BAT_FILE_FREQ_MST에 FB(D)로 기록
5. 미도착 파일 감지 (95th percentile deadline 초과 체크)
6. Isolation Forest로 anomaly score 계산 (.pkl 모델 로드)
7. Ollama EXAONE 2.4B로 한국어 알람 메시지 생성
8. BAT_ALARM_HIS INSERT (SEND_STS='0')

### sender.py (cron 5분마다)
1. BAT_ALARM_HIS에서 SEND_STS='0' 조회
2. txt 파일 생성 ({ALARM_DIR}/ALARM_{FILE_ID}_{YYYYMMDD_HHMMSS}.txt)
3. mon_slack.sh 실행
4. 성공 → SEND_STS='1', SEND_DT 기록
5. 실패 → SEND_STS='9', ERR_MSG 기록

### trainer.py (cron 매주 일요일 02:00)
1. 과거 180일 데이터로 FILE_ID별 Isolation Forest 재학습
2. BAT_MNTLST_EXC USE_YN='Y' 파일은 학습에서도 제외
3. {MODEL_DIR}/{FILE_ID}_iso.pkl 저장
4. {MODEL_DIR}/{FILE_ID}_scaler.pkl 저장
5. 학습 완료 후 수신 주기 분류 → BAT_FILE_FREQ_MST MAIN(T) 컬럼 UPSERT

## cron 설정
```
*/10 * * * * python3 {설치경로}/detector.py >> /var/log/detector.log 2>&1
*/5  * * * * python3 {설치경로}/sender.py   >> /var/log/sender.log  2>&1
0 2  * * 0   python3 {설치경로}/trainer.py  >> /var/log/trainer.log 2>&1
```

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
    USE_YN          CHAR(1),                   -- 제외여부 Y=제외중 N=해제
    REGR_ID         VARCHAR2(8),               -- 등록자ID
    REG_DT          DATE,                      -- 등록일시
    UPDR_ID         VARCHAR2(8),               -- 변경자ID
    UPD_DT          DATE                       -- 변경일시
);
```
- USE_YN='Y' 인 FILE_ID는 감지 및 학습 대상에서 제외
- 삭제 없이 USE_YN으로 ON/OFF 관리 (이력 보존)

### BAT_ALARM_HIS (신규 - 알람 이력 및 중복 방지)
```sql
CREATE TABLE BAT_ALARM_HIS (
    MBRSH_PGM_ID    VARCHAR2(1)     NOT NULL,  -- 멤버쉽프로그램ID
    FILE_ID         VARCHAR2(10)    NOT NULL,  -- 파일ID
    ALARM_ID        NUMBER          NOT NULL,  -- 알람ID (SEQ_BAT_ALARM_HIS.NEXTVAL)
    ALARM_DT        DATE,                      -- 알람발생일시
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
```
- ALARM_ID 채번: SEQ_BAT_ALARM_HIS 시퀀스 사용
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

## 핵심 로직

### 주기 분류 — freq_utils.classify_frequency()
```python
# file_df['arrival_date'].unique() 기준 날짜 간격 계산
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

### BAT_FILE_FREQ_MST 갱신 흐름
```
trainer (매주)  → MAIN_* UPSERT + EFFECTIVE_SRC='T'
detector (10분) → run 시작 시 전체 로드(1회 쿼리)
                → 미등록 FILE_ID만 FB_* UPSERT + EFFECTIVE_SRC='D'
                → MAIN_FREQ_TYPE IS NOT NULL이면 FB UPDATE 건너뜀
```

### 도착 window 계산
- 컨텍스트 필터: 같은 요일 + 월말여부(day >= 25) 동일한 날만 사용
- 5th percentile  → EXP_MIN_TIME
- 50th percentile → EXP_MED_TIME
- 95th percentile → EXP_MAX_TIME (deadline 기준)
- sample_cnt < 3 이면 알람 제외 (오탐 방지)
- window는 매 run마다 90일 이력으로 재계산 (컨텍스트 의존적이라 캐싱 불가)

### 알람 발동 조건 (모두 만족해야 알람)
1. 오늘 도착한 FILE_ID가 아닐 것
2. BAT_MNTLST_EXC USE_YN='Y' 가 아닐 것
3. 오늘 이미 BAT_ALARM_HIS에 적재된 FILE_ID가 아닐 것
4. FREQUENCY_TYPE이 IRREGULAR가 아닐 것
5. 현재 시각이 EXP_MAX_TIME(95th)을 초과했을 것

### Isolation Forest 피처
```python
features = [
    arrival_sec,    # REG_DT 기준 하루 중 도착 시각(초)
    tot_rec_cnt,    # TOT_REC_CNT
    send_rec_cnt,   # SEND_REC_CNT
    weekday,        # 요일 (0=월 ~ 6=일)
    is_month_end    # 월말여부 (day >= 25 이면 1)
]
```

## LLM 설정
- 모델: EXAONE 2.4B (Ollama 로컬)
- 엔드포인트: http://localhost:11434/api/generate
- 언어: 한국어
- LLM 실패 시 fallback 템플릿 메시지 자동 사용 (알람은 반드시 발송)

### 한국어 프롬프트 구조
```
배치 파일명, 수신 주기(BAT_FILE_FREQ_MST 참조), 예상 도착 window,
현재 시각, 지연 분수, anomaly score, 월말여부 전달
→ 3~4문장 한국어 알람 메시지 생성
→ 마지막 문장: "즉시 확인이 필요합니다."
```

## 슬랙 알람
- 명령어: sh {SLACK_SCRIPT} {SLACK_CHANNEL} {txt파일경로}
- txt 저장 경로: {ALARM_DIR}
- 파일명 형식: ALARM_{FILE_ID}_{YYYYMMDD_HHMMSS}.txt
- 메시지 비교용 사본: {ALARM_DIR}/fallback/ (템플릿), {ALARM_DIR}/llm/ (EXAONE)

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
├── .env            ← 민감 정보 (git 제외)
├── config.py       ← DB접속정보, 경로, 설정값 (.env에서 로드)
├── freq_utils.py   ← 공통 주기 분류 유틸 (classify_frequency, sec_to_hms)
├── detector.py     ← 감지 프로세스
├── sender.py       ← 전송 프로세스
├── trainer.py      ← 모델 재학습 + 주기 프로필 갱신
├── llm.py          ← Ollama EXAONE 메시지 생성
├── log_utils.py    ← 공통 로그 설정
└── sql/
    ├── detector_sql.py  ← GET_HISTORICAL_DATA, HAS_ALARM_TODAY, INSERT_ALARM,
    │                       GET_FREQ_MST, UPSERT_FREQ_MST_FB
    ├── sender_sql.py    ← sender 전용 SQL
    └── trainer_sql.py   ← GET_TRAINING_DATA, UPSERT_FREQ_MST

{MODEL_DIR}/
├── {FILE_ID}_iso.pkl
└── {FILE_ID}_scaler.pkl

{ALARM_DIR}/
├── ALARM_{FILE_ID}_{YYYYMMDD_HHMMSS}.txt  ← sender.py가 생성, 슬랙 전송용
├── fallback/
│   └── ALARM_{FILE_ID}_{YYYYMMDD_HHMMSS}.txt  ← 템플릿 메시지 비교용
└── llm/
    └── ALARM_{FILE_ID}_{YYYYMMDD_HHMMSS}.txt  ← LLM 메시지 비교용
```

## 시퀀스
```sql
CREATE SEQUENCE SEQ_BAT_ALARM_HIS
START WITH 1
INCREMENT BY 1
NOCACHE
NOCYCLE;
```
