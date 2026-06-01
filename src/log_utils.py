# ============================================================
# log_utils.py - 공통 로그 설정
# ============================================================
"""
모든 프로세스(detector / sender / trainer / recommender)가 공유하는 로거 팩토리.

[출력 대상]
  - 파일: {LOG_DIR}/{name}_YYYYMMDD.log  (날짜별 자동 생성, append 모드)
  - 콘솔: 동시 출력

[로그 포맷]
  [YYYYMMDD][HHMMSS] [LEVEL   ] 파일명:줄번호 | 메시지

[사용법]
  from log_utils import setup_logger
  log = setup_logger('detector', LOG_DIR)
  log.info("처리 시작")
"""

import os
import logging
from datetime import datetime


def setup_logger(name: str, log_dir: str) -> logging.Logger:
    """
    날짜별 로그 파일을 생성하고 콘솔과 파일에 동시 출력하는 로거를 반환한다.

    Args:
        name:    로거 이름 (예: 'detector'). 로그 파일명과 logging 네임스페이스에 사용.
        log_dir: 로그 파일을 저장할 디렉토리 경로. 없으면 자동 생성.

    Returns:
        설정 완료된 logging.Logger 인스턴스.

    Note:
        - 동일 name으로 재호출해도 핸들러가 중복 추가되지 않도록 propagate=False 적용.
        - 로그 레벨은 DEBUG (모든 레벨 기록).
    """
    os.makedirs(log_dir, exist_ok=True)

    today    = datetime.now().strftime("%Y%m%d")
    log_path = os.path.join(log_dir, f"{name}_{today}.log")

    fmt = logging.Formatter(
        fmt="[%(asctime)s] [%(levelname)-8s] %(filename)s:%(lineno)d | %(message)s",
        datefmt="%Y%m%d][%H%M%S",
    )

    file_handler = logging.FileHandler(log_path, mode='a', encoding='utf-8')
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.propagate = False

    return logger
