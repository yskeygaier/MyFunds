import akshare as ak

# 测试不同基金的行业配置
test_funds = ['519674', '161039', '163406']

for fund_code in test_funds:
    print(f"\n=== 测试基金 {fund_code} ===")
    try:
        industry_df = ak.fund_portfolio_industry_allocation_em(symbol=fund_code)
        print(f"数据形状: {industry_df.shape}")
        if not industry_df.empty:
            print("前5行数据:")
            print(industry_df.head())
        else:
            print("无行业配置数据")
    except Exception as e:
        print(f"获取行业配置失败: {e}")
