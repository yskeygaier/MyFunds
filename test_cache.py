import redis
import json
from datetime import datetime

# 测试Redis连接
try:
    r = redis.Redis(
        host='127.0.0.1',
        port=6379,
        db=0,
        decode_responses=True
    )
    print("Redis连接成功:", r.ping())
except Exception as e:
    print(f"Redis连接失败: {e}")
    exit(1)

# 测试缓存设置和获取
def test_cache():
    print("\n=== 测试缓存功能 ===")
    
    # 测试数据
    fund_code = '519674'
    cache_key = f'fund:info:{fund_code}'
    test_data = {
        '基金代码': fund_code,
        '基金名称': '银河创新成长混合',
        '单位净值': '9.1287',
        '净值日期': '2026-03-06',
        '日增长率': '0.50%',
        '缓存时间': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    
    # 设置缓存
    print(f"设置缓存: {cache_key}")
    try:
        serialized_data = json.dumps(test_data)
        print(f"数据序列化成功，长度: {len(serialized_data)}")
        
        # 存储到Redis
        result = r.setex(cache_key, 3600, serialized_data)
        print(f"Redis存储结果: {result}")
        
        # 验证存储是否成功
        stored_data = r.get(cache_key)
        print(f"存储后验证: {'成功' if stored_data else '失败'}")
        if stored_data:
            print(f"存储的数据: {stored_data[:100]}...")
    except Exception as e:
        print(f"设置缓存失败: {e}")
    
    # 获取缓存
    print("\n获取缓存:")
    try:
        cached_data = r.get(cache_key)
        if cached_data:
            print(f"获取成功，数据长度: {len(cached_data)}")
            parsed_data = json.loads(cached_data)
            print(f"解析后的数据: {parsed_data}")
        else:
            print("获取失败，缓存不存在")
    except Exception as e:
        print(f"获取缓存失败: {e}")
    
    # 查看所有键
    print("\n所有键:")
    try:
        keys = r.keys('*')
        print(f"当前Redis中的键: {keys}")
        print(f"键数量: {len(keys)}")
    except Exception as e:
        print(f"获取键失败: {e}")

if __name__ == '__main__':
    test_cache()
