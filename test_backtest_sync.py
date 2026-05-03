import requests
import time

# 测试回测功能时间同步问题
def test_backtest_time_sync():
    print("=== 测试回测功能时间同步问题 ===")
    
    test_cases = [
        {
            "name": "短期测试 (10天)",
            "fund_code": "519674",
            "start_date": "20250101",
            "end_date": "20250110"
        },
        {
            "name": "中期测试 (1个月)",
            "fund_code": "161039",
            "start_date": "20250101",
            "end_date": "20250131"
        },
        {
            "name": "长期测试 (3个月)",
            "fund_code": "510300",
            "start_date": "20250101",
            "end_date": "20250331"
        }
    ]
    
    for test_case in test_cases:
        print(f"\n{test_case['name']}")
        print(f"时间段: {test_case['start_date']} 至 {test_case['end_date']}")
        print(f"基金: {test_case['fund_code']}")
        
        url = "http://127.0.0.1:5000/api/fund/backtest"
        
        try:
            response = requests.get(url, params=test_case, timeout=30)
            print(f"状态码: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    print("✅ 成功!")
                    print(f"总收益率: {data['data']['total_return']}%")
                    print(f"起始净值: {data['data']['start_price']}")
                    print(f"结束净值: {data['data']['end_price']}")
                    
                    # 检查日期范围
                    if data['data'].get('dates'):
                        dates = data['data']['dates']
                        if dates:
                            first_date = dates[0]
                            last_date = dates[-1]
                            print(f"数据时间范围: {first_date} 至 {last_date}")
                            
                            # 验证时间范围是否匹配
                            expected_start = test_case['start_date']
                            expected_end = test_case['end_date']
                            
                            # 转换格式进行比较
                            actual_start = first_date.replace('-', '')
                            actual_end = last_date.replace('-', '')
                            
                            if actual_start >= expected_start and actual_end <= expected_end:
                                print("✅ 时间范围正确!")
                            else:
                                print(f"❌ 时间范围不匹配! 预期: {expected_start} - {expected_end}, 实际: {actual_start} - {actual_end}")
                    
                    # 检查图表
                    if 'chart' in data['data']:
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
            "start_date": "20250101",
            "end_date": "20250101"
        },
        {
            "name": "开始日期晚于结束日期",
            "fund_code": "519674",
            "start_date": "20250201",
            "end_date": "20250101"
        }
    ]
    
    for case in edge_cases:
        print(f"\n{case['name']}")
        
        url = "http://127.0.0.1:5000/api/fund/backtest"
        
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
    test_backtest_time_sync()
    test_edge_cases()
    print("\n=== 测试完成 ===")
