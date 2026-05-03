import akshare as ak

# 查找基金实时估值相关API
fund_funcs = [x for x in dir(ak) if 'fund' in x.lower() and ('value' in x.lower() or 'estimate' in x.lower() or 'real' in x.lower() or 'live' in x.lower())]
print("基金估值相关API:")
for f in fund_funcs:
    print(f)

# 测试是否有实时估值API
print("\n测试基金实时估值API:")
try:
    # 尝试不同的API
    apis_to_test = [
        'fund_value_estimation_em',
        'fund_real_time_em',
        'fund_live_em',
        'fund_estimate_em'
    ]
    
    for api_name in apis_to_test:
        if api_name in dir(ak):
            print(f"找到API: {api_name}")
            # 测试调用
            try:
                result = getattr(ak, api_name)()
                print(f"  调用成功, 结果形状: {result.shape}")
                print(f"  列名: {result.columns.tolist()}")
                print(f"  前5行: {result.head()}")
            except Exception as e:
                print(f"  调用失败: {e}")
except Exception as e:
    print(f"测试失败: {e}")
