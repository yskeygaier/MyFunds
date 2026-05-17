#!/usr/bin/env python3
"""Step 0: 净值数据交叉验证
选3只已知基金，将回测数据与天天基金/支付宝官方数据对比。
验证: 年化收益误差 < 2%, 最大回撤误差 < 5%
"""
import time
import sys

def verify_fund(code, name, years=3):
    """验证单只基金的回测数据"""
    from fund_crawler import crawl_fund_nav_df
    import pandas as pd
    import numpy as np

    print(f"\n{'='*50}")
    print(f"验证: {name} ({code})")
    print(f"{'='*50}")

    # 获取净值数据
    t0 = time.time()
    data = crawl_fund_nav_df(code, years=years)
    fetch_time = time.time() - t0
    if not data or len(data) < 20:
        print(f"  ❌ 数据不足: {len(data) if data else 0} 行 (需要≥20)")
        return None

    print(f"  数据获取: {fetch_time:.2f}s, {len(data)} 行")

    # 转为DataFrame计算指标
    df = pd.DataFrame(data)
    df['date'] = pd.to_datetime(df['净值日期'])
    df['nav'] = pd.to_numeric(df['单位净值'], errors='coerce')
    df = df.dropna(subset=['nav', 'date']).sort_values('date')

    start_nav = df['nav'].iloc[0]
    end_nav = df['nav'].iloc[-1]
    total_return = (end_nav / start_nav - 1) * 100

    # 年化收益率 (CAGR)
    days = (df['date'].iloc[-1] - df['date'].iloc[0]).days
    years_actual = days / 365.25
    if total_return > -100:
        annualized = ((1 + total_return/100) ** (1 / max(years_actual, 0.5))) - 1
    else:
        annualized = -1

    # 最大回撤
    peak = df['nav'].iloc[0]
    max_dd = 0
    max_dd_date = ''
    for _, row in df.iterrows():
        v = row['nav']
        d = row['date']
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd:
            max_dd = dd
            max_dd_date = d.strftime('%Y-%m-%d')

    # 年化波动率
    df['return'] = df['nav'].pct_change()
    volatility = df['return'].std() * (252 ** 0.5) * 100

    # 夏普比率（无风险利率 2.5%）
    rf = 2.5
    sharpe = (annualized * 100 - rf) / volatility if volatility > 0 else 0

    print(f"  回测周期: {df['date'].iloc[0].strftime('%Y-%m-%d')} → {df['date'].iloc[-1].strftime('%Y-%m-%d')}")
    print(f"  数据天数: {days}天 ({years_actual:.1f}年)")
    print(f"  ─────────────────────────────")
    print(f"  总收益率:    {total_return:.2f}%")
    print(f"  年化收益率:  {annualized*100:.2f}%")
    print(f"  最大回撤:    -{max_dd:.2f}% ({max_dd_date})")
    print(f"  年化波动率:  {volatility:.2f}%")
    print(f"  夏普比率:    {sharpe:.2f}")

    return {
        'code': code, 'name': name,
        'annual_return': round(annualized * 100, 2),
        'max_drawdown': round(max_dd, 2),
        'volatility': round(volatility, 2),
        'sharpe': round(sharpe, 2),
        'years': round(years_actual, 1),
        'data_rows': len(data),
    }


if __name__ == '__main__':
    print("=" * 55)
    print("Step 0: 净值数据交叉验证")
    print("请在天天基金/支付宝上手动查找以下基金的")
    print("年化收益和最大回撤数据，对比下方计算结果")
    print("=" * 55)

    results = []

    # 测试3只已知基金
    tests = [
        ('000001', '华夏成长混合', 3),
        ('161725', '招商中证白酒', 3),
        ('110011', '易方达消费行业', 3),
    ]

    for code, name, years in tests:
        try:
            r = verify_fund(code, name, years)
            if r:
                results.append(r)
        except Exception as e:
            print(f"  ❌ 验证券{code}失败: {e}")

    print(f"\n{'='*55}")
    print("验证结论")
    print(f"{'='*55}")
    if results:
        print(f"{'基金':20s} {'年化收益':>8s} {'最大回撤':>8s} {'波动率':>8s} {'夏普':>6s}")
        print("-" * 55)
        for r in results:
            print(f"{r['name']:20s} {r['annual_return']:>7.1f}% {r['max_drawdown']:>7.1f}% {r['volatility']:>7.1f}% {r['sharpe']:>5.2f}")
        print()
        print("请与官方数据对比:")
        print("  ✅ 年化收益误差 < 2% → 通过")
        print("  ✅ 最大回撤误差 < 5% → 通过")
        print("  ❌ 超出范围 → 检查净值数据源或计算公式")
    else:
        print("  ❌ 全部验证失败")
