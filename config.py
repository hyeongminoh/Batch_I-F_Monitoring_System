# ============================================================
# config.py - 배치 파일 모니터링 시스템 설정
# 민감 정보(DB 접속 등)는 .env 파일에서 로드
# ============================================================

import os

# ============================================================
# .env 로더 (python-dotenv 없이 내장 기능으로 파싱)
# ============================================================
def _load_env(env_path=None):
    if env_path is None:
        env_path = os.path.join(os.path.dirname(__file__), '.env')
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding='utf-8') as f:
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
ALARM_DIR = os.environ.get('ALARM_DIR', os.path.join(BASE_DATA_DIR, 'batch_alarms'))
MODEL_DIR = os.environ.get('MODEL_DIR', os.path.join(BASE_DATA_DIR, 'models'))

# ============================================================
# Ollama LLM 설정
# ============================================================
# Ollama에 모델 등록 시 사용한 이름으로 변경 필요
# 예: ollama create exaone3.5:2.4b -f Modelfile
OLLAMA_URL     = "http://localhost:11434/api/generate"
OLLAMA_MODEL   = "exaone3.5:2.4b"
OLLAMA_TIMEOUT = 60  # 초

# ============================================================
# 모니터링 파라미터
# ============================================================
HISTORY_DAYS       = 90    # detector 조회 기간 (일)
TRAIN_HISTORY_DAYS = 180   # trainer 학습 기간 (일)
MIN_SAMPLE_COUNT   = 3     # 알람 발동 최소 샘플 수 (미만이면 알람 제외)

# ============================================================
# 공통 메타 정보
# ============================================================
REGR_ID = "BAT_MON"  # 등록자 ID
