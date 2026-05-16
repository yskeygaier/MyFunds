# -*- coding: utf-8 -*-
"""回测 + 定投 Blueprint"""
import threading
import pandas as pd
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify
import plotly.graph_objects as go

from app import get_cache, set_cache, generate_cache_key, CACHE_CONFIG, _eastmoney_get

backtest_bp = Blueprint('backtest', __name__)


def _http_fetch_etf_lof_nav(fund_code: str, start_date: str, end_date: str):
    """
    通过东方财富 push2his 接口获取 ETF/LOF 历史净值。
    尝试上交所(secid=1.)和深交所(secid=0.)两种格式。
    返回 DataFrame: {净值日期, 单位净值, 日增长率} 或空 DataFrame。
    """
    import pandas as _pd2
    for market_prefix in ('1', '0'):
        secid = f"{market_prefix}.{fund_code}"
        url = (
            f"https://push2his.eastmoney.com/api/qt/stock/lszc/get"
            f"?fields1=f1,f2,f3,f4,f5,f6"
            f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
            f"&ut=7eea3edcaed734bea9cbfc24409ed989"
            f"&klt=01&fqt=1&secid={secid}"
            f"&beg={start_date}&end={end_date}&smplmt=460&lmt=1000000"
        )
        raw = _eastmoney_get(url)
        if not raw:
            continue
        try:
            import json
            data = json.loads(raw)
            klines = data.get('data', {}).get('lszc', [])
            if not klines:
                continue
        except Exception:
            continue

        dates, navs, changes = [], [], []
        for item in klines:
            date_str = item.get('f51', '')
            if not date_str:
                continue
            try:
                nav = float(item.get('f53', 0))
                change = float(item.get('f54', 0))
            except (ValueError, TypeError):
                continue
            dates.append(date_str)
            navs.append(nav)
            changes.append(change / 100)

        if len(navs) >= 5:
            df = _pd2.DataFrame({'净值日期': dates, '单位净值': navs, '日增长率': changes})
            return df

    return _pd2.DataFrame()


def _compute_dca_result(fund_hist, start_date_obj, end_date_obj, amount, frequency):
    """纯计算函数：从 fund_hist DataFrame 计算定投结果"""
    if '日期' in fund_hist.columns:
        fund_hist['日期'] = pd.to_datetime(fund_hist['日期'])
    else:
        fund_hist['日期'] = pd.to_datetime(fund_hist.index)
    fund_hist = fund_hist[(fund_hist['日期'] >= start_date_obj) & (fund_hist['日期'] <= end_date_obj)]
    fund_hist = fund_hist.sort_values('日期')

    if fund_hist.empty:
        return None

    if frequency == 'daily':
        invest_dates = fund_hist['日期'].tolist()
    elif frequency == 'weekly':
        fund_hist['week'] = fund_hist['日期'].dt.isocalendar().week
        fund_hist['year'] = fund_hist['日期'].dt.year
        invest_dates = fund_hist.groupby(['year', 'week']).first()['日期'].tolist()
    else:
        fund_hist['month'] = fund_hist['日期'].dt.month
        fund_hist['year'] = fund_hist['日期'].dt.year
        invest_dates = fund_hist.groupby(['year', 'month']).first()['日期'].tolist()

    invest_dates = [d for d in invest_dates if start_date_obj <= d <= end_date_obj]

    total_invested = 0
    total_shares = 0
    investment_records = []

    for date in invest_dates:
        row = fund_hist[fund_hist['日期'] == date]
        if not row.empty:
            price = row['收盘'].iloc[0]
            shares = amount / price
            total_invested += amount
            total_shares += shares
            investment_records.append({
                'date': date.strftime('%Y-%m-%d'),
                'price': round(price, 4),
                'amount': amount,
                'shares': round(shares, 4),
                'total_invested': round(total_invested, 2),
                'total_shares': round(total_shares, 4)
            })

    if total_shares > 0:
        end_price_row = fund_hist[fund_hist['日期'] <= end_date_obj].tail(1)
        current_price = end_price_row['收盘'].iloc[0] if not end_price_row.empty else fund_hist['收盘'].iloc[-1]
        current_value = total_shares * current_price
        profit = current_value - total_invested
        profit_rate = (profit / total_invested) * 100 if total_invested > 0 else 0
    else:
        current_value = profit = profit_rate = 0

    portfolio_values = []
    if investment_records and len(fund_hist) > 0:
        for _, row in fund_hist.iterrows():
            if start_date_obj <= row['日期'] <= end_date_obj:
                portfolio_values.append({
                    'date': row['日期'].strftime('%Y-%m-%d'),
                    'value': round(total_shares * row['收盘'], 2),
                    'invested': round(total_invested, 2)
                })

    return {
        'total_invested': round(total_invested, 2),
        'current_value': round(current_value, 2),
        'profit': round(profit, 2),
        'profit_rate': round(profit_rate, 2),
        'total_shares': round(total_shares, 4),
        'investment_count': len(investment_records),
        'records': investment_records[-10:] if len(investment_records) > 10 else investment_records,
        'portfolio_values': portfolio_values,
        'fund_hist': fund_hist,
    }


def _build_dca_chart(result, fund_code):
    """从计算结果构建 Plotly 图表"""
    portfolio_values = result.get('portfolio_values', [])
    total_invested = result['total_invested']
    current_value = result['current_value']
    profit = result['profit']
    profit_rate = result['profit_rate']

    invested_values = []
    invested_dates = []
    if 'records' in result:
        invested_values = [r['total_invested'] for r in result['records']]
        invested_dates = [r['date'] for r in result['records']]

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=invested_dates, y=invested_values, mode='lines', name='累计投入',
        line=dict(color='#8B5CF6', width=2.5, dash='dash', shape='spline', smoothing=0.3),
        hovertemplate='<b>日期</b>: %{x}<br><b>累计投入</b>: ¥%{y:,.2f}<extra></extra>'
    ))

    portfolio_dates, portfolio_values_list = [], []
    if portfolio_values:
        sample = portfolio_values[::max(1, len(portfolio_values)//20)]
        for item in sample:
            if item and 'date' in item and 'value' in item:
                portfolio_dates.append(item['date'])
                portfolio_values_list.append(item['value'])

    profit_color = '#EF4444' if profit >= 0 else '#10B981'
    fig.add_trace(go.Scatter(
        x=portfolio_dates, y=portfolio_values_list, mode='lines', name='账户价值',
        fill='tonexty',
        fillcolor=f'rgba(239, 68, 68, 0.2)' if profit >= 0 else 'rgba(16, 185, 129, 0.2)',
        line=dict(color=profit_color, width=2.5, shape='spline', smoothing=0.3),
        hovertemplate='<b>日期</b>: %{x}<br><b>账户价值</b>: ¥%{y:,.2f}<extra></extra>'
    ))

    fig.update_layout(
        title=dict(
            text=f'<b style="color:#8B5CF6">💰</b> {fund_code} 定投收益走势 '
                 f'<span style="font-size:12px;color:#94A3B8">(收益率: <b style="color:{profit_color}">'
                 f'{"+" if profit_rate >= 0 else ""}{profit_rate:.2f}%</b>)</span>',
            font=dict(size=16, family='IBM Plex Sans', color='#F8FAFC'), x=0.5, xanchor='center'
        ),
        xaxis=dict(title='', showgrid=True, gridcolor='rgba(139, 92, 246, 0.1)',
                   linecolor='rgba(148, 163, 184, 0.3)', tickfont=dict(size=11, color='#94A3B8'), hoverformat='%Y-%m-%d'),
        yaxis=dict(title='金额 (元)', showgrid=True, gridcolor='rgba(139, 92, 246, 0.1)',
                   linecolor='rgba(148, 163, 184, 0.3)', tickfont=dict(size=11, color='#94A3B8'), hoverformat=',.2f'),
        margin=dict(l=60, r=30, t=60, b=50),
        hoverlabel=dict(font=dict(color='white', family='IBM Plex Sans'),
                        bgcolor='rgba(15, 23, 42, 0.95)', bordercolor='rgba(139, 92, 246, 0.5)'),
        paper_bgcolor='rgba(30, 41, 59, 0.95)', plot_bgcolor='rgba(15, 23, 42, 0.4)',
        height=380, hovermode='x unified',
        legend=dict(font=dict(color='#94A3B8')),
        annotations=[dict(
            text=f'总投入: ¥{total_invested:,.2f} | 当前价值: ¥{current_value:,.2f} | 收益: {"+" if profit >= 0 else ""}{profit:,.2f}',
            x=0.02, y=0.98, xref='paper', yref='paper', showarrow=False,
            font=dict(size=14, color=profit_color), bgcolor='rgba(30, 41, 59, 0.9)',
            borderpad=6, bordercolor='rgba(148, 163, 184, 0.3)'
        )]
    )
    return fig.to_html(full_html=False, include_plotlyjs=False)


@backtest_bp.route('/api/fund/backtest', methods=['GET'])
def get_fund_backtest():
    fund_code = request.args.get('fund_code', '').strip()
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    if not fund_code:
        return jsonify({'error': '请输入基金代码'})

    if not start_date:
        start_date = (datetime.now() - timedelta(days=365)).strftime('%Y%m%d')
    if not end_date:
        end_date = datetime.now().strftime('%Y%m%d')

    # 解析用户指定的时间范围
    start_date_obj = datetime.strptime(start_date, '%Y%m%d')
    end_date_obj = datetime.strptime(end_date, '%Y%m%d')

    try:
        from fund_crawler import crawl_fund_nav_df
        fund_hist = crawl_fund_nav_df(fund_code)
        import pandas as _pd
        if isinstance(fund_hist, list):
            fund_hist = _pd.DataFrame(fund_hist)

        if not fund_hist.empty:
            fund_hist['日期'] = _pd.to_datetime(fund_hist['净值日期'])
            fund_hist = fund_hist.rename(columns={'单位净值': '收盘'})
            # 过滤用户指定的时间范围
            fund_hist = fund_hist[(fund_hist['日期'] >= start_date_obj) & (fund_hist['日期'] <= end_date_obj)]
        else:
            # 东方财富直调 ETF/LOF 历史净值（尝试上交所+深交所）
            fund_hist = _http_fetch_etf_lof_nav(fund_code, start_date, end_date)
            if not fund_hist.empty:
                fund_hist['日期'] = _pd.to_datetime(fund_hist['净值日期'])

        if fund_hist.empty:
            return jsonify({'error': '未找到基金数据'})

        if '日期' in fund_hist.columns:
            fund_hist['日期'] = pd.to_datetime(fund_hist['日期'])
        else:
            fund_hist['日期'] = pd.to_datetime(fund_hist.index)

        # 再次确保数据在时间范围内
        fund_hist = fund_hist[(fund_hist['日期'] >= start_date_obj) & (fund_hist['日期'] <= end_date_obj)]
        fund_hist = fund_hist.sort_values('日期')

        if fund_hist.empty:
            return jsonify({'error': '指定时间段内无基金数据'})

        prices = fund_hist['收盘'].tolist()
        dates = fund_hist['日期'].dt.strftime('%Y-%m-%d').tolist()

        if len(prices) >= 2:
            total_return = (prices[-1] - prices[0]) / prices[0] * 100
            max_price = max(prices)
            min_price = min(prices)
            volatility = (max_price - min_price) / prices[0] * 100
        else:
            total_return = 0
            max_price = prices[0] if prices else 0
            min_price = prices[0] if prices else 0
            volatility = 0

        fig = go.Figure()

        # 渐变色面积图效果
        fig.add_trace(go.Scatter(
            x=dates,
            y=prices,
            mode='lines',
            name='净值',
            line=dict(
                color='#F59E0B',
                width=2.5,
                shape='spline',
                smoothing=0.3
            ),
            fill='tonexty',
            fillcolor='rgba(245, 158, 11, 0.15)',
            hovertemplate='<b>日期</b>: %{x}<br><b>净值</b>: %{y:.4f}<extra></extra>'
        ))

        # 添加价格区间范围（可选，用于显示波动）
        fig.update_layout(
            title=dict(
                text=f'<b style="color:#F59E0B">📈</b> {fund_code} 历史净值走势 <span style="font-size:12px;color:#94A3B8">({start_date} ~ {end_date})</span>',
                font=dict(size=16, family='IBM Plex Sans', color='#F8FAFC'),
                x=0.5,
                xanchor='center'
            ),
            xaxis=dict(
                title='',
                showgrid=True,
                gridcolor='rgba(245, 158, 11, 0.1)',
                linecolor='rgba(148, 163, 184, 0.3)',
                rangeslider=dict(visible=True, thickness=0.05),
                range=[dates[0] if dates else None, dates[-1] if dates else None],
                tickfont=dict(size=11, color='#94A3B8'),
                hoverformat='%Y-%m-%d'
            ),
            yaxis=dict(
                title='单位净值',
                showgrid=True,
                gridcolor='rgba(245, 158, 11, 0.1)',
                linecolor='rgba(148, 163, 184, 0.3)',
                tickfont=dict(size=11, color='#94A3B8'),
                hoverformat='.4f'
            ),
            paper_bgcolor='rgba(30, 41, 59, 0.95)',
            plot_bgcolor='rgba(15, 23, 42, 0.4)',
            margin=dict(l=50, r=30, t=60, b=50),
            height=380,
            hovermode='x unified',
            hoverlabel=dict(
                bgcolor='rgba(15, 23, 42, 0.95)',
                bordercolor='rgba(245, 158, 11, 0.5)',
                font=dict(color='white', family='IBM Plex Sans')
            ),
            annotations=[
                dict(
                    text=f'总收益率: <b>{"+" if total_return >= 0 else ""}{total_return:.2f}%</b>',
                    x=0.02, y=0.98, xref='paper', yref='paper',
                    showarrow=False,
                    font=dict(size=13, color='#EF4444' if total_return >= 0 else '#10B981'),
                    bgcolor='rgba(30, 41, 59, 0.9)',
                    borderpad=4
                )
            ]
        )

        chart_html = fig.to_html(full_html=False, include_plotlyjs=False)

        return jsonify({
            'success': True,
            'data': {
                'dates': dates,
                'prices': prices,
                'total_return': round(total_return, 2),
                'volatility': round(volatility, 2),
                'start_price': prices[0],
                'end_price': prices[-1],
                'max_price': max_price,
                'min_price': min_price,
                'chart': chart_html
            }
        })
    except Exception as e:
        return jsonify({'error': f'获取回测数据失败: {str(e)}'})


@backtest_bp.route('/api/fund/dca', methods=['GET'])
def calculate_dca():
    fund_code = request.args.get('fund_code', '').strip()
    amount = request.args.get('amount', 1000)
    frequency = request.args.get('frequency', 'weekly')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    try:
        amount = float(amount)
    except:
        amount = 1000

    if not fund_code:
        return jsonify({'error': '请输入基金代码'})

    if not start_date:
        start_date = (datetime.now() - timedelta(days=365)).strftime('%Y%m%d')
    if not end_date:
        end_date = datetime.now().strftime('%Y%m%d')

    start_date_obj = datetime.strptime(start_date, '%Y%m%d')
    end_date_obj = datetime.strptime(end_date, '%Y%m%d')

    try:
        from fund_crawler import crawl_fund_nav_df

        # 缓存键
        cache_key = generate_cache_key(CACHE_CONFIG['fund_dca']['prefix'],
                                       f"{fund_code}:{amount}:{frequency}:{start_date}:{end_date}")
        cached = get_cache(cache_key)
        if cached:
            chart_html = _build_dca_chart(cached, fund_code)
            return jsonify({'success': True, 'data': {**cached, 'chart': chart_html}})

        # 先用 1 年数据快速响应（~1.5s vs ~5s）
        fund_hist_1y = crawl_fund_nav_df(fund_code, years=1)
        if isinstance(fund_hist_1y, list):
            fund_hist_1y_df = pd.DataFrame(fund_hist_1y)
        else:
            fund_hist_1y_df = fund_hist_1y

        if not fund_hist_1y_df.empty:
            fund_hist_1y_df['日期'] = pd.to_datetime(fund_hist_1y_df['净值日期'])
            fund_hist_1y_df = fund_hist_1y_df.rename(columns={'单位净值': '收盘'})
            fund_hist_1y_df = fund_hist_1y_df[(fund_hist_1y_df['日期'] >= start_date_obj) & (fund_hist_1y_df['日期'] <= end_date_obj)]

        if fund_hist_1y_df.empty:
            fund_hist_1y_df = _http_fetch_etf_lof_nav(fund_code, start_date, end_date)
            if not fund_hist_1y_df.empty:
                fund_hist_1y_df['日期'] = pd.to_datetime(fund_hist_1y_df['净值日期'])

        if fund_hist_1y_df.empty:
            return jsonify({'error': '未找到基金数据'})

        result = _compute_dca_result(fund_hist_1y_df, start_date_obj, end_date_obj, amount, frequency)
        if result is None:
            return jsonify({'error': '指定时间段内无基金数据'})

        response_data = {k: v for k, v in result.items() if k not in ('portfolio_values', 'fund_hist')}
        chart_html = _build_dca_chart(result, fund_code)
        set_cache(cache_key, response_data, expiry=CACHE_CONFIG['fund_dca']['expiry'])

        # 后台线程用 3 年数据更新缓存
        def _refresh_dca():
            try:
                full_hist = crawl_fund_nav_df(fund_code, years=3)
                if isinstance(full_hist, list):
                    full_hist_df = pd.DataFrame(full_hist)
                else:
                    full_hist_df = full_hist
                if not full_hist_df.empty:
                    full_hist_df['日期'] = pd.to_datetime(full_hist_df['净值日期'])
                    full_hist_df = full_hist_df.rename(columns={'单位净值': '收盘'})
                    full_hist_df = full_hist_df[(full_hist_df['日期'] >= start_date_obj) & (full_hist_df['日期'] <= end_date_obj)]
                if not full_hist_df.empty:
                    full_result = _compute_dca_result(full_hist_df, start_date_obj, end_date_obj, amount, frequency)
                    if full_result:
                        refreshed = {k: v for k, v in full_result.items() if k not in ('portfolio_values', 'fund_hist')}
                        set_cache(cache_key, refreshed, expiry=CACHE_CONFIG['fund_dca']['expiry'])
            except Exception:
                pass
        threading.Thread(target=_refresh_dca, daemon=True).start()

        return jsonify({'success': True, 'data': {**response_data, 'chart': chart_html}})
    except Exception as e:
        return jsonify({'error': f'定投计算失败: {str(e)}'})
