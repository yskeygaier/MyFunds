import requests

# 测试不同基金的信息
test_funds = ['519674', '161039', '163406']

for fund_code in test_funds:
    print(f"\n=== 测试基金 {fund_code} ===")
    try:
        response = requests.get(f"http://127.0.0.1:5001/api/fund/info?fund_code={fund_code}")
        data = response.json()
        if data.get('success'):
            fund_data = data.get('data', {})
            print(f"基金名称: {fund_data.get('基金简称')}")
            print(f"基金风格: {fund_data.get('基金风格')}")
            print(f"风格描述: {fund_data.get('风格描述')}")
            print(f"第一大行业: {fund_data.get('第一大行业')}")
            print(f"行业占比: {fund_data.get('行业占比')}")
        else:
            print(f"错误: {data.get('error')}")
    except Exception as e:
        print(f"请求失败: {e}")
