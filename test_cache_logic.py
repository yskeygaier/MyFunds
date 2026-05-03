# 测试内存缓存逻辑
memory_cache = {}

# 模拟缓存键生成
def get_cache_key(prefix, fund_code):
    return f'{prefix}:{fund_code}'

# 模拟缓存读取
def get_from_cache(cache_key):
    if cache_key in memory_cache:
        print(f'缓存命中: {cache_key}')
        return memory_cache[cache_key]
    else:
        print(f'缓存未命中: {cache_key}')
        return None

# 模拟缓存写入
def set_to_cache(cache_key, data, expiry=3600):
    memory_cache[cache_key] = data
    print(f'缓存写入: {cache_key}')

# 测试缓存逻辑
def test_cache_logic():
    fund_code = '519674'
    
    # 测试基金信息缓存
    print('测试基金信息缓存:')
    cache_key = get_cache_key('fund_info', fund_code)
    
    # 第一次查询（缓存未命中）
    data = get_from_cache(cache_key)
    if not data:
        # 模拟从API获取数据
        print('从API获取数据...')
        data = {
            '基金代码': fund_code,
            '基金简称': '银河创新成长混合',
            '单位净值': '1.5483',
            '净值日期': '2026-03-07',
            '日增长率': '1.25%'
        }
        # 写入缓存
        set_to_cache(cache_key, data)
    
    print(f'基金信息: {data}')
    print('-' * 50)
    
    # 第二次查询（缓存命中）
    data = get_from_cache(cache_key)
    print(f'基金信息: {data}')
    print('-' * 50)
    
    # 测试实时估值缓存
    print('测试实时估值缓存:')
    cache_key = get_cache_key('fund_valuation', fund_code)
    
    # 第一次查询（缓存未命中）
    data = get_from_cache(cache_key)
    if not data:
        # 模拟从API获取数据
        print('从API获取数据...')
        data = {
            '基金代码': fund_code,
            '基金名称': '银河创新成长混合',
            '实时估值': 1.5678,
            '估算涨跌幅': 1.25,
            '估值时间': '2026-03-08 10:00:00'
        }
        # 写入缓存
        set_to_cache(cache_key, data, expiry=300)
    
    print(f'实时估值: {data}')
    print('-' * 50)
    
    # 第二次查询（缓存命中）
    data = get_from_cache(cache_key)
    print(f'实时估值: {data}')
    print('-' * 50)
    
    # 查看缓存内容
    print('缓存内容:')
    for key, value in memory_cache.items():
        print(f'{key}: {value}')

if __name__ == '__main__':
    test_cache_logic()
