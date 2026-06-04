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
│  2. 수신 주기 결정 (도착 여부 분기 전 공통 수행)             │
│     ├─ MST에 있음 → 저장된 FREQ_TYPE 사용 (T 우선 > D)      │
│     └─ MST에 없음 → 직접 계산 → MST에 FB(D)로 기록         │
│  3. 오늘 도착 여부 분기                                     │
│     ├─ 도착   → V 알람: TOT_REC_CNT Z-score 탐지           │
│     │           (M 알람 존재 시 억제, 임계값 초과 시 INSERT) │
│     └─ 미도착 → M 알람: deadline(95th) 초과 감지            │
│                  → Isolation Forest anomaly score           │
│                  → fallback / LLM 메시지 생성               │
│  4. BAT_ALARM_HIS INSERT (SEND_STS='0', ALARM_TYPE=M/V)    │
└───────────────────────────┬─────────────────────────────────┘
                            │ SEND_STS='0' (대기)
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                       sender.py                              │  ← cron 5분마다
│                                                             │
│  1. 대기 알람 조회 (BAT_ALARM_HIS SEND_STS='0')             │
│  2. DB 원문 → ALARM_DIR/…_src.txt 저장 (항상)              │
│  3. (USE_LLM=1) LLM으로 슬랙용 문구 재작성                  │
│     └─ 성공 → ALARM_DIR_LLM/…_llm.txt / 실패 → src 사용    │
│  4. mon_slack.sh 실행 (llm 성공 시 llm 경로, 아니면 src)    │
│  5. SEND_STS 업데이트 (1=완료 / 9=실패)                     │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                       trainer.py                             │  ← cron 매주 일요일 02:00
│                                                             │
│  180일 데이터로 FILE_ID별 재학습 (피처 6개)                 │
│  → {FILE_ID}_iso.pkl / {FILE_ID}_scaler.pkl 저장            │
│  → 수신 주기 분류 + DOM_PATTERN 탐지                        │
│  → BAT_FILE_FREQ_MST MAIN(T) UPSERT                        │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                    recommender.py                            │  ← cron 매주 월요일 03:00
│                                                             │
│  1. 90일 이력 분석 → 제외 후보 탐지                        │
│     ├─ IRREGULAR 분류 파일                                  │
│     └─ 샘플 부족 (< MIN_SAMPLE_COUNT) 파일                 │
│  2. LLM(EXAONE)으로 한국어 제외 사유 생성                  │
│  3. BAT_MNTLST_EXC INSERT (USE_YN='P' 추천대기)            │
│     → 담당자가 'Y'(제외) 또는 'N'(유지)로 최종 결정        │
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

### 탐지 가능한 수신 패턴

#### 수신 주기별 탐지 동작

| FREQ_TYPE | median_gap 범위 | 실제 예시 | 컨텍스트 필터 | tolerance |
|---|---|---|---|---|
| `DAILY` | ~1일 | 매일 09:00 수신 | 요일 + 월말여부 | — |
| `WEEKLY` | 6~8일 | 매주 화요일 수신 | 요일 + 월말여부 | — |
| `EVERY_N_DAYS` | 2~5일, 9~24일, 36일↑ | 격일·N일마다·격월 수신 | day_of_month ± tolerance | `max(2, N//5)` |
| `MONTHLY` | 25~35일 | 매월 특정일 수신 | day_of_month ± tolerance | `max(2, 6)` = 6 |
| `IRREGULAR` | — | 불규칙 수신 | **알람 없음 (SKIP)** | — |

> `IRREGULAR` 판정: `gap == 0` 또는 `std_gap > median_gap × 0.5` (변동성 과다)

#### 컨텍스트 필터 상세

**DAILY / WEEKLY**  
같은 요일 AND 월말여부(`day ≥ 25`)가 일치하는 이력만 도착 window 계산에 사용합니다.  
월말 수요일과 일반 수요일을 서로 다른 컨텍스트로 분리해 비교합니다.

**MONTHLY / EVERY_N_DAYS**  
`DOM_PATTERN` anchor ± tolerance 범위 내 이력을 사용합니다.  
anchor는 오늘 날짜와 가장 가까운 anchor_day가 선택됩니다.

```
tolerance = max(2, round_gap // 5)

EVERY_10_DAYS  → max(2,  2) =  2  → ±2일 범위
EVERY_14_DAYS  → max(2,  2) =  2  → ±2일 범위
EVERY_20_DAYS  → max(2,  4) =  4  → ±4일 범위
MONTHLY(30일)  → max(2,  6) =  6  → ±6일 범위
EVERY_60_DAYS  → max(2, 12) = 12  → ±12일 범위
```

#### 알람이 발동되지 않는 케이스

| 케이스 | 원인 |
|---|---|
| `IRREGULAR` 파일 | 수신 변동성 과다, 정상 패턴 정의 불가 |
| 분기·반기 파일 (gap ≥ 90일) | 90일 이력 내 샘플 1건 → `MIN_SAMPLE_COUNT` 미달 |
| `MONTHLY` 파일 + 직전 월 결번 | anchor ± tolerance 범위 내 샘플 1건 → `MIN_SAMPLE_COUNT` 미달 |
| `WEEKLY` 파일 + 월말 발생일 | 90일 내 동일 월말 요일은 3~4건, 결번 시 `MIN_SAMPLE_COUNT` 미달 가능 |
| deadline 미초과 | 현재 시각 < `EXP_MAX_TIME` (95th percentile), 아직 대기 시간 내 |
| 오늘 이미 동일 유형 알람 발생 | 중복 방지 로직 |

### 월중 수신일 패턴 탐지 — `freq_utils.detect_dom_pattern()`

`EVERY_N_DAYS` / `MONTHLY` 파일이 **월 중 어느 날짜에 오는지** 를 탐지합니다.  
예) 매월 5일·15일에 오는 파일 → `"5,15"`

- 수신 레코드의 day_of_month를 클러스터링해 anchor day 추출
- 결과를 `BAT_FILE_FREQ_MST.MAIN_DOM_PATTERN` / `FB_DOM_PATTERN` 에 저장
- detector가 도착 window를 계산할 때 ±tolerance 근사 대신 **anchor 기반 정확 필터**로 활용
- `DAILY` / `WEEKLY` / `IRREGULAR`는 `None` 반환 (적용 불필요)

**기대 클러스터 수 계산:**  
`max(1, round(30 / round_gap))`  
정수 나눗셈(`//`) 대신 반올림(`round`)을 사용해 **비대칭 간격 패턴도 정확히 탐지**합니다.  
예) 매월 4일·24일 (간격 20일·10일 교번, median=20): `30//20=1` → 24일만 학습 (버그) → `round(30/20)=2` → 4일·24일 모두 학습 (수정)

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

단순 평균이 아닌 **FREQ_TYPE별 컨텍스트 필터**를 적용해 정확도를 높입니다.

| FREQ_TYPE | 필터 기준 |
|---|---|
| `DAILY` / `WEEKLY` | 같은 요일 + 월말여부(day ≥ 25) |
| `MONTHLY` / `EVERY_N_DAYS` | day_of_month ± tolerance (DOM_PATTERN anchor 기반) |

```
  → 5th  percentile = EXP_MIN_TIME  (최솟값)
  → 50th percentile = EXP_MED_TIME  (중앙값)
  → 95th percentile = EXP_MAX_TIME  (deadline 기준)

샘플 수 < MIN_SAMPLE_COUNT(기본 2)이면 "오늘 수신 예정일 아님"으로 판단하여 알람 제외
window는 매 run마다 90일 이력으로 재계산 (컨텍스트 의존)
```

### 알람 발동 조건

**M 알람 (미수신)** — 파일이 제시간에 도착하지 않은 경우
```
✅ 모니터링 제외 목록(BAT_MNTLST_EXC)에 없을 것
✅ 수신 주기가 IRREGULAR가 아닐 것
✅ 오늘 해당 FILE_ID가 수신되지 않았을 것
✅ 오늘 이미 M 알람이 없을 것 (중복 방지)
✅ 현재 시각 > EXP_MAX_TIME (95th percentile 초과)
```

**V 알람 (건수 이상)** — 파일은 도착했으나 건수가 비정상인 경우
```
✅ 모니터링 제외 목록(BAT_MNTLST_EXC)에 없을 것
✅ 수신 주기가 IRREGULAR가 아닐 것
✅ 오늘 해당 FILE_ID가 수신됐을 것
✅ 오늘 M 알람이 없을 것 (M 발생 시 V 억제)
✅ 오늘 이미 V 알람이 없을 것 (중복 방지)
✅ TOT_REC_CNT Z-score > VOLUME_ZSCORE_THRESHOLD (기본 3.0)
```

### Isolation Forest 피처

```python
features = [
    arrival_sec,    # 하루 중 도착 시각 (초 단위)
    tot_rec_cnt,    # 전체 레코드 수
    send_rec_cnt,   # 전송 레코드 수
    weekday,        # 요일 (0=월 ~ 6=일)
    is_month_end,   # 월말 여부 (day ≥ 25 이면 1)
    day_of_month,   # 월 중 도착일 (1~31) — EVERY_N_DAYS/MONTHLY 패턴 인식
]
```

> trainer와 detector가 **동일한 6개 피처**를 사용해야 합니다 (불일치 시 .pkl 로드 오류).  
> 기존 5개 피처 .pkl 파일은 trainer 재실행 전까지 anomaly score `-0.5` 로 fallback 처리됩니다.

### Isolation Forest를 선택한 이유

- **비지도 학습**: 정상/이상 라벨 없이도 학습 가능 (배치 이력 데이터에 라벨이 없음)
- **가볍다**: 온프레미스 서버에서 무거운 딥러닝 없이도 운영 가능
- **다변수 처리**: 도착 시각/건수/요일 등 여러 변수를 동시에 반영
- **설명 가능**: 어떤 시점/패턴이 이상한지 로그로 추적하기 쉬움(점수 기반)
- **빠른 추론**: 주기적으로 실행해도 부담이 적어 cron/스케줄러에 적합

### 알람 메시지 생성 (`llm.py`)

`llm.py`는 detector와 sender 두 곳에서 사용됩니다.

**detector (알람 감지 시)**

```
1. fallback 템플릿 메시지  → ALARM_DIR/fallback/…_fallback.txt 저장
2. LLM(EXAONE) 메시지     → ALARM_DIR/llm/…_llm.txt 저장 (Ollama 연결 시)

BAT_ALARM_HIS.ALARM_MSG: LLM 성공 시 LLM 메시지, 실패 시 fallback 저장
```

**sender (슬랙 전송 시)**

```
DB의 ALARM_MSG 원문을 슬랙에 보내기 좋게 LLM이 재작성
  → 성공: ALARM_DIR_LLM/…_llm.txt 로 전송
  → 실패: ALARM_DIR/…_src.txt (DB 원문) 로 전송
```

**fallback 메시지 형식 (detector):**
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
| `MAIN_DOM_PATTERN` | trainer | — | `EVERY_N_DAYS`/`MONTHLY`만, 예: `"5,15"` |
| `FB_DOM_PATTERN` | detector | — | trainer 미실행 FILE_ID 한정 |

### BAT_ALARM_HIS ALARM_TYPE

| 값 | 의미 | ANOMALY_SCORE 용도 | EXP_*/DELAY_MIN |
|---|---|---|---|
| `M` | 미수신 알람 | Isolation Forest score | 도착 window 기록 |
| `V` | 건수 이상 알람 | Z-score 값 | NULL |

### BAT_MNTLST_EXC USE_YN 흐름

```
USE_YN
 ├── 'Y' : 제외 중    ← detector / trainer 가 체크하여 모니터링·학습 제외
 ├── 'N' : 해제       ← 다시 모니터링 대상
 └── 'P' : 추천대기  ← recommender 가 INSERT, 담당자 검토 후 Y/N 결정
```

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
├── .env.docker              ← Docker 환경용 .env
├── .env.example             ← 환경변수 템플릿
├── .dockerignore
├── .gitignore
├── requirements.txt         ← 의존 패키지 목록
├── CLAUDE.md
├── README.md
├── docker_run.txt           ← docker 실행 명령어 메모
├── src/                     ← Python 소스 전체
│   ├── config.py            ← .env 로드 및 설정값 관리 (USE_LLM 포함)
│   ├── freq_utils.py        ← 공통 주기 분류 유틸 (classify_frequency, detect_dom_pattern, sec_to_hms)
│   ├── detector.py          ← 미수신 감지 프로세스
│   ├── sender.py            ← 슬랙 전송 프로세스
│   ├── trainer.py           ← 모델 재학습 + BAT_FILE_FREQ_MST MAIN 갱신
│   ├── recommender.py       ← 모니터링 제외 파일 자동 추천 (USE_YN='P')
│   ├── llm.py               ← Ollama EXAONE 메시지 생성 (generate / generate_sender)
│   ├── log_utils.py         ← 공통 로그 설정 (날짜별 파일, 포맷 정의)
│   ├── test_db.py           ← DB 연동 테스트 스크립트
│   └── sql/
│       ├── detector_sql.py    ← detector 전용 SQL
│       ├── sender_sql.py      ← sender 전용 SQL
│       ├── trainer_sql.py     ← trainer 전용 SQL
│       └── recommender_sql.py ← recommender 전용 SQL
├── docker/                  ← Docker 관련 파일
│   ├── Dockerfile
│   └── docker-compose.yml
├── table_sql/               ← DB DDL (테이블·시퀀스 생성 스크립트)
│   ├── bat_alarm_his.sql
│   ├── bat_file_freq_mst.sql
│   ├── bat_mntlsth_exc.sql
│   ├── seq_bat_alarm_his.sql
│   └── test_data_insert.sql ← 테스트 데이터 INSERT
└── sql/                     ← DB 변경 DDL
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

{LOG_DIR}/                ← 기본: {BASE_DATA_DIR}/logs
├── detector_YYYYMMDD.log
├── sender_YYYYMMDD.log
├── trainer_YYYYMMDD.log
├── recommender_YYYYMMDD.log
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
# ALARM_DIR / MODEL_DIR / LOG_DIR 미설정 시 BASE_DATA_DIR 하위 기본값 사용
BASE_DATA_DIR=/data/batch_monitoring_system
ALARM_DIR=/data/batch_monitoring_system/batch_alarms
MODEL_DIR=/data/batch_monitoring_system/models
LOG_DIR=/data/batch_monitoring_system/logs

# LLM (Ollama) — 선택사항, 기본값 사용 시 생략 가능
USE_LLM=1                                      # 0 으로 설정하면 LLM 비활성화
OLLAMA_URL=http://localhost:11434/api/generate
OLLAMA_MODEL=exaone3.5:2.4b
OLLAMA_TIMEOUT=60

# 모니터링 파라미터 — 선택사항, 기본값 사용 시 생략 가능
VOLUME_ZSCORE_THRESHOLD=3.0   # V 알람 민감도. 낮을수록 민감 (기본 3.0)
```

### 4. DB 연동 확인

```bash
python3 src/test_db.py
# → {LOG_DIR}/test_db_YYYYMMDD.log 에서 결과 확인
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
0 3  * * 1   python3 {설치경로}/src/recommender.py
```

> recommender.py 실행 후 `BAT_MNTLST_EXC`에서 `USE_YN='P'` 레코드를 검토하여 `'Y'`(제외) 또는 `'N'`(유지)으로 직접 업데이트합니다.

> 로그는 각 스크립트가 `{LOG_DIR}` 에 날짜별로 직접 기록합니다.

---

## 보안

- DB 접속 정보, 슬랙 채널명, 서버 경로 등 모든 민감 정보는 `.env` 파일에서 관리
- `.env`는 `.gitignore`에 등록되어 git에 포함되지 않음
