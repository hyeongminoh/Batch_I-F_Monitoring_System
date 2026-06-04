# ============================================================
# config.py - 배치 파일 모니터링 시스템 설정
# 민감 정보(DB 접속 등)는 .env 파일에서 로드
# ============================================================
"""
시스템 전체에서 공유하는 설정값을 한 곳에서 관리한다.
모든 프로세스(detector / sender / trainer / recommender)가 이 파일을 import한다.

[민감 정보 관리]
  DB 접속 정보·슬랙 채널 등 민감값은 .env 파일에 기재하고 git에는 올리지 않는다.
  .env 위치: src/.env 또는 프로젝트 루트 .env (우선순위: src/.env 먼저 탐색)
  .env.example 파일을 참고해 .env 작성.

[주요 설정 항목]
  DB_USER / DB_PASSWORD / DB_DSN   : Oracle DB 접속 정보
  SLACK_CHANNEL / SLACK_SCRIPT     : 슬랙 알람 채널 및 전송 스크립트 경로
  BASE_DATA_DIR                    : 모델·알람파일·로그 저장 루트 경로
  USE_LLM                          : 0이면 LLM 비활성화, fallback 메시지 사용
  HISTORY_DAYS / TRAIN_HISTORY_DAYS: detector 90일, trainer 180일 조회 기간
  MIN_SAMPLE_COUNT                 : window 계산에 필요한 최소 샘플 수 (기본 3)
  VOLUME_ZSCORE_THRESHOLD          : V 알람 발동 Z-score 임계값 (기본 3.0)
"""

import os

# ============================================================
# .env 로더 (python-dotenv 없이 내장 기능으로 파싱)
# ============================================================
def _load_env(env_path=None):
    if env_path is None:
        candidates = [
            os.path.join(os.path.dirname(__file__), '.env'),                 # src/.env
            os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env') # project-root/.env
        ]
    else:
        candidates = [env_path]

    target = next((p for p in candidates if os.path.exists(p)), None)
    if not target:
        return

    with open(target, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            os.environ.setdefault(key.strip(), value.strip())

_load_env()

# ============================================================
# Oracle DB 접속 정보 (.env 에서 주입)
# ============================================================
DB_USER     = os.environ['DB_USER']
DB_PASSWORD = os.environ['DB_PASSWORD']
DB_DSN      = (
    f"(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)"
    f"(HOST={os.environ['DB_HOST']})(PORT={os.environ.get('DB_PORT', '1521')}))"
    f"(CONNECT_DATA=(SID={os.environ['DB_SID']})))"
)

# 멤버쉽 프로그램 ID
MBRSH_PGM_ID = os.environ.get('MBRSH_PGM_ID', 'A')

# ============================================================
# 슬랙 설정 (.env 에서 주입)
# ============================================================
SLACK_CHANNEL = os.environ['SLACK_CHANNEL']
SLACK_SCRIPT  = os.environ['SLACK_SCRIPT']

# ============================================================
# 파일 경로 (.env 에서 주입)
# ============================================================
BASE_DATA_DIR = os.environ.get('BASE_DATA_DIR', '/data/batch_monitoring_system')
ALARM_DIR          = os.environ.get('ALARM_DIR', os.path.join(BASE_DATA_DIR, 'batch_alarms'))
ALARM_DIR_FALLBACK = os.path.join(ALARM_DIR, 'fallback')
ALARM_DIR_LLM      = os.path.join(ALARM_DIR, 'llm')
MODEL_DIR          = os.environ.get('MODEL_DIR', os.path.join(BASE_DATA_DIR, 'models'))
LOG_DIR            = os.environ.get('LOG_DIR',   os.path.join(BASE_DATA_DIR, 'logs'))

# ============================================================
# Ollama LLM 설정
# ============================================================
# Ollama에 모델 등록 시 사용한 이름으로 변경 필요
# 예: ollama create exaone3.5:2.4b -f Modelfile
USE_LLM        = os.environ.get('USE_LLM', '1') not in ('0', 'false', 'False', 'FALSE')
OLLAMA_URL     = os.environ.get('OLLAMA_URL', 'http://localhost:11434/api/generate')
OLLAMA_MODEL   = os.environ.get('OLLAMA_MODEL', 'exaone3.5:2.4b')
OLLAMA_TIMEOUT = int(os.environ.get('OLLAMA_TIMEOUT', '60'))

# ============================================================
# 모니터링 파라미터
# ============================================================
HISTORY_DAYS            = 90    # detector 조회 기간 (일)
TRAIN_HISTORY_DAYS      = 180   # trainer 학습 기간 (일)
MIN_SAMPLE_COUNT        = 2     # 알람 발동 최소 샘플 수 (미만이면 알람 제외)
VOLUME_ZSCORE_THRESHOLD = float(os.environ.get('VOLUME_ZSCORE_THRESHOLD', '3.0'))  # 건수 이상 탐지 Z-score 임계값

# ============================================================
# 공통 메타 정보
# ============================================================
REGR_ID = "BAT_MON"  # 등록자 ID
