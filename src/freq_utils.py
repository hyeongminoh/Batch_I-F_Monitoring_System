"""
배치 파일의 수신 주기를 분류하고 월중 수신일 패턴을 탐지하는 공통 유틸리티.
trainer.py 와 detector.py 양쪽에서 동일한 로직으로 사용된다.

[주요 함수]
  classify_frequency(file_df)
      → 수신 날짜 간격의 중앙값·표준편차를 기반으로 주기 유형을 반환.
      → 반환값: (freq_type, median_gap, std_gap)
      → freq_type 종류: DAILY / WEEKLY / MONTHLY / EVERY_N_DAYS / IRREGULAR

  detect_dom_pattern(file_df, freq_type, round_gap)
      → MONTHLY / EVERY_N_DAYS 파일에서 월중 특정 수신일(anchor) 패턴을 탐지.
      → 반환값: "5,15" 형태 문자열 또는 None
      → DAILY·WEEKLY·IRREGULAR는 None 반환 (해당 없음)

  sec_to_hms(sec)
      → 초(int)를 "HH:MM:SS" 형식 문자열로 변환. 도착 window 표시에 사용.

[주기 분류 기준 (classify_frequency)]
  round_gap = round(median_gap)
  ┌─────────────────────────────────────────────────────┐
  │ 조건                              │ 분류            │
  ├───────────────────────────────────┼─────────────────┤
  │ round_gap == 0                    │ IRREGULAR       │
  │ std_gap > median_gap × 0.5        │ IRREGULAR       │
  │ round_gap == 1                    │ DAILY           │
  │ round_gap in (6, 7, 8)            │ WEEKLY          │
  │ 25 ≤ round_gap ≤ 35              │ MONTHLY         │
  │ 그 외                             │ EVERY_{N}_DAYS  │
  └───────────────────────────────────┴─────────────────┘
"""

import numpy as np


def detect_dom_pattern(file_df, freq_type, round_gap):
    """
    EVERY_N_DAYS / MONTHLY 파일의 월중 수신일(day_of_month) 패턴을 탐지한다.
    예: 매월 5일·15일 수신 → "5,15"

    DAILY·WEEKLY·IRREGULAR 파일은 날짜 패턴이 의미 없으므로 None을 반환한다.
    탐지된 패턴은 BAT_FILE_FREQ_MST의 DOM_PATTERN 컬럼에 저장되며,
    detector의 filter_by_context()에서 anchor 기반 정확 필터로 활용된다.

    [알고리즘]
    1. 전체 수신 레코드의 day_of_month 빈도 집계
    2. round_gap // 2 를 최소 간격으로 탐욕적 클러스터링
       (예: round_gap=15 → 7일 이내 날짜들을 같은 클러스터로 묶음)
    3. 각 클러스터에서 가장 자주 등장한 day를 anchor로 선택
    4. 기대 클러스터 수(= 30 // round_gap) 초과 시 빈도 낮은 클러스터 제거

    Args:
        file_df:   FILE_ID 단위로 필터된 수신 이력 DataFrame.
                   reg_dt 컬럼(datetime) 필수.
        freq_type: classify_frequency()가 반환한 주기 유형 문자열.
        round_gap: round(median_gap). 클러스터 최소 간격 계산에 사용.

    Returns:
        "5,15" 형태의 쉼표 구분 문자열, 또는 해당 없을 때 None.
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
    수신 날짜(arrival_date) 간격의 통계를 분석해 배치 파일의 수신 주기를 분류한다.

    동일 날짜에 여러 건이 수신되더라도 날짜 단위(unique)로 간격을 계산한다.
    샘플이 2건 미만이면 간격 계산이 불가능하므로 IRREGULAR를 반환한다.

    Args:
        file_df: FILE_ID 단위로 필터된 수신 이력 DataFrame.
                 arrival_date 컬럼(date 타입) 필수.

    Returns:
        (freq_type, median_gap, std_gap) 튜플.
        - freq_type  : 주기 유형 문자열 (DAILY / WEEKLY / MONTHLY / EVERY_N_DAYS / IRREGULAR)
        - median_gap : 수신 날짜 간격의 중앙값 (단위: 일)
        - std_gap    : 수신 날짜 간격의 표준편차 (단위: 일)
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
    """초(int/float)를 'HH:MM:SS' 형식 문자열로 변환한다. percentile 결과 표시에 사용."""
    sec = int(sec)
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"
