import requests
import time

# 测试定投计算功能
def test_dca():
    print("=== 测试定投计算功能 ===")
    
    test_cases = [
        {
            "fund_code": "519674",
            "amount": 1000,
            "frequency": "weekly",
            "start_date": "20250101",
            "end_date": "20250307"
        },
        {
            "fund_code": "161039",
            "amount": 500,
            "frequency": "monthly",
            "start_date": "20250101",
            "end_date": "20250307"
        },
        {
            "fund_code": "510300",
            "amount": 2000,
            "frequency": "daily",
            "start_date": "20250101",
            "end_date": "20250110"
        }
    ]
    
    for i, test_case in enumerate(test_cases):
        print(f"\n测试用例 {i+1}: {test_case['fund_code']} - {test_case['frequency']}")
        
        url = "http://127.0.0.1:5000/api/fund/dca"
        
        try:
            response = requests.get(url, params=test_case, timeout=30)
            print(f"状态码: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    print("✅ 成功!")
                    print(f"累计投入: {data['data']['total_invested']}")
                    print(f"当前价值: {data['data']['current_value']}")
                    print(f"总收益: {data['data']['profit']}")
                    print(f"收益率: {data['data']['profit_rate']}%")
                else:
                    print(f"❌ 失败: {data.get('error')}")
            else:
                print(f"❌ 失败: 状态码 {response.status_code}")
                print(f"响应: {response.text}")
        except Exception as e:
            print(f"❌ 异常: {str(e)}")
        
        time.sleep(2)  # 避免请求过快

if __name__ == "__main__":
    test_dca()
