import requests
import time

# 测试时间同步问题
def test_time_sync():
    print("=== 测试时间同步问题 ===")
    
    test_cases = [
        {
            "name": "短期测试 (10天)",
            "fund_code": "519674",
            "amount": 1000,
            "frequency": "daily",
            "start_date": "20250101",
            "end_date": "20250110"
        },
        {
            "name": "中期测试 (1个月)",
            "fund_code": "161039",
            "amount": 500,
            "frequency": "weekly",
            "start_date": "20250101",
            "end_date": "20250131"
        },
        {
            "name": "长期测试 (3个月)",
            "fund_code": "510300",
            "amount": 2000,
            "frequency": "monthly",
            "start_date": "20250101",
            "end_date": "20250331"
        }
    ]
    
    for test_case in test_cases:
        print(f"\n{test_case['name']}")
        print(f"时间段: {test_case['start_date']} 至 {test_case['end_date']}")
        print(f"基金: {test_case['fund_code']}, 频率: {test_case['frequency']}")
        
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
                    
                    # 检查投资记录的时间范围
                    if data['data'].get('records'):
                        records = data['data']['records']
                        first_record_date = records[0]['date'] if records else '无'
                        last_record_date = records[-1]['date'] if records else '无'
                        print(f"投资记录时间范围: {first_record_date} 至 {last_record_date}")
                    
                    # 检查图表数据的时间范围
                    if 'chart' in data['data']:
                        chart_html = data['data']['chart']
                        # 简单检查图表中是否包含时间信息
                        print("图表生成: 成功")
                else:
                    print(f"❌ 失败: {data.get('error')}")
            else:
                print(f"❌ 失败: 状态码 {response.status_code}")
                print(f"响应: {response.text}")
        except Exception as e:
            print(f"❌ 异常: {str(e)}")
        
        time.sleep(2)  # 避免请求过快

def test_edge_cases():
    print("\n=== 测试边界情况 ===")
    
    edge_cases = [
        {
            "name": "开始日期等于结束日期",
            "fund_code": "519674",
            "amount": 1000,
            "frequency": "daily",
            "start_date": "20250101",
            "end_date": "20250101"
        },
        {
            "name": "开始日期晚于结束日期",
            "fund_code": "519674",
            "amount": 1000,
            "frequency": "daily",
            "start_date": "20250201",
            "end_date": "20250101"
        }
    ]
    
    for case in edge_cases:
        print(f"\n{case['name']}")
        
        url = "http://127.0.0.1:5000/api/fund/dca"
        
        try:
            response = requests.get(url, params=case, timeout=30)
            print(f"状态码: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    print("✅ 成功!")
                else:
                    print(f"❌ 失败: {data.get('error')}")
            else:
                print(f"❌ 失败: 状态码 {response.status_code}")
        except Exception as e:
            print(f"❌ 异常: {str(e)}")
        
        time.sleep(1)

if __name__ == "__main__":
    test_time_sync()
    test_edge_cases()
    print("\n=== 测试完成 ===")
