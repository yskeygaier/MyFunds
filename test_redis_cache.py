import requests
import time
import json

# 测试Redis缓存功能
def test_redis_cache():
    fund_code = '519674'
    
    print('测试Redis缓存功能')
    print('=' * 60)
    
    # 测试基金信息缓存
    print('1. 测试基金信息缓存:')
    url = f'http://localhost:5000/api/fund/info?fund_code={fund_code}'
    
    # 第一次请求（缓存未命中）
    print('   第一次请求（缓存未命中）:')
    start_time = time.time()
    response = requests.get(url, timeout=30)
    end_time = time.time()
    print(f'   响应时间: {end_time - start_time:.4f} 秒')
    
    if response.status_code == 200:
        data = response.json()
        print(f'   响应状态: {data.get("success")}')
        print(f'   是否来自缓存: {data.get("from_cache", False)}')
        print(f'   基金名称: {data.get("data", {}).get("基金简称")}')
    else:
        print(f'   请求失败: {response.status_code}')
    
    print('   ' + '-' * 50)
    
    # 第二次请求（缓存命中）
    print('   第二次请求（缓存命中）:')
    start_time = time.time()
    response = requests.get(url, timeout=30)
    end_time = time.time()
    print(f'   响应时间: {end_time - start_time:.4f} 秒')
    
    if response.status_code == 200:
        data = response.json()
        print(f'   响应状态: {data.get("success")}')
        print(f'   是否来自缓存: {data.get("from_cache", False)}')
        print(f'   基金名称: {data.get("data", {}).get("基金简称")}')
    else:
        print(f'   请求失败: {response.status_code}')
    
    print('=' * 60)
    
    # 测试实时估值（不应缓存）
    print('2. 测试实时估值（不应缓存）:')
    url = f'http://localhost:5000/api/fund/valuation?fund_code={fund_code}'
    
    # 第一次请求
    print('   第一次请求:')
    start_time = time.time()
    response = requests.get(url, timeout=30)
    end_time = time.time()
    print(f'   响应时间: {end_time - start_time:.4f} 秒')
    
    if response.status_code == 200:
        data = response.json()
        print(f'   响应状态: {data.get("success")}')
        print(f'   是否来自缓存: {data.get("from_cache", False)}')
        print(f'   实时估值: {data.get("data", {}).get("实时估值")}')
        print(f'   估值时间: {data.get("data", {}).get("估值时间")}')
    else:
        print(f'   请求失败: {response.status_code}')
    
    print('   ' + '-' * 50)
    
    # 等待2秒后再次请求
    time.sleep(2)
    print('   第二次请求（2秒后）:')
    start_time = time.time()
    response = requests.get(url, timeout=30)
    end_time = time.time()
    print(f'   响应时间: {end_time - start_time:.4f} 秒')
    
    if response.status_code == 200:
        data = response.json()
        print(f'   响应状态: {data.get("success")}')
        print(f'   是否来自缓存: {data.get("from_cache", False)}')
        print(f'   实时估值: {data.get("data", {}).get("实时估值")}')
        print(f'   估值时间: {data.get("data", {}).get("估值时间")}')
    else:
        print(f'   请求失败: {response.status_code}')
    
    print('=' * 60)
    print('测试完成!')

if __name__ == '__main__':
    test_redis_cache()
