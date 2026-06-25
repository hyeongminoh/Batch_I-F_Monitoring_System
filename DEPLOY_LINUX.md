# 리눅스 서버 배포 및 운영 절차

> 망분리 온프레미스 환경 기준 — 외부 인터넷 연결 없이 Docker 이미지를 반입·실행한다.

---

## 1. 전체 흐름 요약

```
[개발 PC (Windows)]
  1. Docker 이미지 빌드
  2. batch-monitor.tar 로 저장
  3. Ollama 바이너리 + EXAONE 모델 다운로드 (USE_LLM=1 사용 시)

[망분리 반입]
  USB / 승인된 매체로 서버에 전달

[리눅스 서버]
  4. Docker 이미지 로드
  5. 디렉토리 생성 및 .env.docker 작성
  6. Ollama 설치 및 모델 등록 (USE_LLM=1 사용 시)
  7. 최초 동작 확인 (수동 실행)
  8. Cron 등록
```

---

## 2. 개발 PC 작업 (이미지 빌드 및 저장)

### 2.1 Docker 이미지 빌드

프로젝트 루트 디렉토리에서 실행:

```powershell
# 방법 A: docker compose 사용 (권장)
docker compose -f docker/docker-compose.yml --profile build build

# 방법 B: 직접 빌드
docker build `
  -f docker/Dockerfile `
  --build-arg GIT_COMMIT=$(git rev-parse --short HEAD) `
  -t batch-monitor:latest `
  .
```

### 2.2 이미지를 tar로 저장 (반입용)

```powershell
docker save batch-monitor:latest -o batch-monitor.tar
```

### 2.3 Ollama 관련 파일 다운로드 (USE_LLM=1 사용 시에만 필요)

아래 2개 파일을 인터넷이 되는 환경에서 별도 다운로드:

- **Ollama 바이너리** (`ollama-linux-amd64`, ~50 MB)
  - GitHub Releases: `ollama/ollama` → `ollama-linux-amd64`
- **EXAONE 모델** (`EXAONE-3.5-2.4B-Instruct-Q4_K_M.gguf`, ~1.6 GB)
  - Hugging Face: `LGAI-EXAONE/EXAONE-3.5-2.4B-Instruct-GGUF`

또는 Ollama를 Docker로 실행할 경우:
```bash
docker pull ollama/ollama
docker save ollama/ollama -o ollama.tar
```

---

## 3. 반입 파일 목록

| 파일 | 설명 | 필수 여부 |
|---|---|---|
| `batch-monitor.tar` | 배치 모니터 Docker 이미지 | **필수** |
| `.env.docker` (신규 작성) | 운영 환경변수 (DB 접속 정보 등) | **필수** |
| `ollama-linux-amd64` | Ollama 바이너리 | USE_LLM=1 시 필수 |
| `EXAONE-3.5-2.4B-Instruct-Q4_K_M.gguf` | EXAONE LLM 모델 | USE_LLM=1 시 필수 |
| `ollama.tar` | Ollama Docker 이미지 (Docker 방식 사용 시) | USE_LLM=1 + Docker 방식 시 |

> **소스 코드 반입 불필요** — Python 소스 전체가 Docker 이미지 안에 포함됨

---

## 4. 리눅스 서버 준비

### 4.1 Docker 설치 확인

```bash
docker --version
docker compose version    # 또는: docker-compose --version
```

미설치 시:
```bash
# RHEL / CentOS 계열
sudo yum install -y docker
sudo systemctl enable --now docker

# Debian / Ubuntu 계열
sudo apt-get install -y docker.io
sudo systemctl enable --now docker

# 현재 사용자를 docker 그룹에 추가 (sudo 없이 실행하려면)
sudo usermod -aG docker $USER && newgrp docker
```

### 4.2 디렉토리 구조 생성

설치 경로: `/app/puser/app/puser/opt/batch_monitor`

```bash
export INSTALL_DIR=/app/puser/app/puser/opt/batch_monitor

mkdir -p ${INSTALL_DIR}/data/batch_alarms/fallback
mkdir -p ${INSTALL_DIR}/data/batch_alarms/llm
mkdir -p ${INSTALL_DIR}/data/models
mkdir -p ${INSTALL_DIR}/logs
```

### 4.3 환경변수 파일 작성

`docker run --env-file` 로 컨테이너에 주입되는 파일.  
컨테이너 내부 경로 기준으로 작성한다 (볼륨 마운트와 일치해야 함).

```bash
cat > ${INSTALL_DIR}/.env.docker << 'EOF'
# Oracle DB 접속 정보
DB_USER=운영DB계정
DB_PASSWORD=운영DB패스워드
DB_HOST=운영DB서버IP
DB_PORT=1521
DB_SID=운영SID

# 멤버쉽 프로그램 ID
MBRSH_PGM_ID=A

# 슬랙 설정 (컨테이너 내부 경로 기준 — 아래 4.4 참고)
SLACK_CHANNEL=운영알림채널명
SLACK_SCRIPT=/data/tpwork/shell/NXCOM/mon_slack.sh

# 파일 경로 (컨테이너 내부 절대경로, 볼륨 마운트와 일치)
ALARM_DIR=/data/batch_alarms
MODEL_DIR=/data/models
LOG_DIR=/logs

# LLM 설정 (0=비활성화, 비활성화 시 fallback 메시지 자동 사용)
USE_LLM=0

# Ollama URL (USE_LLM=1 + 호스트 직접 설치 시 아래 주석 해제)
# OLLAMA_URL=http://172.17.0.1:11434/api/generate

# 건수 이상 탐지 Z-score 임계값 (기본 3.0, 낮출수록 민감)
VOLUME_ZSCORE_THRESHOLD=3.0
EOF

chmod 600 ${INSTALL_DIR}/.env.docker
```

### 4.4 mon_slack.sh 경로 설정

`mon_slack.sh`는 컨테이너 안에서 실행되므로, 컨테이너가 접근 가능한 경로에 위치해야 한다.

**방법 A (권장)**: `/data` 볼륨 하위에 스크립트 배치

```bash
mkdir -p ${INSTALL_DIR}/data/tpwork/shell/NXCOM
cp /기존경로/mon_slack.sh ${INSTALL_DIR}/data/tpwork/shell/NXCOM/
chmod +x ${INSTALL_DIR}/data/tpwork/shell/NXCOM/mon_slack.sh
```

→ `.env.docker`에서 `SLACK_SCRIPT=/data/tpwork/shell/NXCOM/mon_slack.sh` 유지

**방법 B**: 별도 볼륨 마운트 (스크립트 원본 위치를 바꾸고 싶지 않을 때)

→ `docker run` 명령어에 `-v /실제경로/scripts:/scripts` 추가하고  
  `.env.docker`에서 `SLACK_SCRIPT=/scripts/mon_slack.sh` 로 변경

### 4.5 Docker 이미지 로드

```bash
docker load < /app/puser/opt/batch_monitor/batch-monitor.tar
docker images | grep batch-monitor    # 로드 확인
```

---

## 5. Ollama + EXAONE 설정 (USE_LLM=1 사용 시)

`USE_LLM=0` 이면 이 섹션 전체 생략.

### 5.1 Ollama 바이너리 설치

```bash
cp ollama-linux-amd64 /usr/local/bin/ollama
chmod +x /usr/local/bin/ollama
ollama --version    # 설치 확인
```

### 5.2 Ollama systemd 서비스 등록

```bash
sudo tee /etc/systemd/system/ollama.service << 'EOF'
[Unit]
Description=Ollama LLM Service
After=network.target

[Service]
ExecStart=/usr/local/bin/ollama serve
Restart=always
RestartSec=3
Environment="HOME=/root"
Environment="OLLAMA_MODELS=/app/puser/opt/batch_monitor/ollama_models"

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now ollama
systemctl status ollama    # 실행 확인
```

### 5.3 EXAONE 모델 등록

```bash
mkdir -p /app/puser/opt/batch_monitor/ollama_models

# Modelfile 생성 (GGUF 파일 경로 수정)
cat > /tmp/Modelfile << 'EOF'
FROM /경로/EXAONE-3.5-2.4B-Instruct-Q4_K_M.gguf
EOF

OLLAMA_MODELS=/app/puser/opt/batch_monitor/ollama_models \
  ollama create exaone3.5:2.4b -f /tmp/Modelfile

ollama list    # 등록 확인: exaone3.5:2.4b 출력 여부
```

### 5.4 컨테이너 → Ollama 연결 URL 설정

호스트에 Ollama를 직접 설치한 경우, 컨테이너에서 `docker0` 인터페이스 IP로 접근:

```bash
ip addr show docker0 | grep 'inet '    # 보통 172.17.0.1
```

`${INSTALL_DIR}/.env.docker` 수정:
```
USE_LLM=1
OLLAMA_URL=http://172.17.0.1:11434/api/generate
```

---

## 6. 최초 동작 확인 (수동 실행)

cron 등록 전 각 스크립트를 수동으로 실행해 정상 동작을 검증한다.

```bash
# 반복 사용할 공통 옵션 (현재 셸 세션에서만 유효)
export INSTALL_DIR=/app/puser/opt/batch_monitor
RUN_BASE="docker run --rm \
  --env-file ${INSTALL_DIR}/.env.docker \
  -e TZ=Asia/Seoul \
  -v ${INSTALL_DIR}/data:/data \
  -v ${INSTALL_DIR}/logs:/logs \
  batch-monitor:latest"
```

### 6.1 trainer.py — 최초 모델 학습 (가장 먼저 실행)

```bash
${RUN_BASE} python trainer.py
```

완료 후 확인:
```bash
ls ${INSTALL_DIR}/data/models/       # *_iso.pkl, *_scaler.pkl 파일 생성 여부
tail -50 ${INSTALL_DIR}/logs/trainer_$(date +%Y%m%d).log
```

### 6.2 detector.py — 탐지 테스트

```bash
${RUN_BASE} python detector.py
tail -50 ${INSTALL_DIR}/logs/detector_$(date +%Y%m%d).log
```

### 6.3 sender.py — 슬랙 전송 테스트

```bash
${RUN_BASE} python sender.py
tail -50 ${INSTALL_DIR}/logs/sender_$(date +%Y%m%d).log
# 슬랙 채널에서 알람 메시지 수신 확인
```

### 6.4 recommender.py — 제외 추천 테스트

```bash
${RUN_BASE} python recommender.py
tail -50 ${INSTALL_DIR}/logs/recommender_$(date +%Y%m%d).log
```

---

## 7. Cron 자동화 등록

컨테이너는 실행 후 종료(`--rm`) 방식이므로 **호스트 crontab**에 등록한다.

```bash
crontab -e
```

아래 내용 추가 (`/app/puser/opt/batch_monitor` 경로를 실제 설치 경로로 변경):

```cron
# batch-monitor: detector (10분마다)
*/10 * * * * docker run --rm --env-file /app/puser/opt/batch_monitor/.env.docker -e TZ=Asia/Seoul -v /app/puser/opt/batch_monitor/data:/data -v /app/puser/opt/batch_monitor/logs:/logs batch-monitor:latest python detector.py >> /app/puser/opt/batch_monitor/logs/cron_detector.log 2>&1

# batch-monitor: sender (5분마다)
*/5  * * * * docker run --rm --env-file /app/puser/opt/batch_monitor/.env.docker -e TZ=Asia/Seoul -v /app/puser/opt/batch_monitor/data:/data -v /app/puser/opt/batch_monitor/logs:/logs batch-monitor:latest python sender.py >> /app/puser/opt/batch_monitor/logs/cron_sender.log 2>&1

# batch-monitor: trainer (매주 일요일 02:00)
0 2 * * 0   docker run --rm --env-file /app/puser/opt/batch_monitor/.env.docker -e TZ=Asia/Seoul -v /app/puser/opt/batch_monitor/data:/data -v /app/puser/opt/batch_monitor/logs:/logs batch-monitor:latest python trainer.py >> /app/puser/opt/batch_monitor/logs/cron_trainer.log 2>&1

# batch-monitor: recommender (매주 월요일 03:00)
0 3 * * 1   docker run --rm --env-file /app/puser/opt/batch_monitor/.env.docker -e TZ=Asia/Seoul -v /app/puser/opt/batch_monitor/data:/data -v /app/puser/opt/batch_monitor/logs:/logs batch-monitor:latest python recommender.py >> /app/puser/opt/batch_monitor/logs/cron_recommender.log 2>&1
```

> **주의**: crontab 내부에서 변수 확장(`${INSTALL_DIR}`)이 동작하지 않을 수 있으므로 절대 경로를 직접 기재한다.

등록 확인:
```bash
crontab -l
```

---

## 8. 운영 관리

### 8.1 로그 확인

```bash
# 오늘 실시간 로그
tail -f /app/puser/opt/batch_monitor/logs/detector_$(date +%Y%m%d).log
tail -f /app/puser/opt/batch_monitor/logs/sender_$(date +%Y%m%d).log

# cron 실행 기록
tail -30 /app/puser/opt/batch_monitor/logs/cron_detector.log
tail -30 /app/puser/opt/batch_monitor/logs/cron_sender.log
```

### 8.2 알람 파일 확인

```bash
# 최근 생성된 알람 파일 (DB 원문)
ls -lt /app/puser/opt/batch_monitor/data/batch_alarms/ | head -20

# LLM 재작성본 (슬랙 전송용, USE_LLM=1 시)
ls -lt /app/puser/opt/batch_monitor/data/batch_alarms/llm/ | head -10

# 내용 확인
cat /app/puser/opt/batch_monitor/data/batch_alarms/ALARM_파일ID_날짜_시각_src.txt
```

### 8.3 Docker 이미지 업데이트 (신버전 반입 시)

```bash
# 기존 이미지 확인
docker images | grep batch-monitor

# 신버전 로드 (이미지 이름 동일 → cron 자동 반영)
docker load < batch-monitor-new.tar

# 구버전 이미지 정리 (필요 시)
docker rmi batch-monitor:<구버전태그>
```

### 8.4 즉시 수동 실행 (장애 대응)

```bash
docker run --rm \
  --env-file /app/puser/opt/batch_monitor/.env.docker \
  -e TZ=Asia/Seoul \
  -v /app/puser/opt/batch_monitor/data:/data \
  -v /app/puser/opt/batch_monitor/logs:/logs \
  batch-monitor:latest \
  python detector.py
```

---

## 9. 장애 대응 체크리스트

| 증상 | 확인 사항 |
|---|---|
| 슬랙 알람 미수신 | sender 로그 확인 / DB `BAT_ALARM_HIS.SEND_STS='9'` 레코드 확인 |
| 알람 미탐지 | detector 로그 확인 / DB `BAT_ALARM_HIS` 레코드 존재 여부 |
| 컨테이너 실행 즉시 종료 | 수동으로 `docker run` 실행 후 에러 메시지 확인 |
| DB 접속 실패 | `.env.docker` 접속 정보 확인 / 서버 방화벽 / Oracle 리스너 확인 |
| `*_iso.pkl` 없음 오류 | `trainer.py` 먼저 실행하여 모델 파일 생성 |
| LLM 오류 / 타임아웃 | `.env.docker`에서 `USE_LLM=0` 설정 후 재실행 (fallback 자동 사용) |
| 슬랙 스크립트 실행 오류 | `mon_slack.sh` 경로·실행 권한 확인 (`chmod +x`) |

---

## 10. 서버 디렉토리 구조 (설치 완료 후)

```
/app/puser/opt/batch_monitor/
├── .env.docker                   ← 운영 환경변수 (chmod 600 필수)
├── data/
│   ├── batch_alarms/
│   │   ├── ALARM_*_src.txt       ← 알람 원문 (sender 생성)
│   │   ├── fallback/             ← detector fallback 비교용
│   │   └── llm/                  ← LLM 재작성본 (슬랙 전송용)
│   ├── models/
│   │   ├── {FILE_ID}_iso.pkl     ← Isolation Forest 모델
│   │   └── {FILE_ID}_scaler.pkl  ← 스케일러
│   └── tpwork/shell/NXCOM/
│       └── mon_slack.sh          ← 슬랙 전송 스크립트
└── logs/
    ├── detector_YYYYMMDD.log
    ├── sender_YYYYMMDD.log
    ├── trainer_YYYYMMDD.log
    ├── recommender_YYYYMMDD.log
    ├── cron_detector.log         ← cron stdout/stderr
    ├── cron_sender.log
    ├── cron_trainer.log
    └── cron_recommender.log
```
