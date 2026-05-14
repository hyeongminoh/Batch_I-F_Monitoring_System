import numpy as np


def detect_dom_pattern(file_df, freq_type, round_gap):
    """
    EVERY_N_DAYS / MONTHLY 파일의 월중 수신일(day_of_month) 패턴 탐지.
    예: 매월 5일·15일 수신 → "5,15"

    알고리즘:
    1. 전체 수신 레코드의 day_of_month 빈도 집계
    2. round_gap//2 을 최소 간격으로 탐욕적 클러스터링
    3. 각 클러스터에서 최빈 day를 anchor로 선택
    4. 30//round_gap 을 기대 클러스터 수 상한으로, 초과 시 빈도 낮은 것 제거

    Returns: "5,15" 형태 문자열 / DAILY·WEEKLY·IRREGULAR는 None
    """
    if freq_type != 'MONTHLY' and not freq_type.startswith('EVERY_'):
        return None

    dom_counts = file_df['reg_dt'].dt.day.value_counts().sort_index()
    all_doms = sorted(dom_counts.index.tolist())
    if not all_doms:
        return None

    min_gap = max(3, round_gap // 2)

    # 탐욕적 클러스터링
    clusters = [[all_doms[0]]]
    for d in all_doms[1:]:
        if d - clusters[-1][-1] < min_gap:
            clusters[-1].append(d)
        else:
            clusters.append([d])

    # 클러스터별 최빈 day → anchor
    anchor_days = [max(c, key=lambda d: dom_counts[d]) for c in clusters]

    # 기대 클러스터 수 초과 시 빈도 낮은 클러스터 제거
    max_clusters = max(1, 30 // max(1, round_gap))
    if len(clusters) > max_clusters:
        ranked = sorted(
            zip([sum(dom_counts[d] for d in c) for c in clusters], anchor_days),
            reverse=True,
        )
        anchor_days = sorted(a for _, a in ranked[:max_clusters])

    return ','.join(str(d) for d in sorted(anchor_days))


def classify_frequency(file_df):
    """
    file_df['arrival_date'] 기준 수신 주기 분류.
    Returns: (freq_type, median_gap, std_gap)
    """
    dates = sorted(file_df['arrival_date'].unique())
    if len(dates) < 2:
        return "IRREGULAR", 0.0, 0.0

    gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
    median_gap = float(np.median(gaps))
    std_gap    = float(np.std(gaps))
    gap        = round(median_gap)

    if gap == 0:
        return "IRREGULAR", median_gap, std_gap

    if std_gap > median_gap * 0.5:
        freq_type = "IRREGULAR"
    elif gap == 1:
        freq_type = "DAILY"
    elif gap in (6, 7, 8):
        freq_type = "WEEKLY"
    elif 25 <= gap <= 35:
        freq_type = "MONTHLY"
    else:
        freq_type = f"EVERY_{gap}_DAYS"

    return freq_type, median_gap, std_gap


def sec_to_hms(sec):
    sec = int(sec)
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"
