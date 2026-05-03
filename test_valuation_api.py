import requests
import json

# 测试实时估值API
fund_code = '519674'
url = f'http://localhost:5000/api/fund/valuation?fund_code={fund_code}'

print(f'测试实时估值API: {url}')
try:
    response = requests.get(url, timeout=10)
    data = response.json()
    print('API响应:', json.dumps(data, ensure_ascii=False, indent=2))
    
    if data.get('success'):
        valuation_data = data['data']
        print(f'实时估值: {valuation_data.get("实时估值")}')
        print(f'估算涨跌幅: {valuation_data.get("估算涨跌幅")}%')
        print(f'估值时间: {valuation_data.get("估值时间")}')
        print(f'净值日期: {valuation_data.get("净值日期")}')
    else:
        print('API返回错误:', data.get('error'))
except Exception as e:
    print(f'测试失败: {e}')
