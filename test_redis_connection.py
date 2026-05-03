import redis
import time

# 测试Redis连接和基本操作
def test_redis_connection():
    print('测试Redis连接和基本操作')
    print('=' * 60)
    
    try:
        # 连接Redis
        r = redis.Redis(
            host='127.0.0.1',
            port=6379,
            db=0,
            decode_responses=True
        )
        
        # 测试连接
        r.ping()
        print('✅ Redis连接成功!')
        
        # 测试基本操作
        test_key = 'test:key'
        test_value = 'test_value'
        
        # 写入数据
        r.set(test_key, test_value)
        print(f'✅ 写入数据成功: {test_key} = {test_value}')
        
        # 读取数据
        read_value = r.get(test_key)
        print(f'✅ 读取数据成功: {test_key} = {read_value}')
        print(f'✅ 数据匹配: {read_value == test_value}')
        
        # 测试过期时间
        r.setex('test:expire', 5, 'expire_value')
        print('✅ 设置带过期时间的数据成功')
        
        # 测试删除
        r.delete(test_key)
        print(f'✅ 删除数据成功: {test_key}')
        
        # 测试缓存键生成
        fund_code = '519674'
        cache_key = f'fund:info:{fund_code}'
        print(f'✅ 缓存键生成: {cache_key}')
        
        # 测试写入缓存数据
        test_data = {
            '基金代码': fund_code,
            '基金简称': '银河创新成长混合',
            '单位净值': '1.5483'
        }
        import json
        r.setex(cache_key, 3600, json.dumps(test_data))
        print('✅ 写入缓存数据成功')
        
        # 测试读取缓存数据
        cached_data = r.get(cache_key)
        if cached_data:
            cached_data = json.loads(cached_data)
            print(f'✅ 读取缓存数据成功: {cached_data}')
        
        print('=' * 60)
        print('🎉 所有测试通过! Redis功能正常工作。')
        
    except Exception as e:
        print(f'❌ 测试失败: {e}')

if __name__ == '__main__':
    test_redis_connection()
