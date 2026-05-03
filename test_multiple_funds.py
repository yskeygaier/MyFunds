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

# 测试多个基金代码的缓存
def test_multiple_funds():
    print("\n=== 测试多个基金代码的缓存 ===")
    
    # 测试的基金代码列表
    fund_codes = ['519674', '161039', '110011', '000001', '510300']
    
    for fund_code in fund_codes:
        print(f"\n测试基金代码: {fund_code}")
        
        # 生成缓存键
        cache_key = f'fund:info:{fund_code}'
        
        # 模拟基金信息数据
        test_data = {
            '基金代码': fund_code,
            '基金名称': f'测试基金_{fund_code}',
            '单位净值': f'{1.0 + int(fund_code[-3:])/1000:.4f}',
            '净值日期': datetime.now().strftime('%Y-%m-%d'),
            '日增长率': f'{(int(fund_code[-2:])/10):.2f}%',
            '缓存时间': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        # 设置缓存
        print(f"设置缓存: {cache_key}")
        try:
            serialized_data = json.dumps(test_data)
            result = r.setex(cache_key, 3600, serialized_data)
            print(f"存储结果: {result}")
            
            # 验证存储是否成功
            stored_data = r.get(cache_key)
            print(f"存储验证: {'成功' if stored_data else '失败'}")
        except Exception as e:
            print(f"设置缓存失败: {e}")
    
    # 查看所有键
    print("\n=== 查看所有键 ===")
    try:
        keys = r.keys('fund:info:*')
        print(f"基金信息缓存键: {keys}")
        print(f"基金信息缓存数量: {len(keys)}")
        
        # 查看所有键
        all_keys = r.keys('*')
        print(f"所有键: {all_keys}")
        print(f"总键数量: {len(all_keys)}")
    except Exception as e:
        print(f"获取键失败: {e}")

if __name__ == '__main__':
    test_multiple_funds()
