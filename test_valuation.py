import akshare as ak
import inspect

fund_code = "165520"

print(f"测试基金代码: {fund_code}")
print("=" * 50)

# 检查 fund_open_fund_daily_em 的函数签名
print("\n1. 检查 fund_open_fund_daily_em() 函数签名:")
try:
    sig = inspect.signature(ak.fund_open_fund_daily_em)
    print(f"   签名: {sig}")
except Exception as e:
    print(f"   获取签名失败: {e}")

# 检查 fund_value_estimation_em
print("\n2. 检查 fund_value_estimation_em() 函数签名:")
try:
    sig = inspect.signature(ak.fund_value_estimation_em)
    print(f"   签名: {sig}")
except Exception as e:
    print(f"   获取签名失败: {e}")

# 测试正确的实时估值方法
print("\n3. 测试 fund_value_estimation_em()")
try:
    df = ak.fund_value_estimation_em()
    print(f"   返回数据形状: {df.shape}")
    print(f"   列名: {df.columns.tolist()}")
    # 查找165520
    if '基金代码' in df.columns:
        fund_data = df[df['基金代码'] == fund_code]
        if not fund_data.empty:
            print(f"   找到基金:\n{fund_data}")
        else:
            print(f"   未找到基金 {fund_code}")
    elif '代码' in df.columns:
        fund_data = df[df['代码'] == fund_code]
        if not fund_data.empty:
            print(f"   找到基金:\n{fund_data}")
        else:
            print(f"   未找到基金 {fund_code}")
except Exception as e:
    print(f"   异常: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 50)
