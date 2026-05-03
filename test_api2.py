import akshare as ak

# 测试基金持仓
print("=== fund_portfolio_hold_em ===")
try:
    df = ak.fund_portfolio_hold_em(symbol="519674")
    print("Columns:", df.columns.tolist())
    print(df)
except Exception as e:
    print(f"Error: {e}")

# 测试基金收益统计（夏普比率等）
print("\n=== fund_em_fund_info (如果有收益指标) ===")
try:
    # 尝试不同的指标
    for period in ['近1年', '近2年', '近3年', '近5年']:
        df = ak.fund_open_fund_info_em(symbol="519674", indicator='单位净值走势', period=period)
        print(f"{period}: {len(df)} rows")
except Exception as e:
    print(f"Error: {e}")
