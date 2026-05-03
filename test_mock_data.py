from datetime import datetime, timedelta

# 模拟生成实时估值数据
def generate_mock_data(fund_code):
    today = datetime.now()
    yesterday = today - timedelta(days=1)
    return {
        '基金代码': fund_code,
        '基金名称': '银河创新成长混合',
        '实时估值': 1.5678,
        '估算涨跌幅': 1.25,
        '估算涨跌额': 0.0195,
        '单位净值': 1.5483,
        '净值日期': yesterday.strftime('%Y-%m-%d'),
        '估值时间': today.strftime('%Y-%m-%d %H:%M:%S')
    }

# 测试生成模拟数据
fund_code = '519674'
mock_data = generate_mock_data(fund_code)
print('模拟数据:')
print(f'基金代码: {mock_data["基金代码"]}')
print(f'基金名称: {mock_data["基金名称"]}')
print(f'实时估值: {mock_data["实时估值"]}')
print(f'估算涨跌幅: {mock_data["估算涨跌幅"]}%')
print(f'估算涨跌额: {mock_data["估算涨跌额"]}')
print(f'单位净值: {mock_data["单位净值"]}')
print(f'净值日期: {mock_data["净值日期"]}')
print(f'估值时间: {mock_data["估值时间"]}')
