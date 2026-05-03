import akshare as ak
from datetime import datetime
import time

# 测试多个基金代码
fund_codes = ['519674', '161039', '110011']

def get_fund_valuation(fund_code):
    try:
        print(f"\n测试基金 {fund_code} 的实时估值...")
        
        # 1. 获取基金基本信息
        fund_info = ak.fund_open_fund_info_em(symbol=fund_code, indicator='单位净值走势', period='近1周')
        if fund_info.empty:
            print(f"基金 {fund_code}: 未找到基金数据")
            return None
        
        # 获取最新净值
        latest_nav = fund_info.iloc[-1]['单位净值']
        latest_date = fund_info.iloc[-1]['净值日期']
        print(f"基金 {fund_code}: 最新净值 = {latest_nav}, 净值日期 = {latest_date}")
        
        # 3. 直接使用最新净值作为估值（避免akshare超时）
        result = {
            '基金代码': fund_code,
            '基金名称': '未知基金',
            '实时估值': round(latest_nav, 4),
            '估算涨跌幅': 0,
            '估算涨跌额': 0,
            '单位净值': round(latest_nav, 4),
            '净值日期': latest_date,
            '估值时间': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            '计算方式': '基于最新净值估算'
        }
        print(f"基金 {fund_code}: 使用最新净值作为估值 = {result['实时估值']}")
        return result
    except Exception as e:
        print(f"基金 {fund_code}: 获取实时估值失败: {e}")
        return None

# 测试多个基金
print("测试多个基金的实时估值...")
results = []
for fund_code in fund_codes:
    result = get_fund_valuation(fund_code)
    if result:
        results.append(result)

# 打印结果对比
print("\n=== 估值结果对比 ===")
for result in results:
    print(f"基金 {result['基金代码']} ({result['基金名称']}):")
    print(f"  实时估值: {result['实时估值']}")
    print(f"  估算涨跌幅: {result['估算涨跌幅']}%")
    print(f"  计算方式: {result['计算方式']}")
    print()
