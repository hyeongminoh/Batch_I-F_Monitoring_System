import numpy as np


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
