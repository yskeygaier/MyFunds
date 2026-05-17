#!/usr/bin/env python3
"""QA 全链路功能测试 — 独立组合诊所"""
import os, sys, json

os.environ['DOUBAO_API_KEY'] = 'ark-187eaceb-af7b-4d15-b405-eabb6d58e041-602bd'
os.environ['DOUBAO_MODEL'] = 'doubao-seed-1-6-vision-250815'
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

from app import app
from db import init as _db_init
from portfolio_manager import init_portfolio_tables
from routes_portfolio_eval import portfolio_eval_bp

SQLITE_DB_PATH = os.path.join(BASE_DIR, 'fund_data.db')
_db_init(
    mysql_config={'user':'yskey','password':'yskey','host':'127.0.0.1','port':3306,
                  'database':'fund_data','charset':'utf8mb4','ssl_disabled':True},
    sqlite_db_path=SQLITE_DB_PATH, pool_size=5)
init_portfolio_tables()
app.register_blueprint(portfolio_eval_bp)

tests = []

with app.test_client() as client:
    # 1. 页面加载
    r = client.get('/portfolio-eval')
    ok = r.status_code == 200
    tests.append(('页面加载 200', ok, f'HTTP {r.status_code}', f'HTTP {r.status_code}'))
    html = r.data.decode()
    tests.append(('含健康分组件', '组合健康分' in html, '存在', '不存在'))
    tests.append(('含调仓模拟器', '调仓模拟' in html, '存在', '不存在'))
    tests.append(('含方法卡', '方法卡' in html or '这些分数怎么算' in html, '存在', '不存在'))
    tests.append(('含分享按钮', '分享' in html or 'share' in html, '存在', '不存在'))

    # 2. 组合分析（Mock模式）
    r = client.post('/api/portfolio-eval/analyze', json={'holdings':[
        {'fund_code':'161725','weight':30},{'fund_code':'005918','weight':25},
        {'fund_code':'163406','weight':25},{'fund_code':'519674','weight':20}
    ]})
    d = r.get_json()
    tests.append(('组合分析成功', d.get('success'), 'true', 'false'))
    hs = d.get('health_score', 0)
    tests.append(('健康分在0-100', 0 <= hs <= 100, str(hs), f'超出范围 {hs}'))
    nf = len(d.get('holdings', []))
    tests.append(('基金数据获取', nf > 0, f'{nf}只', '0只'))
    nr = len(d.get('recommendations', {}).get('list', []))
    tests.append(('有调仓建议', nr > 0, f'{nr}条', '0条'))
    ar = d.get('metrics', {}).get('annual_return')
    tests.append(('有年化收益', ar is not None, f'{ar}%', 'None'))

    # 3. 空持仓
    r = client.post('/api/portfolio-eval/analyze', json={'holdings':[]})
    tests.append(('空持仓拒绝', not r.get_json().get('success'), '拒绝', '未拒绝'))

    # 4. 无效代码
    r = client.post('/api/portfolio-eval/analyze', json={'holdings':[{'fund_code':'abc12','weight':100}]})
    tests.append(('无效代码拒绝', not r.get_json().get('success'), '拒绝', '未拒绝'))

    # 5. 回测
    r = client.post('/api/portfolio-eval/backtest', json={'holdings':[
        {'fund_code':'161725','weight':50},{'fund_code':'005918','weight':50}
    ],'years':1})
    d = r.get_json()
    tests.append(('回测成功', d.get('success'), 'true', 'false'))
    dp = d.get('data_points', 0)
    tests.append(('回测数据量', dp >= 50, f'{dp}点', f'{dp}点'))
    tr = d.get('total_return')
    tests.append(('有累计收益', tr is not None, f'{tr}%', 'None'))

    # 6. 单只回测拒绝
    r = client.post('/api/portfolio-eval/backtest', json={'holdings':[{'fund_code':'161725','weight':100}]})
    tests.append(('单只回测拒绝', not r.get_json().get('success'), '拒绝', '未拒绝'))

    # 7. LLM摘要
    r = client.post('/api/portfolio-eval/llm-summary', json={'holdings':[
        {'fund_code':'161725','weight':50},{'fund_code':'005918','weight':50}
    ]})
    d = r.get_json()
    tests.append(('LLM摘要端点', d.get('success'), '可用', '不可用'))
    summary = d.get('summary', '')
    if summary:
        tests.append(('LLM有内容', len(summary) > 20, f'{len(summary)}字符', f'{len(summary)}字符'))
    else:
        tests.append(('LLM有内容', False, '', '空'))

    # 8. 公式验证
    r = client.post('/api/portfolio-eval/verify-formulas', json={
        'holdings':[{'fund_code':'161725','weight':30},{'fund_code':'005918','weight':25}],
        'adjusted_weights':[{'fund_code':'161725','weight':20},{'fund_code':'005918','weight':35}]
    })
    d = r.get_json()
    tests.append(('公式验证成功', d.get('success'), 'true', 'false'))

    # 9. 批量回测（5只基金）
    r = client.post('/api/portfolio-eval/backtest', json={'holdings':[
        {'fund_code':'000001','weight':20},{'fund_code':'161725','weight':20},
        {'fund_code':'110011','weight':20},{'fund_code':'163406','weight':20},
        {'fund_code':'005918','weight':20}
    ],'years':1})
    d = r.get_json()
    tests.append(('5只基金回测', d.get('success'), 'true', 'false'))

# Report
print('=' * 55)
print('  QA 测试报告 — 独立组合诊所')
print('=' * 55)
print()
passed = sum(1 for _, ok, _, _ in tests if ok)
total = len(tests)

for name, ok, actual, expected in tests:
    icon = '✅' if ok else '❌'
    status = actual if ok else f'期望={expected}, 实际={actual}'
    print(f'  {icon} {name:20s} → {status}')

print()
print(f'  {'='*50}')
print(f'  通过率: {passed}/{total} ({passed*100//total}%)')
print(f'  健康评分: {passed*100//total}/100')
print(f'  {'='*50}')

if passed == total:
    print('\n  判定: ✅ 全部通过')
else:
    print(f'\n  判定: ⚠️ {total-passed}项失败')
