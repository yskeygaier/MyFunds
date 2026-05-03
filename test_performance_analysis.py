import requests
import time
import json
import redis

# 测试性能分析
def test_performance_analysis():
    fund_code = '519674'
    
    print('性能分析测试')
    print('=' * 80)
    
    # 测试基金信息查询
    print('1. 测试基金信息查询性能:')
    url = f'http://localhost:5000/api/fund/info?fund_code={fund_code}'
    
    # 第一次请求（缓存未命中）
    print('   第一次请求（缓存未命中）:')
    start_time = time.time()
    response = requests.get(url, timeout=60)
    end_time = time.time()
    first_time = end_time - start_time
    print(f'   响应时间: {first_time:.4f} 秒')
    
    if response.status_code == 200:
        data = response.json()
        print(f'   响应状态: {data.get("success")}')
        print(f'   是否来自缓存: {data.get("from_cache", False)}')
        print(f'   基金名称: {data.get("data", {}).get("基金简称")}')
    else:
        print(f'   请求失败: {response.status_code}')
    
    print('   ' + '-' * 70)
    
    # 第二次请求（缓存命中）
    print('   第二次请求（缓存命中）:')
    start_time = time.time()
    response = requests.get(url, timeout=60)
    end_time = time.time()
    second_time = end_time - start_time
    print(f'   响应时间: {second_time:.4f} 秒')
    
    if response.status_code == 200:
        data = response.json()
        print(f'   响应状态: {data.get("success")}')
        print(f'   是否来自缓存: {data.get("from_cache", False)}')
        print(f'   基金名称: {data.get("data", {}).get("基金简称")}')
    else:
        print(f'   请求失败: {response.status_code}')
    
    # 计算性能提升
    if first_time > 0:
        improvement = ((first_time - second_time) / first_time) * 100
        print(f'   性能提升: {improvement:.2f}%')
    
    print('=' * 80)
    
    # 检查Redis缓存状态
    print('2. 检查Redis缓存状态:')
    try:
        r = redis.Redis(
            host='127.0.0.1',
            port=6379,
            db=0,
            decode_responses=True
        )
        
        # 检查缓存键
        cache_key = f'fund:info:{fund_code}'
        cached_data = r.get(cache_key)
        print(f'   缓存键存在: {cached_data is not None}')
        
        if cached_data:
            cached_data = json.loads(cached_data)
            print(f'   缓存数据大小: {len(str(cached_data))} 字符')
            print(f'   缓存数据包含基金名称: {"基金简称" in cached_data}')
        
        # 检查缓存过期时间
        ttl = r.ttl(cache_key)
        print(f'   缓存剩余过期时间: {ttl} 秒')
        
        # 检查Redis内存使用情况
        info = r.info('memory')
        used_memory = info.get('used_memory_human', 'N/A')
        print(f'   Redis内存使用: {used_memory}')
        
    except Exception as e:
        print(f'   Redis检查失败: {e}')
    
    print('=' * 80)
    
    # 分析性能瓶颈
    print('3. 性能瓶颈分析:')
    print('   可能的性能瓶颈:')
    print('   - 第一次请求: API调用和数据处理时间')
    print('   - 网络延迟: 与akshare API的通信时间')
    print('   - 数据处理: 计算收益率、波动率等指标的时间')
    print('   - 缓存写入: 将数据写入Redis的时间')
    
    # 测试API调用时间
    print('   ' + '-' * 70)
    print('   测试API调用时间:')
    start_time = time.time()
    # 只测试API调用，不处理数据
    response = requests.get(url, timeout=60)
    end_time = time.time()
    api_time = end_time - start_time
    print(f'   API调用总时间: {api_time:.4f} 秒')
    
    print('=' * 80)
    
    # 提供优化建议
    print('4. 优化建议:')
    print('   1. 优化API调用: 减少不必要的API调用，合并请求')
    print('   2. 优化数据处理: 减少计算复杂度，缓存中间结果')
    print('   3. 优化缓存策略: 调整缓存过期时间，使用更高效的缓存键')
    print('   4. 异步处理: 使用异步方式处理耗时操作')
    print('   5. 数据库优化: 如果使用数据库，优化查询语句')
    
    print('=' * 80)
    print('测试完成!')

if __name__ == '__main__':
    test_performance_analysis()
