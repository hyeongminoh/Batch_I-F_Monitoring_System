# ============================================================
# log_utils.py - 공통 로그 설정
# ============================================================

import os
import logging
from datetime import datetime


def setup_logger(name: str, log_dir: str) -> logging.Logger:
    """
    날짜별 로그 파일 생성 ({name}_YYYYMMDD.log), append 모드.
    콘솔 + 파일 동시 출력.
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
