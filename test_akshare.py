import akshare as ak

print("测试akshare fund_value_estimation_em")
try:
    df = ak.fund_value_estimation_em()
    print(f"成功获取数据，形状: {df.shape}")
    print(f"列名: {df.columns.tolist()}")
    print(f"前5行: {df.head()}")
except Exception as e:
    print(f"错误: {e}")
