# Batch I/F Monitoring System

> 망분리 온프레미스 리눅스 서버에서 동작하는 **배치 파일 미수신 탐지 및 슬랙 알람 시스템**  
> 외부 인터넷 통신 없이 완전 로컬에서 동작합니다.

![Python](https://img.shields.io/badge/Python-3.9.18-blue?logo=python)
![Oracle](https://img.shields.io/badge/Oracle-DB-red?logo=oracle)
![Scikit-learn](https://img.shields.io/badge/Scikit--learn-IsolationForest-orange?logo=scikit-learn)
![Ollama](https://img.shields.io/badge/Ollama-EXAONE_2.4B-black)

---

## 개요

배치 파일이 정해진 시간 안에 수신되지 않을 경우, 이를 자동으로 감지하고 슬랙으로 알람을 발송하는 시스템입니다.

- 과거 수신 이력을 분석해 **파일별 예상 도착 시간대(window)** 를 동적으로 계산
- **BAT_FILE_FREQ_MST** 에 파일별 수신 주기 프로필을 저장 · 재사용 (trainer 180일 / detector fallback 90일)
- **Isolation Forest** 로 이상 수신 패턴 탐지
- **Ollama EXAONE 2.4B** (로컬 LLM)으로 한국어 알람 메시지 자동 생성
- LLM 장애 시에도 fallback 메시지로 **알람은 반드시 발송**

---

## 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│                   COM_BATFILE_TRN (Oracle)                   │
│              배치파일 수신 트랜잭션 이력 테이블               │
└──────────────────────────┬──────────────────────────────────┘
                           │ 조회 (90일 이력)
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                      detector.py                             │  ← cron 10분마다
│                                                             │
│  0. BAT_FILE_FREQ_MST 전체 로드 (run 시작 1회)              │
│  1. 제외 FILE_ID 필터링 (BAT_MNTLST_EXC)                   │
│  2. 수신 주기 결정                                          │
│     ├─ MST에 있음 → 저장된 FREQ_TYPE 사용 (T 우선 > D)      │
│     └─ MST에 없음 → 직접 계산 → MST에 FB(D)로 기록         │
│  3. 도착 window 계산 (5/50/95th percentile)                 │
│  4. deadline(95th) 초과 감지                                │
│  5. Isolation Forest anomaly score                          │
│  6. fallback / LLM 메시지 생성 (비교 저장)                  │
│  7. BAT_ALARM_HIS INSERT (SEND_STS='0')                     │
└───────────────────────────┬─────────────────────────────────┘
                            │ SEND_STS='0' (대기)
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                       sender.py                              │  ← cron 5분마다
│                                                             │
│  1. 대기 알람 조회 (BAT_ALARM_HIS SEND_STS='0')             │
│  2. ALARM_*.txt 파일 생성                                   │
│  3. mon_slack.sh 실행                                       │
│  4. SEND_STS 업데이트 (1=완료 / 9=실패)                     │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                       trainer.py                             │  ← cron 매주 일요일 02:00
│                                                             │
│  180일 데이터로 FILE_ID별 재학습                            │
│  → {FILE_ID}_iso.pkl / {FILE_ID}_scaler.pkl 저장            │
│  → 수신 주기 분류 → BAT_FILE_FREQ_MST MAIN(T) UPSERT       │
└─────────────────────────────────────────────────────────────┘
```

---

## 핵심 로직

### 수신 주기 분류 — `freq_utils.classify_frequency()`

과거 수신 날짜 간격의 중앙값(median_gap)으로 주기를 자동 분류합니다.  
trainer와 detector가 `freq_utils.py` 를 공통으로 import해 사용합니다.

| 조건 | 분류 |
|---|---|
| gap == 0 | `IRREGULAR` |
| std_gap > median_gap × 0.5 | `IRREGULAR` ← 알람 제외 |
| median_gap == 1일 | `DAILY` |
| median_gap == 6~8일 | `WEEKLY` |
| median_gap == 25~35일 | `MONTHLY` |
| 그 외 | `EVERY_{n}_DAYS` |

### BAT_FILE_FREQ_MST 주기 프로필 갱신 흐름

```
trainer (매주 일요일)
  └─ 학습 완료 → MAIN_* UPSERT, EFFECTIVE_SRC='T'

detector (10분마다, run 시작 시 1회 로드)
  ├─ MST에 있음 → 저장된 FREQ_TYPE 사용
  └─ MST에 없음 → 직접 계산 → FB_* UPSERT, EFFECTIVE_SRC='D'
                  (MAIN_FREQ_TYPE IS NOT NULL이면 FB UPDATE 건너뜀)
```

- `EFFECTIVE_SRC='T'` (trainer, 180일)가 `'D'` (detector, 90일)보다 항상 우선
- trainer 실행 후에도 FB_* 컬럼은 보존되어 두 출처 결과가 공존

### 도착 window 계산

단순 평균이 아닌 **컨텍스트 필터**를 적용해 정확도를 높입니다.

```
필터 조건: 같은 요일 + 월말여부(day ≥ 25) 동일한 날만 사용
  → 5th  percentile = EXP_MIN_TIME  (최솟값)
  → 50th percentile = EXP_MED_TIME  (중앙값)
  → 95th percentile = EXP_MAX_TIME  (deadline 기준)

샘플 수 < 3이면 오탐 방지를 위해 알람 제외
window는 매 run마다 90일 이력으로 재계산 (요일·월말 컨텍스트 의존)
```

### 알람 발동 조건 (모두 만족해야 알람)

```
✅ 오늘 해당 FILE_ID가 수신되지 않았을 것
✅ 모니터링 제외 목록(BAT_MNTLST_EXC)에 없을 것
✅ 오늘 이미 발송된 알람이 없을 것 (중복 방지)
✅ 수신 주기가 IRREGULAR가 아닐 것
✅ 현재 시각 > EXP_MAX_TIME (95th percentile 초과)
```

### Isolation Forest 피처

```python
features = [
    arrival_sec,    # 하루 중 도착 시각 (초 단위)
    tot_rec_cnt,    # 전체 레코드 수
    send_rec_cnt,   # 전송 레코드 수
    weekday,        # 요일 (0=월 ~ 6=일)
    is_month_end    # 월말 여부 (day ≥ 25 이면 1)
]
```

### 알람 메시지 생성 (`llm.py`)

detector.py는 알람 발동 시 항상 두 가지 메시지를 모두 생성합니다.

```
1. fallback 템플릿 메시지  → batch_alarms/fallback/ 저장
2. LLM(EXAONE) 메시지     → batch_alarms/llm/ 저장 (Ollama 연결 시)

최종 슬랙 전송 메시지: LLM 성공 시 LLM 메시지, 실패 시 fallback
```

**fallback 메시지 형식:**
```
[배치 미수신 알람] EB140402
마감: 09:07:36 / 지연: 134분 / 주기: DAILY
즉시 확인이 필요합니다.
```

---

## DB 테이블

| 테이블 | 유형 | 설명 |
|---|---|---|
| `COM_BATFILE_TRN` | 기존 | 배치파일 수신 트랜잭션 이력 |
| `BAT_MNTLST_EXC` | 신규 | 모니터링 제외 파일 관리 |
| `BAT_ALARM_HIS` | 신규 | 알람 이력 및 전송 상태 관리 |
| `BAT_FILE_FREQ_MST` | 신규 | 파일별 수신 주기 프로필 (trainer/detector 공유) |

```sql
-- 알람 ID 채번 시퀀스
CREATE SEQUENCE SEQ_BAT_ALARM_HIS
START WITH 1 INCREMENT BY 1 NOCACHE NOCYCLE;
```

### BAT_FILE_FREQ_MST 컬럼 그룹

| 컬럼 그룹 | 갱신 주체 | 분석 윈도우 | EFFECTIVE_SRC |
|---|---|---|---|
| `MAIN_*` | trainer | 180일 | `'T'` |
| `FB_*` | detector (미등록 FILE_ID 한정) | 90일 | `'D'` |
| `EFFECTIVE_SRC` | 애플리케이션 | — | T 우선, 없으면 D |

### SEND_STS 흐름

```
INSERT → '0' (대기)  →  '1' (전송 완료)
                     →  '9' (전송 실패, ERR_MSG 기록)
```

---

## 파일 구조

```
{설치경로}/
├── .env                     ← 민감 정보 (git 제외)
├── .env.example             ← 환경변수 템플릿
├── .dockerignore
├── .gitignore
├── requirements.txt         ← 의존 패키지 목록
├── CLAUDE.md
├── README.md
├── src/                     ← Python 소스 전체
│   ├── config.py            ← .env 로드 및 설정값 관리
│   ├── freq_utils.py        ← 공통 주기 분류 유틸 (classify_frequency, sec_to_hms)
│   ├── detector.py          ← 미수신 감지 프로세스
│   ├── sender.py            ← 슬랙 전송 프로세스
│   ├── trainer.py           ← 모델 재학습 + BAT_FILE_FREQ_MST MAIN 갱신
│   ├── llm.py               ← Ollama EXAONE 메시지 생성 (detector에서 호출)
│   ├── log_utils.py         ← 공통 로그 설정 (날짜별 파일, 포맷 정의)
│   ├── test_db.py           ← DB 연동 테스트 스크립트
│   └── sql/
│       ├── detector_sql.py  ← GET_HISTORICAL_DATA, HAS_ALARM_TODAY, INSERT_ALARM,
│       │                       GET_FREQ_MST, UPSERT_FREQ_MST_FB
│       ├── sender_sql.py    ← sender 전용 SQL
│       └── trainer_sql.py   ← GET_TRAINING_DATA, UPSERT_FREQ_MST
├── docker/                  ← Docker 관련 파일
│   ├── Dockerfile
│   └── docker-compose.yml
└── sql/                     ← DB DDL
    └── bat_file_freq_mst.sql

{MODEL_DIR}/              ← 기본: {BASE_DATA_DIR}/models
├── {FILE_ID}_iso.pkl
└── {FILE_ID}_scaler.pkl

{ALARM_DIR}/              ← 기본: {BASE_DATA_DIR}/batch_alarms
├── ALARM_{FILE_ID}_{YYYYMMDD_HHMMSS}.txt   ← 슬랙 전송용 (sender.py 생성)
├── fallback/
│   └── ALARM_{FILE_ID}_{YYYYMMDD_HHMMSS}.txt  ← 템플릿 메시지 비교용
└── llm/
    └── ALARM_{FILE_ID}_{YYYYMMDD_HHMMSS}.txt  ← LLM 메시지 비교용

{LOG_DIR}/                ← 기본: {BASE_DATA_DIR}/logs
├── detector_YYYYMMDD.log
├── sender_YYYYMMDD.log
├── trainer_YYYYMMDD.log
└── test_db_YYYYMMDD.log
```

---

## 로그

### 포맷

```
[20260422][164950] [INFO    ] detector.py:287 | DB 연결 성공
[20260422][164950] [WARNING ] detector.py:183 | [1500BIL906] 모델 파일 없음 → 기본값 -0.5 사용
```

- `[YYYYMMDD][HHMMSS]` — 실행 일시
- `[LEVELNAME]` — INFO / WARNING / ERROR / DEBUG
- `filename:lineno` — 소스 위치 (디버깅용)

### detector.log 실행 구분

cron으로 10분마다 실행되므로 각 실행 단위를 구분합니다.

```
▼▼▼ (60자) ▼▼▼
  [RUN START] detector.py  2026-04-22 16:49:49
▼▼▼▼▼▼▼▼▼▼▼▼▼▼
  BAT_FILE_FREQ_MST 로드: 42건 (T=38, D=4)
  ... 처리 내용 ...
▲▲▲ (60자) ▲▲▲
  [RUN END  ] detector.py  2026-04-22 16:49:52  |  알람 1건  |  소요 3초
▲▲▲▲▲▲▲▲▲▲▲▲▲▲
```

실행 이력 빠른 확인:
```bash
grep "RUN\|FREQ_MST" logs/detector_20260422.log
```

---

## 설치 및 설정

### 1. 패키지 설치

```bash
pip install -r requirements.txt
```

### 2. LLM 설치 (망분리 환경은 파일 반입 필요)

```bash
# 인터넷 환경
curl -fsSL https://ollama.ai/install.sh | sh
ollama pull exaone3.5:2.4b

# 망분리 환경 (반입 필요 파일)
# - ollama-linux-amd64 (~50MB)
# - EXAONE-3.5-2.4B-Instruct-Q4_K_M.gguf (~1.6GB)
chmod +x ollama-linux-amd64
./ollama-linux-amd64 serve &
ollama create exaone3.5:2.4b -f Modelfile
```

### 3. 환경 설정

```bash
vi .env
```

```dotenv
# Oracle DB
DB_USER=your_user
DB_PASSWORD=your_password
DB_HOST=your_host
DB_PORT=1521
DB_SID=your_sid
MBRSH_PGM_ID=A

# 슬랙
SLACK_CHANNEL=your_slack_channel
SLACK_SCRIPT=/path/to/mon_slack.sh

# 경로 (절대경로 권장)
BASE_DATA_DIR=/data/batch_monitoring_system
ALARM_DIR=/data/batch_monitoring_system/batch_alarms
MODEL_DIR=/data/batch_monitoring_system/models
LOG_DIR=/data/batch_monitoring_system/logs
```

### 4. DB 연동 확인

```bash
python3 test_db.py
# → logs/test_db_YYYYMMDD.log 에서 결과 확인
```

### 5. 최초 모델 학습 및 주기 프로필 적재

```bash
python3 trainer.py
# → {MODEL_DIR}/{FILE_ID}_iso.pkl, {FILE_ID}_scaler.pkl 생성
# → BAT_FILE_FREQ_MST에 전체 FILE_ID 주기 프로필 적재 (EFFECTIVE_SRC='T')
```

### 6. cron 등록

```cron
*/10 * * * * python3 {설치경로}/src/detector.py
*/5  * * * * python3 {설치경로}/src/sender.py
0 2  * * 0   python3 {설치경로}/src/trainer.py
```

> 로그는 각 스크립트가 `{LOG_DIR}` 에 날짜별로 직접 기록합니다.

---

## 보안

- DB 접속 정보, 슬랙 채널명, 서버 경로 등 모든 민감 정보는 `.env` 파일에서 관리
- `.env`는 `.gitignore`에 등록되어 git에 포함되지 않음
