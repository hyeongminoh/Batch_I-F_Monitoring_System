FROM python:3.9-slim

WORKDIR /app

# 시스템 패키지 (한국어 로케일)
RUN apt-get update && apt-get install -y --no-install-recommends \
    locales \
    ca-certificates \
 && echo "ko_KR.UTF-8 UTF-8" >> /etc/locale.gen \
 && locale-gen \
 && rm -rf /var/lib/apt/lists/*

ENV LANG=ko_KR.UTF-8
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# pip 기본값 (이미지 재현성/용량)
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_NO_CACHE_DIR=1

# 패키지 설치
COPY requirements.txt .
RUN pip install -r requirements.txt

# 소스 복사
COPY config.py detector.py sender.py trainer.py \
     llm.py log_utils.py freq_utils.py ./
COPY sql/ ./sql/

# 데이터/로그 디렉토리 생성
RUN mkdir -p /data/batch_alarms/fallback \
             /data/batch_alarms/llm \
             /data/models \
             /logs

VOLUME ["/data", "/logs"]

# compose에서 보통 override하지만, 단독 실행도 가능하게 기본값 제공
CMD ["python", "detector.py"]
