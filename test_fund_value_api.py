import akshare as ak

print("测试akshare基金实时估值API")
print("=" * 50)

# 测试1: 查找所有基金相关API
print("\n1. 查找所有基金相关API:")
fund_funcs = [x for x in dir(ak) if 'fund' in x.lower()]
print(f"找到 {len(fund_funcs)} 个基金相关API")
print("前20个:", fund_funcs[:20])

# 测试2: 查找估值相关API
print("\n2. 查找估值相关API:")
estimate_funcs = [x for x in fund_funcs if any(keyword in x.lower() for keyword in ['value', 'estimate', 'real', 'live'])]
print("估值相关API:", estimate_funcs)

# 测试3: 测试具体的API
print("\n3. 测试具体API:")
test_apis = [
    'fund_value_estimation_em',
    'fund_real_time_em', 
    'fund_live_em',
    'fund_estimate_em',
    'fund_open_fund_info_em'
]

for api in test_apis:
    if api in dir(ak):
        print(f"\n测试API: {api}")
        try:
            if api == 'fund_open_fund_info_em':
                # 测试基金信息API
                df = ak.fund_open_fund_info_em(symbol='519674', indicator='单位净值走势')
                print(f"  调用成功, 结果形状: {df.shape}")
                print(f"  列名: {df.columns.tolist()}")
                print(f"  最新数据: {df.tail(1).to_dict('records')}")
            else:
                # 测试其他API
                df = getattr(ak, api)()
                print(f"  调用成功, 结果形状: {df.shape}")
                print(f"  列名: {df.columns.tolist()}")
                if not df.empty:
                    print(f"  前3行: {df.head(3).to_dict('records')}")
        except Exception as e:
            print(f"  调用失败: {e}")
    else:
        print(f"API {api} 不存在")

print("\n测试完成!")
