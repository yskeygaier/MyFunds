# -*- coding: utf-8 -*-
"""公开路由 Blueprint — 无需登录"""
from flask import Blueprint, render_template, request, jsonify, send_from_directory
from datetime import datetime
from fund_analyzer import FundScreener
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

    # 用户明确提到的数字优先（按关键字上下文匹配，而非取第一个数字）
    ret_m = re.search(r'(?:收益|回报|赚|跑赢|年化|目标).*?(\d+)\s*%', text) or re.search(r'(\d+)\s*%.*?(?:收益|回报|赚|跑赢|年化)', text)
    dd_m = re.search(r'(?:亏|回撤|跌|损失|承受|风险).*?(\d+)\s*%', text) or re.search(r'(\d+)\s*%.*?(?:亏|回撤|跌|损失|承受|风险)', text)
    if ret_m:
        min_ret = max(3, min(20, int(ret_m.group(1))))
        reason += '；已根据你说的收益目标调整'
    if dd_m:
        max_dd = max(5, min(40, int(dd_m.group(1))))
        reason += '；已根据你说的风险承受调整回撤'

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


@public_bp.route('/api/guide/backtest-portfolio')
def backtest_portfolio():
    """组合回测：根据基金代码和权重计算组合历史净值（带缓存）"""
    codes_str = request.args.get('codes', '')
    weights_str = request.args.get('weights', '')
    codes = [c.strip() for c in codes_str.split(',') if c.strip()] if codes_str else []
    weights_raw = [w.strip() for w in weights_str.split(',') if w.strip()] if weights_str else []

    if len(codes) < 2 or len(codes) != len(weights_raw):
        return jsonify({'success': False, 'error': '参数错误'})

    try:
        weights = [float(w) / 100 for w in weights_raw]
    except ValueError:
        return jsonify({'success': False, 'error': '权重格式错误'})

    # 时间段参数
    try:
        years = float(request.args.get('years', 1))
    except ValueError:
        years = 1
    period_map = {'1m': 0.08, '3m': 0.25, '6m': 0.5, '1y': 1, '2y': 2, '3y': 3, '5y': 5, 'all': 10}
    period_str = request.args.get('period', '')
    if period_str in period_map:
        years = period_map[period_str]

    # 缓存检查（含时间段）
    cache_key = f"backtest:{':'.join(codes)}:{':'.join(weights_raw)}:{years}"
    from app import get_cache, set_cache
    cached = get_cache(cache_key)
    if cached:
        return jsonify(cached)

    from fund_crawler import crawl_fund_nav_df
    import pandas as pd
    import concurrent.futures

    fund_navs = {}
    # 成立以来：先探测各基金实际数据范围，取最短的
    actual_years = years
    if period_str == 'all':
        def _probe(code):
            try:
                d = crawl_fund_nav_df(code, years=10)
                if d and len(d) > 5:
                    dates = [pd.to_datetime(r.get('净值日期', r.get('date', ''))) for r in d if r.get('净值日期') or r.get('date')]
                    if dates:
                        return (max(dates) - min(dates)).days / 365.25
                return 10
            except Exception:
                return 10
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(codes), 5)) as executor:
            probes = list(executor.map(_probe, codes))
        actual_years = max(0.5, min(probes)) if probes else years

    def _fetch_one(code):
        try:
            data = crawl_fund_nav_df(code, years=actual_years)
            if data:
                df = pd.DataFrame(data)
                df['date'] = pd.to_datetime(df['净值日期'])
                df['nav'] = pd.to_numeric(df['单位净值'], errors='coerce')
                df = df.dropna(subset=['nav'])
                df = df.set_index('date').sort_index()
                return code, df['nav']
        except Exception:
            pass
        return code, None

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(codes), 5)) as executor:
        futures = {executor.submit(_fetch_one, c): c for c in codes}
        for future in concurrent.futures.as_completed(futures):
            try:
                fc, nav = future.result(timeout=15)
                if nav is not None and len(nav) > 5:
                    fund_navs[fc] = nav
            except Exception:
                pass

    if len(fund_navs) < 2:
        return jsonify({'success': False, 'error': '无法获取足够的净值数据'})

    # Align dates and compute weighted portfolio NAV
    all_dates = sorted(set().union(*[set(s.index) for s in fund_navs.values()]))
    portfolio_nav = []
    base_date = all_dates[0]
    for d in all_dates:
        weighted = 0
        total_w = 0
        for code, w in zip(codes, weights):
            if code in fund_navs:
                s = fund_navs[code]
                if d in s.index:
                    nav_val = s.loc[d]
                else:
                    nearby = s.index[s.index <= d]
                    if len(nearby) == 0:
                        continue
                    nav_val = s.loc[nearby[-1]]
                base_val = s.loc[s.index[0]]
                if base_val > 0:
                    weighted += w * (nav_val / base_val)
                    total_w += w
        if total_w > 0:
            portfolio_nav.append({
                'date': d.strftime('%Y-%m-%d'),
                'nav': round(weighted / total_w, 4),
            })

    if len(portfolio_nav) < 10:
        return jsonify({'success': False, 'error': '组合净值数据不足'})

    # Calculate metrics
    navs = [p['nav'] for p in portfolio_nav]
    total_return = round((navs[-1] / navs[0] - 1) * 100, 2)
    peak = navs[0]
    max_dd = 0
    max_dd_date = ''
    for i, v in enumerate(navs):
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd:
            max_dd = dd
            max_dd_date = portfolio_nav[i]['date']
    max_dd = round(max_dd, 2)

    # 年化收益率 (CAGR)
    years_calc = len(portfolio_nav) / 252
    annualized = ((1 + total_return / 100) ** (1 / max(years_calc, 0.08)) - 1) * 100 if total_return > -100 else total_return

    result = {
        'success': True,
        'dates': [p['date'] for p in portfolio_nav],
        'navs': navs,
        'total_return': total_return,
        'annualized_return': round(annualized, 2),
        'max_drawdown': max_dd,
        'max_dd_date': max_dd_date,
        'data_points': len(portfolio_nav),
    }
    set_cache(cache_key, result, expiry=7200)
    return jsonify(result)


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
        "SELECT fund_code, fund_name, fund_type, p1_performance, p2_philosophy, p3_people, p4_process, "
        "total_score, annual_return, max_drawdown, sharpe_ratio, updated_at "
        "FROM fund_scores "
        "WHERE annual_return >= %s AND max_drawdown <= %s "
        "ORDER BY total_score DESC",
        (min_return, max_drawdown), fetch=True)

    # 过滤不可购买基金
    BLOCKED_KEYWORDS = ['持有期', '个月持有', '年持有', '封闭', '定期开放', '锁定', '定开', '限购', '暂停申购', '滚动持有']
    rows = [r for r in rows if not any(kw in r['fund_name'] for kw in BLOCKED_KEYWORDS)]

    # 排除非主流份额（B/E/Y/D/F/H/I等），只保留A/C和单一份額基金
    EXCLUDED_SUFFIXES = set('BEYDFHIJKLMPQRTUVWXZ')
    def _is_ac_or_single(name):
        if len(name) < 2: return True
        last = name[-1]
        if last in EXCLUDED_SUFFIXES and len(name) > 1 and name[-2] not in 'ABCDE':
            return False
        if last in EXCLUDED_SUFFIXES and len(name) > 2:
            return False
        return True
    rows = [r for r in rows if _is_ac_or_single(r['fund_name'])]

    # 份额去重：同名基金（A/C份额）按评分高者保留
    seen_base = {}
    deduped = []
    for r in rows:
        base = r['fund_name'].rstrip('AC')
        if base not in seen_base or r['total_score'] > seen_base[base].get('total_score', 0):
            seen_base[base] = r
    deduped = sorted(seen_base.values(), key=lambda x: x['total_score'], reverse=True)
    rows = deduped

    # 补充分类：fund_type 为空时根据名称推断
    for r in rows:
        if not r.get('fund_type'):
            nm = r['fund_name']
            if any(w in nm for w in ['债券', '债', '纯债', '信用债', '利率债', '转债', '可转债']): r['fund_type'] = '债券型'
            elif any(w in nm for w in ['货币', '货基', '现金']): r['fund_type'] = '货币型'
            elif '指数' in nm or 'ETF' in nm or 'etf' in nm: r['fund_type'] = '指数型'
            elif '混合' in nm: r['fund_type'] = '混合型'
            elif '股票' in nm: r['fund_type'] = '股票型'
            else: r['fund_type'] = '混合型'

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
            for code in stale_codes[:3]:
                try:
                    from routes_fund import fetch_fund_info
                    info = fetch_fund_info(code)
                    if info:
                        screener = FundScreener(
                            fund_info=info,
                            holdings={"前十大持仓": info.get('前十大持仓', [])}
                        )
                        result = screener.screen()
                        fp = result.four_p
                        if fp is None:
                            continue
                        an = float(str(info.get('年化收益率', '0%')).replace('%', '').replace('nan', '0') or 0)
                        dd = abs(float(str(info.get('最大回撤', '0%')).replace('%', '').replace('nan', '0') or 0))
                        sr = float(str(info.get('夏普比率', '0')).replace('nan', '0') or 0)
                        db_execute(
                            "UPDATE fund_scores SET p1_performance=%s, p2_philosophy=%s, p3_people=%s, "
                            "p4_process=%s, total_score=%s, annual_return=%s, max_drawdown=%s, "
                            "sharpe_ratio=%s, updated_at=NOW() WHERE fund_code=%s",
                            (fp.performance, fp.philosophy, fp.people, fp.process,
                             fp.total, an, dd, sr, code), fetch=False)
                except Exception:
                    pass
        threading.Thread(target=_refresh_stale, daemon=True).start()

    if not rows:
        return jsonify({'success': True, 'total': 0, 'top': []})

    import random
    total = len(rows)

    # 分离债券基金和权益基金（可转债波动大，归入权益池）
    bond_pool = [r for r in rows if r.get('fund_type') == '债券型' and '可转债' not in r.get('fund_name', '') and '转债' not in r.get('fund_name', '')]
    equity_pool = [r for r in rows if r not in bond_pool]

    # 保证至少 1-3 只债券基金出现在推荐中
    if bond_pool:
        bond_sample_n = min(len(bond_pool), random.randint(1, min(3, len(bond_pool))))
        bond_sampled = random.sample(bond_pool, bond_sample_n) if bond_sample_n > 1 else [random.choice(bond_pool)]
    else:
        bond_sampled = []

    # 剩余名额从权益基金中分层抽样（总数 5-6 只）
    remaining_slots = random.randint(5, 6) - len(bond_sampled)
    remaining_slots = max(0, min(remaining_slots, len(equity_pool)))

    eq_sampled = []
    if equity_pool and remaining_slots > 0:
        if len(equity_pool) <= remaining_slots:
            eq_sampled = equity_pool[:]
        else:
            equity_sorted = sorted(equity_pool, key=lambda x: x['total_score'], reverse=True)
            top_n = max(2, int(remaining_slots * 0.6))
            mid_n = max(1, remaining_slots - top_n)
            top_cut = max(top_n, int(len(equity_sorted) * 0.25))
            top_pool = equity_sorted[:top_cut]
            mid_pool = equity_sorted[top_cut:max(top_cut + mid_n, len(equity_sorted))]
            eq_sampled = random.sample(top_pool, min(top_n, len(top_pool)))
            if mid_pool and len(eq_sampled) < remaining_slots:
                eq_sampled += random.sample(mid_pool, min(remaining_slots - len(eq_sampled), len(mid_pool)))

    sampled = bond_sampled + eq_sampled
    random.shuffle(sampled)

    result_funds = []
    for r in sampled:
        result_funds.append({
            'fund_code': r['fund_code'],
            'fund_name': r['fund_name'],
            'fund_type': r.get('fund_type', ''),
            'total_score': r['total_score'],
            'p1': r['p1_performance'],
            'p2': r['p2_philosophy'],
            'p3': r['p3_people'],
            'p4': r['p4_process'],
            'annual_return': float(r['annual_return']),
            'max_drawdown': float(r['max_drawdown']),
            'sharpe_ratio': float(r['sharpe_ratio']),
        })

    return jsonify({'success': True, 'total': total, 'top': result_funds})


@public_bp.route('/api/guide/build-portfolio')
def build_portfolio():
    """教练向导步骤 2：根据选中基金构建组合"""
    from db import db_execute
    import math

    codes_str = request.args.get('codes', '')
    codes = [c.strip() for c in codes_str.split(',') if c.strip()] if codes_str else []
    if not codes or len(codes) < 2:
        return jsonify({'success': False, 'error': '请至少选择 2 只基金'})
    if len(codes) > 10:
        return jsonify({'success': False, 'error': '最多选择 10 只基金'})

    rows = db_execute(
        "SELECT fund_code, fund_name, fund_type, total_score, p1_performance, p2_philosophy, "
        "p3_people, p4_process, annual_return, max_drawdown, sharpe_ratio "
        "FROM fund_scores WHERE fund_code IN ("
        + ','.join(['%s'] * len(codes)) + ")",
        tuple(codes), fetch=True)

    if not rows or len(rows) < 2:
        return jsonify({'success': False, 'error': '部分基金评分数据缺失或数量不足'})

    # 补充分类：fund_type 为空时根据名称推断
    for r in rows:
        if not r.get('fund_type'):
            nm = r['fund_name']
            if any(w in nm for w in ['债券', '债', '纯债', '信用债', '利率债', '转债', '可转债']): r['fund_type'] = '债券型'
            elif any(w in nm for w in ['货币', '货基', '现金']): r['fund_type'] = '货币型'
            elif '指数' in nm or 'ETF' in nm: r['fund_type'] = '指数型'
            elif '混合' in nm: r['fund_type'] = '混合型'
            elif '股票' in nm: r['fund_type'] = '股票型'
            else: r['fund_type'] = '混合型'

    # Calmar 比率计算权重（年化收益-无风险利率 / 最大回撤）
    RF_RATE = 2.5
    def _calmar(annual_return, max_drawdown):
        dd = max(float(max_drawdown), 1.0)
        return max(float(annual_return) - RF_RATE, 0.5) / dd

    # 分类基金
    equity_funds = []
    bond_funds = []
    other_funds = []
    for r in rows:
        ft = (r.get('fund_type') or '').strip()
        if ft in ('股票型', '指数型', '混合型', '混合型-偏股'):
            equity_funds.append(r)
        elif ft in ('债券型', '货币型', '混合型-偏债'):
            bond_funds.append(r)
        else:
            equity_funds.append(r)

    # 用户年龄（从请求参数获取，默认 35）
    try:
        user_age = int(request.args.get('age', 35))
    except ValueError:
        user_age = 35
    user_age = max(18, min(80, user_age))

    # 用户设定的最大回撤（从请求参数获取，默认 20）
    try:
        user_max_dd = float(request.args.get('max_drawdown', 20))
    except ValueError:
        user_max_dd = 20
    user_max_dd = max(5.0, min(40.0, user_max_dd))

    # ===== 动态股债配比模型 =====
    # 参考：年龄法则（"120-年龄"）+ 风险平价 + 目标波动率（SOA 2025）
    #
    # Step 1: 基础权益比 = 120 - 年龄（生命周期理论）
    base_equity = max(10.0, min(90.0, 120.0 - user_age))

    # Step 2: 风险调整系数（根据用户设定的最大回撤）
    if user_max_dd <= 10:
        risk_mult, risk_label = 0.5, '保守'
    elif user_max_dd <= 15:
        risk_mult, risk_label = 0.75, '谨慎'
    elif user_max_dd <= 20:
        risk_mult, risk_label = 1.0, '平衡'
    elif user_max_dd <= 30:
        risk_mult, risk_label = 1.25, '成长'
    else:
        risk_mult, risk_label = 1.5, '进取'

    # Step 3: 最终权益比 = 基础 × 风险系数，限制 10%-90%
    equity_pct_float = base_equity * risk_mult
    equity_pct = max(10.0, min(90.0, equity_pct_float))
    bond_pct = round(100.0 - equity_pct)
    equity_pct = round(equity_pct)

    # 确保权益+固收 = 100
    if equity_pct + bond_pct != 100:
        equity_pct = 100 - bond_pct

    # 如果只有一类基金，调整配比
    if not bond_funds:
        bond_pct = 0
        equity_pct = 100
    if not equity_funds:
        equity_pct = 0
        bond_pct = 100

    # 股债配比解释
    explanation_lines = [
        f'【{risk_label}型配置】基于"120-年龄"生命周期法则（120-{user_age}={base_equity:.0f}%基础权益）'
        f'× 风险调整系数 {risk_mult}（最大回撤≤{user_max_dd:.0f}%）'
        f'→ 最终配比 {equity_pct}% 权益 + {bond_pct}% 固收。',
        f'理论基础：年龄越大权益越低（退休后更需要稳定现金流），'
        f'能承受的回撤越小固收占比越高（参考桥水全天候策略与 SOA 2025 目标波动率模型）。',
    ]

    # Calmar 加权分配（权重取整到 5 的倍数，方便操作）
    funds = []
    layers = {}

    def _round_to_5(v):
        return max(5, min(40, round(v / 5) * 5))

    def _allocate(pool, total_weight_pct, layer_name):
        if not pool or total_weight_pct <= 0:
            return
        calmar_scores = {r['fund_code']: _calmar(r['annual_return'], r['max_drawdown']) for r in pool}
        total_calm = sum(calmar_scores.values())
        if total_calm <= 0:
            return
        layer_funds = []
        for r in pool:
            raw_w = calmar_scores[r['fund_code']] / total_calm * total_weight_pct
            w = _round_to_5(raw_w)
            layer_funds.append((r, w))
        layer_total = sum(w for _, w in layer_funds)
        if layer_total == 0:
            return
        for r, w in layer_funds:
            final_w = _round_to_5(w / layer_total * total_weight_pct)
            funds.append({
                'fund_code': r['fund_code'],
                'fund_name': r['fund_name'],
                'fund_type': r.get('fund_type', ''),
                'weight': final_w,
                'score': r['total_score'],
                'annual_return': float(r['annual_return']),
                'max_drawdown': float(r['max_drawdown']),
                'sharpe_ratio': float(r['sharpe_ratio']),
                'p1': r['p1_performance'], 'p2': r['p2_philosophy'],
                'p3': r['p3_people'], 'p4': r['p4_process'],
            })
        layers[layer_name] = layer_funds

    # 权益部分：核心+卫星
    if equity_funds and equity_pct > 0:
        eq_sorted = sorted(equity_funds, key=lambda x: _calmar(x['annual_return'], x['max_drawdown']), reverse=True)
        if len(eq_sorted) >= 2:
            core_n = max(1, len(eq_sorted) // 2)
            core_pool = eq_sorted[:core_n]
            sat_pool = eq_sorted[core_n:]
            core_pct = equity_pct * 0.65
            sat_pct = equity_pct * 0.35
            _allocate(core_pool, core_pct, '权益核心')
            _allocate(sat_pool, sat_pct, '权益卫星')
            explanation_lines.append(
                f'【权益部分 {equity_pct}%】核心层（{round(core_pct,0)}%）：配置高夏普比率基金，负责长期稳健增值。'
                f'卫星层（{round(sat_pct,0)}%）：配置特色主题基金，捕捉阶段性机会。'
            )
        else:
            _allocate(eq_sorted, equity_pct, '权益')
            explanation_lines.append(f'【权益部分 {equity_pct}%】配置高分基金，获取市场长期增长收益。')

    # 固收部分
    if bond_funds and bond_pct > 0:
        bd_sorted = sorted(bond_funds, key=lambda x: _calmar(x['annual_return'], x['max_drawdown']), reverse=True)
        _allocate(bd_sorted, bond_pct, '固收')
        explanation_lines.append(
            f'【固收部分 {bond_pct}%】配置债券型基金，提供稳定票息收益，降低组合整体波动。'
        )

    # 权重约束：确保和为 100，每只取 5 的倍数
    total_w = sum(f['weight'] for f in funds)
    if total_w > 0 and total_w != 100:
        # 按比例缩放后取整到 5
        for f in funds:
            f['weight'] = _round_to_5(f['weight'] / total_w * 100)
    # 最终补齐差值（分配余数到权重最大的基金）
    final_total = sum(f['weight'] for f in funds)
    if final_total != 100 and funds:
        funds.sort(key=lambda x: x['weight'], reverse=True)
        diff = 100 - final_total
        funds[0]['weight'] += diff

    portfolio_return = sum(f['annual_return'] * f['weight'] / 100 for f in funds)
    portfolio_dd = sum(f['max_drawdown'] * f['weight'] / 100 for f in funds)
    portfolio_sharpe = sum(f['sharpe_ratio'] * f['weight'] / 100 for f in funds)

    explanation_lines.append(
        f'【权重方法】采用 Calmar 比率（(年化收益-无风险利率)/最大回撤）分配各基金权重，'
        f'单只基金占比控制在 5%-40%，同类基金合计不超过 50%。权重不平均分配——回撤低的基金权重更高，'
        f'体现了"同等收益下优先选择更稳定的基金"的原则。'
    )

    # 买入策略推荐（根据组合最大回撤）
    if portfolio_dd < 8:
        strategy = '一次性买入'
        strategy_detail = '组合波动低（回撤<8%），一次性全额买入可最大化资金利用效率，无需择时。建议在近期净值回调日买入。'
    elif portfolio_dd < 18:
        strategy = '分批买入'
        batches = 3 if portfolio_dd < 13 else 4
        strategy_detail = f'组合波动适中（回撤{portfolio_dd:.0f}%），建议分{batches}批买入，每批间隔1-2周。例如总资金{batches*5}万，每批{(batches*5)//batches}万。避免一次性买在高点。'
    else:
        strategy = '定投买入'
        strategy_detail = f'组合波动较大（回撤{portfolio_dd:.0f}%），建议采用定投策略。每月固定金额买入，坚持12个月以上，利用波动摊平成本。熊市多买份额，牛市享受收益。'

    return jsonify({
        'success': True,
        'funds': funds,
        'metrics': {
            'annual_return': round(portfolio_return, 1),
            'max_drawdown': round(portfolio_dd, 1),
            'sharpe_ratio': round(portfolio_sharpe, 2),
        },
        'explanation': '\n'.join(explanation_lines),
        'risk_level': risk_label,
        'strategy': {'name': strategy, 'detail': strategy_detail},
    })
