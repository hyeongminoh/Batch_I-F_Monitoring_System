# 🔍 Batch I/F Monitoring System

> 망분리 온프레미스 리눅스 서버에서 동작하는 **배치 파일 미수신 탐지 및 슬랙 알람 시스템**  
> 외부 인터넷 통신 없이 완전 로컬에서 동작합니다.

![Python](https://img.shields.io/badge/Python-3.9.18-blue?logo=python)
![Oracle](https://img.shields.io/badge/Oracle-DB-red?logo=oracle)
![Scikit-learn](https://img.shields.io/badge/Scikit--learn-IsolationForest-orange?logo=scikit-learn)
![Ollama](https://img.shields.io/badge/Ollama-EXAONE_2.4B-black)

---

## 📌 개요

배치 파일이 정해진 시간 안에 수신되지 않을 경우, 이를 자동으로 감지하고 슬랙으로 알람을 발송하는 시스템입니다.

- 과거 수신 이력을 분석해 **파일별 예상 도착 시간대(window)** 를 동적으로 계산
- **Isolation Forest** 로 이상 수신 패턴 탐지
- **Ollama EXAONE 2.4B** (로컬 LLM)으로 한국어 알람 메시지 자동 생성
- LLM 장애 시에도 fallback 메시지로 **알람은 반드시 발송**

---

## 🏗️ 아키텍처

```
┌─────────────────────────────────────────────────────────┐
│                   COM_BATFILE_TRN (Oracle)               │
│              배치파일 수신 트랜잭션 이력 테이블             │
└───────────────────────┬─────────────────────────────────┘
                        │ 조회 (90일 이력)
                        ▼
┌──────────────────────────────────────┐
│           detector.py                │  ← cron 10분마다
│                                      │
│  1. 제외 FILE_ID 필터링              │
│  2. 수신 주기 분류                   │
│  3. 도착 window 계산 (5/50/95th)     │
│  4. deadline(95th) 초과 감지         │
│  5. Isolation Forest anomaly score   │
│  6. EXAONE 한국어 알람 메시지 생성   │
│  7. BAT_ALARM_HIS INSERT             │
└──────────────────┬───────────────────┘
                   │ SEND_STS='0' (대기)
                   ▼
┌──────────────────────────────────────┐
│            sender.py                 │  ← cron 5분마다
│                                      │
│  1. 대기 알람 조회                   │
│  2. ALARM_*.txt 파일 생성            │
│  3. mon_slack.sh 실행                │
│  4. SEND_STS 업데이트 (1=완료/9=실패)│
└──────────────────────────────────────┘

┌──────────────────────────────────────┐
│            trainer.py                │  ← cron 매주 일요일 02:00
│                                      │
│  180일 데이터로 FILE_ID별 재학습     │
│  → {FILE_ID}_iso.pkl 저장            │
│  → {FILE_ID}_scaler.pkl 저장         │
└──────────────────────────────────────┘
```

---

## ⚙️ 핵심 로직

### 수신 주기 분류

과거 수신 날짜 간격의 중앙값(median_gap)으로 주기를 자동 분류합니다.

| 조건 | 분류 |
|---|---|
| median_gap == 1일 | `DAILY` |
| median_gap == 6~8일 | `WEEKLY` |
| median_gap == 25~35일 | `MONTHLY` |
| std_gap > median_gap × 0.5 | `IRREGULAR` ← 알람 제외 |
| 그 외 | `EVERY_{n}_DAYS` |

### 도착 window 계산

단순 평균이 아닌 **컨텍스트 필터**를 적용해 정확도를 높입니다.

```
필터 조건: 같은 요일 + 월말여부(day ≥ 25) 동일한 날만 사용
  → 5th  percentile = EXP_MIN_TIME  (최솟값)
  → 50th percentile = EXP_MED_TIME  (중앙값)
  → 95th percentile = EXP_MAX_TIME  (deadline 기준)

샘플 수 < 3이면 오탐 방지를 위해 알람 제외
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

---

## 🗄️ DB 테이블

| 테이블 | 유형 | 설명 |
|---|---|---|
| `COM_BATFILE_TRN` | 기존 | 배치파일 수신 트랜잭션 이력 |
| `BAT_MNTLST_EXC` | 신규 | 모니터링 제외 파일 관리 |
| `BAT_ALARM_HIS` | 신규 | 알람 이력 및 전송 상태 관리 |

```sql
-- 알람 ID 채번 시퀀스
CREATE SEQUENCE SEQ_BAT_ALARM_HIS
START WITH 1 INCREMENT BY 1 NOCACHE NOCYCLE;
```

### SEND_STS 흐름

```
INSERT → '0' (대기)  →  '1' (전송 완료)
                     →  '9' (전송 실패, ERR_MSG 기록)
```

---

## 📁 파일 구조

```
{설치경로}/
├── .env              ← 민감 정보 (git 제외)
├── config.py         ← .env 로드 및 설정값 관리
├── detector.py       ← 미수신 감지 프로세스
├── sender.py         ← 슬랙 전송 프로세스
└── trainer.py        ← 모델 재학습 프로세스

{MODEL_DIR}/
├── {FILE_ID}_iso.pkl
└── {FILE_ID}_scaler.pkl

{ALARM_DIR}/
└── ALARM_{FILE_ID}_{YYYYMMDD_HHMMSS}.txt
```

---

## 🚀 설치 및 설정

### 1. 필수 패키지 (별도 설치 불필요)

```
Python    3.9.18
oracledb  3.3       # Oracle Thin mode (Instant Client 불필요)
pandas
numpy
scikit-learn
joblib
requests
```

### 2. LLM 설치 (망분리 환경 반입 필요)

```bash
# Ollama 설치
chmod +x ollama-linux-amd64
./ollama-linux-amd64 serve &

# EXAONE 모델 등록
ollama create exaone3.5:2.4b -f Modelfile
```

> 반입 필요 파일: `ollama-linux-amd64` (~50MB), `EXAONE-3.5-2.4B-Instruct-Q4_K_M.gguf` (~1.6GB)

### 3. 환경 설정

```bash
# .env 파일 생성 후 실제 값 입력
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

# 경로
ALARM_DIR=/path/to/batch_alarms
MODEL_DIR=/path/to/models
```

### 4. 모델 초기 학습

```bash
python3 trainer.py
```

### 5. cron 등록

```cron
*/10 * * * * python3 {설치경로}/detector.py >> /var/log/detector.log 2>&1
*/5  * * * * python3 {설치경로}/sender.py   >> /var/log/sender.log  2>&1
0 2  * * 0   python3 {설치경로}/trainer.py  >> /var/log/trainer.log 2>&1
```

---

## 🤖 LLM 알람 메시지 예시

```
[배치 파일 미수신 알람] 파일ID EB140402의 배치 파일이 오늘 예정된
수신 시간(09:00:00 ~ 11:30:00)을 45분 초과하여 아직 도착하지 않았습니다.
해당 파일의 수신 주기는 DAILY이며, 이상 점수는 -0.6732로 평소와 다른
패턴이 감지되었습니다. 즉시 확인이 필요합니다.
```

> LLM 호출 실패 시 fallback 템플릿 메시지로 자동 대체되어 알람은 **반드시** 발송됩니다.

---

## 🔐 보안

- DB 접속 정보, 슬랙 채널명, 서버 경로 등 모든 민감 정보는 `.env` 파일에서 관리
- `.env`는 `.gitignore`에 등록되어 git에 포함되지 않음
