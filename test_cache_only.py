# 测试缓存逻辑（不调用API）
import sys
import os

# 添加当前目录到Python路径
sys.path.insert(0, os.path.abspath('.'))

from app import generate_cache_key, get_cache, set_cache, CACHE_CONFIG

# 测试缓存功能
def test_cache():
    fund_code = '519674'
    
    print('测试缓存功能')
    print('=' * 60)
    
    # 测试基金信息缓存
    print('1. 测试基金信息缓存:')
    cache_config = CACHE_CONFIG['fund_info']
    cache_key = generate_cache_key(cache_config['prefix'], fund_code)
    
    # 模拟数据
    test_data = {
        '基金代码': fund_code,
        '基金简称': '银河创新成长混合',
        '单位净值': '1.5483',
        '净值日期': '2026-03-07',
        '日增长率': '1.25%'
    }
    
    # 测试缓存写入
    print('   写入缓存:')
    set_cache(cache_key, test_data, cache_config['expiry'])
    print(f'   缓存键: {cache_key}')
    print(f'   缓存数据: {test_data}')
    
    print('   ' + '-' * 50)
    
    # 测试缓存读取
    print('   读取缓存:')
    cached_data = get_cache(cache_key)
    print(f'   缓存数据: {cached_data}')
    print(f'   数据匹配: {cached_data == test_data}')
    
    print('=' * 60)
    
    # 测试实时估值（不应缓存）
    print('2. 测试实时估值（不应缓存）:')
    # 注意：实时估值在实际API中不会缓存，这里只是测试缓存函数
    
    print('=' * 60)
    print('测试完成!')

if __name__ == '__main__':
    test_cache()
