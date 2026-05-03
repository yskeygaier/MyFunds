import akshare as ak

# 尝试获取基金名称
fund_code = "161039"

# 尝试 fund_etf_fund_info_em
print("=== fund_etf_fund_info_em ===")
try:
    df = ak.fund_etf_fund_info_em(fund=fund_code)
    print("Columns:", df.columns.tolist())
    print(df)
except Exception as e:
    print(f"Error: {e}")

# 尝试 fund_financial_fund_info_em
print("\n=== fund_financial_fund_info_em ===")
try:
    df = ak.fund_financial_fund_info_em(fund=fund_code)
    print("Columns:", df.columns.tolist())
except Exception as e:
    print(f"Error: {e}")

# 尝试 fund_graded_fund_info_em  
print("\n=== fund_graded_fund_info_em ===")
try:
    df = ak.fund_graded_fund_info_em(fund=fund_code)
    print("Columns:", df.columns.tolist())
except Exception as e:
    print(f"Error: {e}")
