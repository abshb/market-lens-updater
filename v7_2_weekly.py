"""Precompute the V7.2 Weekly Top-30 stock scan (current + past 52 weeks) during Massive GitHub cache publication."""
from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path

SECTORS = ('XLB', 'XLC', 'XLE', 'XLF', 'XLI', 'XLK', 'XLP', 'XLRE', 'XLU', 'XLV', 'XLY')
STOP_LOSS_PCT = 12.0
STOP_LOSS_MULT = 1.0 - STOP_LOSS_PCT / 100.0

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


def _prefix(arr: list[float]) -> list[float]:
    out = [0.0] * (len(arr) + 1)
    acc = 0.0
    for i, v in enumerate(arr):
        acc += v
        out[i + 1] = acc
    return out


def _range_var(ps: list[float], ps2: list[float], a: int, b: int) -> float:
    n = b - a
    if n < 2:
        return math.nan
    s = ps[b] - ps[a]
    s2 = ps2[b] - ps2[a]
    v = (s2 - (s * s) / n) / (n - 1)
    return v if v > 0.0 else 0.0


def _range_cov(ps_x: list[float], ps_y: list[float], ps_xy: list[float], a: int, b: int) -> float:
    n = b - a
    if n < 2:
        return math.nan
    sx = ps_x[b] - ps_x[a]
    sy = ps_y[b] - ps_y[a]
    sxy = ps_xy[b] - ps_xy[a]
    return (sxy - (sx * sy) / n) / (n - 1)


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
    elif change_pct < -2.0 and latest_close < trend_rows[0][4]:
        direction = 'downtrend'
    else:
        direction = 'sideways'
    return direction, change_pct


def _evaluate_post_signal(
    full_rows: list[list],
    signal_date: str,
    planned_sell_date: str,
    suggested_close: float,
) -> dict:
    """Evaluate post-signal open entry, -12% hard stop, 20-session hold, and current price/trend."""
    last_close = round(float(full_rows[-1][4]), 4) if full_rows else round(suggested_close, 4)
    cur_ch = trend_channel_summary(full_rows[-20:]) if len(full_rows) >= 15 else None
    current_trend = cur_ch[0] if cur_ch else None

    after = [r for r in full_rows if r[0] > signal_date]
    if not after:
        return {
            'lastClose': last_close,
            'currentTrend': current_trend,
            'entryOpen': None,
            'stopPrice': round(suggested_close * STOP_LOSS_MULT, 2),
            'sessionsHeld': 0,
            'exit': None,
        }

    raw_entry = float(after[0][1])
    raw_stop = raw_entry * STOP_LOSS_MULT
    entry_open = round(raw_entry, 4)
    stop_price = round(raw_stop, 2)

    hold_window = min(len(after), 20)
    for i in range(hold_window):
        bar = after[i]
        if float(bar[3]) <= raw_stop:
            fill = float(bar[1]) if float(bar[1]) <= raw_stop else raw_stop
            return {
                'lastClose': last_close,
                'currentTrend': current_trend,
                'entryOpen': entry_open,
                'stopPrice': stop_price,
                'sessionsHeld': i + 1,
                'exit': {
                    'kind': 'stop_loss',
                    'signalDate': bar[0],
                    'sellDate': bar[0],
                    'reason': f'تفعيل وقف الخسارة (-{int(STOP_LOSS_PCT)}%)',
                    'exitOpen': round(fill, 4),
                },
            }

    if len(after) >= 21:
        exit_bar = after[20]
        return {
            'lastClose': last_close,
            'currentTrend': current_trend,
            'entryOpen': entry_open,
            'stopPrice': stop_price,
            'sessionsHeld': 20,
            'exit': {
                'kind': 'horizon',
                'signalDate': after[19][0],
                'sellDate': exit_bar[0],
                'reason': 'انتهاء مدة الاحتفاظ (20 جلسة تداول)',
                'exitOpen': round(float(exit_bar[1]), 4),
            },
        }

    if len(after) == 20:
        return {
            'lastClose': last_close,
            'currentTrend': current_trend,
            'entryOpen': entry_open,
            'stopPrice': stop_price,
            'sessionsHeld': 20,
            'exit': {
                'kind': 'horizon',
                'signalDate': after[19][0],
                'sellDate': planned_sell_date,
                'reason': 'انتهاء مدة الاحتفاظ (20 جلسة تداول)',
                'exitOpen': None,
            },
        }

    return {
        'lastClose': last_close,
        'currentTrend': current_trend,
        'entryOpen': entry_open,
        'stopPrice': stop_price,
        'sessionsHeld': len(after),
        'exit': None,
    }


def compute_v7_2_weekly(
    stocks_by_ticker: dict[str, list[list]],
    session_dates: list[str],
    latest_bundle_date: str,
    updated_at: str,
    universe_path: Path | None = None,
    history_weeks: int = 52,
) -> dict | None:
    """Compute the V7.2 Weekly Top-30 scan payload (current week + up to `history_weeks` past weeks).
    Returns None only if SPY/sector benchmark history is insufficient (e.g. tiny synthetic unit-test fixtures).
    """
    u_path = universe_path or (Path(__file__).resolve().parent / 'v7_2_universe.json')
    if not u_path.exists():
        return None
    universe = json.loads(u_path.read_bytes())
    exchanges: dict[str, str] = universe.get('exchanges', {})
    issuer_groups: dict[str, str] = universe.get('issuerGroups', {})

    spy_all = stocks_by_ticker.get('SPY', [])[-530:]
    m_len = len(spy_all)
    if m_len < 245 or spy_all[-1][0] != latest_bundle_date:
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

    week_end_indices: list[int] = []
    for idx in range(m_len - 1, -1, -1):
        if idx < 240:
            break
        if is_week_end(spy_all[idx][0]):
            week_end_indices.append(idx)
            if len(week_end_indices) >= history_weeks:
                break

    if not week_end_indices:
        return None

    spy_dates = [r[0] for r in spy_all]
    spy_lr = [math.log(spy_all[i + 1][4] / spy_all[i][4]) for i in range(m_len - 1)]
    spy_lr_ps = _prefix(spy_lr)
    spy_lr2_ps = _prefix([v * v for v in spy_lr])

    # Precompute aligned sector ETF arrays across spy_dates
    sec_data: dict[str, tuple[list[int], list[float], list[float], list[float], list[float]]] = {}
    for sec in SECTORS:
        s_raw = stocks_by_ticker.get(sec, [])[-540:]
        if not s_raw:
            continue
        by_d = {r[0]: r for r in s_raw}
        aligned = [by_d.get(d) for d in spy_dates]
        run_len = [0] * m_len
        closes = [0.0] * m_len
        lr = [0.0] * (m_len - 1)
        for i, row in enumerate(aligned):
            if row is None or not (row[1] > 0 and row[4] > 0):
                continue
            c_val = float(row[4])
            closes[i] = c_val
            if i == 0 or run_len[i - 1] == 0:
                run_len[i] = 1
            else:
                run_len[i] = run_len[i - 1] + 1
                lr[i - 1] = math.log(c_val / closes[i - 1])
        sec_data[sec] = (
            run_len,
            closes,
            lr,
            _prefix(lr),
            _prefix([v * v for v in lr]),
        )

    if not sec_data:
        return None

    # Precompute aligned stock series & prefix sums once per ticker
    prepared_stocks: list[dict] = []
    for ticker, exch in exchanges.items():
        full_rows = stocks_by_ticker.get(ticker)
        if not full_rows:
            continue
        recent_rows = full_rows[-540:]
        if len(recent_rows) < 241:
            continue
        by_d = {r[0]: r for r in recent_rows}
        aligned = [by_d.get(d) for d in spy_dates]
        run_len = [0] * m_len
        closes = [0.0] * m_len
        dv = [0.0] * m_len
        lr = [0.0] * (m_len - 1)
        on_r = [0.0] * (m_len - 1)
        id_r = [0.0] * (m_len - 1)
        sr = [0.0] * (m_len - 1)
        max_run = 0
        for i, row in enumerate(aligned):
            if row is None or not (row[1] > 0 and row[4] > 0):
                continue
            o_val = float(row[1])
            c_val = float(row[4])
            closes[i] = c_val
            dv[i] = c_val * float(row[5])
            if i == 0 or run_len[i - 1] == 0:
                run_len[i] = 1
            else:
                ratio = c_val / closes[i - 1]
                if ratio > 3.0 or ratio < (1.0 / 3.0):
                    run_len[i] = 1
                else:
                    run_len[i] = run_len[i - 1] + 1
                    lr[i - 1] = math.log(c_val / closes[i - 1])
                    on_r[i - 1] = math.log(o_val / closes[i - 1])
                    id_r[i - 1] = math.log(c_val / o_val)
                    sr[i - 1] = ratio - 1.0
            if run_len[i] > max_run:
                max_run = run_len[i]
        if max_run < 241:
            continue

        lr_ps = _prefix(lr)
        lr2_ps = _prefix([v * v for v in lr])
        lr_spy_ps = _prefix([lr[i] * spy_lr[i] for i in range(m_len - 1)])
        lr_sec_ps = {
            sec: _prefix([lr[i] * s_tup[2][i] for i in range(m_len - 1)])
            for sec, s_tup in sec_data.items()
        }
        prepared_stocks.append({
            'ticker': ticker,
            'exchange': exch,
            'full_rows': recent_rows,
            'aligned': aligned,
            'run_len': run_len,
            'closes': closes,
            'dv_ps': _prefix(dv),
            'lr_ps': lr_ps,
            'lr2_ps': lr2_ps,
            'on_ps': _prefix(on_r),
            'id_ps': _prefix(id_r),
            'sr': sr,
            'lr_spy_ps': lr_spy_ps,
            'lr_sec_ps': lr_sec_ps,
        })

    if not prepared_stocks:
        return None

    sqrt_21 = math.sqrt(21.0)
    cal_index_by_date = {d: i for i, d in enumerate(session_dates)}
    weeks_out: list[dict] = []

    for week_pos, end_idx in enumerate(week_end_indices):
        # Preserve exact window on current week (`spy_all[-252:]` snapped to Friday) and 252-bar windows on prior weeks
        start_idx = max(0, (m_len - 252) if week_pos == 0 else (end_idx + 1 - 252))
        spy_len = end_idx - start_idx + 1
        if spy_len < 241:
            continue
        week_end_date = spy_dates[end_idx]
        ret_start = start_idx
        ret_end = end_idx  # exclusive index in lr (length spy_len - 1)
        n_ret = ret_end - ret_start  # spy_len - 1

        avail_secs = [
            sec for sec, s_tup in sec_data.items()
            if s_tup[0][end_idx] >= spy_len
        ]
        if not avail_secs:
            continue

        r60_start = ret_end - 60
        r20_start = ret_end - 20
        r21_start = ret_end - 21
        r250_start = max(ret_start, ret_end - 250)

        spy_var60 = _range_var(spy_lr_ps, spy_lr2_ps, r60_start, ret_end)
        if not (math.isfinite(spy_var60) and spy_var60 > 0.0):
            spy_var60 = 1e-8
        spy_var_all = _range_var(spy_lr_ps, spy_lr2_ps, ret_start, ret_end)
        if not (math.isfinite(spy_var_all) and spy_var_all > 0.0):
            spy_var_all = 1e-8

        spy12_1_sum = spy_lr_ps[r21_start] - spy_lr_ps[ret_start]
        mom_win = n_ret - 21
        sqrt_mom = math.sqrt(mom_win)

        sec_var250: dict[str, float] = {}
        for sec in avail_secs:
            s_tup = sec_data[sec]
            sv = _range_var(s_tup[3], s_tup[4], r250_start, ret_end)
            sec_var250[sec] = sv if (math.isfinite(sv) and sv > 0.0) else 0.0

        raw_rows: list[dict] = []
        for st in prepared_stocks:
            if st['run_len'][end_idx] < spy_len:
                continue
            closes = st['closes']
            last_close = closes[end_idx]
            if last_close < 5.0:
                continue
            dv_ps = st['dv_ps']
            dv20 = (dv_ps[end_idx + 1] - dv_ps[end_idx + 1 - 20]) / 20.0
            if dv20 < 10_000_000.0:
                continue

            lr_ps = st['lr_ps']
            lr2_ps = st['lr2_ps']
            lr_spy_ps = st['lr_spy_ps']

            var20 = _range_var(lr_ps, lr2_ps, r20_start, ret_end)
            var60 = _range_var(lr_ps, lr2_ps, r60_start, ret_end)
            vol20 = math.sqrt(var20) if math.isfinite(var20) else math.nan
            vol60 = math.sqrt(var60) if math.isfinite(var60) else math.nan

            cov60 = _range_cov(lr_ps, spy_lr_ps, lr_spy_ps, r60_start, ret_end)
            beta60 = cov60 / spy_var60
            # Var(r - beta * spy) = Var(r) - 2*beta*Cov(r, spy) + beta^2*Var(spy)
            idio_var60 = max(0.0, var60 - 2.0 * beta60 * cov60 + (beta60 * beta60) * spy_var60)
            idio60 = math.sqrt(idio_var60)

            cov252 = _range_cov(lr_ps, spy_lr_ps, lr_spy_ps, ret_start, ret_end)
            beta252 = cov252 / spy_var_all

            var250 = _range_var(lr_ps, lr2_ps, r250_start, ret_end)
            best_sec = 'XLK' if 'XLK' in avail_secs else avail_secs[0]
            best_corr = -2.0
            if var250 > 0.0:
                lr_sec_ps = st['lr_sec_ps']
                for sec in avail_secs:
                    sv = sec_var250[sec]
                    if sv <= 0.0:
                        continue
                    s_tup = sec_data[sec]
                    c_sec = _range_cov(lr_ps, s_tup[3], lr_sec_ps[sec], r250_start, ret_end)
                    c_val = c_sec / math.sqrt(var250 * sv)
                    if math.isfinite(c_val) and c_val > best_corr:
                        best_corr = c_val
                        best_sec = sec

            sec_closes = sec_data[best_sec][1]
            c_m1 = closes[end_idx - 21]
            resmom_12_1 = ((lr_ps[r21_start] - lr_ps[ret_start]) - beta252 * spy12_1_sum) / max(1e-4, idio60 * sqrt_mom)
            rs_ind12 = math.log(c_m1 / closes[start_idx]) - math.log(sec_closes[end_idx - 21] / sec_closes[start_idx])
            rs_ind12_adj = rs_ind12 / max(1e-4, idio60 * sqrt_mom)
            id_ps = st['id_ps']
            id_sharpe_12_1 = (id_ps[r21_start] - id_ps[ret_start]) / max(1e-4, vol60 * sqrt_mom)

            rev_1m = last_close / c_m1 - 1.0
            rev_1m_adj = -rev_1m / max(1e-4, vol20 * sqrt_21)
            on_ps = st['on_ps']
            on_1m = on_ps[ret_end] - on_ps[r21_start]
            rev_on_1m_adj = -on_1m / max(1e-4, vol20 * sqrt_21)
            log_dvol20 = math.log(dv20)
            maxret21 = max(st['sr'][r21_start:ret_end])
            low_lotto = -maxret21 / max(1e-4, vol20)

            vals = (vol60, resmom_12_1, rs_ind12_adj, id_sharpe_12_1, rev_1m_adj, rev_on_1m_adj, log_dvol20, low_lotto)
            if any(not math.isfinite(v) for v in vals):
                continue

            raw_rows.append({
                'st': st,
                'ticker': st['ticker'],
                'exchange': st['exchange'],
                'close': last_close,
                'vol60': vol60,
                'end_idx': end_idx,
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
            continue

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
        sig_idx = cal_index_by_date.get(week_end_date, -1)
        if sig_idx < 0:
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
            rows20 = r['st']['aligned'][end_idx + 1 - 20: end_idx + 1]
            ch = trend_channel_summary(rows20)
            if not ch or ch[0] != 'uptrend':
                continue
            overall_rank = idx + 1
            raw_fit = (1.0 - overall_rank / total_ranked) * 100.0
            fit_pct = float(f'{raw_fit:.1f}')
            ch_pct = float(f'{ch[1]:.1f}')
            score_disp = float(f"{r['score'] * 100.0:.3f}")
            close_val = round(float(r['close']), 4)
            est_stop = round(close_val * STOP_LOSS_MULT, 2)
            rank = len(candidates) + 1
            post = _evaluate_post_signal(
                r['st']['full_rows'],
                week_end_date,
                planned_sell_date,
                close_val,
            )
            candidates.append({
                'ticker': r['ticker'],
                'exchange': r['exchange'],
                'signalDate': week_end_date,
                'rank': rank,
                'overallRank': overall_rank,
                'totalRanked': total_ranked,
                'fitPct': fit_pct,
                'close': close_val,
                'stopLossPct': STOP_LOSS_PCT,
                'estStopPrice': est_stop,
                'channelChangePct': ch_pct,
                'score': score_disp,
                'bestSectorEtf': r['bestSectorEtf'],
                'entryDate': entry_date,
                'plannedSellDate': planned_sell_date,
                'lastClose': post['lastClose'],
                'currentTrend': post['currentTrend'],
                'entryOpen': post['entryOpen'],
                'stopPrice': post['stopPrice'],
                'sessionsHeld': post['sessionsHeld'],
                'exit': post['exit'],
                'reason': (
                    f"V7.2 Weekly Top-30; Fit {fit_pct:.1f}%; Score {score_disp:.3f}; "
                    f"Rank #{overall_rank}/{total_ranked} ({r['exchange']}); 15–20d uptrend +{ch_pct:.1f}%; "
                    f"Hard stop -{int(STOP_LOSS_PCT)}%"
                ),
            })

        weeks_out.append({
            'asOfDate': week_end_date,
            'entryDate': entry_date,
            'plannedSellDate': planned_sell_date,
            'totalLiquid': total_ranked,
            'nasdaqLiquid': sum(1 for r in distinct_ranked if r['exchange'] == 'NASDAQ'),
            'candidates': candidates,
        })

    if not weeks_out:
        return None

    current_week = weeks_out[0]
    return {
        'schema': 1,
        'model': 'v7.2-weekly-top30',
        'stopLossPct': STOP_LOSS_PCT,
        'dataSource': {
            'provider': 'github',
            'session': latest_bundle_date,
            'updatedAt': updated_at,
        },
        'asOfDate': current_week['asOfDate'],
        'latestBundleDate': latest_bundle_date,
        'totalLiquid': current_week['totalLiquid'],
        'nasdaqLiquid': current_week['nasdaqLiquid'],
        'candidates': current_week['candidates'],
        'weeks': weeks_out,
    }
