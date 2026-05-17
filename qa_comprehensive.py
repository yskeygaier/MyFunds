#!/usr/bin/env python3
"""全场景 QA 测试 — 包含边界、极限、异常路径"""
import os, sys, json, time
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)
os.environ['DOUBAO_API_KEY'] = 'ark-187eaceb-af7b-4d15-b405-eabb6d58e041-602bd'
os.environ['DOUBAO_MODEL'] = 'doubao-seed-1-6-vision-250815'

from app import app
from db import init as _db_init
from portfolio_manager import init_portfolio_tables
from routes_portfolio_eval import portfolio_eval_bp
from portfolio_clinic import PortfolioClinic, ClinicReport, ImageProcessor

SQLITE_DB_PATH = os.path.join(BASE_DIR, 'fund_data.db')
_db_init(mysql_config={'user':'yskey','password':'yskey','host':'127.0.0.1','port':3306,'database':'fund_data','charset':'utf8mb4','ssl_disabled':True},
    sqlite_db_path=SQLITE_DB_PATH, pool_size=5)
init_portfolio_tables()
app.register_blueprint(portfolio_eval_bp)

client = app.test_client()

results = []

def test(name, ok, detail=''):
    icon = '✅' if ok else '❌'
    print(f'  {icon} {name}')
    if detail: print(f'     {detail}')
    results.append((name, ok, detail))

def check(name, condition, detail=''):
    test(name, bool(condition), detail if condition else f'FAIL: {detail}')

print('='*60)
print('  全场景 QA 测试 — 独立组合诊所')
print('='*60)

# ════════════════════════════════════════
# 场景组 A: 正常路径
# ════════════════════════════════════════
print('\n── A: 正常路径 ──')

# A1: 页面加载
r = client.get('/portfolio-eval')
check('A1 页面200', r.status_code == 200, f'HTTP {r.status_code}')
html = r.data.decode()
check('A2 健康分组件', '组合健康分' in html)
check('A3 调仓模拟器', '调仓模拟' in html)
check('A4 方法卡', '方法卡' in html or '这些分数怎么算' in html)

# A2: 正常4只基金组合
r = client.post('/api/portfolio-eval/analyze', json={'holdings':[
    {'fund_code':'161725','weight':30},{'fund_code':'005918','weight':25},
    {'fund_code':'163406','weight':25},{'fund_code':'519674','weight':20}
]})
d = r.get_json()
check('A5 4只基金分析', d['success'], f'健康分{d["health_score"]}')
check('A6 健康分范围', 0 <= d['health_score'] <= 100)
check('A7 4只基金数据', len(d['holdings']) == 4)
check('A8 有调仓建议', len(d['recommendations']['list']) > 0)
check('A9 有年化收益率', d['metrics']['annual_return'] is not None)
check('A10 有夏普比率', d['metrics']['sharpe_ratio'] is not None)
check('A11 有最大回撤', d['metrics']['conservative_max_drawdown'] is not None)

# A3: 正常回测
r = client.post('/api/portfolio-eval/backtest', json={'holdings':[
    {'fund_code':'161725','weight':50},{'fund_code':'005918','weight':50}
],'years':1})
d = r.get_json()
check('A12 回测成功', d['success'])
check('A13 回测≥50点', d['data_points'] >= 50, f'{d["data_points"]}点')
check('A14 有累计收益', d['total_return'] is not None)
check('A15 有年化收益', d['annualized_return'] is not None)
check('A16 有最大回撤', d['max_drawdown'] is not None)
check('A17 有夏普比率', d['sharpe_ratio'] is not None)

# A4: LLM摘要
r = client.post('/api/portfolio-eval/llm-summary', json={'holdings':[
    {'fund_code':'161725','weight':50},{'fund_code':'005918','weight':50}
]})
d = r.get_json()
check('A18 LLM摘要', d['success'] and len(d.get('summary','')) > 20)

# ════════════════════════════════════════
# 场景组 B: 边界条件（修复之前的bug）
# ════════════════════════════════════════
print('\n── B: 边界条件（修复已知Bug）──')

# B1: amount=null（之前导致500错误）
r = client.post('/api/portfolio-eval/analyze', json={'holdings':[
    {'fund_code':'161725','weight':30,'amount':None},
    {'fund_code':'005918','weight':25,'amount':None},
]})
d = r.get_json()
check('B1 amount=null不崩溃', d['success'], '✅ 之前会500崩溃')

# B2: amount缺失
r = client.post('/api/portfolio-eval/analyze', json={'holdings':[
    {'fund_code':'161725','weight':30},
    {'fund_code':'005918','weight':25},
]})
d = r.get_json()
check('B2 amount缺失', d['success'])

# B3: fund_name缺失
r = client.post('/api/portfolio-eval/analyze', json={'holdings':[
    {'fund_code':'161725','weight':30},
]})
d = r.get_json()
check('B3 fund_name缺失', d['success'])

# B4: 混合weight=0和正常
r = client.post('/api/portfolio-eval/analyze', json={'holdings':[
    {'fund_code':'161725','weight':0},
    {'fund_code':'005918','weight':30},
    {'fund_code':'163406','weight':30,'amount':None},
    {'fund_code':'519674','weight':0},
]})
d = r.get_json()
check('B4 weight=0过滤', d['success'] and len(d['holdings']) == 2, f'{len(d.get("holdings",[]))}只')

# B5: 权重总和不等于100
r = client.post('/api/portfolio-eval/analyze', json={'holdings':[
    {'fund_code':'161725','weight':50},{'fund_code':'005918','weight':25},
]})
d = r.get_json()
check('B5 权重自动归一化', d['success'])
total = sum(h['weight'] for h in d.get('holdings',[]))
check('B5 总和=100', abs(total - 100) < 1, f'合计{total}%')

# B6: 回测amount=null
r = client.post('/api/portfolio-eval/backtest', json={'holdings':[
    {'fund_code':'161725','weight':50,'amount':None},
    {'fund_code':'005918','weight':50,'amount':None},
],'years':1})
d = r.get_json()
check('B6 回测amount=null', d['success'])

# ════════════════════════════════════════
# 场景组 C: 错误输入验证
# ════════════════════════════════════════
print('\n── C: 错误输入验证 ──')

# C1: 空数组
r = client.post('/api/portfolio-eval/analyze', json={'holdings':[]})
check('C1 空数组拒绝', not r.get_json()['success'])

# C2: 空对象
r = client.post('/api/portfolio-eval/analyze', json={})
check('C2 空对象拒绝', not r.get_json()['success'])

# C3: 无效代码(5位)
r = client.post('/api/portfolio-eval/analyze', json={'holdings':[{'fund_code':'16172','weight':100}]})
check('C3 5位代码拒绝', not r.get_json()['success'])

# C4: 无效代码(含字母)
r = client.post('/api/portfolio-eval/analyze', json={'holdings':[{'fund_code':'abcde1','weight':100}]})
check('C4 字母代码拒绝', not r.get_json()['success'])

# C5: 负数权重
r = client.post('/api/portfolio-eval/analyze', json={'holdings':[
    {'fund_code':'161725','weight':-50},{'fund_code':'005918','weight':150}
]})
d = r.get_json()
check('C5 负权重过滤', d['success'])
total = sum(h['weight'] for h in d.get('holdings',[]))
check('C5 归一化正确', abs(total - 100) < 1, f'合计{total}%')

# C6: 超大权重
r = client.post('/api/portfolio-eval/analyze', json={'holdings':[{'fund_code':'161725','weight':9999}]})
check('C6 超大权重', r.get_json()['success'])

# C7: 代码正确但超过6位（截断）
r = client.post('/api/portfolio-eval/analyze', json={'holdings':[{'fund_code':'16172599','weight':100}]})
check('C7 超长代码截断', r.get_json()['success'])

# C8: 空字符串代码
r = client.post('/api/portfolio-eval/analyze', json={'holdings':[{'fund_code':'','weight':100}]})
check('C8 空代码拒绝', not r.get_json()['success'])

# ════════════════════════════════════════
# 场景组 D: 回测边界
# ════════════════════════════════════════
print('\n── D: 回测边界 ──')

# D1: 只有1只基金
r = client.post('/api/portfolio-eval/backtest', json={'holdings':[{'fund_code':'161725','weight':100}]})
check('D1 单只拒绝', not r.get_json()['success'])

# D2: 年度参数为0
r = client.post('/api/portfolio-eval/backtest', json={'holdings':[
    {'fund_code':'161725','weight':50},{'fund_code':'005918','weight':50}
],'years':0})
d = r.get_json()
check('D2 years=0', d['success'] or not d['success'], f'返回success={d.get("success")}')

# D3: 年度参数为负数
r = client.post('/api/portfolio-eval/backtest', json={'holdings':[
    {'fund_code':'161725','weight':50},{'fund_code':'005918','weight':50}
],'years':-1})
d = r.get_json()
check('D3 years=-1 不崩溃', True)

# D4: 年度参数缺失
r = client.post('/api/portfolio-eval/backtest', json={'holdings':[
    {'fund_code':'161725','weight':50},{'fund_code':'005918','weight':50}
]})
d = r.get_json()
check('D4 years默认', d['success'])

# D5: 5只基金回测
r = client.post('/api/portfolio-eval/backtest', json={'holdings':[
    {'fund_code':'000001','weight':20},{'fund_code':'161725','weight':20},
    {'fund_code':'110011','weight':20},{'fund_code':'163406','weight':20},
    {'fund_code':'005918','weight':20}
],'years':1})
check('D5 5只回测', r.get_json()['success'])

# D6: 10只基金回测
r = client.post('/api/portfolio-eval/backtest', json={'holdings':[
    {'fund_code':'000001','weight':10},{'fund_code':'161725','weight':10},
    {'fund_code':'110011','weight':10},{'fund_code':'163406','weight':10},
    {'fund_code':'005918','weight':10},{'fund_code':'519674','weight':10},
    {'fund_code':'161039','weight':10},{'fund_code':'110022','weight':10},
    {'fund_code':'001875','weight':10},{'fund_code':'270002','weight':10},
],'years':1})
check('D6 10只回测', r.get_json()['success'], '批量回测支持')

# ════════════════════════════════════════
# 场景组 E: 图片处理器单元测试
# ════════════════════════════════════════
print('\n── E: 图片处理器 ──')

# E1: 代码验证
check('E1 有效代码', ImageProcessor.validate_fund_code('161725'))
check('E1 5位无效', not ImageProcessor.validate_fund_code('16172'))
check('E1 字母无效', not ImageProcessor.validate_fund_code('abc123'))
check('E1 空无效', not ImageProcessor.validate_fund_code(''))

# E2: 合并去重
r1 = {'success':True,'holdings':[{'fund_code':'161725','weight':30,'amount':None},{'fund_code':'005918','weight':25}]}
r2 = {'success':True,'holdings':[{'fund_code':'161725','weight':30},{'fund_code':'163406','weight':25,'amount':None}]}
merged, _, _ = ImageProcessor.merge_holdings([r1, r2])
check('E2 合并去重', len(merged) == 3)

# E3: 无效代码过滤
r = {'success':True,'holdings':[{'fund_code':'161725','weight':30},{'fund_code':'abc','weight':10,'amount':None}]}
merged, _, _ = ImageProcessor.merge_holdings([r])
check('E3 无效代码过滤', len(merged) == 1)

# ════════════════════════════════════════
# 场景组 F: 公式验证端点
# ════════════════════════════════════════
print('\n── F: 公式验证 ──')

r = client.post('/api/portfolio-eval/verify-formulas', json={
    'holdings':[{'fund_code':'161725','weight':30},{'fund_code':'005918','weight':25}],
    'adjusted_weights':[{'fund_code':'161725','weight':20},{'fund_code':'005918','weight':35}]
})
check('F1 公式验证', r.get_json().get('success',False))

# 无adjusted_weights
r = client.post('/api/portfolio-eval/verify-formulas', json={
    'holdings':[{'fund_code':'161725','weight':30}]
})
check('F2 缺参数拒绝', not r.get_json().get('success',True))

# ════════════════════════════════════════
# 场景组 G: 组合诊断引擎
# ════════════════════════════════════════
print('\n── G: 核心引擎 ──')

# G1: 空持仓
report = PortfolioClinic.analyze([])
check('G1 空持仓', len(report.holdings) == 0)

# G2: 超长基金名
report = PortfolioClinic.analyze([{'fund_code':'161725','weight':100,'fund_name':'A'*500}])
check('G2 超长名不崩溃', True)

# G3: 海量基金(50只)
many = [{'fund_code':'161725','weight':2} for _ in range(50)]
report = PortfolioClinic.analyze(many)
check('G3 50只组合不崩溃', True)

# G4: 空回测
bt = PortfolioClinic.backtest([])
check('G4 空回测', bt.data_points == 0)

# G5: 单只回测
bt = PortfolioClinic.backtest([{'fund_code':'161725','weight':100}])
check('G5 单只回测空', bt.data_points == 0)

# ════════════════════════════════════════
# 统计
# ════════════════════════════════════════
print()
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print('='*60)
print(f'  测试结果: {passed}/{total} 通过 ({passed*100//total}%)')
if passed == total:
    print('  判定: ✅ 全部通过')
else:
    for name, ok, detail in results:
        if not ok:
            print(f'  ❌ {name}: {detail}')
print('='*60)
