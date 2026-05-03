import requests
import time
import json

# 测试基金信息查询性能
def test_fund_info_performance(fund_code):
    url = f'http://localhost:5000/api/fund/info?fund_code={fund_code}'
    
    print(f'测试基金信息查询性能: {fund_code}')
    print('=' * 50)
    
    # 第一次查询（缓存未命中）
    print('第一次查询（缓存未命中）:')
    start_time = time.time()
    response = requests.get(url, timeout=30)
    end_time = time.time()
    first_query_time = end_time - start_time
    
    if response.status_code == 200:
        data = response.json()
        print(f'响应状态: {data.get("success")}')
        print(f'是否来自缓存: {data.get("from_cache", False)}')
        print(f'响应时间: {first_query_time:.4f} 秒')
        print(f'基金名称: {data.get("data", {}).get("基金简称")}')
    else:
        print(f'请求失败: {response.status_code}')
    
    print('-' * 50)
    
    # 第二次查询（缓存命中）
    print('第二次查询（缓存命中）:')
    start_time = time.time()
    response = requests.get(url, timeout=30)
    end_time = time.time()
    second_query_time = end_time - start_time
    
    if response.status_code == 200:
        data = response.json()
        print(f'响应状态: {data.get("success")}')
        print(f'是否来自缓存: {data.get("from_cache", False)}')
        print(f'响应时间: {second_query_time:.4f} 秒')
        print(f'基金名称: {data.get("data", {}).get("基金简称")}')
    else:
        print(f'请求失败: {response.status_code}')
    
    print('-' * 50)
    
    # 计算性能提升
    if first_query_time > 0:
        improvement = ((first_query_time - second_query_time) / first_query_time) * 100
        print(f'性能提升: {improvement:.2f}%')
    
    print('=' * 50)

# 测试实时估值查询性能
def test_valuation_performance(fund_code):
    url = f'http://localhost:5000/api/fund/valuation?fund_code={fund_code}'
    
    print(f'测试实时估值查询性能: {fund_code}')
    print('=' * 50)
    
    # 第一次查询（缓存未命中）
    print('第一次查询（缓存未命中）:')
    start_time = time.time()
    response = requests.get(url, timeout=30)
    end_time = time.time()
    first_query_time = end_time - start_time
    
    if response.status_code == 200:
        data = response.json()
        print(f'响应状态: {data.get("success")}')
        print(f'是否来自缓存: {data.get("from_cache", False)}')
        print(f'响应时间: {first_query_time:.4f} 秒')
        print(f'实时估值: {data.get("data", {}).get("实时估值")}')
    else:
        print(f'请求失败: {response.status_code}')
    
    print('-' * 50)
    
    # 第二次查询（缓存命中）
    print('第二次查询（缓存命中）:')
    start_time = time.time()
    response = requests.get(url, timeout=30)
    end_time = time.time()
    second_query_time = end_time - start_time
    
    if response.status_code == 200:
        data = response.json()
        print(f'响应状态: {data.get("success")}')
        print(f'是否来自缓存: {data.get("from_cache", False)}')
        print(f'响应时间: {second_query_time:.4f} 秒')
        print(f'实时估值: {data.get("data", {}).get("实时估值")}')
    else:
        print(f'请求失败: {response.status_code}')
    
    print('-' * 50)
    
    # 计算性能提升
    if first_query_time > 0:
        improvement = ((first_query_time - second_query_time) / first_query_time) * 100
        print(f'性能提升: {improvement:.2f}%')
    
    print('=' * 50)

if __name__ == '__main__':
    fund_code = '519674'  # 银河创新成长混合
    test_fund_info_performance(fund_code)
    test_valuation_performance(fund_code)
