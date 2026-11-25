"""HTML Dashboard generator (v4.1 — Clean Modern UX) with multi-currency, live FX, Macro Radar, 4Q trends, and Backtest tab."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import html as html_lib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from .config import DEFAULT_FX_USD_THB, ROOT_DIR

THAI_TZ = timezone(timedelta(hours=7))


def _esc(val: Any) -> str:
    return html_lib.escape(str(val if val is not None else ""), quote=True)


def _fmt_compact_money(val: float | None, cur: str) -> str:
    if val is None:
        return "N/A"
    abs_v = abs(val)
    sign = "-" if val < 0 else ""
    if abs_v >= 1e9:
        return f"{sign}{cur}{abs_v / 1e9:.2f}B"
    if abs_v >= 1e6:
        return f"{sign}{cur}{abs_v / 1e6:.1f}M"
    return f"{sign}{cur}{abs_v:,.0f}"


def _format_cio_meta_badge(cio: dict[str, Any]) -> str:
    meta = cio.get("_meta") or {}
    if not meta:
        return ""
    if meta.get("cached"):
        age_h = meta.get("age_hours", 0.0)
        age_txt = f"{age_h:.1f} ชม. ที่แล้ว" if age_h < 24 else f"{age_h / 24.0:.1f} วัน ที่แล้ว"
        return f'<span class="cio-meta-pill">อัปเดต {age_txt} · รีเฟรชเมื่อครบ 7 วันหรือราคาขยับ ±8%</span>'
    return '<span class="cio-meta-pill fresh">✨ วิเคราะห์ใหม่ล่าสุด</span>'


def generate_html_report(
    results: list[dict[str, Any]],
    ai_weights: dict[str, float],
    signals_by_ticker: dict[str, list[dict]],
    fund_name: str | None,
    fx_usd_thb: float = DEFAULT_FX_USD_THB,
    macro: dict[str, Any] | None = None,
) -> Path:
    cards_html = []
    screener_rows_html = []
    chart_configs = []

    results_sorted = sorted(results, key=lambda x: x["dip_score"], reverse=True)

    for idx, info in enumerate(results_sorted):
        tkr = _esc(info["ticker"])
        tkr_raw = str(info["ticker"])
        comp_esc = _esc(info["company_name"])
        comp_short_esc = _esc(str(info["company_name"])[:22])
        cur = _esc(info.get("currency_symbol") or "$")
        cur_code = str(info.get("currency") or "USD")
        chart_id = f"tv_chart_{idx}"
        sigs = signals_by_ticker.get(tkr_raw, [])
        cio = info.get("cio_advice") or {}
        hit_label_esc = _esc(info["hit_label"])
        dca_mult_esc = _esc(info["dca_mult"])

        fpe_val = info["forward_pe"] or 999.0
        peg_val = info["peg_ratio"] or 999.0
        upside_val = info["wall_st_upside"] if info["wall_st_upside"] is not None else -999.0

        fpe_str = f"{info['forward_pe']:.1f}x" if info["forward_pe"] else "N/A"
        peg_str = f"{info['peg_ratio']:.2f}" if info["peg_ratio"] else "N/A"
        roe_str = f"{info['roe']*100:.1f}%" if info["roe"] else "N/A"
        rev_g = f"{info['rev_growth']*100:+.1f}%" if info["rev_growth"] is not None else "N/A"
        earn_g = f"{info['earn_growth']*100:+.1f}%" if info["earn_growth"] is not None else "N/A"
        ws_str = (
            f"{cur}{info['target_mean']:.2f} ({info['wall_st_upside']:+.1f}%)"
            if info["target_mean"] and info["wall_st_upside"] is not None
            else "N/A"
        )

        hit_class = "badge-hit" if info["hit_status"] in ("hit_t1", "hit_t2") else "badge-wait"
        is_peg_value = "1" if (info["peg_ratio"] and 0 < info["peg_ratio"] <= 1.25) else "0"
        is_hit = "1" if info["hit_status"] in ("hit_t1", "hit_t2") else "0"
        is_high_score = "1" if info["dip_score"] >= 80 else "0"

        score_pill_cls = (
            "score-high" if info["dip_score"] >= 80
            else ("score-mid" if info["dip_score"] >= 65 else "score-low")
        )

        range_bar_html = f"""
        <div class="range-wrap" title="จุดต่ำสุด 1 ปี: {cur}{info['low_52w']:.2f} | ปัจจุบัน: {cur}{info['close']:.2f} | สูงสุด 1 ปี: {cur}{info['high_52w']:.2f}">
            <div class="range-labels"><span>{cur}{info['low_52w']:.0f}</span><span>{cur}{info['high_52w']:.0f}</span></div>
            <div class="range-track">
                <div class="range-fill" style="width:{info['pos_52w_pct']}%;"></div>
                <div class="range-dot" style="left:{info['pos_52w_pct']}%;"></div>
            </div>
        </div>
        """

        nxt_ed = info.get("next_earnings_date")
        days_ed = info.get("days_to_earnings")
        earnings_badge_html = ""
        if nxt_ed and days_ed is not None and 0 <= days_ed <= 14:
            earnings_badge_html = f'<span class="badge-warn">⚠️ งบใน {days_ed} วัน</span>'
        earnings_metric_str = (
            f"{_esc(nxt_ed)} (อีก {days_ed} วัน)"
            if (nxt_ed and days_ed is not None)
            else "N/A"
        )

        eps_list = info.get("eps_surprises") or []
        eps_beats = sum(1 for e in eps_list if e.get("beat") is True)
        eps_badge_str = f"Beat {eps_beats}/{len(eps_list)} ไตรมาส" if eps_list else "N/A"

        q_trend = info.get("quarterly_trend") or []
        q_rows_html = ""
        for q in q_trend:
            m_str = f"{q['net_margin_pct']:.1f}%" if q.get("net_margin_pct") is not None else "N/A"
            q_rows_html += f"""
            <tr>
                <td>{_esc(q.get('date'))}</td>
                <td>{_fmt_compact_money(q.get('revenue'), cur)}</td>
                <td class="{'green' if (q.get('net_income') or 0) > 0 else 'red'}">{_fmt_compact_money(q.get('net_income'), cur)}</td>
                <td>{m_str}</td>
            </tr>
            """
        eps_rows_html = ""
        for e in eps_list:
            b_txt = '<span class="green">✅ Beat</span>' if e.get("beat") else '<span class="red">❌ Miss</span>'
            s_txt = f"{e['surprise_pct']:+.1f}%" if e.get("surprise_pct") is not None else "N/A"
            eps_rows_html += f"""
            <tr>
                <td>{_esc(e.get('date'))}</td>
                <td>{e.get('estimate') if e.get('estimate') is not None else 'N/A'}</td>
                <td><strong>{e.get('actual') if e.get('actual') is not None else 'N/A'}</strong></td>
                <td>{b_txt} ({s_txt})</td>
            </tr>
            """
        quarterly_section = ""
        if q_rows_html or eps_rows_html:
            q_part = f"""
                <div class="sub-panel">
                    <div class="sub-panel-title">📊 งบย้อนหลัง 4 ไตรมาส (Revenue &amp; Net Income)</div>
                    <table><thead><tr><th>งวด</th><th>รายได้</th><th>กำไรสุทธิ</th><th>Margin</th></tr></thead><tbody>{q_rows_html}</tbody></table>
                </div>
            """ if q_rows_html else ""
            e_part = f"""
                <div class="sub-panel">
                    <div class="sub-panel-title">🎯 ประวัติ EPS Beat/Miss ({eps_badge_str})</div>
                    <table><thead><tr><th>งวด</th><th>คาดการณ์</th><th>ทำได้จริง</th><th>ผลลัพธ์</th></tr></thead><tbody>{eps_rows_html}</tbody></table>
                </div>
            """ if eps_rows_html else ""
            quarterly_section = f'<div class="two-col collapsible-section">{q_part}{e_part}</div>'

        screener_rows_html.append(f"""
        <tr class="screener-row" data-ticker="{tkr}" data-name="{comp_esc.lower()}" data-score="{is_high_score}" data-hit="{is_hit}" data-peg="{is_peg_value}" onclick="openStockDeepDive('{tkr}')" title="คลิกเพื่อดูกราฟและแผนช้อนซื้อของ {tkr}">
            <td>
                <div class="cell-ticker">{tkr} {earnings_badge_html}</div>
                <div class="cell-sub">{comp_short_esc}</div>
            </td>
            <td data-val="{info['close']}">
                <div class="cell-price">{cur}{info['close']:.2f}</div>
                <div class="cell-dd">{info['drawdown_pct']:.1f}% จาก High</div>
            </td>
            <td data-val="{info['pos_52w_pct']}">{range_bar_html}</td>
            <td data-val="{1 if is_hit == '1' else 0}"><span class="{hit_class}">{hit_label_esc}</span></td>
            <td data-val="{info['dip_score']}"><span class="score-pill {score_pill_cls}">{info['dip_score']}</span></td>
            <td data-val="{info['rsi']}" class="{'green' if info['rsi'] < 40 else ''}"><strong>{info['rsi']:.1f}</strong></td>
            <td data-val="{fpe_val}">{fpe_str}</td>
            <td data-val="{peg_val}" class="{'green' if is_peg_value == '1' else ''}"><strong>{peg_str}</strong></td>
            <td data-val="{upside_val}" class="{'green' if upside_val > 20 else ''}">{ws_str}</td>
            <td class="tiers-cell">
                <span class="t1">{cur}{info['tier1_price']:.2f}</span> /
                <span class="t2">{cur}{info['tier2_price']:.2f}</span> /
                <span class="t3">{cur}{info['tier3_price']:.2f}</span>
            </td>
        </tr>
        """)

        sig_rows = ""
        for s in sigs:
            val = float(s.get("value") or 0.0)
            if val > 0.1:
                v_badge = f'<span class="green">▲ มองบวก ({abs(val)*100:.0f}%)</span>'
            elif val < -0.1:
                v_badge = f'<span class="amber">▼ ราคาตึงตัว ({abs(val)*100:.0f}%)</span>'
            else:
                v_badge = f'<span class="muted">- เป็นกลาง ({abs(val)*100:.0f}%)</span>'
            sig_rows += f"""
            <div class="analyst-item">
                <div class="analyst-head">{_esc(str(s.get('model_name', '')).upper())} &middot; {v_badge}</div>
                <div class="analyst-body">{_esc(s.get('reasoning', ''))}</div>
            </div>
            """
        analysts_section = f"""
        <details class="analysts-box">
            <summary>ดูความเห็นต้นฉบับจากทีมนักวิเคราะห์ AI รายบุคคล</summary>
            <div class="analysts-list">{sig_rows}</div>
        </details>
        """ if sig_rows else ""

        chart_configs.append({
            "id": chart_id,
            "ticker": tkr_raw,
            "company_name": info["company_name"],
            "currency": cur_code,
            "currency_symbol": info.get("currency_symbol") or "$",
            "close": round(info["close"], 2),
            "dip_score": info["dip_score"],
            "dca_mult_num": float(info.get("dca_mult_num") or (2.0 if info["dip_score"] >= 82 else (1.5 if info["dip_score"] >= 68 else (1.0 if info["dip_score"] >= 48 else 0.5)))),
            "ai_weight": float(ai_weights.get(tkr_raw) or 0.0),
            "upside": round(info["wall_st_upside"], 1) if info["wall_st_upside"] is not None else 15.0,
            "candles": info["candles"],
            "ema50": info["ema50_series"],
            "ema200": info["ema200_series"],
            "markers": info["markers"],
            "tier1": info["tier1_price"],
            "tier2": info["tier2_price"],
            "tier3": info["tier3_price"],
            "target_mean": round(info["target_mean"], 2) if info["target_mean"] else None,
        })

        cio_meta_badge = _format_cio_meta_badge(cio)

        cards_html.append(f"""
        <article class="card stock-card" id="stock-{tkr}" data-ticker="{tkr}" data-score="{is_high_score}" data-hit="{is_hit}" data-peg="{is_peg_value}">
            <div class="card-header">
                <div class="card-title-group">
                    <span class="ticker">{tkr}</span>
                    <span class="comp-name">{comp_esc}</span>
                    <span class="price">{cur}{info['close']:.2f}</span>
                    <span class="discount">{info['drawdown_pct']:.1f}% จาก High</span>
                    <span class="{hit_class}">{hit_label_esc}</span>
                    {earnings_badge_html}
                </div>
                <div class="card-actions">
                    <span class="score-pill-lg {score_pill_cls}">
                        Score {info['dip_score']}/100 &middot; {dca_mult_esc}
                    </span>
                    <button class="btn-remove" onclick="removeStock('{tkr}')" title="ลบหุ้น {tkr} ออกจากพอร์ต">✕ ลบ</button>
                </div>
            </div>

            <div class="cio-box">
                <div class="cio-header-row">
                    <span class="cio-title">บทวิเคราะห์ AI Chief Investment Officer</span>
                    {cio_meta_badge}
                </div>
                <div class="cio-headline">{_esc(cio.get('verdict_headline', ''))}</div>
                <div class="cio-grid">
                    <div class="cio-item">
                        <strong>🔍 โอกาสช้อน (Dip) หรือ กับดัก (Trap)?</strong>
                        <p>{_esc(cio.get('dip_vs_trap_analysis', ''))}</p>
                    </div>
                    <div class="cio-item">
                        <strong>💎 มูลค่า &amp; Upside เทียบเป้า Wall Street</strong>
                        <p>{_esc(cio.get('valuation_and_upside', ''))}</p>
                    </div>
                    <div class="cio-item">
                        <strong>🎯 แผนเคาะซื้อ &amp; แบ่งไม้สะสม</strong>
                        <p>{_esc(cio.get('actionable_dca_playbook', ''))}</p>
                    </div>
                    <div class="cio-item">
                        <strong>⚠️ ความเสี่ยงที่ต้องจับตา</strong>
                        <p>{_esc(cio.get('key_risk_to_watch', ''))}</p>
                    </div>
                </div>
            </div>

            <div class="metrics-grid">
                <div class="metric"><span class="label">เป้า Wall Street ({info['num_analysts']} สำนัก)</span><span class="val green">{ws_str}</span></div>
                <div class="metric"><span class="label">Fwd P/E &middot; PEG</span><span class="val cyan">{fpe_str} &middot; {peg_str}</span></div>
                <div class="metric"><span class="label">โต รายได้ / กำไร (YoY)</span><span class="val">{rev_g} / {earn_g}</span></div>
                <div class="metric"><span class="label">ROE &middot; EMA 200</span><span class="val">{roe_str} &middot; {cur}{info['ema200']:.2f} ({info['dist_ema200_pct']:+.1f}%)</span></div>
                <div class="metric"><span class="label">Volume Node</span><span class="val green">{cur}{info['vp_node_1']:.2f}</span></div>
                <div class="metric"><span class="label">RSI (14 วัน)</span><span class="val {'green' if info['rsi'] < 40 else ''}">{info['rsi']:.1f} {'(Oversold)' if info['rsi'] < 35 else ''}</span></div>
                <div class="metric"><span class="label">📅 วันประกาศงบถัดไป</span><span class="val" style="color:{'#fbbf24' if (days_ed is not None and days_ed <= 14) else '#e2e8f0'}">{earnings_metric_str}</span></div>
                <div class="metric"><span class="label">🎯 ประวัติ EPS Surprise</span><span class="val green">{eps_badge_str}</span></div>
            </div>

            {quarterly_section}

            <div class="chart-wrapper collapsible-section">
                <div class="chart-legend">
                    <span><i style="background:#f59e0b"></i> EMA 50 ({cur}{info['ema50']:.2f})</span>
                    <span><i style="background:#a855f7"></i> EMA 200 ({cur}{info['ema200']:.2f})</span>
                    <span><i style="background:#4ade80"></i> ไม้ 1: {cur}{info['tier1_price']:.2f}</span>
                    <span><i style="background:#22c55e"></i> ไม้ 2: {cur}{info['tier2_price']:.2f}</span>
                    <span><i style="background:#eab308"></i> ไม้ 3: {cur}{info['tier3_price']:.2f}</span>
                </div>
                <div id="{chart_id}" class="tv-chart"></div>
            </div>

            <div class="collapsible-section" style="overflow-x:auto;">
                <table>
                    <thead>
                        <tr>
                            <th>บันไดตั้งรับ 3 ไม้ (Buy the Dip Ladder)</th>
                            <th>ราคาตั้งรับ</th>
                            <th>ส่วนลดจาก High</th>
                            <th>สัดส่วนงบ</th>
                            <th>จุดอ้างอิงเชิงเทคนิค</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td><strong class="t1">ไม้ที่ 1: โซนสะสมแรก</strong></td>
                            <td class="green"><strong>{cur}{info['tier1_price']:.2f}</strong></td>
                            <td>{info['tier1_disc']:.1f}%</td>
                            <td><strong>30%</strong></td>
                            <td>Volume Node ({cur}{info['vp_node_1']:.2f}) / Fib 38.2% ({cur}{info['fib_382']:.2f})</td>
                        </tr>
                        <tr>
                            <td><strong class="t2">ไม้ที่ 2: โซนคุณค่าหลัก</strong></td>
                            <td class="green"><strong>{cur}{info['tier2_price']:.2f}</strong></td>
                            <td>{info['tier2_disc']:.1f}%</td>
                            <td><strong>40% (ไม้หลัก)</strong></td>
                            <td>EMA 200 ({cur}{info['ema200']:.2f}) / Fib 50% ({cur}{info['fib_500']:.2f})</td>
                        </tr>
                        <tr>
                            <td><strong class="t3">ไม้ที่ 3: โซนลดราคาพิเศษ</strong></td>
                            <td class="amber"><strong>{cur}{info['tier3_price']:.2f}</strong></td>
                            <td>{info['tier3_disc']:.1f}%</td>
                            <td><strong>30% สุดท้าย</strong></td>
                            <td>Fib 61.8% ({cur}{info['fib_618']:.2f}) / ใกล้ Low 1 ปี ({cur}{info['low_52w']:.2f})</td>
                        </tr>
                    </tbody>
                </table>
            </div>
            {analysts_section}
        </article>
        """)

    nav_pills = "".join(
        f'<button class="stock-pick-btn" data-pick="{_esc(r["ticker"])}" onclick="openStockDeepDive(\'{_esc(r["ticker"])}\')">{_esc(r["ticker"])} <small>{r["dip_score"]}</small></button>'
        for r in results_sorted
    )
    top3_cards_html = ""
    for rank, r in enumerate(results_sorted[:3], 1):
        rtkr = _esc(r["ticker"])
        rcur = _esc(r.get("currency_symbol") or "$")
        cio_h = _esc((r.get("cio_advice") or {}).get("verdict_headline", ""))
        fpe_s = f"{r['forward_pe']:.1f}x" if r["forward_pe"] else "N/A"
        peg_s = f"{r['peg_ratio']:.2f}" if r["peg_ratio"] else "N/A"
        up_s = f"{r['wall_st_upside']:+.1f}%" if r["wall_st_upside"] is not None else "N/A"
        top3_cards_html += f"""
        <div class="top3-card" onclick="openStockDeepDive('{rtkr}')">
          <div class="top3-head">
            <div style="display:flex;align-items:center;gap:8px;">
              <span class="rank-badge">#{rank}</span>
              <strong style="font-size:20px;color:#fff;">{rtkr}</strong>
              <span class="top3-price">{rcur}{r['close']:.2f}</span>
            </div>
            <span class="score-pill score-high">{r['dip_score']}/100</span>
          </div>
          <div class="top3-meta">
            <span>{r['drawdown_pct']:.1f}% จาก High</span> &middot;
            <span>Fwd P/E {fpe_s}</span> &middot;
            <span>PEG {peg_s}</span> &middot;
            <span class="green">Upside {up_s}</span>
          </div>
          <div class="top3-verdict">{cio_h}</div>
          <div class="top3-foot">
            <span>ไม้ 1: <strong class="t1">{rcur}{r['tier1_price']:.2f}</strong> &middot; ไม้ 2: <strong class="t2">{rcur}{r['tier2_price']:.2f}</strong></span>
            <span class="link-arrow">ดูแผน →</span>
          </div>
        </div>
        """

    charts_json = json.dumps(chart_configs).replace("</", "<\\/")
    updated_at_str = datetime.now(THAI_TZ).strftime("%d/%m/%Y %H:%M น.")
    fx_display = f"{fx_usd_thb:.2f}"

    macro_bar_html = ""
    if macro:
        r_color = _esc(macro.get("regime_color") or "#38bdf8")
        r_label = _esc(macro.get("regime_label") or "ตลาดปกติ")
        r_advice = _esc(macro.get("regime_advice") or "")
        vix_val = f"{macro['vix']:.2f}" if macro.get("vix") is not None else "N/A"
        vix_chg = f" ({macro['vix_chg_pct']:+.1f}%)" if macro.get("vix_chg_pct") is not None else ""
        vix_color = "#22c55e" if (macro.get("vix") or 0) >= 25 else ("#f59e0b" if (macro.get("vix") or 0) < 14 else "#38bdf8")
        y10_val = f"{macro['us10y']:.2f}%" if macro.get("us10y") is not None else "N/A"
        sp_val = f"{macro['sp500']:,.0f}" if macro.get("sp500") is not None else "N/A"
        sp_dist = (
            f" ({macro['sp500_dist_ema200_pct']:+.1f}%)"
            if macro.get("sp500_dist_ema200_pct") is not None
            else ""
        )
        macro_bar_html = f"""
    <div class="macro-strip" style="border-left-color:{r_color};">
      <div class="macro-main">
        <span class="macro-regime" style="color:{r_color};">{r_label}</span>
        <span class="macro-advice">{r_advice}</span>
      </div>
      <div class="macro-pills">
        <span class="m-pill"><small>VIX</small> <strong style="color:{vix_color};">{vix_val}{vix_chg}</strong></span>
        <span class="m-pill"><small>US10Y</small> <strong class="amber">{y10_val}</strong></span>
        <span class="m-pill"><small>S&amp;P500</small> <strong class="cyan">{sp_val}{sp_dist}</strong></span>
        <span class="m-pill"><small>USD/THB</small> <strong class="green">฿{fx_display}</strong></span>
      </div>
    </div>
        """

    hit_count = sum(1 for r in results_sorted if r["hit_status"] in ("hit_t1", "hit_t2"))
    high_score_count = sum(1 for r in results_sorted if r["dip_score"] >= 80)

    html = f"""<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Smart DCA &amp; AI CIO — ระบบวางแผนออมหุ้นอัจฉริยะ</title>
<script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Sarabun:wght@400;500;600;700&display=swap');
  :root {{
    --bg: #090d16;
    --surface: #111827;
    --surface-2: #172033;
    --border: rgba(148, 163, 184, 0.16);
    --border-strong: rgba(148, 163, 184, 0.28);
    --text: #f1f5f9;
    --muted: #94a3b8;
    --green: #4ade80;
    --green-deep: #22c55e;
    --cyan: #38bdf8;
    --amber: #facc15;
    --red: #f87171;
  }}
  *, *::before, *::after {{ box-sizing: border-box; }}
  body {{
    font-family: 'Sarabun', 'Segoe UI', Tahoma, sans-serif;
    background: var(--bg);
    color: var(--text);
    margin: 0;
    padding: 0 20px 40px 20px;
    line-height: 1.6;
    overflow-x: hidden;
  }}
  .container {{ max-width: 1220px; margin: 0 auto; }}
  .green {{ color: var(--green); }}
  .cyan {{ color: var(--cyan); }}
  .amber {{ color: var(--amber); }}
  .red {{ color: var(--red); }}
  .muted {{ color: var(--muted); }}
  .t1 {{ color: #4ade80; }}
  .t2 {{ color: #22c55e; font-weight: 700; }}
  .t3 {{ color: #facc15; }}

  /* Sticky Top Navbar */
  .top-navbar {{
    position: sticky;
    top: 0;
    z-index: 100;
    background: rgba(9, 13, 22, 0.92);
    border-bottom: 1px solid var(--border);
    backdrop-filter: blur(14px);
    margin: 0 -20px 20px -20px;
    padding: 10px 20px;
  }}
  .navbar-inner {{
    max-width: 1220px;
    margin: 0 auto;
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
  }}
  .brand-box {{ display: flex; align-items: center; gap: 10px; }}
  .brand-title {{ font-size: 18px; font-weight: 700; color: #fff; letter-spacing: -0.2px; }}
  .brand-sub {{ font-size: 11.5px; color: var(--muted); }}
  .nav-tabs {{
    display: flex;
    gap: 4px;
    background: var(--surface);
    padding: 4px;
    border-radius: 10px;
    border: 1px solid var(--border);
  }}
  .nav-tab-btn {{
    background: transparent;
    color: var(--muted);
    border: none;
    padding: 7px 14px;
    border-radius: 7px;
    font-family: inherit;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: 0.16s;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    white-space: nowrap;
  }}
  .nav-tab-btn:hover {{ color: #fff; background: rgba(255,255,255,0.05); }}
  .nav-tab-btn.active {{
    background: var(--green-deep);
    color: #052e16;
    font-weight: 700;
  }}
  .nav-right {{ display: flex; align-items: center; gap: 8px; }}
  .updated-pill {{ font-size: 12px; color: var(--muted); }}
  .btn-primary {{
    background: var(--green-deep);
    color: #052e16;
    border: none;
    border-radius: 8px;
    padding: 8px 14px;
    font-weight: 700;
    font-size: 13.5px;
    cursor: pointer;
    font-family: inherit;
    transition: 0.15s;
  }}
  .btn-primary:hover {{ background: var(--green); }}
  .btn-primary:disabled {{ opacity: 0.55; cursor: wait; }}
  .btn-ghost {{
    background: var(--surface-2);
    color: var(--text);
    border: 1px solid var(--border-strong);
    border-radius: 8px;
    padding: 7px 12px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    font-family: inherit;
    transition: 0.15s;
  }}
  .btn-ghost:hover {{ border-color: var(--cyan); color: var(--cyan); }}
  .btn-remove {{
    background: rgba(239,68,68,0.12);
    color: #f87171;
    border: 1px solid rgba(239,68,68,0.3);
    border-radius: 7px;
    padding: 5px 10px;
    font-size: 12px;
    font-weight: 700;
    cursor: pointer;
    font-family: inherit;
  }}
  .btn-remove:hover {{ background: #ef4444; color: #fff; }}

  /* Compact Macro Strip */
  .macro-strip {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-left: 4px solid var(--cyan);
    border-radius: 12px;
    padding: 12px 16px;
    margin-bottom: 18px;
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
  }}
  .macro-main {{ display: flex; flex-direction: column; gap: 2px; }}
  .macro-regime {{ font-size: 15px; font-weight: 700; }}
  .macro-advice {{ font-size: 13px; color: var(--muted); }}
  .macro-pills {{ display: flex; flex-wrap: wrap; gap: 8px; }}
  .m-pill {{
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 5px 11px;
    font-size: 13px;
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }}
  .m-pill small {{ color: var(--muted); font-size: 11px; font-weight: 600; }}

  /* Collapsible Add-Stock Drawer */
  .add-drawer {{
    display: none;
    background: var(--surface);
    border: 1px solid rgba(56,189,248,0.4);
    border-radius: 12px;
    padding: 14px 16px;
    margin-bottom: 18px;
    animation: fadeIn 0.16s ease-out;
  }}
  .add-drawer.open {{ display: block; }}
  .add-row {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: center; justify-content: space-between; }}
  .add-form {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
  .add-input {{
    background: var(--bg);
    color: #fff;
    border: 1px solid var(--border-strong);
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 14px;
    font-weight: 600;
    width: 240px;
    text-transform: uppercase;
    font-family: inherit;
    outline: none;
  }}
  .add-input:focus {{ border-color: var(--cyan); }}
  .quick-chip {{
    background: var(--surface-2);
    color: var(--cyan);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 12.5px;
    font-weight: 600;
    cursor: pointer;
    font-family: inherit;
  }}
  .quick-chip:hover {{ background: var(--cyan); color: #090d16; }}

  /* Section Header */
  .section-head {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 10px;
    margin-bottom: 12px;
  }}
  .section-title {{ font-size: 16.5px; font-weight: 700; color: #fff; }}
  .section-sub {{ font-size: 13px; color: var(--muted); }}

  /* Top 3 Cards */
  .top3-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 320px), 1fr));
    gap: 14px;
    margin-bottom: 22px;
  }}
  .top3-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px;
    cursor: pointer;
    transition: 0.16s;
  }}
  .top3-card:hover {{
    transform: translateY(-2px);
    border-color: rgba(74, 222, 128, 0.55);
    background: var(--surface-2);
  }}
  .top3-head {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }}
  .rank-badge {{
    background: rgba(34,197,94,0.16);
    color: var(--green);
    border-radius: 6px;
    padding: 2px 7px;
    font-size: 12px;
    font-weight: 700;
  }}
  .top3-price {{ color: var(--amber); font-weight: 700; font-size: 16px; }}
  .top3-meta {{ font-size: 12.5px; color: var(--muted); margin-bottom: 8px; }}
  .top3-verdict {{
    font-size: 13.5px;
    color: #e2e8f0;
    margin-bottom: 10px;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }}
  .top3-foot {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-top: 1px solid var(--border);
    padding-top: 8px;
    font-size: 12.5px;
    color: var(--muted);
  }}
  .link-arrow {{ color: var(--cyan); font-weight: 700; }}

  /* Screener Card & Toolbar */
  .panel-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 18px 20px;
    margin-bottom: 24px;
  }}
  .screener-toolbar {{
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: center;
    gap: 10px;
    margin-bottom: 14px;
  }}
  .search-input {{
    background: var(--bg);
    color: #fff;
    border: 1px solid var(--border-strong);
    border-radius: 8px;
    padding: 7px 12px;
    font-size: 13.5px;
    width: 220px;
    font-family: inherit;
    outline: none;
  }}
  .search-input:focus {{ border-color: var(--cyan); }}
  .filter-pills {{ display: flex; flex-wrap: wrap; gap: 6px; }}
  .f-pill {{
    background: var(--bg);
    color: var(--muted);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 6px 11px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    font-family: inherit;
    transition: 0.14s;
  }}
  .f-pill:hover {{ color: #fff; border-color: var(--border-strong); }}
  .f-pill.active {{ background: rgba(34,197,94,0.18); color: var(--green); border-color: rgba(34,197,94,0.5); }}

  /* Tables */
  table {{ width: 100%; border-collapse: collapse; background: var(--bg); border-radius: 10px; overflow: hidden; }}
  th, td {{ padding: 11px 12px; text-align: left; border-bottom: 1px solid var(--border); font-size: 13.5px; }}
  th {{ background: var(--surface-2); color: var(--muted); font-size: 12.5px; font-weight: 700; white-space: nowrap; }}
  #screenerTable th {{ cursor: pointer; user-select: none; }}
  #screenerTable th:hover {{ color: #fff; }}
  .screener-row {{ cursor: pointer; transition: background 0.12s; }}
  .screener-row:hover {{ background: rgba(56,189,248,0.07); }}
  .cell-ticker {{ font-size: 15px; font-weight: 700; color: var(--cyan); display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }}
  .cell-sub {{ font-size: 11.5px; color: var(--muted); }}
  .cell-price {{ font-weight: 700; color: var(--amber); font-size: 14.5px; }}
  .cell-dd {{ font-size: 11.5px; color: var(--muted); }}
  .tiers-cell {{ white-space: nowrap; font-size: 13px; }}

  /* Badges & Pills */
  .badge-hit {{
    background: rgba(34,197,94,0.16);
    color: var(--green);
    border: 1px solid rgba(34,197,94,0.4);
    padding: 3px 9px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 700;
    white-space: nowrap;
  }}
  .badge-wait {{
    background: rgba(148,163,184,0.1);
    color: var(--muted);
    padding: 3px 9px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 600;
    white-space: nowrap;
  }}
  .badge-warn {{
    background: rgba(245,158,11,0.16);
    color: #fbbf24;
    border: 1px solid rgba(245,158,11,0.4);
    padding: 2px 7px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 700;
    white-space: nowrap;
  }}
  .score-pill {{
    display: inline-block;
    padding: 3px 10px;
    border-radius: 999px;
    font-weight: 700;
    font-size: 13.5px;
    min-width: 44px;
    text-align: center;
  }}
  .score-pill-lg {{
    padding: 5px 12px;
    border-radius: 999px;
    font-weight: 700;
    font-size: 13px;
  }}
  .score-high {{ background: rgba(34,197,94,0.18); color: #4ade80; border: 1px solid rgba(34,197,94,0.45); }}
  .score-mid {{ background: rgba(56,189,248,0.16); color: #38bdf8; border: 1px solid rgba(56,189,248,0.4); }}
  .score-low {{ background: rgba(245,158,11,0.16); color: #facc15; border: 1px solid rgba(245,158,11,0.4); }}

  /* 52W Range Bar */
  .range-wrap {{ width: 115px; }}
  .range-labels {{ display: flex; justify-content: space-between; font-size: 10.5px; color: var(--muted); margin-bottom: 3px; }}
  .range-track {{ position: relative; height: 5px; background: #1e293b; border-radius: 999px; }}
  .range-fill {{ height: 100%; background: linear-gradient(90deg, #22c55e 0%, #38bdf8 60%, #f59e0b 100%); border-radius: 999px; }}
  .range-dot {{ position: absolute; top: -3px; width: 11px; height: 11px; background: #fff; border: 2px solid var(--bg); border-radius: 50%; transform: translateX(-50%); }}

  /* Deep Dive Stock Card */
  .card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 20px 22px;
    margin-bottom: 22px;
  }}
  .card-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
    border-bottom: 1px solid var(--border);
    padding-bottom: 14px;
    margin-bottom: 16px;
  }}
  .card-title-group {{ display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }}
  .card-actions {{ display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }}
  .ticker {{ font-size: 26px; font-weight: 700; color: #fff; }}
  .comp-name {{ font-size: 14px; color: var(--muted); }}
  .price {{ font-size: 22px; font-weight: 700; color: var(--amber); }}
  .discount {{ font-size: 13px; color: var(--cyan); background: rgba(56,189,248,0.12); padding: 3px 9px; border-radius: 6px; font-weight: 600; }}

  /* AI CIO Box */
  .cio-box {{
    background: linear-gradient(145deg, rgba(15,23,42,0.9) 0%, rgba(23,37,84,0.45) 100%);
    border: 1px solid rgba(56,189,248,0.25);
    border-radius: 12px;
    padding: 16px 18px;
    margin-bottom: 16px;
  }}
  .cio-header-row {{ display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 6px; }}
  .cio-title {{ font-size: 13px; font-weight: 700; color: var(--cyan); letter-spacing: 0.3px; }}
  .cio-meta-pill {{ font-size: 11.5px; color: var(--muted); }}
  .cio-meta-pill.fresh {{ color: var(--green); font-weight: 600; }}
  .cio-headline {{ font-size: 17.5px; font-weight: 700; color: #fff; margin-bottom: 14px; line-height: 1.5; }}
  .cio-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 420px), 1fr)); gap: 12px; }}
  .cio-item {{ background: rgba(9,13,22,0.65); padding: 12px 14px; border-radius: 9px; border: 1px solid var(--border); }}
  .cio-item strong {{ display: block; color: #e2e8f0; font-size: 13.5px; margin-bottom: 4px; }}
  .cio-item p {{ margin: 0; color: #cbd5e1; font-size: 14px; }}

  /* Metrics & Sub-panels */
  .metrics-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 10px; margin-bottom: 16px; }}
  .metric {{ background: var(--bg); padding: 10px 13px; border-radius: 9px; border: 1px solid var(--border); }}
  .metric .label {{ display: block; font-size: 11.5px; color: var(--muted); }}
  .metric .val {{ font-size: 15px; font-weight: 700; }}
  .two-col {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 340px), 1fr)); gap: 14px; margin-bottom: 16px; }}
  .sub-panel {{ background: var(--bg); border: 1px solid var(--border); border-radius: 10px; padding: 12px; overflow-x: auto; }}
  .sub-panel-title {{ font-size: 13px; font-weight: 700; color: var(--cyan); margin-bottom: 8px; }}

  /* Charts */
  .chart-wrapper {{ background: var(--bg); border: 1px solid var(--border); border-radius: 10px; padding: 12px; margin-bottom: 16px; }}
  .chart-legend {{ display: flex; flex-wrap: wrap; gap: 12px; font-size: 12.5px; color: var(--muted); margin-bottom: 8px; }}
  .chart-legend span {{ display: inline-flex; align-items: center; gap: 6px; }}
  .chart-legend i {{ display: inline-block; width: 11px; height: 3px; border-radius: 2px; }}
  .tv-chart {{ width: 100%; height: 380px; }}

  /* Deep Dive Stock Picker Bar */
  .picker-bar {{
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    align-items: center;
    background: var(--surface);
    border: 1px solid var(--border);
    padding: 10px 14px;
    border-radius: 12px;
    margin-bottom: 16px;
  }}
  .stock-pick-btn {{
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border);
    padding: 6px 11px;
    border-radius: 7px;
    font-family: inherit;
    font-weight: 700;
    font-size: 13px;
    cursor: pointer;
    transition: 0.14s;
  }}
  .stock-pick-btn small {{ color: var(--muted); font-weight: 600; margin-left: 2px; }}
  .stock-pick-btn:hover, .stock-pick-btn.active {{ background: var(--cyan); color: #090d16; border-color: var(--cyan); }}
  .stock-pick-btn.active small {{ color: #090d16; }}

  /* Calculator & Backtest */
  .calc-controls {{
    display: flex;
    flex-wrap: wrap;
    gap: 14px;
    align-items: flex-end;
    background: var(--bg);
    padding: 14px 16px;
    border-radius: 10px;
    border: 1px solid var(--border);
    margin-bottom: 14px;
  }}
  .ctrl-group {{ display: flex; flex-direction: column; gap: 5px; }}
  .ctrl-group label {{ font-size: 12.5px; color: var(--muted); font-weight: 600; }}
  .ctrl-group input, .ctrl-group select {{
    background: var(--surface-2);
    color: #fff;
    border: 1px solid var(--border-strong);
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 15px;
    font-weight: 700;
    font-family: inherit;
    outline: none;
  }}
  .ctrl-group input:focus, .ctrl-group select:focus {{ border-color: var(--green); }}
  .preset-row {{ display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: 14px; }}
  .ticker-toggles {{ display: flex; flex-wrap: wrap; gap: 7px; margin-bottom: 16px; align-items: center; }}
  .ticker-toggle {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: var(--bg);
    border: 1px solid var(--border);
    padding: 5px 11px;
    border-radius: 8px;
    cursor: pointer;
    user-select: none;
    font-size: 13px;
    font-weight: 600;
  }}
  .ticker-toggle input {{ accent-color: var(--green-deep); width: 15px; height: 15px; cursor: pointer; }}
  .calc-summary-bar {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 10px; margin-bottom: 16px; }}
  .sum-box {{ background: var(--bg); padding: 12px 14px; border-radius: 9px; border: 1px solid var(--border); }}
  .sum-box .s-label {{ font-size: 12px; color: var(--muted); display: block; }}
  .sum-box .s-val {{ font-size: 18px; font-weight: 700; }}

  .analysts-box {{ margin-top: 14px; background: var(--bg); padding: 10px 14px; border-radius: 8px; border: 1px solid var(--border); }}
  .analysts-box summary {{ cursor: pointer; font-weight: 600; color: var(--cyan); font-size: 13.5px; }}
  .analyst-item {{ padding: 9px 0; border-bottom: 1px solid var(--border); }}
  .analyst-head {{ font-size: 13.5px; margin-bottom: 3px; }}
  .analyst-body {{ font-size: 13.5px; color: #cbd5e1; }}

  body.compact-mode .collapsible-section {{ display: none; }}
  .tab-pane {{ display: none; }}
  .tab-pane.active {{ display: block; animation: fadeIn 0.18s ease-out; }}
  @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(4px); }} to {{ opacity: 1; transform: translateY(0); }} }}

  .toast-banner {{
    position: fixed;
    right: 20px;
    bottom: 20px;
    z-index: 999;
    background: var(--surface-2);
    color: #fff;
    border: 1px solid rgba(56,189,248,0.5);
    border-radius: 10px;
    padding: 11px 16px;
    font-size: 13.5px;
    font-weight: 600;
    box-shadow: 0 10px 28px rgba(0,0,0,0.45);
    display: none;
    align-items: center;
    gap: 8px;
  }}
  .toast-banner.show {{ display: inline-flex; animation: fadeIn 0.16s ease-out; }}

  @media (max-width: 768px) {{
    body {{ padding: 0 12px 32px 12px; }}
    .top-navbar {{ margin: 0 -12px 14px -12px; padding: 10px 12px; }}
    .nav-tabs {{ width: 100%; justify-content: space-between; }}
    .nav-tab-btn {{ padding: 7px 8px; font-size: 12.5px; flex: 1 1 auto; justify-content: center; }}
    .nav-right {{ width: 100%; justify-content: space-between; }}
    .panel-card, .card {{ padding: 14px; }}
    .add-input, .search-input {{ width: 100%; }}
    .add-form {{ width: 100%; }}
    .ticker {{ font-size: 22px; }}
    .price {{ font-size: 19px; }}
    .cio-headline {{ font-size: 16px; }}
    .tv-chart {{ height: 290px; }}
    th, td {{ padding: 8px 9px; font-size: 12.5px; }}
  }}
</style>
</head>
<body>

<header class="top-navbar">
  <div class="navbar-inner">
    <div class="brand-box">
      <div>
        <div class="brand-title">Smart DCA &amp; AI CIO</div>
        <div class="brand-sub">อัปเดต {updated_at_str} &middot; ฿{fx_display}/USD</div>
      </div>
    </div>

    <nav class="nav-tabs">
      <button class="nav-tab-btn active" id="tabBtn-overview" onclick="switchMainTab('overview')">📊 ภาพรวม ({len(results_sorted)})</button>
      <button class="nav-tab-btn" id="tabBtn-calculator" onclick="switchMainTab('calculator')">💰 คำนวณ DCA</button>
      <button class="nav-tab-btn" id="tabBtn-deepdive" onclick="switchMainTab('deepdive')">📈 เจาะลึก &amp; กราฟ</button>
      <button class="nav-tab-btn" id="tabBtn-backtest" onclick="switchMainTab('backtest')">🧪 Backtest</button>
    </nav>

    <div class="nav-right">
      <button class="btn-ghost" onclick="toggleAddDrawer()">➕ เพิ่มหุ้น</button>
      <button class="btn-primary" id="refreshBtn" onclick="refreshAllPrices()">🔄 ดึงราคาล่าสุด</button>
    </div>
  </div>
</header>

<div class="container">

  <!-- Collapsible Add Stock Drawer (accessible from any tab) -->
  <div class="add-drawer" id="addDrawer">
    <div class="add-row">
      <div class="add-form">
        <strong style="color:var(--cyan);font-size:14px;">เพิ่มหุ้นเข้าพอร์ต:</strong>
        <input type="text" id="newTickerInput" class="add-input" placeholder="เช่น AAPL, AVGO, PTT.BK" onkeydown="if(event.key==='Enter') addStockFromInput()">
        <button id="addStockBtn" class="btn-primary" onclick="addStockFromInput()">เพิ่ม &amp; วิเคราะห์</button>
        <span id="addStatusMsg" style="font-size:13px;color:var(--amber);font-weight:600;"></span>
      </div>
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
        <small class="muted">เพิ่มด่วน:</small>
        <button class="quick-chip" onclick="quickAdd('AAPL')">+ AAPL</button>
        <button class="quick-chip" onclick="quickAdd('AMZN')">+ AMZN</button>
        <button class="quick-chip" onclick="quickAdd('AVGO')">+ AVGO</button>
        <button class="quick-chip" onclick="quickAdd('COST')">+ COST</button>
        <button class="quick-chip" onclick="quickAdd('BRK-B')">+ BRK-B</button>
        <button class="quick-chip" onclick="quickAdd('PTT.BK')">+ PTT.BK</button>
      </div>
    </div>
  </div>

  <!-- ==================== TAB 1: OVERVIEW & SCREENER ==================== -->
  <section id="tab-overview" class="tab-pane active">
    {macro_bar_html}

    <div class="section-head">
      <div>
        <div class="section-title">🏆 Top 3 หุ้นน่าช้อนสะสมที่สุดวันนี้</div>
        <div class="section-sub">แตะโซนซื้อแล้ว {hit_count} ตัว &middot; คะแนน 80+ จำนวน {high_score_count} ตัว (คลิกการ์ดเพื่อดูกราฟและแผนซื้อ)</div>
      </div>
      <button class="btn-ghost" onclick="switchMainTab('calculator')">ไปหน้าคำนวณแบ่งเงินออม 💰 →</button>
    </div>
    <div class="top3-grid">
      {top3_cards_html}
    </div>

    <div class="panel-card">
      <div class="screener-toolbar">
        <div>
          <div class="section-title">📊 ตารางสแกนหุ้นทั้งพอร์ต</div>
          <div class="section-sub">คลิกหัวตารางเพื่อเรียงลำดับ หรือคลิกชื่อหุ้นเพื่อดูแผนช้อนซื้อ</div>
        </div>
        <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;">
          <input type="text" id="screenerSearch" class="search-input" placeholder="🔍 ค้นหาชื่อหุ้น..." oninput="filterScreenerTable()">
          <div class="filter-pills">
            <button class="f-pill active" data-sf="all" onclick="setScreenerFilter('all', this)">ทั้งหมด ({len(results_sorted)})</button>
            <button class="f-pill" data-sf="hit" onclick="setScreenerFilter('hit', this)">🎯 แตะโซนซื้อ ({hit_count})</button>
            <button class="f-pill" data-sf="score" onclick="setScreenerFilter('score', this)">🔥 Score 80+ ({high_score_count})</button>
            <button class="f-pill" data-sf="peg" onclick="setScreenerFilter('peg', this)">💎 PEG &le; 1.25</button>
          </div>
        </div>
      </div>
      <div style="overflow-x:auto;">
        <table id="screenerTable">
          <thead>
            <tr>
              <th onclick="sortScreener(0, false)">หุ้น ⇅</th>
              <th onclick="sortScreener(1, true)">ราคาล่าสุด ⇅</th>
              <th onclick="sortScreener(2, true)">กรอบ 52 สัปดาห์ ⇅</th>
              <th onclick="sortScreener(3, true)">สถานะ ⇅</th>
              <th onclick="sortScreener(4, true)">Score ⇅</th>
              <th onclick="sortScreener(5, true)">RSI ⇅</th>
              <th onclick="sortScreener(6, true)">Fwd P/E ⇅</th>
              <th onclick="sortScreener(7, true)">PEG ⇅</th>
              <th onclick="sortScreener(8, true)">เป้า Wall St ⇅</th>
              <th>ตั้งรับ ไม้ 1 / ไม้ 2 / ไม้ 3</th>
            </tr>
          </thead>
          <tbody>
            {''.join(screener_rows_html)}
          </tbody>
        </table>
      </div>
    </div>
  </section>

  <!-- ==================== TAB 2: SMART DCA BUDGET CALCULATOR ==================== -->
  <section id="tab-calculator" class="tab-pane">
    <div class="panel-card">
      <div class="section-head">
        <div>
          <div class="section-title">💰 เครื่องคิดเลขแบ่งเงินออม Smart DCA รายเดือน</div>
          <div class="section-sub">กรอกงบออมของคุณ ระบบจะแบ่งสัดส่วนตาม Dip Score และแตกยอดไม้ 1–3 ให้ทันที (เรตสด ฿{fx_display}/USD)</div>
        </div>
      </div>

      <div class="calc-controls">
        <div class="ctrl-group">
          <label for="budgetInput">งบออมเดือนนี้</label>
          <input type="number" id="budgetInput" value="1000" min="10" step="50" oninput="saveAndRecalc()">
        </div>
        <div class="ctrl-group">
          <label for="currencySelect">สกุลเงิน</label>
          <select id="currencySelect" onchange="onCurrencyChange()">
            <option value="USD" selected>USD ($)</option>
            <option value="THB">THB (฿)</option>
          </select>
        </div>
        <div class="ctrl-group" id="fxGroup" style="display:none;">
          <label for="fxInput">เรต บาท / 1 USD</label>
          <input type="number" id="fxInput" value="{fx_display}" min="20" max="50" step="0.05" style="width:110px;" oninput="saveAndRecalc()">
        </div>
        <div class="ctrl-group" style="flex:1;min-width:240px;">
          <label for="modeSelect">สูตรการกระจายเงิน</label>
          <select id="modeSelect" onchange="saveAndRecalc()">
            <option value="smart_dip" selected>Smart Dip-Weighted (เน้นตัวลดแรง + พื้นฐานดี — แนะนำ)</option>
            <option value="equal_mult">Equal + Multiplier (ตั้งต้นเท่ากัน คูณตามตัวคูณ DCA)</option>
            <option value="top3_only">Top 3 Conviction (โฟกัสเฉพาะ 3 อันดับแรก)</option>
          </select>
        </div>
      </div>

      <div class="preset-row">
        <small class="muted">งบด่วน:</small>
        <button class="quick-chip" onclick="setBudgetPreset(500, 'USD')">$500</button>
        <button class="quick-chip" onclick="setBudgetPreset(1000, 'USD')">$1,000</button>
        <button class="quick-chip" onclick="setBudgetPreset(2000, 'USD')">$2,000</button>
        <button class="quick-chip" onclick="setBudgetPreset(10000, 'THB')">฿10,000</button>
        <button class="quick-chip" onclick="setBudgetPreset(20000, 'THB')">฿20,000</button>
        <button class="quick-chip" onclick="setBudgetPreset(50000, 'THB')">฿50,000</button>
      </div>

      <div class="ticker-toggles" id="tickerToggles">
        <span style="font-size:12.5px;color:var(--muted);margin-right:4px;">เลือกหุ้นที่จะออม:</span>
        <button type="button" class="quick-chip" onclick="selectAllTickers(true)">เลือกทั้งหมด</button>
        <button type="button" class="quick-chip" onclick="selectAllTickers(false)" style="color:var(--muted);">ล้างทั้งหมด</button>
      </div>

      <div class="calc-summary-bar">
        <div class="sum-box"><span class="s-label">งบออมรวมทั้งหมด</span><span class="s-val cyan" id="sumTotal">$1,000.00</span></div>
        <div class="sum-box"><span class="s-label">🟢 เคาะซื้อวันนี้ (Market)</span><span class="s-val green" id="sumBuyNow">$0.00</span></div>
        <div class="sum-box"><span class="s-label">🎯 ตั้งรอรับ ไม้ 1 + ไม้ 2</span><span class="s-val cyan" id="sumLimit12">$0.00</span></div>
        <div class="sum-box"><span class="s-label">🛡️ สำรอง ไม้ 3 (เผื่อวิกฤต)</span><span class="s-val amber" id="sumReserve">$0.00</span></div>
      </div>

      <div style="overflow-x:auto;">
        <table id="calcTable">
          <thead>
            <tr>
              <th>หุ้น</th>
              <th>Score</th>
              <th>สัดส่วน</th>
              <th>งบรวมที่ได้</th>
              <th>🟢 ซื้อวันนี้</th>
              <th>🎯 ไม้ 1 (Limit)</th>
              <th>💎 ไม้ 2 (Limit)</th>
              <th>🛡️ ไม้ 3 (สำรอง)</th>
            </tr>
          </thead>
          <tbody id="calcTableBody"></tbody>
        </table>
      </div>
    </div>
  </section>

  <!-- ==================== TAB 3: AI DEEP DIVE & CHARTS ==================== -->
  <section id="tab-deepdive" class="tab-pane">
    <div class="picker-bar">
      <button class="btn-ghost" onclick="switchMainTab('overview')" style="padding:5px 10px;font-size:12.5px;">← กลับภาพรวม</button>
      {nav_pills}
      <button class="f-pill active" id="showAllStocksBtn" onclick="filterCards('all', this)" style="margin-left:auto;">ดูทั้งหมด ({len(results_sorted)})</button>
      <button class="btn-ghost" id="toggleCompactBtn" onclick="toggleCompactMode()" style="padding:5px 10px;font-size:12.5px;">🗂️ ย่อกราฟ</button>
    </div>

    {''.join(cards_html)}
  </section>

  <!-- ==================== TAB 4: SMART DCA vs FIXED DCA BACKTEST ==================== -->
  <section id="tab-backtest" class="tab-pane">
    <div class="panel-card">
      <div class="section-head">
        <div>
          <div class="section-title">🧪 ทดสอบย้อนหลัง: Smart DCA vs Fixed DCA</div>
          <div class="section-sub">เปรียบเทียบการออมเท่ากันทุกเดือน (Fixed DCA) กับการปรับสัดส่วนตาม Dip Score + เก็บเงินสดช้อนตอนย่อ (ราคาปรับแตกพาร์/ปันผลแล้ว)</div>
        </div>
        <div style="display:flex;gap:8px;align-items:center;">
          <select id="btPeriodSelect" style="background:var(--surface-2);color:#fff;border:1px solid var(--border-strong);border-radius:8px;padding:7px 12px;font-weight:700;font-family:inherit;">
            <option value="2y">ย้อนหลัง 2 ปี</option>
            <option value="3y" selected>ย้อนหลัง 3 ปี</option>
            <option value="5y">ย้อนหลัง 5 ปี</option>
          </select>
          <button id="runBtBtn" class="btn-primary" onclick="runBacktestFromApi()">▶️ รัน Backtest</button>
        </div>
      </div>

      <div id="btStatus" style="font-size:13.5px;color:var(--amber);margin-bottom:12px;"></div>

      <div class="calc-summary-bar" id="btSummaryBar" style="display:none;">
        <div class="sum-box"><span class="s-label">Fixed DCA ผลตอบแทนรวม</span><span class="s-val" id="btFixedRet">-</span></div>
        <div class="sum-box"><span class="s-label">Smart DCA ผลตอบแทนรวม</span><span class="s-val green" id="btSmartRet">-</span></div>
        <div class="sum-box"><span class="s-label">ส่วนต่าง (Alpha)</span><span class="s-val cyan" id="btAlpha">-</span></div>
        <div class="sum-box"><span class="s-label">Max Drawdown เฉลี่ย</span><span class="s-val" id="btMaxDD">-</span></div>
      </div>

      <div style="overflow-x:auto;">
        <table id="btTable" style="display:none;">
          <thead>
            <tr>
              <th>หุ้น</th>
              <th>ช่วงเวลา</th>
              <th>Fixed DCA</th>
              <th>Smart DCA</th>
              <th>Alpha</th>
              <th>ต้นทุนเฉลี่ย (Fix → Smart)</th>
              <th>Max Drawdown</th>
              <th>ช้อน Dip</th>
            </tr>
          </thead>
          <tbody id="btTableBody"></tbody>
        </table>
      </div>
    </div>
  </section>

</div>

<div class="toast-banner" id="toastBanner"></div>

<script>
const CHART_CONFIGS = {charts_json};
const LIVE_FX_USD_THB = {fx_display};
const renderedCharts = new Map();

function toggleAddDrawer() {{
  const d = document.getElementById('addDrawer');
  if (!d) return;
  d.classList.toggle('open');
  if (d.classList.contains('open')) {{
    const inp = document.getElementById('newTickerInput');
    if (inp) inp.focus();
  }}
}}

// --- Screener Search & Quick Filter ---
let activeScreenerFilter = 'all';
function setScreenerFilter(mode, btn) {{
  activeScreenerFilter = mode;
  document.querySelectorAll('.filter-pills .f-pill').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  filterScreenerTable();
}}

function filterScreenerTable() {{
  const q = (document.getElementById('screenerSearch')?.value || '').trim().toLowerCase();
  document.querySelectorAll('#screenerTable tbody tr.screener-row').forEach(row => {{
    const t = (row.dataset.ticker || '').toLowerCase();
    const n = (row.dataset.name || '').toLowerCase();
    const matchText = !q || t.includes(q) || n.includes(q);
    let matchFilter = true;
    if (activeScreenerFilter === 'hit') matchFilter = row.dataset.hit === '1';
    else if (activeScreenerFilter === 'score') matchFilter = row.dataset.score === '1';
    else if (activeScreenerFilter === 'peg') matchFilter = row.dataset.peg === '1';
    row.style.display = (matchText && matchFilter) ? '' : 'none';
  }});
}}

function renderChart(cfg) {{
  const container = document.getElementById(cfg.id);
  if (!container || container.clientWidth === 0) return;
  if (renderedCharts.has(cfg.id)) {{
    const existing = renderedCharts.get(cfg.id);
    existing.applyOptions({{ width: container.clientWidth }});
    existing.timeScale().fitContent();
    return;
  }}

  const chart = LightweightCharts.createChart(container, {{
    width: container.clientWidth,
    layout: {{ background: {{ type: 'solid', color: '#090d16' }}, textColor: '#94a3b8' }},
    grid: {{ vertLines: {{ color: '#172033' }}, horzLines: {{ color: '#172033' }} }},
    crosshair: {{ mode: LightweightCharts.CrosshairMode.Normal }},
    rightPriceScale: {{ borderColor: '#26334d' }},
    timeScale: {{ borderColor: '#26334d', timeVisible: true }},
  }});
  renderedCharts.set(cfg.id, chart);

  const candleSeries = chart.addCandlestickSeries({{
    upColor: '#22c55e', downColor: '#ef4444', borderVisible: false, wickUpColor: '#22c55e', wickDownColor: '#ef4444',
  }});
  candleSeries.setData(cfg.candles);
  if (cfg.markers && cfg.markers.length > 0) candleSeries.setMarkers(cfg.markers);

  chart.addLineSeries({{ color: '#f59e0b', lineWidth: 2, title: 'EMA 50' }}).setData(cfg.ema50);
  chart.addLineSeries({{ color: '#a855f7', lineWidth: 2, title: 'EMA 200' }}).setData(cfg.ema200);

  candleSeries.createPriceLine({{ price: cfg.tier1, color: '#4ade80', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: 'ไม้ 1 (30%)' }});
  candleSeries.createPriceLine({{ price: cfg.tier2, color: '#22c55e', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Solid, axisLabelVisible: true, title: 'ไม้ 2 (40%)' }});
  candleSeries.createPriceLine({{ price: cfg.tier3, color: '#eab308', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: 'ไม้ 3 (30%)' }});

  chart.timeScale().fitContent();
  window.addEventListener('resize', () => {{
    if (container.clientWidth > 0) chart.applyOptions({{ width: container.clientWidth }});
  }});
}}

function renderVisibleCharts() {{
  CHART_CONFIGS.forEach(cfg => {{
    const el = document.getElementById(cfg.id);
    if (el && el.offsetParent !== null) renderChart(cfg);
  }});
}}

let backtestLoaded = false;
let deepDiveInitialized = false;
function switchMainTab(tabName) {{
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-tab-btn').forEach(b => b.classList.remove('active'));
  const targetPane = document.getElementById('tab-' + tabName);
  const targetBtn = document.getElementById('tabBtn-' + tabName);
  if (targetPane) targetPane.classList.add('active');
  if (targetBtn) targetBtn.classList.add('active');
  window.scrollTo({{ top: 0, behavior: 'smooth' }});
  if (tabName === 'deepdive') {{
    if (!deepDiveInitialized && CHART_CONFIGS.length > 0) {{
      openStockDeepDive(CHART_CONFIGS[0].ticker);
      return;
    }}
    setTimeout(renderVisibleCharts, 60);
  }}
  if (tabName === 'backtest' && !backtestLoaded) runBacktestFromApi();
}}

function openStockDeepDive(ticker) {{
  deepDiveInitialized = true;
  switchMainTab('deepdive');
  document.querySelectorAll('.stock-pick-btn').forEach(b => b.classList.toggle('active', b.getAttribute('data-pick') === ticker));
  const showAllBtn = document.getElementById('showAllStocksBtn');
  if (showAllBtn) showAllBtn.classList.remove('active');
  document.querySelectorAll('.stock-card').forEach(card => {{
    card.style.display = (card.getAttribute('data-ticker') === ticker) ? '' : 'none';
  }});
  setTimeout(renderVisibleCharts, 60);
}}

function filterCards(mode, btn) {{
  deepDiveInitialized = true;
  document.querySelectorAll('.stock-pick-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  document.querySelectorAll('.stock-card').forEach(card => {{
    if (mode === 'all') card.style.display = '';
    else if (mode === 'score') card.style.display = card.dataset.score === '1' ? '' : 'none';
    else if (mode === 'hit') card.style.display = card.dataset.hit === '1' ? '' : 'none';
    else if (mode === 'peg') card.style.display = card.dataset.peg === '1' ? '' : 'none';
  }});
  setTimeout(renderVisibleCharts, 60);
}}

function toggleCompactMode() {{
  document.body.classList.toggle('compact-mode');
  const btn = document.getElementById('toggleCompactBtn');
  if (document.body.classList.contains('compact-mode')) {{
    btn.textContent = '📈 แสดงกราฟ';
  }} else {{
    btn.textContent = '🗂️ ย่อกราฟ';
    setTimeout(renderVisibleCharts, 60);
  }}
}}

let sortAsc = false;
function sortScreener(colIdx, numeric) {{
  sortAsc = !sortAsc;
  const tbody = document.querySelector('#screenerTable tbody');
  const rows = Array.from(tbody.querySelectorAll('tr'));
  rows.sort((a, b) => {{
    const cellA = a.children[colIdx];
    const cellB = b.children[colIdx];
    if (numeric) {{
      const vA = parseFloat(cellA.getAttribute('data-val')) || 0;
      const vB = parseFloat(cellB.getAttribute('data-val')) || 0;
      return sortAsc ? (vA - vB) : (vB - vA);
    }}
    return sortAsc ? cellA.innerText.localeCompare(cellB.innerText) : cellB.innerText.localeCompare(cellA.innerText);
  }});
  rows.forEach(r => tbody.appendChild(r));
}}

// --- Interactive Monthly Budget Calculator ---
const STORAGE_KEY = 'smart_dca_prefs_v4';
const selectedTickers = new Set(CHART_CONFIGS.map(c => c.ticker));

function setBudgetPreset(amount, cur) {{
  document.getElementById('currencySelect').value = cur;
  document.getElementById('fxGroup').style.display = cur === 'THB' ? 'flex' : 'none';
  document.getElementById('budgetInput').value = amount;
  document.getElementById('budgetInput').step = cur === 'THB' ? '1000' : '50';
  saveAndRecalc();
}}

function selectAllTickers(checked) {{
  selectedTickers.clear();
  if (checked) CHART_CONFIGS.forEach(c => selectedTickers.add(c.ticker));
  document.querySelectorAll('#tickerToggles input[type="checkbox"]').forEach(cb => {{
    cb.checked = checked;
  }});
  saveAndRecalc();
}}

function loadSavedPrefs() {{
  try {{
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const prefs = JSON.parse(raw);
    if (prefs.currency) {{
      document.getElementById('currencySelect').value = prefs.currency;
      document.getElementById('fxGroup').style.display = prefs.currency === 'THB' ? 'flex' : 'none';
    }}
    if (prefs.budget) document.getElementById('budgetInput').value = prefs.budget;
    if (prefs.mode) document.getElementById('modeSelect').value = prefs.mode;
    if (Array.isArray(prefs.unchecked)) prefs.unchecked.forEach(t => selectedTickers.delete(t));
  }} catch (e) {{}}
}}

function savePrefs() {{
  try {{
    const unchecked = CHART_CONFIGS.map(c => c.ticker).filter(t => !selectedTickers.has(t));
    localStorage.setItem(STORAGE_KEY, JSON.stringify({{
      budget: document.getElementById('budgetInput').value,
      currency: document.getElementById('currencySelect').value,
      mode: document.getElementById('modeSelect').value,
      unchecked: unchecked,
    }}));
  }} catch (e) {{}}
}}

function saveAndRecalc() {{ savePrefs(); recalcBudget(); }}

function initBudgetCalculator() {{
  loadSavedPrefs();
  const wrap = document.getElementById('tickerToggles');
  if (!wrap) return;
  CHART_CONFIGS.forEach(cfg => {{
    const lbl = document.createElement('label');
    lbl.className = 'ticker-toggle';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = selectedTickers.has(cfg.ticker);
    cb.onchange = () => {{
      if (cb.checked) selectedTickers.add(cfg.ticker);
      else selectedTickers.delete(cfg.ticker);
      saveAndRecalc();
    }};
    lbl.appendChild(cb);
    lbl.appendChild(document.createTextNode(`${{cfg.ticker}} (${{cfg.dip_score}})`));
    wrap.appendChild(lbl);
  }});
  recalcBudget();
}}

function onCurrencyChange() {{
  const cur = document.getElementById('currencySelect').value;
  const fxGroup = document.getElementById('fxGroup');
  const budgetInput = document.getElementById('budgetInput');
  const fx = parseFloat(document.getElementById('fxInput').value) || LIVE_FX_USD_THB;
  if (cur === 'THB') {{
    fxGroup.style.display = 'flex';
    budgetInput.value = Math.round((parseFloat(budgetInput.value) || 1000) * fx / 500) * 500;
    budgetInput.step = '1000';
  }} else {{
    fxGroup.style.display = 'none';
    budgetInput.value = Math.round((parseFloat(budgetInput.value) || 35000) / fx / 50) * 50;
    budgetInput.step = '50';
  }}
  saveAndRecalc();
}}

function fmtMoney(val, cur) {{
  const sym = cur === 'THB' ? '฿' : '$';
  return sym + val.toLocaleString('en-US', {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }});
}}

function recalcBudget() {{
  const budget = Math.max(0, parseFloat(document.getElementById('budgetInput').value) || 0);
  const cur = document.getElementById('currencySelect').value;
  const fx = parseFloat(document.getElementById('fxInput').value) || LIVE_FX_USD_THB;
  const mode = document.getElementById('modeSelect').value;

  let active = CHART_CONFIGS.filter(c => selectedTickers.has(c.ticker));
  if (mode === 'top3_only') active = active.slice().sort((a, b) => b.dip_score - a.dip_score).slice(0, 3);

  let rawWeights = active.map(c => {{
    if (mode === 'equal_mult') return c.dca_mult_num;
    const dipFactor = Math.pow(Math.max(c.dip_score - 30, 10) / 50.0, 2.2);
    const upsideBonus = c.upside > 25 ? 1.25 : (c.upside > 10 ? 1.0 : 0.8);
    const aiBonus = c.ai_weight > 0 ? 1.35 : 1.0;
    return dipFactor * upsideBonus * aiBonus;
  }});

  const totalWeight = rawWeights.reduce((acc, v) => acc + v, 0) || 1.0;
  const tbody = document.getElementById('calcTableBody');
  tbody.innerHTML = '';

  let totalNow = 0, totalLim12 = 0, totalRes = 0;

  active.forEach((c, i) => {{
    const w = rawWeights[i] / totalWeight;
    const alloc = budget * w;
    let pNow = 0.15, pT1 = 0.30, pT2 = 0.35, pT3 = 0.20;
    if (c.dip_score >= 85) {{ pNow = 0.35; pT1 = 0.25; pT2 = 0.25; pT3 = 0.15; }}
    else if (c.dip_score >= 68) {{ pNow = 0.25; pT1 = 0.30; pT2 = 0.30; pT3 = 0.15; }}

    const aNow = alloc * pNow, aT1 = alloc * pT1, aT2 = alloc * pT2, aT3 = alloc * pT3;
    totalNow += aNow; totalLim12 += (aT1 + aT2); totalRes += aT3;

    let allocInStockCur = alloc;
    if (cur === 'THB' && c.currency === 'USD') allocInStockCur = alloc / fx;
    else if (cur === 'USD' && c.currency === 'THB') allocInStockCur = alloc * fx;
    const estShares = allocInStockCur / (c.close || 1);
    const sSym = c.currency_symbol || '$';

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><a href="javascript:void(0)" onclick="openStockDeepDive('${{c.ticker}}')" style="color:#38bdf8;font-weight:700;text-decoration:none;">${{c.ticker}}</a> <small class="muted">(${{sSym}}${{c.close.toFixed(2)}})</small></td>
      <td><span class="green"><strong>${{c.dip_score}}</strong></span> <small class="muted">(${{c.dca_mult_num.toFixed(1)}}x)</small></td>
      <td><strong>${{(w * 100).toFixed(1)}}%</strong></td>
      <td class="amber"><strong>${{fmtMoney(alloc, cur)}}</strong><br><small class="muted">≈ ${{estShares.toFixed(2)}} หุ้น</small></td>
      <td class="green"><strong>${{fmtMoney(aNow, cur)}}</strong><br><small class="muted">@ ${{sSym}}${{c.close.toFixed(2)}}</small></td>
      <td class="cyan"><strong>${{fmtMoney(aT1, cur)}}</strong><br><small class="muted">@ ${{sSym}}${{c.tier1.toFixed(2)}}</small></td>
      <td class="cyan"><strong>${{fmtMoney(aT2, cur)}}</strong><br><small class="muted">@ ${{sSym}}${{c.tier2.toFixed(2)}}</small></td>
      <td class="amber"><strong>${{fmtMoney(aT3, cur)}}</strong><br><small class="muted">@ ${{sSym}}${{c.tier3.toFixed(2)}}</small></td>
    `;
    tbody.appendChild(tr);
  }});

  document.getElementById('sumTotal').textContent = fmtMoney(budget, cur);
  document.getElementById('sumBuyNow').textContent = fmtMoney(totalNow, cur);
  document.getElementById('sumLimit12').textContent = fmtMoney(totalLim12, cur);
  document.getElementById('sumReserve').textContent = fmtMoney(totalRes, cur);
}}

initBudgetCalculator();

// --- API Client ---
const API_BASE = (window.location.protocol === 'file:') ? 'http://127.0.0.1:8765' : '';

async function runBacktestFromApi() {{
  const period = document.getElementById('btPeriodSelect').value || '3y';
  const status = document.getElementById('btStatus');
  const btn = document.getElementById('runBtBtn');
  btn.disabled = true;
  status.textContent = `⏳ กำลังจำลอง Backtest (${{period}})...`;
  try {{
    const resp = await fetch(`${{API_BASE}}/api/backtest?period=${{encodeURIComponent(period)}}`);
    const data = await resp.json();
    if (!data || !data.tickers || data.tickers.length === 0) {{
      status.textContent = '⚠️ ไม่สามารถดึงข้อมูลย้อนหลังได้ในขณะนี้';
      btn.disabled = false;
      return;
    }}
    backtestLoaded = true;
    const s = data.summary || {{}};
    document.getElementById('btSummaryBar').style.display = 'grid';
    document.getElementById('btTable').style.display = 'table';
    document.getElementById('btFixedRet').textContent = `${{s.fixed_return_pct >= 0 ? '+' : ''}}${{s.fixed_return_pct}}%`;
    document.getElementById('btSmartRet').textContent = `${{s.smart_return_pct >= 0 ? '+' : ''}}${{s.smart_return_pct}}%`;
    const alphaEl = document.getElementById('btAlpha');
    alphaEl.textContent = `${{s.portfolio_alpha_pct >= 0 ? '+' : ''}}${{s.portfolio_alpha_pct}}% (ชนะ ${{s.smart_wins}}/${{s.count}})`;
    alphaEl.className = 's-val ' + (s.portfolio_alpha_pct >= 0 ? 'green' : 'red');
    document.getElementById('btMaxDD').textContent = `${{s.avg_fixed_max_dd_pct}}% → ${{s.avg_smart_max_dd_pct}}%`;

    const tbody = document.getElementById('btTableBody');
    tbody.innerHTML = '';
    data.tickers.forEach(r => {{
      const aClass = r.alpha_pct >= 0 ? 'green' : 'red';
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><strong class="cyan">${{r.ticker}}</strong></td>
        <td><small class="muted">${{r.start_date}} → ${{r.end_date}}</small></td>
        <td>${{r.fixed_return_pct >= 0 ? '+' : ''}}${{r.fixed_return_pct}}%</td>
        <td class="green"><strong>${{r.smart_return_pct >= 0 ? '+' : ''}}${{r.smart_return_pct}}%</strong></td>
        <td class="${{aClass}}"><strong>${{r.alpha_pct >= 0 ? '+' : ''}}${{r.alpha_pct}}%</strong></td>
        <td>${{r.fixed_avg_cost.toFixed(2)}} → <strong>${{r.smart_avg_cost.toFixed(2)}}</strong></td>
        <td>${{r.fixed_max_dd_pct}}% → <strong class="cyan">${{r.smart_max_dd_pct}}%</strong></td>
        <td>${{r.dip_deployments}} ครั้ง</td>
      `;
      tbody.appendChild(tr);
    }});
    status.textContent = `✅ จำลองย้อนหลังสำเร็จ (${{data.period}} · งบสมมติ 1,000/เดือน/ตัว)`;
  }} catch (err) {{
    status.textContent = '⚠️ กรุณาเปิดผ่าน http://127.0.0.1:8765 เพื่อใช้งาน API Backtest';
  }} finally {{
    btn.disabled = false;
  }}
}}

async function addStockFromInput() {{
  const inp = document.getElementById('newTickerInput');
  const raw = (inp.value || '').trim().toUpperCase();
  if (!raw) return;
  await callPortfolioApi('/api/add', {{ tickers: raw }});
}}

async function quickAdd(tkr) {{
  document.getElementById('newTickerInput').value = tkr;
  await callPortfolioApi('/api/add', {{ tickers: tkr }});
}}

async function removeStock(tkr) {{
  if (!confirm(`ต้องการลบหุ้น ${{tkr}} ออกจากหน้า Dashboard ใช่หรือไม่?`)) return;
  await callPortfolioApi('/api/remove', {{ tickers: tkr }});
}}

async function refreshAllPrices() {{
  await callPortfolioApi('/api/refresh', {{}});
}}

function showToast(text, isSuccess = false) {{
  const t = document.getElementById('toastBanner');
  if (!t) return;
  t.textContent = text;
  t.style.borderColor = isSuccess ? 'rgba(34,197,94,0.6)' : 'rgba(56,189,248,0.5)';
  t.classList.add('show');
}}

function hideToast() {{
  const t = document.getElementById('toastBanner');
  if (t) t.classList.remove('show');
}}

async function callPortfolioApi(path, payload) {{
  const btn = document.getElementById('addStockBtn');
  const refBtn = document.getElementById('refreshBtn');
  const msg = document.getElementById('addStatusMsg');
  if (btn) btn.disabled = true;
  if (refBtn) {{ refBtn.disabled = true; refBtn.textContent = '⏳ กำลังอัปเดต...'; }}
  if (msg) msg.textContent = '⏳ กำลังอัปเดตข้อมูลและบทวิเคราะห์ AI...';
  showToast('⏳ กำลังดึงราคาล่าสุดและซิงก์ข้อมูล...');
  try {{
    const headers = {{ 'Content-Type': 'application/json' }};
    const savedToken = sessionStorage.getItem('smart_dca_admin_token');
    if (savedToken) headers['X-API-Key'] = savedToken;

    let resp = await fetch(API_BASE + path, {{
      method: 'POST',
      headers,
      body: JSON.stringify(payload),
    }});
    if (resp.status === 401) {{
      const tok = prompt('กรุณากรอก Admin Token (SMART_DCA_ADMIN_TOKEN) เพื่อแก้ไขพอร์ต:');
      if (!tok) {{
        if (msg) msg.textContent = '';
        if (btn) btn.disabled = false;
        if (refBtn) {{ refBtn.disabled = false; refBtn.textContent = '🔄 ดึงราคาล่าสุด'; }}
        hideToast();
        return;
      }}
      sessionStorage.setItem('smart_dca_admin_token', tok.trim());
      headers['X-API-Key'] = tok.trim();
      resp = await fetch(API_BASE + path, {{
        method: 'POST',
        headers,
        body: JSON.stringify(payload),
      }});
    }}
    const data = await resp.json();
    if (!data.ok) {{
      if (resp.status === 401) sessionStorage.removeItem('smart_dca_admin_token');
      alert(data.message || 'เกิดข้อผิดพลาด');
      if (msg) msg.textContent = '';
      if (btn) btn.disabled = false;
      if (refBtn) {{ refBtn.disabled = false; refBtn.textContent = '🔄 ดึงราคาล่าสุด'; }}
      hideToast();
      return;
    }}
    if (msg) msg.textContent = '✅ สำเร็จ! กำลังรีโหลด...';
    showToast('✅ อัปเดตสำเร็จ! กำลังรีโหลดหน้าเว็บ...', true);
    setTimeout(() => window.location.reload(), 300);
  }} catch (err) {{
    if (msg) msg.textContent = '';
    if (btn) btn.disabled = false;
    if (refBtn) {{ refBtn.disabled = false; refBtn.textContent = '🔄 ดึงราคาล่าสุด'; }}
    hideToast();
    alert('หากต้องการกดเพิ่ม/ลบหุ้น ให้เปิดผ่าน http://127.0.0.1:8765');
  }}
}}
</script>
</body>
</html>"""

    out_path = ROOT_DIR / "trade_plan.html"
    fd, tmp_name = tempfile.mkstemp(prefix=".trade_plan_", suffix=".tmp", dir=str(out_path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_f:
            tmp_f.write(html)
        os.replace(tmp_name, out_path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return out_path
