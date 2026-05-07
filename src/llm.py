# ============================================================
# llm.py - Ollama EXAONE 한국어 알람 메시지 생성
# ============================================================

import logging
import requests

# detector.py가 setup_logger('detector', ...) 로 설정한 로거를 공유
log = logging.getLogger('detector')


def build_prompt(file_id, freq_type, window, check_time, delay_min, anomaly_score, today):
    is_month_end = today.day >= 25
    return (
        f"다음 배치 파일 미수신 상황에 대한 한국어 알람 메시지를 아래 형식으로 작성하세요.\n\n"
        f"형식:\n"
        f"[배치 미수신 알람] {{파일ID}}\n"
        f"마감: {{EXP_MAX_TIME}} / 지연: {{지연분}}분 / 주기: {{수신주기}}\n"
        f"즉시 확인이 필요합니다.\n\n"
        f"- 파일ID: {file_id}\n"
        f"- 수신 주기: {freq_type}\n"
        f"- 예상 도착 범위: {window['exp_min']} ~ {window['exp_max']} (중앙값: {window['exp_med']})\n"
        f"- 현재 시각: {check_time}\n"
        f"- 지연 시간: {delay_min}분\n"
        f"- 이상 점수: {anomaly_score:.4f} (음수일수록 이상)\n"
        f"- 월말 여부: {'예' if is_month_end else '아니오'}\n\n"
        f"알람 메시지:"
    )


def generate(file_id, freq_type, window, check_time, delay_min, anomaly_score, today,
             ollama_url, ollama_model, ollama_timeout):
    """
    LLM 메시지 생성 시도.
    성공 시 (메시지, True) 반환.
    실패 시 (None, False) 반환.
    """
    prompt = build_prompt(file_id, freq_type, window, check_time, delay_min, anomaly_score, today)

    try:
        log.info(f"  [{file_id}] LLM 호출 중 ({ollama_model}) ...")
        resp = requests.post(
            ollama_url,
            json={"model": ollama_model, "prompt": prompt, "stream": False},
            timeout=ollama_timeout,
        )
        if resp.status_code == 200:
            msg = resp.json().get('response', '').strip()
            if msg:
                return msg, True
        log.warning(f"  [{file_id}] LLM 응답 비정상 (status={resp.status_code})")
    except Exception as e:
        log.warning(f"  [{file_id}] LLM 호출 실패: {e}")

    return None, False


def build_sender_prompt(file_id, alarm_msg):
    """sender용: DB에 저장된 알람 원문을 슬랙 전송 문구로 다듬기."""
    body = (alarm_msg or "").strip()
    if len(body) > 8000:
        body = body[:8000] + "\n…(이하 생략)"
    return (
        "다음은 배치 미수신 알람의 DB 저장 원문입니다. "
        "슬랙에 보내기 좋게 한국어로 정리하되, 파일ID·지연·마감 등 핵심 수치는 빠지지 않게 하세요.\n\n"
        f"- 파일ID: {file_id}\n\n"
        f"원문:\n{body}\n\n"
        "슬랙용 메시지(한 덩어리):"
    )


def generate_sender(file_id, alarm_msg, ollama_url, ollama_model, ollama_timeout):
    """
    sender 전용 LLM. 성공 시 (문자열, True), 실패 시 (None, False).
    """
    log = logging.getLogger("sender")
    prompt = build_sender_prompt(file_id, alarm_msg)
    try:
        log.info(f"  [{file_id}] (sender) LLM 호출 중 ({ollama_model}) ...")
        resp = requests.post(
            ollama_url,
            json={"model": ollama_model, "prompt": prompt, "stream": False},
            timeout=ollama_timeout,
        )
        if resp.status_code == 200:
            msg = resp.json().get("response", "").strip()
            if msg:
                return msg, True
        log.warning(f"  [{file_id}] (sender) LLM 응답 비정상 (status={resp.status_code})")
    except Exception as e:
        log.warning(f"  [{file_id}] (sender) LLM 호출 실패: {e}")
    return None, False
