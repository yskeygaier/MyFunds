import redis

try:
    r = redis.Redis(host='127.0.0.1', port=6379, decode_responses=True)
    r.ping()
    r.flushall()
    print('Redis数据已清除')
except Exception as e:
    print(f'清除失败: {e}')
