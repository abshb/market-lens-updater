"""Precompute the V7.2 Weekly Top-30 stock scan during Massive GitHub cache publication."""
from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path

SECTORS = ('XLB', 'XLC', 'XLE', 'XLF', 'XLI', 'XLK', 'XLP', 'XLRE', 'XLU', 'XLV', 'XLY')

# Walk-forward Ridge(alpha=50,000) weights fit strictly on causal panel < 2025-11-27 (exit_date20 <= 2025-12-22)
W_V7_2 = {
    'resmom_12_1': 0.005784,
    'rs_ind12_adj': 0.001294,
    'id_sharpe_12_1': 0.006951,
    'rev_1m_adj': 0.00511,
    'rev_on_1m_adj': 0.006505,
    'log_dvol20': 0.008224,
    'low_lotto': -0.000353,
}

FEAT_COLS = (
    'resmom_12_1',
    'rs_ind12_adj',
    'id_sharpe_12_1',
    'rev_1m_adj',
    'rev_on_1m_adj',
    'log_dvol20',
    'low_lotto',
)


def _js_round(x: float) -> int:
    return math.floor(x + 0.5)


def _median(values: list[float]) -> float:
    s = sorted(values)
    mid = len(s) // 2
    return (s[mid - 1] + s[mid]) / 2.0 if len(s) % 2 == 0 else s[mid]


def _monday_key(day_str: str) -> str:
    d = date.fromisoformat(day_str)
    return (d - timedelta(days=d.weekday())).isoformat()


def _std(arr: list[float]) -> float:
    n = len(arr)
    if n < 2:
        return math.nan
    mean = sum(arr) / n
    var = sum((x - mean) ** 2 for x in arr) / (n - 1)
    return math.sqrt(var)


def _cov(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n < 2:
        return math.nan
    ma = sum(a[:n]) / n
    mb = sum(b[:n]) / n
    return sum((a[i] - ma) * (b[i] - mb) for i in range(n)) / (n - 1)


def _corr(a: list[float], b: list[float]) -> float:
    sa = _std(a)
    sb = _std(b)
    if not (sa > 0 and sb > 0):
        return math.nan
    return _cov(a, b) / (sa * sb)


def trend_channel_summary(rows: list[list]) -> tuple[str, float] | None:
    """Exact port of calculateTrendChannel(rows)[0] from mobile/src/pattern-trend.ts.
    Each row is [time, open, high, low, close, volume].
    Returns (direction, changePercent) or None.
    """
    valid_rows = [
        r for r in rows
        if all(isinstance(v, (int, float)) and math.isfinite(v) for v in r[1:5])
    ]
    trend_rows = valid_rows[-20:]
    if len(trend_rows) < 15:
        return None

    true_ranges: list[float] = []
    for idx in range(1, len(trend_rows)):
        prev_c = trend_rows[idx - 1][4]
        hi = trend_rows[idx][2]
        lo = trend_rows[idx][3]
        true_ranges.append(max(hi - lo, abs(hi - prev_c), abs(lo - prev_c)))

    atr = _median(true_ranges)
    latest_close = trend_rows[-1][4]
    if not math.isfinite(atr) or atr <= 0:
        return None
    contact_tolerance = max(atr * 0.5, latest_close * 0.002)

    lows: list[tuple[int, float]] = []
    highs: list[tuple[int, float]] = []
    for idx in range(1, len(trend_rows) - 1):
        row = trend_rows[idx]
        neighbors = trend_rows[idx - 1: idx + 2]
        if all(row[3] <= nb[3] for nb in neighbors):
            lows.append((idx, float(row[3])))
        if all(row[2] >= nb[2] for nb in neighbors):
            highs.append((idx, float(row[2])))

    candidate_slopes: list[float] = []

    def add_pair_slopes(contacts: list[tuple[int, float]]) -> None:
        for i in range(len(contacts) - 1):
            for j in range(i + 1, len(contacts)):
                span = contacts[j][0] - contacts[i][0]
                if span < 3:
                    continue
                sl = (contacts[j][1] - contacts[i][1]) / span
                if math.isfinite(sl):
                    candidate_slopes.append(sl)

    add_pair_slopes(lows)
    add_pair_slopes(highs)
    slope_step = max(contact_tolerance / 50.0, latest_close * 0.00002)
    slope_map: dict[int, float] = {}
    for sl in candidate_slopes:
        slope_map[_js_round(sl / slope_step)] = sl
    unique_slopes = list(slope_map.values())

    def best_contact_cluster(contacts: list[tuple[int, float]], slope: float):
        best = None
        best_score = -math.inf
        residuals = [(c, c[1] - slope * c[0]) for c in contacts]
        for _, anchor_res in residuals:
            nearby = [item for item in residuals if abs(item[1] - anchor_res) <= contact_tolerance]
            if len(nearby) < 2:
                continue
            offset = _median([item[1] for item in nearby])
            refined = [item for item in residuals if abs(item[1] - offset) <= contact_tolerance]
            if len(refined) < 2:
                continue
            refined_offset = _median([item[1] for item in refined])
            error = sum(abs(item[1] - refined_offset) for item in refined)
            touches = [item[0] for item in refined]
            span = touches[-1][0] - touches[0][0]
            recency = touches[-1][0]
            score = len(touches) * 100.0 + span + recency * 0.25 - error / contact_tolerance
            if score > best_score:
                best_score = score
                best = (refined_offset, touches, error)
        return best

    best_channel = None
    latest_index = len(trend_rows) - 1
    validation_index = max(0, latest_index - 1)

    for slope in unique_slopes:
        if abs(slope) > atr * 0.6:
            continue
        lower = best_contact_cluster(lows, slope)
        upper = best_contact_cluster(highs, slope)
        if not lower or not upper or upper[0] <= lower[0]:
            continue

        possible_starts = sorted(
            {t[0] for t in lower[1]} | {t[0] for t in upper[1]},
            reverse=True,
        )
        active = None
        for cand_start in possible_starts:
            act_lows = [t for t in lower[1] if t[0] >= cand_start]
            act_highs = [t for t in upper[1] if t[0] >= cand_start]
            if len(act_lows) < 2 or len(act_highs) < 2:
                continue
            first_c = min(act_lows[0][0], act_highs[0][0])
            last_c = max(act_lows[-1][0], act_highs[-1][0])
            if last_c - first_c < 5:
                continue
            active = (act_lows, act_highs, first_c)
            break
        if not active:
            continue

        act_lows, act_highs, start_idx = active
        lower_offset = _median([t[1] - slope * t[0] for t in act_lows])
        upper_offset = _median([t[1] - slope * t[0] for t in act_highs])
        if upper_offset <= lower_offset:
            continue
        width = upper_offset - lower_offset
        if width < atr * 0.8 or width > min(latest_close * 0.35, atr * 6.0):
            continue

        last_low_touch = act_lows[-1][0]
        last_high_touch = act_highs[-1][0]
        last_touch_idx = max(last_low_touch, last_high_touch)
        contact_span = last_touch_idx - start_idx
        if contact_span < 5 or latest_index - min(last_low_touch, last_high_touch) > 12:
            continue

        violations = 0
        for idx in range(start_idx, validation_index + 1):
            lv = slope * idx + lower_offset
            uv = slope * idx + upper_offset
            cl = trend_rows[idx][4]
            if cl < lv - contact_tolerance or cl > uv + contact_tolerance:
                violations += 1
        if violations > 2:
            continue

        total_touches = len(act_lows) + len(act_highs)
        balanced_touches = min(len(act_lows), len(act_highs))
        if total_touches < 4:
            continue
        norm_err = (lower[2] + upper[2]) / contact_tolerance
        exc_width = max(0.0, width / atr - 4.0)
        score = (
            total_touches * 120.0
            + balanced_touches * 80.0
            + contact_span * 2.0
            - (latest_index - last_touch_idx) * 4.0
            - violations * 35.0
            - norm_err * 8.0
            - exc_width * 15.0
        )
        if best_channel is None or score > best_channel[3]:
            best_channel = (slope, lower_offset, start_idx, score)

    if best_channel is None:
        n = len(trend_rows)
        mean_x = (n - 1) / 2.0
        mean_y = sum(r[4] for r in trend_rows) / n
        num = sum((i - mean_x) * (trend_rows[i][4] - mean_y) for i in range(n))
        den = sum((i - mean_x) ** 2 for i in range(n))
        slope = num / den if den > 0 else 0.0
        high_res = sorted((trend_rows[i][2] - slope * i for i in range(n)), reverse=True)[:4]
        low_res = sorted(trend_rows[i][3] - slope * i for i in range(n))[:4]
        upper_offset = _median(high_res)
        lower_offset = _median(low_res)
        if upper_offset > lower_offset:
            best_channel = (slope, lower_offset, 0, 0.0)

    if best_channel is None:
        return None

    selected_slope, lower_offset, selected_start, _ = best_channel
    last_idx = len(trend_rows) - 1
    ch_start = selected_slope * selected_start + lower_offset
    ch_end = selected_slope * last_idx + lower_offset
    change_pct = 0.0 if ch_start == 0 else ((ch_end - ch_start) / abs(ch_start)) * 100.0
    if change_pct > 2.0 and latest_close > trend_rows[0][4]:
        direction = 'uptrend'
    elif change_pct < -2.0:
        direction = 'downtrend'
    else:
        direction = 'sideways'
    return direction, change_pct


def compute_v7_2_weekly(
    stocks_by_ticker: dict[str, list[list]],
    session_dates: list[str],
    latest_bundle_date: str,
    updated_at: str,
    universe_path: Path | None = None,
) -> dict | None:
    """Compute the V7.2 Weekly Top-30 scan payload from split-adjusted [day,o,h,l,c,v] histories.
    Returns None only if SPY/sector benchmark history is insufficient (e.g. tiny synthetic unit-test fixtures).
    """
    u_path = universe_path or (Path(__file__).resolve().parent / 'v7_2_universe.json')
    if not u_path.exists():
        return None
    universe = json.loads(u_path.read_bytes())
    exchanges: dict[str, str] = universe.get('exchanges', {})
    issuer_groups: dict[str, str] = universe.get('issuerGroups', {})

    spy_full = [r[-252:] for r in [stocks_by_ticker.get('SPY', [])]][0]
    if len(spy_full) < 245 or spy_full[-1][0] != latest_bundle_date:
        return None

    last_cal = session_dates[-1] if session_dates else ''
    week_ends: set[str] = set()
    for i, cur in enumerate(session_dates):
        nxt = session_dates[i + 1] if i + 1 < len(session_dates) else None
        if not nxt or _monday_key(nxt) != _monday_key(cur):
            week_ends.add(cur)

    def is_week_end(d_str: str) -> bool:
        if d_str <= last_cal:
            return d_str in week_ends
        return date.fromisoformat(d_str).weekday() == 4

    week_end_idx = len(spy_full) - 1
    while week_end_idx >= 0 and not is_week_end(spy_full[week_end_idx][0]):
        week_end_idx -= 1
    if week_end_idx < 240:
        return None

    spy_rows = spy_full[: week_end_idx + 1]
    week_end_date = spy_rows[-1][0]
    spy_len = len(spy_rows)
    spy_dates = [r[0] for r in spy_rows]
    spy_log_rets = [math.log(spy_rows[i][4] / spy_rows[i - 1][4]) for i in range(1, spy_len)]

    sector_closes: dict[str, list[float]] = {}
    sector_rets250: dict[str, list[float]] = {}
    for sec in SECTORS:
        s_full = stocks_by_ticker.get(sec, [])[-252:]
        if len(s_full) < spy_len:
            continue
        end_pos = next((i for i, r in enumerate(s_full) if r[0] == week_end_date), -1)
        if end_pos < spy_len - 1:
            continue
        s_rows = s_full[end_pos - spy_len + 1: end_pos + 1]
        if any(s_rows[i][0] != spy_dates[i] for i in range(spy_len)):
            continue
        closes = [float(r[4]) for r in s_rows]
        lrs = [math.log(closes[i] / closes[i - 1]) for i in range(1, spy_len)]
        sector_closes[sec] = closes
        sector_rets250[sec] = lrs[-250:]

    if not sector_closes:
        return None

    spy60 = spy_log_rets[-60:]
    s_spy60 = _std(spy60)
    spy_var60 = (s_spy60 ** 2) if (math.isfinite(s_spy60) and s_spy60 > 0) else 1e-8
    s_spy_all = _std(spy_log_rets)
    spy_var_all = (s_spy_all ** 2) if (math.isfinite(s_spy_all) and s_spy_all > 0) else 1e-8
    spy12_1_sum = sum(spy_log_rets[:-21])
    mom_win = len(spy_log_rets) - 21
    sqrt_mom = math.sqrt(mom_win)
    sqrt_21 = math.sqrt(21.0)

    raw_rows: list[dict] = []
    for ticker, exch in exchanges.items():
        full_rows = stocks_by_ticker.get(ticker)
        if not full_rows:
            continue
        full_rows = full_rows[-252:]
        if len(full_rows) < spy_len:
            continue
        end_pos = next((i for i, r in enumerate(full_rows) if r[0] == week_end_date), -1)
        if end_pos < spy_len - 1:
            continue
        rows = full_rows[end_pos - spy_len + 1: end_pos + 1]
        last_close = float(rows[-1][4])
        if last_close < 5.0:
            continue

        clean = True
        for i in range(spy_len):
            r = rows[i]
            if r[0] != spy_dates[i] or not (r[4] > 0) or not (r[1] > 0):
                clean = False
                break
            if i > 0:
                ratio = r[4] / rows[i - 1][4]
                if ratio > 3.0 or ratio < (1.0 / 3.0):
                    clean = False
                    break
        if not clean:
            continue

        dv20 = sum(r[4] * r[5] for r in rows[-20:]) / 20.0
        if dv20 < 10_000_000.0:
            continue

        log_rets: list[float] = []
        on_rets: list[float] = []
        id_rets: list[float] = []
        simple_rets: list[float] = []
        for i in range(1, spy_len):
            c_cur, o_cur = rows[i][4], rows[i][1]
            c_prev = rows[i - 1][4]
            log_rets.append(math.log(c_cur / c_prev))
            on_rets.append(math.log(o_cur / c_prev))
            id_rets.append(math.log(c_cur / o_cur))
            simple_rets.append(c_cur / c_prev - 1.0)

        vol20 = _std(log_rets[-20:])
        vol60 = _std(log_rets[-60:])
        r60 = log_rets[-60:]
        beta60 = _cov(r60, spy60) / spy_var60
        idio60 = _std([r60[i] - beta60 * spy60[i] for i in range(len(r60))])
        beta252 = _cov(log_rets, spy_log_rets) / spy_var_all

        r250 = log_rets[-250:]
        best_sec = 'XLK'
        best_corr = -2.0
        for sec, er250 in sector_rets250.items():
            c_val = _corr(r250, er250)
            if math.isfinite(c_val) and c_val > best_corr:
                best_corr = c_val
                best_sec = sec
        sec_c = sector_closes[best_sec]

        resmom_12_1 = (sum(log_rets[:-21]) - beta252 * spy12_1_sum) / max(1e-4, idio60 * sqrt_mom)
        rs_ind12 = math.log(rows[spy_len - 1 - 21][4] / rows[0][4]) - math.log(sec_c[spy_len - 1 - 21] / sec_c[0])
        rs_ind12_adj = rs_ind12 / max(1e-4, idio60 * sqrt_mom)
        id_sharpe_12_1 = sum(id_rets[:-21]) / max(1e-4, vol60 * sqrt_mom)

        rev_1m = last_close / rows[spy_len - 1 - 21][4] - 1.0
        rev_1m_adj = -rev_1m / max(1e-4, vol20 * sqrt_21)
        on_1m = sum(on_rets[-21:])
        rev_on_1m_adj = -on_1m / max(1e-4, vol20 * sqrt_21)
        log_dvol20 = math.log(dv20)
        maxret21 = max(simple_rets[-21:])
        low_lotto = -maxret21 / max(1e-4, vol20)

        vals = (vol60, resmom_12_1, rs_ind12_adj, id_sharpe_12_1, rev_1m_adj, rev_on_1m_adj, log_dvol20, low_lotto)
        if any(not math.isfinite(v) for v in vals):
            continue

        raw_rows.append({
            'ticker': ticker,
            'exchange': exch,
            'close': last_close,
            'vol60': vol60,
            'rows20': rows[-20:],
            'resmom_12_1': resmom_12_1,
            'rs_ind12_adj': rs_ind12_adj,
            'id_sharpe_12_1': id_sharpe_12_1,
            'rev_1m_adj': rev_1m_adj,
            'rev_on_1m_adj': rev_on_1m_adj,
            'log_dvol20': log_dvol20,
            'low_lotto': low_lotto,
            'bestSectorEtf': best_sec,
        })

    if not raw_rows:
        return None

    sorted_by_vol = sorted(raw_rows, key=lambda r: r['vol60'])
    n_raw = len(sorted_by_vol)
    q_map = {r['ticker']: min(5, (idx * 5) // n_raw + 1) for idx, r in enumerate(sorted_by_vol)}

    scores = {r['ticker']: 0.0 for r in raw_rows}
    for q in range(1, 6):
        bucket = [r for r in raw_rows if q_map[r['ticker']] == q]
        if not bucket:
            continue
        b_len = len(bucket)
        for col in FEAT_COLS:
            ordered = sorted(bucket, key=lambda r: r[col])
            i = 0
            while i < b_len:
                j = i
                while j + 1 < b_len and ordered[j + 1][col] == ordered[i][col]:
                    j += 1
                avg_idx = (i + j) / 2.0
                pct = (avg_idx / (b_len - 1) - 0.5) if b_len > 1 else 0.0
                w = W_V7_2[col]
                for k in range(i, j + 1):
                    scores[ordered[k]['ticker']] += pct * w
                i = j + 1

    ranked_all = sorted(raw_rows, key=lambda r: scores[r['ticker']], reverse=True)
    seen_issuers: set[str] = set()
    distinct_ranked: list[dict] = []
    for r in ranked_all:
        key = issuer_groups.get(r['ticker'], r['ticker'])
        if key in seen_issuers:
            continue
        seen_issuers.add(key)
        distinct_ranked.append({**r, 'score': scores[r['ticker']]})

    total_ranked = len(distinct_ranked)
    top_limit = min(30, max(1, _js_round(total_ranked * 0.02)))

    # Schedule 20-session open-to-open hold dates from exchange calendar
    sig_idx = next((i for i, s in enumerate(session_dates) if s >= week_end_date), -1)
    if sig_idx >= 0:
        if session_dates[sig_idx] == week_end_date:
            sig_idx += 1
        entry_date = session_dates[sig_idx] if sig_idx < len(session_dates) else week_end_date
        sell_idx = min(len(session_dates) - 1, sig_idx + 20)
        planned_sell_date = session_dates[sell_idx] if session_dates else entry_date
    else:
        entry_date = week_end_date
        planned_sell_date = week_end_date

    candidates: list[dict] = []
    for idx, r in enumerate(distinct_ranked[:top_limit]):
        ch = trend_channel_summary(r['rows20'])
        if not ch or ch[0] != 'uptrend':
            continue
        overall_rank = idx + 1
        raw_fit = (1.0 - overall_rank / total_ranked) * 100.0
        fit_pct = float(f'{raw_fit:.1f}')
        ch_pct = float(f'{ch[1]:.1f}')
        score_disp = float(f"{r['score'] * 100.0:.3f}")
        rank = len(candidates) + 1
        candidates.append({
            'ticker': r['ticker'],
            'exchange': r['exchange'],
            'rank': rank,
            'overallRank': overall_rank,
            'totalRanked': total_ranked,
            'fitPct': fit_pct,
            'close': round(float(r['close']), 4),
            'channelChangePct': ch_pct,
            'score': score_disp,
            'bestSectorEtf': r['bestSectorEtf'],
            'entryDate': entry_date,
            'plannedSellDate': planned_sell_date,
            'reason': (
                f"V7.2 Weekly Top-30; Fit {fit_pct:.1f}%; Score {score_disp:.3f}; "
                f"Rank #{overall_rank}/{total_ranked} ({r['exchange']}); 15–20d uptrend +{ch_pct:.1f}%"
            ),
        })

    return {
        'schema': 1,
        'model': 'v7.2-weekly-top30',
        'dataSource': {
            'provider': 'github',
            'session': latest_bundle_date,
            'updatedAt': updated_at,
        },
        'asOfDate': week_end_date,
        'latestBundleDate': latest_bundle_date,
        'totalLiquid': total_ranked,
        'nasdaqLiquid': sum(1 for r in distinct_ranked if r['exchange'] == 'NASDAQ'),
        'candidates': candidates,
    }
