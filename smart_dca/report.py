"""HTML Dashboard generator (v4.0) with multi-currency, live USD/THB FX, CIO freshness badge, and Backtest tab."""

from __future__ import annotations

from datetime import datetime
import html as html_lib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from .config import DEFAULT_FX_USD_THB, ROOT_DIR


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
        if age_h < 24:
            age_txt = f"{age_h:.1f} ชม. ที่แล้ว"
        else:
            age_txt = f"{age_h / 24.0:.1f} วัน ที่แล้ว"
        return f'<span style="font-size:11.5px;color:#94a3b8;font-weight:400;margin-left:8px;">(Cache: {age_txt} · รีเฟรชอัตโนมัติทุก 7 วัน หรือเมื่อราคาขยับ ±8%)</span>'
    return '<span style="font-size:11.5px;color:#4ade80;font-weight:600;margin-left:8px;">(อัปเดตใหม่ล่าสุด ✨)</span>'


def generate_html_report(
    results: list[dict[str, Any]],
    ai_weights: dict[str, float],
    signals_by_ticker: dict[str, list[dict]],
    fund_name: str | None,
    fx_usd_thb: float = DEFAULT_FX_USD_THB,
) -> Path:
    cards_html = []
    screener_rows_html = []
    chart_configs = []

    results_sorted = sorted(results, key=lambda x: x["dip_score"], reverse=True)

    for idx, info in enumerate(results_sorted):
        tkr = _esc(info["ticker"])
        tkr_raw = str(info["ticker"])
        comp_esc = _esc(info["company_name"])
        comp_short_esc = _esc(str(info["company_name"])[:18])
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

        hit_class = "hit-badge-hot" if info["hit_status"] in ("hit_t1", "hit_t2") else "hit-badge-wait"
        is_peg_value = "1" if (info["peg_ratio"] and 0 < info["peg_ratio"] <= 1.25) else "0"
        is_hit = "1" if info["hit_status"] in ("hit_t1", "hit_t2") else "0"
        is_high_score = "1" if info["dip_score"] >= 80 else "0"

        range_bar_html = f"""
        <div class="range-wrap" title="จุดต่ำสุด 1 ปี: {cur}{info['low_52w']:.2f} | ปัจจุบัน: {cur}{info['close']:.2f} | สูงสุด 1 ปี: {cur}{info['high_52w']:.2f}">
            <div class="range-labels"><span>L: {cur}{info['low_52w']:.0f}</span><span>H: {cur}{info['high_52w']:.0f}</span></div>
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
            earnings_badge_html = f'<span style="background:rgba(245,158,11,0.2);color:#fbbf24;border:1px solid #f59e0b;padding:2px 8px;border-radius:999px;font-size:11.5px;font-weight:700;white-space:nowrap;">⚠️ งบใน {days_ed} วัน ({_esc(nxt_ed)})</span>'
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
                <div style="flex:1;min-width:280px;">
                    <div style="font-size:13px;font-weight:700;color:#38bdf8;margin-bottom:6px;">📊 แนวโน้มงบ 4 ไตรมาสล่าสุด (Revenue & Net Income)</div>
                    <table><thead><tr><th>งวด</th><th>รายได้</th><th>กำไรสุทธิ</th><th>Net Margin</th></tr></thead><tbody>{q_rows_html}</tbody></table>
                </div>
            """ if q_rows_html else ""
            e_part = f"""
                <div style="flex:1;min-width:280px;">
                    <div style="font-size:13px;font-weight:700;color:#4ade80;margin-bottom:6px;">🎯 ประวัติ EPS Beat/Miss ({eps_badge_str})</div>
                    <table><thead><tr><th>งวด</th><th>คาดการณ์</th><th>ทำได้จริง</th><th>ผลลัพธ์</th></tr></thead><tbody>{eps_rows_html}</tbody></table>
                </div>
            """ if eps_rows_html else ""
            quarterly_section = f'<div class="collapsible-section" style="display:flex;flex-wrap:wrap;gap:14px;margin-bottom:16px;">{q_part}{e_part}</div>'

        screener_rows_html.append(f"""
        <tr onclick="openStockDeepDive('{tkr}')" style="cursor:pointer;" title="คลิกเพื่อเปิดดูกราฟและบทวิเคราะห์เจาะลึกของ {tkr}">
            <td><strong style="color:#38bdf8;font-size:15px;">{tkr} 🔍</strong> {earnings_badge_html}<br><small>{comp_short_esc}</small></td>
            <td data-val="{info['close']}"><strong style="color:#facc15;">{cur}{info['close']:.2f}</strong><br><span style="color:#38bdf8;font-size:12px;">{info['drawdown_pct']:.1f}% จาก High</span></td>
            <td data-val="{info['pos_52w_pct']}">{range_bar_html}</td>
            <td data-val="{1 if is_hit == '1' else 0}"><span class="{hit_class}">{hit_label_esc}</span></td>
            <td data-val="{info['dip_score']}"><span style="color:{info['zone_color']};font-weight:700;font-size:16px;">{info['dip_score']}/100</span></td>
            <td data-val="{info['rsi']}" class="{'green' if info['rsi'] < 40 else ''}"><strong>{info['rsi']:.1f}</strong></td>
            <td data-val="{fpe_val}">{fpe_str}</td>
            <td data-val="{peg_val}" class="{'green' if is_peg_value == '1' else ''}"><strong>{peg_str}</strong></td>
            <td data-val="{upside_val}" class="{'green' if upside_val > 20 else ''}">{ws_str}</td>
            <td><span class="green">{cur}{info['tier1_price']:.2f}</span> / <span style="color:#22c55e;font-weight:700;">{cur}{info['tier2_price']:.2f}</span> / <span style="color:#facc15;">{cur}{info['tier3_price']:.2f}</span></td>
        </tr>
        """)

        sig_rows = ""
        for s in sigs:
            val = float(s.get("value") or 0.0)
            if val > 0.1:
                v_badge = f'<span style="color:#22c55e;font-weight:700;">▲ มองบวก ({abs(val)*100:.0f}%)</span>'
            elif val < -0.1:
                v_badge = f'<span style="color:#f59e0b;font-weight:700;">▼ มองราคาตึงตัว ({abs(val)*100:.0f}%)</span>'
            else:
                v_badge = f'<span style="color:#94a3b8;font-weight:700;">- เป็นกลาง ({abs(val)*100:.0f}%)</span>'
            sig_rows += f"""
            <div class="analyst-item">
                <div class="analyst-head">{_esc(str(s.get('model_name', '')).upper())} &middot; {v_badge}</div>
                <div class="analyst-body">{_esc(s.get('reasoning', ''))}</div>
            </div>
            """
        analysts_section = f"""
        <details class="analysts-box">
            <summary>คลิกดูความเห็นต้นฉบับจากนักวิเคราะห์ AI รายบุคคล (Buffett / Munger / Graham / Lynch / Druckenmiller)</summary>
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
        <div class="card stock-card" id="stock-{tkr}" data-ticker="{tkr}" data-score="{is_high_score}" data-hit="{is_hit}" data-peg="{is_peg_value}">
            <div class="card-header">
                <div style="display:flex;align-items:center;flex-wrap:wrap;gap:10px;">
                    <span class="ticker">{tkr}</span>
                    <span class="comp-name">{comp_esc}</span>
                    <span class="price">{cur}{info['close']:.2f}</span>
                    <span class="discount">ลดจากยอด 1 ปี: {info['drawdown_pct']:.1f}%</span>
                    <span class="{hit_class}">{hit_label_esc}</span>
                    {earnings_badge_html}
                </div>
                <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
                    <span class="dip-score" style="border-color:{info['zone_color']};color:{info['zone_color']}">
                        คะแนนความน่าช้อน: {info['dip_score']}/100 &middot; {dca_mult_esc}
                    </span>
                    <button class="btn-remove" onclick="removeStock('{tkr}')" title="ลบหุ้น {tkr} ออกจากพอร์ต">✕ ลบ</button>
                </div>
            </div>

            <div class="cio-box" style="border-left: 4px solid {info['zone_color']};">
                <div class="cio-title">🧠 บทวิเคราะห์เจาะลึกจาก AI Chief Investment Officer {cio_meta_badge}</div>
                <div class="cio-headline">{_esc(cio.get('verdict_headline', ''))}</div>
                <div class="cio-grid">
                    <div class="cio-item">
                        <strong>🔍 วิเคราะห์: โอกาสช้อน (Buy the Dip) หรือ กับดัก (Value Trap)?</strong>
                        <p>{_esc(cio.get('dip_vs_trap_analysis', ''))}</p>
                    </div>
                    <div class="cio-item">
                        <strong>💎 ความคุ้มค่าและ Upside เทียบเป้า Wall Street:</strong>
                        <p>{_esc(cio.get('valuation_and_upside', ''))}</p>
                    </div>
                    <div class="cio-item">
                        <strong>🎯 แผนเคาะซื้อและแบ่งไม้สะสม (Actionable Playbook):</strong>
                        <p>{_esc(cio.get('actionable_dca_playbook', ''))}</p>
                    </div>
                    <div class="cio-item">
                        <strong>⚠️ ความเสี่ยงสำคัญที่ต้องระวัง:</strong>
                        <p>{_esc(cio.get('key_risk_to_watch', ''))}</p>
                    </div>
                </div>
            </div>

            <div class="metrics-grid">
                <div class="metric"><span class="label">เป้าเฉลี่ย Wall Street ({info['num_analysts']} สำนัก)</span><span class="val green">{ws_str}</span></div>
                <div class="metric"><span class="label">Forward P/E &middot; PEG Ratio</span><span class="val cyan">{fpe_str} &middot; PEG {peg_str}</span></div>
                <div class="metric"><span class="label">การเติบโต รายได้ / กำไร (YoY)</span><span class="val">{rev_g} / {earn_g}</span></div>
                <div class="metric"><span class="label">ROE &middot; ต้นทุนเฉลี่ย EMA 200</span><span class="val">{roe_str} &middot; {cur}{info['ema200']:.2f} ({info['dist_ema200_pct']:+.1f}%)</span></div>
                <div class="metric"><span class="label">โซนเจ้ามือสะสม (Volume Node)</span><span class="val green">{cur}{info['vp_node_1']:.2f}</span></div>
                <div class="metric"><span class="label">ดัชนี RSI (14 วัน)</span><span class="val {'green' if info['rsi'] < 40 else ''}">{info['rsi']:.1f} {'(Oversold ของถูก!)' if info['rsi'] < 35 else ''}</span></div>
                <div class="metric"><span class="label">📅 วันประกาศงบถัดไป</span><span class="val" style="color:{'#fbbf24' if (days_ed is not None and days_ed <= 14) else '#e2e8f0'}">{earnings_metric_str}</span></div>
                <div class="metric"><span class="label">🎯 ประวัติ EPS Surprise</span><span class="val green">{eps_badge_str}</span></div>
            </div>

            {quarterly_section}

            <div class="chart-wrapper collapsible-section">
                <div class="chart-legend">
                    <span><i style="background:#f59e0b"></i> EMA 50 ({cur}{info['ema50']:.2f})</span>
                    <span><i style="background:#a855f7"></i> EMA 200 ต้นทุนระยะยาว ({cur}{info['ema200']:.2f})</span>
                    <span><i style="background:#4ade80"></i> ไม้ 1 (30%): {cur}{info['tier1_price']:.2f}</span>
                    <span><i style="background:#22c55e"></i> ไม้ 2 (40%): {cur}{info['tier2_price']:.2f}</span>
                    <span><i style="background:#eab308"></i> ไม้ 3 (30%): {cur}{info['tier3_price']:.2f}</span>
                </div>
                <div id="{chart_id}" class="tv-chart"></div>
            </div>

            <table class="collapsible-section">
                <thead>
                    <tr>
                        <th>ลำดับไม้ช้อนซื้อ (Buy the Dip Confluence Ladder)</th>
                        <th>ราคาตั้งรับ (Target Buy Price)</th>
                        <th>ส่วนลดจากยอด 1 ปี</th>
                        <th>สัดส่วนงบสะสม</th>
                        <th>จุดอ้างอิงเชิงเทคนิคและปริมาณการซื้อขาย (Confluence)</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td><strong style="color:#4ade80">ไม้ที่ 1: โซนสะสมแรก (Mild Dip / Support Node)</strong></td>
                        <td class="green"><strong>{cur}{info['tier1_price']:.2f}</strong></td>
                        <td>{info['tier1_disc']:.1f}% จาก High</td>
                        <td><strong>30% ของงบ</strong></td>
                        <td>แนวรับ Volume สะสม ({cur}{info['vp_node_1']:.2f}) / Fibonacci 38.2% ({cur}{info['fib_382']:.2f}) / EMA 50</td>
                    </tr>
                    <tr>
                        <td><strong style="color:#22c55e">ไม้ที่ 2: โซนคุณค่าหลัก (Major Value Zone)</strong></td>
                        <td class="green"><strong>{cur}{info['tier2_price']:.2f}</strong></td>
                        <td>{info['tier2_disc']:.1f}% จาก High</td>
                        <td><strong>40% ของงบ (ไม้หลัก)</strong></td>
                        <td>เส้นต้นทุนระยะยาว EMA 200 ({cur}{info['ema200']:.2f}) / Fibonacci 50% ({cur}{info['fib_500']:.2f})</td>
                    </tr>
                    <tr>
                        <td><strong style="color:#facc15">ไม้ที่ 3: โซนลดราคาพิเศษ (Deep Panic Bargain)</strong></td>
                        <td style="color:#facc15"><strong>{cur}{info['tier3_price']:.2f}</strong></td>
                        <td>{info['tier3_disc']:.1f}% จาก High</td>
                        <td><strong>30% สุดท้าย</strong></td>
                        <td>แนวรับใหญ่ Fibonacci 61.8% ({cur}{info['fib_618']:.2f}) / โซนใกล้จุดต่ำสุดรอบ 1 ปี ({cur}{info['low_52w']:.2f})</td>
                    </tr>
                </tbody>
            </table>
            {analysts_section}
        </div>
        """)

    nav_pills = "".join(
        f'<button class="stock-pick-btn" data-pick="{_esc(r["ticker"])}" onclick="openStockDeepDive(\'{_esc(r["ticker"])}\')">{_esc(r["ticker"])} <small>({r["dip_score"]})</small></button>'
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
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
            <div>
              <span class="rank-badge">#{rank} น่าช้อนสูงสุด</span>
              <strong style="font-size:22px;color:#fff;margin-left:6px;">{rtkr}</strong>
              <span style="color:#facc15;font-weight:700;font-size:18px;margin-left:6px;">{rcur}{r['close']:.2f}</span>
            </div>
            <span style="color:#4ade80;font-weight:700;font-size:16px;">Score {r['dip_score']}/100</span>
          </div>
          <div style="font-size:13px;color:#38bdf8;margin-bottom:8px;">
            ลดจาก High {r['drawdown_pct']:.1f}% &middot; Fwd P/E {fpe_s} &middot; PEG {peg_s} &middot; Upside {up_s}
          </div>
          <div style="font-size:13.5px;color:#cbd5e1;margin-bottom:10px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;">
            {cio_h}
          </div>
          <div style="display:flex;justify-content:space-between;align-items:center;border-top:1px solid #1e293b;padding-top:8px;font-size:12.5px;">
            <span>ไม้ 1: <strong class="green">{rcur}{r['tier1_price']:.2f}</strong> | ไม้ 2: <strong class="green">{rcur}{r['tier2_price']:.2f}</strong></span>
            <span style="color:#38bdf8;font-weight:700;">ดูกราฟ & แผนช้อน →</span>
          </div>
        </div>
        """

    charts_json = json.dumps(chart_configs).replace("</", "<\\/")
    updated_at_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fx_display = f"{fx_usd_thb:.2f}"

    html = f"""<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="utf-8">
<title>AI Chief Investment Officer — ระบบวางแผนออมหุ้น Smart DCA & Buy the Dip (v4.0)</title>
<script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Sarabun:wght@400;600;700&display=swap');
  body {{
    font-family: 'Sarabun', 'Segoe UI', Tahoma, sans-serif;
    background: #0b0f19;
    color: #f1f5f9;
    margin: 0;
    padding: 24px;
    line-height: 1.65;
  }}
  .container {{ max-width: 1240px; margin: 0 auto; }}
  h1 {{ margin-top: 0; color: #4ade80; font-size: 26px; }}
  .subtitle {{ color: #94a3b8; margin-bottom: 16px; }}
  .nav-bar {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 20px; position: sticky; top: 8px; z-index: 50; background: rgba(15,23,42,0.94); padding: 10px 14px; border-radius: 10px; border: 1px solid #334155; backdrop-filter: blur(8px); align-items: center; }}
  .nav-pill {{ color: #e2e8f0; text-decoration: none; background: #1e293b; padding: 5px 12px; border-radius: 6px; font-weight: 700; font-size: 13.5px; border: 1px solid #334155; transition: 0.15s; }}
  .nav-pill:hover {{ background: #22c55e; color: #0f172a; border-color: #22c55e; }}
  @keyframes pulseGlow {{
    0%, 100% {{ box-shadow: 0 0 0 0 rgba(34, 197, 94, 0.45); }}
    50% {{ box-shadow: 0 0 12px 3px rgba(34, 197, 94, 0.35); }}
  }}
  .hit-badge-hot {{
    background: rgba(34, 197, 94, 0.22);
    color: #4ade80;
    border: 1px solid #22c55e;
    padding: 4px 10px;
    border-radius: 999px;
    font-size: 12.5px;
    font-weight: 700;
    animation: pulseGlow 2.2s infinite;
    white-space: nowrap;
  }}
  .hit-badge-wait {{
    background: rgba(148, 163, 184, 0.12);
    color: #94a3b8;
    border: 1px solid #334155;
    padding: 4px 10px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 600;
    white-space: nowrap;
  }}
  .range-wrap {{ width: 130px; }}
  .range-labels {{ display: flex; justify-content: space-between; font-size: 10.5px; color: #94a3b8; margin-bottom: 3px; }}
  .range-track {{ position: relative; height: 6px; background: #1e293b; border-radius: 999px; }}
  .range-fill {{ height: 100%; background: linear-gradient(90deg, #22c55e 0%, #38bdf8 60%, #f59e0b 100%); border-radius: 999px; }}
  .range-dot {{ position: absolute; top: -3px; width: 12px; height: 12px; background: #fff; border: 2px solid #0b0f19; border-radius: 50%; transform: translateX(-50%); }}
  .filter-bar {{
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: center;
    gap: 10px;
    background: #131b2e;
    border: 1px solid #26334d;
    padding: 12px 16px;
    border-radius: 12px;
    margin-bottom: 22px;
  }}
  .filter-tabs {{ display: flex; flex-wrap: wrap; gap: 8px; }}
  .filter-btn {{
    background: #0b0f19;
    color: #cbd5e1;
    border: 1px solid #334155;
    padding: 7px 14px;
    border-radius: 8px;
    font-family: inherit;
    font-size: 13.5px;
    font-weight: 700;
    cursor: pointer;
    transition: 0.15s;
  }}
  .filter-btn.active, .filter-btn:hover {{ background: #22c55e; color: #0b0f19; border-color: #22c55e; }}
  .screener-card {{
    background: #131b2e;
    border: 1px solid #334155;
    border-radius: 14px;
    padding: 20px 22px;
    margin-bottom: 26px;
  }}
  .screener-card th {{ cursor: pointer; user-select: none; }}
  .screener-card th:hover {{ color: #38bdf8; }}
  .card {{
    background: #131b2e;
    border: 1px solid #26334d;
    border-radius: 14px;
    padding: 24px;
    margin-bottom: 30px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.4);
  }}
  .card-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
    border-bottom: 1px solid #26334d;
    padding-bottom: 12px;
    margin-bottom: 16px;
  }}
  .ticker {{ font-size: 28px; font-weight: 700; color: #fff; }}
  .comp-name {{ font-size: 15px; color: #94a3b8; }}
  .price {{ font-size: 24px; font-weight: 700; color: #facc15; }}
  .discount {{ font-size: 13.5px; color: #38bdf8; background: rgba(56,189,248,0.12); padding: 4px 10px; border-radius: 6px; font-weight: 600; }}
  .dip-score {{ padding: 6px 14px; border-radius: 999px; font-weight: 700; font-size: 14px; border: 1px solid #22c55e; background: rgba(15,23,42,0.8); }}
  .cio-box {{
    background: linear-gradient(135deg, #0f172a 0%, #172554 100%);
    border: 1px solid #1e3a8a;
    border-radius: 10px;
    padding: 18px 20px;
    margin-bottom: 18px;
  }}
  .cio-title {{ font-size: 14px; font-weight: 700; color: #38bdf8; letter-spacing: 0.4px; margin-bottom: 6px; }}
  .cio-headline {{ font-size: 19px; font-weight: 700; color: #4ade80; margin-bottom: 14px; }}
  .cio-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(460px, 1fr)); gap: 14px; }}
  .cio-item {{ background: rgba(11,15,25,0.65); padding: 12px 16px; border-radius: 8px; border: 1px solid rgba(51,65,85,0.7); }}
  .cio-item strong {{ display: block; color: #f8fafc; font-size: 14px; margin-bottom: 4px; }}
  .cio-item p {{ margin: 0; color: #cbd5e1; font-size: 14.5px; }}
  .chart-wrapper {{ background: #0b0f19; border: 1px solid #1e293b; border-radius: 10px; padding: 12px; margin-bottom: 16px; }}
  .chart-legend {{ display: flex; flex-wrap: wrap; gap: 14px; font-size: 13px; color: #cbd5e1; margin-bottom: 8px; }}
  .chart-legend span {{ display: inline-flex; align-items: center; gap: 6px; }}
  .chart-legend i {{ display: inline-block; width: 12px; height: 3px; border-radius: 2px; }}
  .tv-chart {{ width: 100%; height: 400px; }}
  .metrics-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; margin-bottom: 16px; }}
  .metric {{ background: #0b0f19; padding: 10px 14px; border-radius: 8px; border: 1px solid #1e293b; }}
  .metric .label {{ display: block; font-size: 12px; color: #94a3b8; }}
  .metric .val {{ font-size: 16px; font-weight: 700; }}
  .green {{ color: #4ade80; }}
  .cyan {{ color: #38bdf8; }}
  .red {{ color: #f87171; }}
  table {{ width: 100%; border-collapse: collapse; background: #0b0f19; border-radius: 8px; overflow: hidden; }}
  th, td {{ padding: 11px 13px; text-align: left; border-bottom: 1px solid #1e293b; font-size: 14px; }}
  th {{ background: #1e293b; color: #e2e8f0; font-size: 13.5px; }}
  .analysts-box {{ margin-top: 14px; background: #0b0f19; padding: 10px 14px; border-radius: 8px; }}
  .analysts-box summary {{ cursor: pointer; font-weight: 600; color: #38bdf8; }}
  .analyst-item {{ padding: 10px 0; border-bottom: 1px solid #1e293b; }}
  .analyst-head {{ font-size: 14px; margin-bottom: 4px; }}
  .analyst-body {{ font-size: 14px; color: #cbd5e1; }}
  .calc-card {{
    background: linear-gradient(145deg, #0f172a 0%, #1e293b 100%);
    border: 2px solid #22c55e;
    border-radius: 14px;
    padding: 22px 24px;
    margin-bottom: 24px;
    box-shadow: 0 10px 30px rgba(34, 197, 94, 0.15);
  }}
  .calc-title {{ font-size: 21px; font-weight: 700; color: #4ade80; margin-bottom: 6px; }}
  .calc-sub {{ font-size: 14px; color: #94a3b8; margin-bottom: 18px; }}
  .calc-controls {{ display: flex; flex-wrap: wrap; gap: 16px; align-items: flex-end; background: #0b0f19; padding: 16px; border-radius: 10px; border: 1px solid #334155; margin-bottom: 16px; }}
  .ctrl-group {{ display: flex; flex-direction: column; gap: 6px; }}
  .ctrl-group label {{ font-size: 13px; color: #cbd5e1; font-weight: 600; }}
  .ctrl-group input, .ctrl-group select {{ background: #1e293b; color: #fff; border: 1px solid #475569; border-radius: 8px; padding: 9px 14px; font-size: 16px; font-weight: 700; font-family: inherit; outline: none; }}
  .ctrl-group input:focus, .ctrl-group select:focus {{ border-color: #4ade80; }}
  .ticker-toggles {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px; align-items: center; }}
  .ticker-toggle {{ display: inline-flex; align-items: center; gap: 6px; background: #0b0f19; border: 1px solid #334155; padding: 6px 12px; border-radius: 8px; cursor: pointer; user-select: none; font-size: 14px; font-weight: 700; }}
  .ticker-toggle input {{ accent-color: #22c55e; width: 16px; height: 16px; cursor: pointer; }}
  .calc-summary-bar {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 16px; }}
  .sum-box {{ background: #0b0f19; padding: 12px 16px; border-radius: 8px; border: 1px solid #1e293b; }}
  .sum-box .s-label {{ font-size: 12px; color: #94a3b8; display: block; }}
  .sum-box .s-val {{ font-size: 18px; font-weight: 700; }}
  .add-bar {{ background: #131b2e; border: 1px solid #38bdf8; border-radius: 12px; padding: 14px 18px; margin-bottom: 18px; display: flex; flex-wrap: wrap; gap: 12px; align-items: center; justify-content: space-between; }}
  .add-form {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
  .add-input {{ background: #0b0f19; color: #fff; border: 1px solid #475569; border-radius: 8px; padding: 8px 14px; font-size: 15px; font-weight: 700; width: 240px; text-transform: uppercase; }}
  .btn-add {{ background: #22c55e; color: #0b0f19; border: none; border-radius: 8px; padding: 9px 16px; font-weight: 700; font-size: 14px; cursor: pointer; font-family: inherit; transition: 0.15s; }}
  .btn-add:hover {{ background: #4ade80; }}
  .btn-add:disabled {{ opacity: 0.6; cursor: wait; }}
  .quick-chip {{ background: #1e293b; color: #38bdf8; border: 1px solid #334155; border-radius: 6px; padding: 4px 10px; font-size: 13px; font-weight: 700; cursor: pointer; font-family: inherit; }}
  .quick-chip:hover {{ background: #38bdf8; color: #0b0f19; }}
  .btn-remove {{ background: rgba(239,68,68,0.15); color: #f87171; border: 1px solid rgba(239,68,68,0.4); border-radius: 8px; padding: 5px 10px; font-size: 12px; font-weight: 700; cursor: pointer; }}
  .btn-remove:hover {{ background: #ef4444; color: #fff; }}
  body.compact-mode .collapsible-section {{ display: none; }}
  .top-navbar {{ position: sticky; top: 0; z-index: 100; background: rgba(11, 15, 25, 0.95); border-bottom: 1px solid #26334d; backdrop-filter: blur(12px); margin: -24px -24px 24px -24px; padding: 12px 24px; }}
  .navbar-inner {{ max-width: 1240px; margin: 0 auto; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 14px; }}
  .brand-box {{ display: flex; align-items: center; gap: 10px; }}
  .brand-title {{ font-size: 20px; font-weight: 700; color: #4ade80; margin: 0; }}
  .nav-tabs {{ display: flex; gap: 6px; flex-wrap: wrap; background: #131b2e; padding: 5px; border-radius: 12px; border: 1px solid #26334d; }}
  .nav-tab-btn {{ background: transparent; color: #cbd5e1; border: none; padding: 8px 14px; border-radius: 8px; font-family: inherit; font-size: 14px; font-weight: 700; cursor: pointer; transition: 0.18s; display: inline-flex; align-items: center; gap: 6px; }}
  .nav-tab-btn:hover {{ color: #fff; background: rgba(56, 189, 248, 0.12); }}
  .nav-tab-btn.active {{ background: linear-gradient(135deg, #22c55e 0%, #16a34a 100%); color: #0b0f19; box-shadow: 0 4px 12px rgba(34, 197, 94, 0.3); }}
  .tab-pane {{ display: none; }}
  .tab-pane.active {{ display: block; animation: fadeIn 0.2s ease-out; }}
  @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(4px); }} to {{ opacity: 1; transform: translateY(0); }} }}
  .top3-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 16px; margin-bottom: 24px; }}
  .top3-card {{ background: linear-gradient(145deg, #131b2e 0%, #0f172a 100%); border: 1px solid #22c55e; border-radius: 12px; padding: 16px 18px; cursor: pointer; transition: 0.18s; box-shadow: 0 6px 18px rgba(0,0,0,0.3); }}
  .top3-card:hover {{ transform: translateY(-3px); border-color: #4ade80; box-shadow: 0 10px 24px rgba(34,197,94,0.18); }}
  .rank-badge {{ background: rgba(34,197,94,0.2); color: #4ade80; border: 1px solid #22c55e; padding: 2px 8px; border-radius: 6px; font-size: 12px; font-weight: 700; }}
  .stock-pick-btn {{ background: #1e293b; color: #e2e8f0; border: 1px solid #334155; padding: 7px 14px; border-radius: 8px; font-family: inherit; font-weight: 700; font-size: 14px; cursor: pointer; transition: 0.15s; }}
  .stock-pick-btn:hover, .stock-pick-btn.active {{ background: #38bdf8; color: #0b0f19; border-color: #38bdf8; }}
</style>
</head>
<body>

<header class="top-navbar">
  <div class="navbar-inner">
    <div class="brand-box">
      <span style="font-size:24px;">🧠</span>
      <div>
        <div class="brand-title">Smart DCA & AI CIO <small style="font-size:12px;color:#38bdf8;">v4.0</small></div>
        <div style="font-size:11.5px;color:#94a3b8;">SQLite Cache &middot; Batch Engine &middot; Live FX ฿{fx_display}/USD</div>
      </div>
    </div>

    <nav class="nav-tabs">
      <button class="nav-tab-btn active" id="tabBtn-overview" onclick="switchMainTab('overview')">
        📊 1. ภาพรวม ({len(results_sorted)})
      </button>
      <button class="nav-tab-btn" id="tabBtn-calculator" onclick="switchMainTab('calculator')">
        💰 2. คำนวณ DCA
      </button>
      <button class="nav-tab-btn" id="tabBtn-deepdive" onclick="switchMainTab('deepdive')">
        📈 3. เจาะลึก & กราฟ
      </button>
      <button class="nav-tab-btn" id="tabBtn-backtest" onclick="switchMainTab('backtest')">
        🧪 4. Backtest
      </button>
    </nav>

    <div style="display:flex;align-items:center;gap:10px;">
      <span style="font-size:12.5px;color:#94a3b8;">🕒 อัปเดต: <strong style="color:#4ade80;">{updated_at_str}</strong></span>
      <button class="quick-chip" style="background:#0284c7;color:#fff;border-color:#38bdf8;padding:7px 12px;" onclick="refreshAllPrices()">🔄 ดึงราคาล่าสุด</button>
    </div>
  </div>
</header>

<div class="container">

  <!-- ==================== TAB 1: OVERVIEW & SCREENER ==================== -->
  <section id="tab-overview" class="tab-pane active">
    <div class="add-bar">
      <div class="add-form">
        <strong style="color:#38bdf8;font-size:15px;">➕ เพิ่มหุ้นเข้าพอร์ต:</strong>
        <input type="text" id="newTickerInput" class="add-input" placeholder="เช่น AAPL, AMD, AVGO, PTT.BK" onkeydown="if(event.key==='Enter') addStockFromInput()">
        <button id="addStockBtn" class="btn-add" onclick="addStockFromInput()">➕ เพิ่มและให้ AI วิเคราะห์</button>
        <span id="addStatusMsg" style="font-size:13px;color:#facc15;font-weight:600;"></span>
      </div>
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
        <small style="color:#94a3b8;">เพิ่มด่วน:</small>
        <button class="quick-chip" onclick="quickAdd('AAPL')">+ AAPL</button>
        <button class="quick-chip" onclick="quickAdd('AMZN')">+ AMZN</button>
        <button class="quick-chip" onclick="quickAdd('AMD')">+ AMD</button>
        <button class="quick-chip" onclick="quickAdd('AVGO')">+ AVGO</button>
        <button class="quick-chip" onclick="quickAdd('COST')">+ COST</button>
        <button class="quick-chip" onclick="quickAdd('BRK-B')">+ BRK-B</button>
        <button class="quick-chip" onclick="quickAdd('PTT.BK')">+ PTT.BK</button>
      </div>
    </div>

    <div style="margin-bottom:10px;display:flex;justify-content:space-between;align-items:center;">
      <strong style="font-size:18px;color:#4ade80;">🏆 Top 3 หุ้นลดราคาน่าช้อนสะสมที่สุดประจำวันนี้ (คลิกเพื่อดูกราฟและแผนช้อนซื้อ)</strong>
      <button class="quick-chip" onclick="switchMainTab('calculator')" style="padding:6px 14px;">ไปหน้าคำนวณแบ่งเงินออม 💰 →</button>
    </div>
    <div class="top3-grid">
      {top3_cards_html}
    </div>

    <div class="screener-card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
        <div>
          <strong style="font-size:18px;color:#38bdf8;">📊 ตารางสแกนเปรียบเทียบหุ้นทั้งพอร์ต (Executive Portfolio Screener)</strong>
          <div style="font-size:13px;color:#94a3b8;">คลิกที่หัวตาราง (⇅) เพื่อเรียงลำดับตามค่าที่ต้องการ หรือ <strong>คลิกที่ชื่อหุ้น</strong> เพื่อเปิดดูกราฟและบทวิเคราะห์ AI ของหุ้นตัวนั้น</div>
        </div>
      </div>
      <div style="overflow-x:auto;">
        <table id="screenerTable">
          <thead>
            <tr>
              <th onclick="sortScreener(0, false)">หุ้น ⇅</th>
              <th onclick="sortScreener(1, true)">ราคาล่าสุด ⇅</th>
              <th onclick="sortScreener(2, true)">กรอบราคา 1 ปี (52W Range) ⇅</th>
              <th onclick="sortScreener(3, true)">สถานะจุดช้อน ⇅</th>
              <th onclick="sortScreener(4, true)">Dip Score ⇅</th>
              <th onclick="sortScreener(5, true)">RSI(14) ⇅</th>
              <th onclick="sortScreener(6, true)">Fwd P/E ⇅</th>
              <th onclick="sortScreener(7, true)">PEG ⇅</th>
              <th onclick="sortScreener(8, true)">เป้า Wall St (Upside) ⇅</th>
              <th>จุดตั้งรับ ไม้ 1 / ไม้ 2 / ไม้ 3</th>
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
    <div class="calc-card">
      <div class="calc-title">💰 เครื่องคิดเลขแบ่งเงินออมรายเดือนอัตโนมัติ (Interactive Smart DCA Budget Allocator)</div>
      <div class="calc-sub">รองรับหุ้นหลายสกุลเงิน (USD / THB) พร้อมอัตราแลกเปลี่ยนจริงจาก Yahoo Finance ({fx_display} บาท/USD)</div>

      <div class="calc-controls">
        <div class="ctrl-group">
          <label for="budgetInput">💵 งบออมเดือนนี้ (Monthly Budget)</label>
          <input type="number" id="budgetInput" value="1000" min="10" step="50" oninput="saveAndRecalc()">
        </div>
        <div class="ctrl-group">
          <label for="currencySelect">🌐 สกุลเงินที่แสดง</label>
          <select id="currencySelect" onchange="onCurrencyChange()">
            <option value="USD" selected>ดอลลาร์สหรัฐ (USD $)</option>
            <option value="THB">บาทไทย (THB ฿)</option>
          </select>
        </div>
        <div class="ctrl-group" id="fxGroup" style="display:none;">
          <label for="fxInput">💱 อัตราแลกเปลี่ยนสด (บาท / 1 USD)</label>
          <input type="number" id="fxInput" value="{fx_display}" min="20" max="50" step="0.05" style="width:120px;" oninput="saveAndRecalc()">
        </div>
        <div class="ctrl-group">
          <label for="modeSelect">⚖️ สูตรการกระจายเงิน (Allocation Strategy)</label>
          <select id="modeSelect" onchange="saveAndRecalc()">
            <option value="smart_dip" selected>เน้นตัวลดราคาแรง + พื้นฐานดี (Smart Dip-Weighted — แนะนำ)</option>
            <option value="equal_mult">ตั้งต้นเท่ากัน + คูณตามตัวคูณ DCA (Equal + Multiplier)</option>
            <option value="top3_only">โฟกัสเฉพาะ 3 อันดับแรกที่น่าช้อนที่สุด (Top 3 Conviction Dip)</option>
          </select>
        </div>
      </div>

      <div class="ticker-toggles" id="tickerToggles">
        <span style="font-size:13px;color:#94a3b8;margin-right:4px;">เลือกหุ้นที่จะออมเดือนนี้:</span>
      </div>

      <div class="calc-summary-bar">
        <div class="sum-box"><span class="s-label">งบออมรวมทั้งหมด</span><span class="s-val cyan" id="sumTotal">$1,000.00</span></div>
        <div class="sum-box"><span class="s-label">🟢 เคาะซื้อทันทีวันนี้ (Market Order)</span><span class="s-val green" id="sumBuyNow">$0.00</span></div>
        <div class="sum-box"><span class="s-label">🎯 ตั้งรอรับไม้ 1 + ไม้ 2 (Limit Orders)</span><span class="s-val" style="color:#38bdf8" id="sumLimit12">$0.00</span></div>
        <div class="sum-box"><span class="s-label">🛡️ เงินสดสำรองรอช้อนไม้ 3 (Panic Reserve)</span><span class="s-val" style="color:#facc15" id="sumReserve">$0.00</span></div>
      </div>

      <div style="overflow-x:auto;">
        <table id="calcTable">
          <thead>
            <tr>
              <th>หุ้น (คลิกดูกราฟ)</th>
              <th>คะแนนน่าช้อน</th>
              <th>สัดส่วนพอร์ต</th>
              <th>งบรวมที่ได้</th>
              <th>🟢 เคาะซื้อวันนี้ (ไม่ตกรถ)</th>
              <th>🎯 ตั้งรับ ไม้ 1 (Limit)</th>
              <th>💎 ตั้งรับ ไม้ 2 (Limit)</th>
              <th>🛡️ สำรอง ไม้ 3 (เผื่อวิกฤต)</th>
            </tr>
          </thead>
          <tbody id="calcTableBody"></tbody>
        </table>
      </div>
    </div>
  </section>

  <!-- ==================== TAB 3: AI DEEP DIVE & CHARTS ==================== -->
  <section id="tab-deepdive" class="tab-pane">
    <div class="nav-bar">
      <span style="color:#94a3b8;font-size:13px;margin-right:6px;">เลือกดูหุ้นรายตัว:</span>
      {nav_pills}
      <button class="filter-btn active" id="showAllStocksBtn" onclick="filterCards('all', this)" style="margin-left:auto;">📋 ดูทุกตัวเรียงกัน ({len(results_sorted)})</button>
    </div>

    <div class="filter-bar">
      <div class="filter-tabs">
        <button class="filter-btn" onclick="filterCards('score', this)">🔥 น่าช้อนทันที Score 80+</button>
        <button class="filter-btn" onclick="filterCards('hit', this)">🎯 แตะโซนไม้ตั้งรับแล้ว</button>
        <button class="filter-btn" onclick="filterCards('peg', this)">💎 หุ้นเติบโตไม่แพง (PEG &le; 1.25)</button>
      </div>
      <div style="display:flex;gap:10px;align-items:center;">
        <button class="quick-chip" id="toggleCompactBtn" onclick="toggleCompactMode()" style="padding:7px 14px;">🗂️ ย่อกราฟทั้งหมด (อ่านเฉพาะบทวิเคราะห์ AI)</button>
      </div>
    </div>

    {''.join(cards_html)}
  </section>

  <!-- ==================== TAB 4: SMART DCA vs FIXED DCA BACKTEST ==================== -->
  <section id="tab-backtest" class="tab-pane">
    <div class="calc-card" style="border-color:#38bdf8;">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;margin-bottom:14px;">
        <div>
          <div class="calc-title" style="color:#38bdf8;">🧪 ทดสอบย้อนหลัง: Smart DCA vs Fixed DCA (Point-in-Time Backtester)</div>
          <div class="calc-sub" style="margin-bottom:0;">เปรียบเทียบการออมเท่ากันทุกเดือน (Fixed DCA) กับการปรับสัดส่วนออมตาม Dip Score + เก็บเงินสดรอช้อนตอนย่อแรง (Smart DCA) แบบไม่ใช้ข้อมูลอนาคต</div>
        </div>
        <div style="display:flex;gap:8px;align-items:center;">
          <select id="btPeriodSelect" style="background:#1e293b;color:#fff;border:1px solid #475569;border-radius:8px;padding:8px 12px;font-weight:700;font-family:inherit;">
            <option value="2y">ย้อนหลัง 2 ปี</option>
            <option value="3y" selected>ย้อนหลัง 3 ปี (แนะนำ)</option>
            <option value="5y">ย้อนหลัง 5 ปี</option>
          </select>
          <button id="runBtBtn" class="btn-add" style="background:#38bdf8;" onclick="runBacktestFromApi()">▶️ รัน Backtest</button>
        </div>
      </div>

      <div id="btStatus" style="font-size:14px;color:#facc15;margin-bottom:12px;"></div>

      <div class="calc-summary-bar" id="btSummaryBar" style="display:none;">
        <div class="sum-box"><span class="s-label">Fixed DCA ผลตอบแทนรวม</span><span class="s-val" id="btFixedRet">-</span></div>
        <div class="sum-box"><span class="s-label">Smart DCA ผลตอบแทนรวม</span><span class="s-val green" id="btSmartRet">-</span></div>
        <div class="sum-box"><span class="s-label">ส่วนต่างผลตอบแทน (Alpha)</span><span class="s-val cyan" id="btAlpha">-</span></div>
        <div class="sum-box"><span class="s-label">Max Drawdown เฉลี่ย (Fixed → Smart)</span><span class="s-val" id="btMaxDD">-</span></div>
      </div>

      <div style="overflow-x:auto;">
        <table id="btTable" style="display:none;">
          <thead>
            <tr>
              <th>หุ้น</th>
              <th>ช่วงเวลา</th>
              <th>Fixed DCA Return</th>
              <th>Smart DCA Return</th>
              <th>Alpha (ส่วนต่าง)</th>
              <th>ต้นทุนเฉลี่ย (Fixed → Smart)</th>
              <th>Max Drawdown (Fixed → Smart)</th>
              <th>จำนวนครั้งช้อน Dip</th>
            </tr>
          </thead>
          <tbody id="btTableBody"></tbody>
        </table>
      </div>
    </div>
  </section>

</div>

<script>
const CHART_CONFIGS = {charts_json};
const LIVE_FX_USD_THB = {fx_display};
const renderedCharts = new Map();

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
    layout: {{ background: {{ type: 'solid', color: '#0b0f19' }}, textColor: '#94a3b8' }},
    grid: {{ vertLines: {{ color: '#1e293b' }}, horzLines: {{ color: '#1e293b' }} }},
    crosshair: {{ mode: LightweightCharts.CrosshairMode.Normal }},
    rightPriceScale: {{ borderColor: '#334155' }},
    timeScale: {{ borderColor: '#334155', timeVisible: true }},
  }});
  renderedCharts.set(cfg.id, chart);

  const candleSeries = chart.addCandlestickSeries({{
    upColor: '#22c55e', downColor: '#ef4444', borderVisible: false, wickUpColor: '#22c55e', wickDownColor: '#ef4444',
  }});
  candleSeries.setData(cfg.candles);
  if (cfg.markers && cfg.markers.length > 0) candleSeries.setMarkers(cfg.markers);

  chart.addLineSeries({{ color: '#f59e0b', lineWidth: 2, title: 'EMA 50' }}).setData(cfg.ema50);
  chart.addLineSeries({{ color: '#a855f7', lineWidth: 2, title: 'EMA 200 (ต้นทุนระยะยาว)' }}).setData(cfg.ema200);

  candleSeries.createPriceLine({{ price: cfg.tier1, color: '#4ade80', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: 'ไม้ 1 (30%): ย่อสะสม' }});
  candleSeries.createPriceLine({{ price: cfg.tier2, color: '#22c55e', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Solid, axisLabelVisible: true, title: 'ไม้ 2 (40%): โซนคุณค่า' }});
  candleSeries.createPriceLine({{ price: cfg.tier3, color: '#eab308', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: 'ไม้ 3 (30%): ของถูกพิเศษ' }});

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
function switchMainTab(tabName) {{
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-tab-btn').forEach(b => b.classList.remove('active'));
  const targetPane = document.getElementById('tab-' + tabName);
  const targetBtn = document.getElementById('tabBtn-' + tabName);
  if (targetPane) targetPane.classList.add('active');
  if (targetBtn) targetBtn.classList.add('active');
  window.scrollTo({{ top: 0, behavior: 'smooth' }});
  if (tabName === 'deepdive') setTimeout(renderVisibleCharts, 60);
  if (tabName === 'backtest' && !backtestLoaded) runBacktestFromApi();
}}

function openStockDeepDive(ticker) {{
  switchMainTab('deepdive');
  document.querySelectorAll('.stock-pick-btn').forEach(b => b.classList.toggle('active', b.getAttribute('data-pick') === ticker));
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.stock-card').forEach(card => {{
    card.style.display = (card.getAttribute('data-ticker') === ticker) ? '' : 'none';
  }});
  setTimeout(renderVisibleCharts, 60);
}}

function filterCards(mode, btn) {{
  document.querySelectorAll('.stock-pick-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
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
    btn.textContent = '📈 แสดงกราฟ TradingView ทั้งหมด';
  }} else {{
    btn.textContent = '🗂️ ย่อกราฟทั้งหมด (อ่านเฉพาะบทวิเคราะห์ AI)';
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

// --- Interactive Monthly Budget Calculator Engine (Multi-Currency + Live FX) ---
const STORAGE_KEY = 'smart_dca_prefs_v4';
const selectedTickers = new Set(CHART_CONFIGS.map(c => c.ticker));

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

    // Accurate cross-currency share conversion
    let allocInStockCur = alloc;
    if (cur === 'THB' && c.currency === 'USD') allocInStockCur = alloc / fx;
    else if (cur === 'USD' && c.currency === 'THB') allocInStockCur = alloc * fx;
    const estShares = allocInStockCur / (c.close || 1);
    const sSym = c.currency_symbol || '$';

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><a href="javascript:void(0)" onclick="openStockDeepDive('${{c.ticker}}')" style="color:#38bdf8;font-weight:700;text-decoration:none;">${{c.ticker}} 🔍</a> <small>(${{sSym}}${{c.close.toFixed(2)}})</small></td>
      <td><span style="color:#4ade80;font-weight:700;">${{c.dip_score}}/100</span> <small>(${{c.dca_mult_num.toFixed(1)}}x)</small></td>
      <td><strong>${{(w * 100).toFixed(1)}}%</strong></td>
      <td style="color:#facc15;font-weight:700;">${{fmtMoney(alloc, cur)}}<br><small>≈ ${{estShares.toFixed(2)}} หุ้น</small></td>
      <td class="green"><strong>${{fmtMoney(aNow, cur)}}</strong><br><small>@ ตลาด (${{sSym}}${{c.close.toFixed(2)}})</small></td>
      <td class="cyan"><strong>${{fmtMoney(aT1, cur)}}</strong><br><small>@ ${{sSym}}${{c.tier1.toFixed(2)}}</small></td>
      <td class="cyan"><strong>${{fmtMoney(aT2, cur)}}</strong><br><small>@ ${{sSym}}${{c.tier2.toFixed(2)}}</small></td>
      <td style="color:#facc15"><strong>${{fmtMoney(aT3, cur)}}</strong><br><small>@ ${{sSym}}${{c.tier3.toFixed(2)}}</small></td>
    `;
    tbody.appendChild(tr);
  }});

  document.getElementById('sumTotal').textContent = fmtMoney(budget, cur);
  document.getElementById('sumBuyNow').textContent = fmtMoney(totalNow, cur);
  document.getElementById('sumLimit12').textContent = fmtMoney(totalLim12, cur);
  document.getElementById('sumReserve').textContent = fmtMoney(totalRes, cur);
}}

initBudgetCalculator();

// --- API Client (Portfolio + Backtest) ---
const API_BASE = (window.location.protocol === 'file:') ? 'http://127.0.0.1:8765' : '';

async function runBacktestFromApi() {{
  const period = document.getElementById('btPeriodSelect').value || '3y';
  const status = document.getElementById('btStatus');
  const btn = document.getElementById('runBtBtn');
  btn.disabled = true;
  status.textContent = `⏳ กำลังจำลอง Backtest (${{period}}) สำหรับหุ้นทั้งพอร์ตแบบ Batch...`;
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
    alphaEl.textContent = `${{s.portfolio_alpha_pct >= 0 ? '+' : ''}}${{s.portfolio_alpha_pct}}% (ชนะ ${{s.smart_wins}}/${{s.count}} ตัว)`;
    alphaEl.className = 's-val ' + (s.portfolio_alpha_pct >= 0 ? 'green' : 'red');
    document.getElementById('btMaxDD').textContent = `${{s.avg_fixed_max_dd_pct}}% → ${{s.avg_smart_max_dd_pct}}%`;

    const tbody = document.getElementById('btTableBody');
    tbody.innerHTML = '';
    data.tickers.forEach(r => {{
      const aClass = r.alpha_pct >= 0 ? 'green' : 'red';
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><strong style="color:#38bdf8;">${{r.ticker}}</strong></td>
        <td><small>${{r.start_date}} → ${{r.end_date}} (${{r.months}} ด.)</small></td>
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

async function callPortfolioApi(path, payload) {{
  const btn = document.getElementById('addStockBtn');
  const msg = document.getElementById('addStatusMsg');
  if (btn) btn.disabled = true;
  if (msg) msg.textContent = '⏳ กำลังดึงข้อมูลแบบ Batch และอัปเดต AI CIO...';
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
      return;
    }}
    if (msg) msg.textContent = '✅ สำเร็จ! กำลังรีโหลดหน้าเว็บ...';
    setTimeout(() => window.location.reload(), 300);
  }} catch (err) {{
    if (msg) msg.textContent = '';
    if (btn) btn.disabled = false;
    alert('หากต้องการกดเพิ่ม/ลบหุ้น ให้เปิดผ่าน http://127.0.0.1:8765');
  }}
}}
</script>
</body>
</html>"""

    out_path = ROOT_DIR / "trade_plan.html"
    # Atomic write: write to temp file in same directory then atomically replace
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
