# -*- coding: utf-8 -*-
"""公开路由 Blueprint — 无需登录"""
from flask import Blueprint, render_template, request, jsonify, send_from_directory
from datetime import datetime
from routes_analysis import _score_performance, _score_philosophy, _score_people, _score_process
import json
import os
import re

public_bp = Blueprint('public', __name__)

# LLM 配置（Anthropic API）
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
ANTHROPIC_MODEL = os.environ.get('ANTHROPIC_MODEL', 'claude-haiku-4-5-20251001')


@public_bp.route('/sitemap.xml')
def sitemap():
    return send_from_directory('templates', 'sitemap.xml')

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


@public_bp.route('/api/guide/onboard', methods=['POST'])
def guide_onboard():
    """自然语言 onboarding：用户描述自身情况，LLM 提取投资参数"""
    data = request.get_json() or {}
    user_input = (data.get('input', '') or '').strip()
    if not user_input or len(user_input) < 6:
        return jsonify({'success': False, 'error': '请描述你的情况（至少 6 个字）'})
    if len(user_input) > 500:
        return jsonify({'success': False, 'error': '描述太长了，控制在 500 字以内'})

    if not ANTHROPIC_API_KEY:
        # 降级：使用规则引擎
        params = _rule_based_onboard(user_input)
        return jsonify({'success': True, 'source': 'rules', **params})

    try:
        import urllib.request as _ur
        prompt = f"""你是一个基金投资顾问。用户描述了他们的个人情况，请提取关键投资参数。

用户输入："{user_input}"

请返回 JSON（只返回 JSON，不要其他文字）：
{{
  "min_return": 数字(1-20，默认8),
  "max_drawdown": 数字(5-40，默认20),
  "reason": "一句话解释为什么推荐这个范围（中文，30字以内）"
}}

规则：
- 年轻、收入高、能承受波动 → 收益偏高(10-15)、回撤放宽(20-30)
- 中年、求稳、有家庭负担 → 收益适中(6-10)、回撤适中(15-20)
- 退休/临近退休、保本为主 → 收益保守(3-6)、回撤保守(5-15)
- 提到具体数字则以用户说的为准"""

        req = _ur.Request(
            'https://api.anthropic.com/v1/messages',
            data=json.dumps({
                'model': ANTHROPIC_MODEL,
                'max_tokens': 200,
                'messages': [{'role': 'user', 'content': prompt}],
            }).encode(),
            headers={
                'x-api-key': ANTHROPIC_API_KEY,
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json',
            })
        resp = _ur.urlopen(req, timeout=10)
        body = json.loads(resp.read().decode())
        text = body['content'][0]['text'].strip()

        # 提取 JSON
        import re
        m = re.search(r'\{[^}]+\}', text)
        if m:
            result = json.loads(m.group())
            min_ret = max(1, min(20, int(result.get('min_return', 8))))
            max_dd = max(5, min(40, int(result.get('max_drawdown', 20))))
            reason = result.get('reason', '根据你的情况推荐')
            return jsonify({'success': True, 'source': 'llm',
                          'min_return': min_ret, 'max_drawdown': max_dd,
                          'reason': str(reason)[:60]})
    except Exception as e:
        print(f"[onboard] LLM failed: {e}")

    # LLM 失败，降级到规则引擎
    params = _rule_based_onboard(user_input)
    return jsonify({'success': True, 'source': 'rules_fallback', **params})


def _rule_based_onboard(text: str):
    """简易规则引擎：从用户描述中提取投资偏好（LLM 不可用时的降级方案）"""
    text_low = text.lower()
    age = 35
    import re
    age_m = re.search(r'(\d{2})\s*岁', text)
    if age_m:
        age = int(age_m.group(1))

    if any(w in text_low for w in ['退休', '养老', '保本', '不亏', '安全']):
        min_ret, max_dd, reason = 4, 12, '倾向保守：以保本和稳定增值为目标'
    elif age > 50:
        min_ret, max_dd, reason = 5, 18, '临近退休：适度收益，控制风险为主'
    elif age > 35:
        min_ret, max_dd, reason = 8, 22, '中年稳健：平衡收益与风险'
    elif any(w in text_low for w in ['激进', '高收益', '翻倍', '承受']):
        min_ret, max_dd, reason = 14, 30, '积极进取：愿意承受较大波动追求高收益'
    else:
        min_ret, max_dd, reason = 8, 22, '默认推荐：适合大多数投资者'

    # 用户明确提到的数字优先
    num_m = re.findall(r'(\d+)\s*%', text)
    if num_m:
        nums = [int(n) for n in num_m]
        if any(w in text_low for w in ['亏', '回撤', '跌', '损失', '承受']):
            max_dd = max(5, min(40, nums[0]))
            reason += '；已根据你说的风险承受调整回撤'
        if any(w in text_low for w in ['收益', '回报', '赚', '跑赢', '年化']):
            min_ret = max(3, min(20, nums[0]))
            reason += '；已根据你说的收益目标调整'

    return {'min_return': min_ret, 'max_drawdown': max_dd, 'reason': reason}


@public_bp.route('/api/guide/compare')
def guide_compare():
    """基金对比：并排展示 2-3 只基金的 4P 评分和关键指标"""
    from db import db_execute

    codes_str = request.args.get('codes', '')
    codes = [c.strip() for c in codes_str.split(',') if c.strip()] if codes_str else []
    if len(codes) < 2 or len(codes) > 3:
        return jsonify({'success': False, 'error': '请选择 2-3 只基金进行对比'})

    rows = db_execute(
        "SELECT fund_code, fund_name, p1_performance, p2_philosophy, p3_people, p4_process, "
        "total_score, annual_return, max_drawdown, sharpe_ratio "
        "FROM fund_scores WHERE fund_code IN (" + ','.join(['%s'] * len(codes)) + ")",
        tuple(codes), fetch=True)

    if len(rows) < 2:
        return jsonify({'success': False, 'error': '部分基金评分数据缺失'})

    funds = []
    for r in rows:
        funds.append({
            'fund_code': r['fund_code'],
            'fund_name': r['fund_name'],
            'total_score': r['total_score'],
            'p1': r['p1_performance'], 'p2': r['p2_philosophy'],
            'p3': r['p3_people'], 'p4': r['p4_process'],
            'annual_return': float(r['annual_return']),
            'max_drawdown': float(r['max_drawdown']),
            'sharpe_ratio': float(r['sharpe_ratio']),
        })

    # 按总分排序，最高的在左边
    funds.sort(key=lambda x: x['total_score'], reverse=True)

    # 找出每列的最高值用于高亮
    highlights = {}
    for key in ['total_score', 'p1', 'p2', 'p3', 'p4', 'annual_return', 'sharpe_ratio']:
        best_val = max(f[key] for f in funds)
        highlights[key] = best_val
    # max_drawdown 越低越好
    best_dd = min(f['max_drawdown'] for f in funds)
    highlights['max_drawdown'] = best_dd

    return jsonify({'success': True, 'funds': funds, 'highlights': highlights})


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


@public_bp.route('/api/guide/build-portfolio')
def build_portfolio():
    """教练向导步骤 2：根据选中基金构建组合"""
    from db import db_execute
    import math

    codes_str = request.args.get('codes', '')
    codes = [c.strip() for c in codes_str.split(',') if c.strip()] if codes_str else []
    if not codes or len(codes) < 2:
        return jsonify({'success': False, 'error': '请至少选择 2 只基金'})
    if len(codes) > 5:
        return jsonify({'success': False, 'error': '最多选择 5 只基金'})

    rows = db_execute(
        "SELECT fund_code, fund_name, total_score, p1_performance, p2_philosophy, "
        "p3_people, p4_process, annual_return, max_drawdown, sharpe_ratio "
        "FROM fund_scores WHERE fund_code IN ("
        + ','.join(['%s'] * len(codes)) + ")",
        tuple(codes), fetch=True)

    if not rows or len(rows) < len(codes):
        return jsonify({'success': False, 'error': '部分基金评分数据缺失'})

    total_weight = sum(max(r['total_score'], 1) for r in rows)

    funds = []
    for r in rows:
        weight = round(max(r['total_score'], 1) / total_weight * 100, 1)
        funds.append({
            'fund_code': r['fund_code'],
            'fund_name': r['fund_name'],
            'weight': weight,
            'score': r['total_score'],
            'annual_return': float(r['annual_return']),
            'max_drawdown': float(r['max_drawdown']),
            'sharpe_ratio': float(r['sharpe_ratio']),
            'p1': r['p1_performance'], 'p2': r['p2_philosophy'],
            'p3': r['p3_people'], 'p4': r['p4_process'],
        })

    portfolio_return = sum(f['annual_return'] * f['weight'] / 100 for f in funds)
    portfolio_dd = sum(f['max_drawdown'] * f['weight'] / 100 for f in funds)
    portfolio_sharpe = sum(f['sharpe_ratio'] * f['weight'] / 100 for f in funds)

    return jsonify({
        'success': True,
        'funds': funds,
        'metrics': {
            'annual_return': round(portfolio_return, 1),
            'max_drawdown': round(portfolio_dd, 1),
            'sharpe_ratio': round(portfolio_sharpe, 2),
        },
    })
