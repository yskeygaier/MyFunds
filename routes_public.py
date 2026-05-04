# -*- coding: utf-8 -*-
"""公开路由 Blueprint — 无需登录"""
from flask import Blueprint, render_template, request, jsonify
from datetime import datetime
from routes_analysis import _score_performance, _score_philosophy, _score_people, _score_process

public_bp = Blueprint('public', __name__)

# 创始人实盘组合 ID（在 portfolio_manager 中创建）
FOUNDER_PORTFOLIO_ID = 1


@public_bp.route('/public/portfolio')
def founder_portfolio():
    """公开页面：创始人实盘组合"""
    from portfolio_manager import get_portfolio, compute_portfolio_nav_data

    try:
        p = get_portfolio(FOUNDER_PORTFOLIO_ID)
    except Exception as e:
        return render_template('public_portfolio.html',
                               error=f'系统维护中，请稍后再试', error_detail=str(e)[:200])
    if not p:
        return render_template('public_portfolio.html',
                               error='组合尚未创建，预计近期上线', error_detail='portfolio_not_found')

    nav_data, _ = compute_portfolio_nav_data(FOUNDER_PORTFOLIO_ID, years=1)
    holdings = p.get('holdings', [])

    # 计算当前指标
    if nav_data and len(nav_data) > 1:
        start_nav = nav_data[0]['nav_value']
        end_nav = nav_data[-1]['nav_value']
        total_return = round((end_nav / start_nav - 1) * 100, 2)
        navs = [d['nav_value'] for d in nav_data]
        peak = navs[0]
        max_dd = 0
        for v in navs:
            if v > peak:
                peak = v
            dd = (peak - v) / peak * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
        max_dd = round(max_dd, 2)
        days = len(nav_data)
        chart_dates = [d['nav_date'] for d in nav_data]
        chart_navs = navs
    else:
        total_return = 0
        max_dd = 0
        days = 0
        chart_dates = []
        chart_navs = []

    # 丰富持仓信息
    fund_holding_details = []
    for h in holdings:
        fc = h.get('fund_code', '')
        try:
            from routes_fund import get_fund_name
            name = get_fund_name(fc)
        except Exception:
            name = h.get('fund_name', fc)
        fund_holding_details.append({
            'code': fc,
            'name': name,
            'weight': h.get('weight', 0),
        })

    return render_template('public_portfolio.html',
                           portfolio=p,
                           holdings=fund_holding_details,
                           total_return=total_return,
                           max_drawdown=max_dd,
                           days=days,
                           chart_dates=chart_dates,
                           chart_navs=chart_navs,
                           updated_at=datetime.now().strftime('%Y-%m-%d'))


@public_bp.route('/api/guide/screen')
def guide_screen():
    """教练向导 API：按收益/回撤筛选基金"""
    from db import db_execute

    try:
        min_return = float(request.args.get('min_return', 5))
    except ValueError:
        min_return = 5
    try:
        max_drawdown = float(request.args.get('max_drawdown', 30))
    except ValueError:
        max_drawdown = 30
    min_return = max(1.0, min(50.0, min_return))
    max_drawdown = max(1.0, min(80.0, max_drawdown))

    rows = db_execute(
        "SELECT fund_code, fund_name, p1_performance, p2_philosophy, p3_people, p4_process, "
        "total_score, annual_return, max_drawdown, sharpe_ratio, updated_at "
        "FROM fund_scores "
        "WHERE annual_return >= %s AND max_drawdown <= %s "
        "ORDER BY total_score DESC",
        (min_return, max_drawdown), fetch=True)

    # 检测过期评分（超过 7 天未更新），后台异步刷新
    stale_codes = []
    for r in rows:
        try:
            updated = r.get('updated_at')
            if updated and (datetime.now() - updated).days > 7:
                stale_codes.append(r['fund_code'])
        except Exception:
            pass
    if stale_codes:
        import threading
        def _refresh_stale():
            for code in stale_codes[:3]:  # 最多刷新 3 只，避免过载
                try:
                    from routes_fund import fetch_fund_info
                    info = fetch_fund_info(code)
                    if info:
                        p1, _, _ = _score_performance(info)
                        p2, _, _ = _score_philosophy(info, info.get('前十大持仓', []))
                        p3, _, _ = _score_people(info)
                        p4, _, _ = _score_process(info, info.get('前十大持仓', []))
                        total = p1 + p2 + p3 + p4
                        an = float(str(info.get('年化收益率', '0%')).replace('%', '').replace('nan', '0') or 0)
                        dd = abs(float(str(info.get('最大回撤', '0%')).replace('%', '').replace('nan', '0') or 0))
                        sr = float(str(info.get('夏普比率', '0')).replace('nan', '0') or 0)
                        db_execute(
                            "UPDATE fund_scores SET p1_performance=%s, p2_philosophy=%s, p3_people=%s, "
                            "p4_process=%s, total_score=%s, annual_return=%s, max_drawdown=%s, "
                            "sharpe_ratio=%s, updated_at=NOW() WHERE fund_code=%s",
                            (p1, p2, p3, p4, total, an, dd, sr, code), fetch=False)
                except Exception:
                    pass
        threading.Thread(target=_refresh_stale, daemon=True).start()

    if not rows:
        return jsonify({'success': True, 'total': 0, 'top': []})

    total = len(rows)
    top3 = []
    for r in rows[:3]:
        top3.append({
            'fund_code': r['fund_code'],
            'fund_name': r['fund_name'],
            'total_score': r['total_score'],
            'p1': r['p1_performance'],
            'p2': r['p2_philosophy'],
            'p3': r['p3_people'],
            'p4': r['p4_process'],
            'annual_return': float(r['annual_return']),
            'max_drawdown': float(r['max_drawdown']),
            'sharpe_ratio': float(r['sharpe_ratio']),
        })

    return jsonify({'success': True, 'total': total, 'top': top3})
