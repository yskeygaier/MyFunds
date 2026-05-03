import akshare as ak
import json
import os
from datetime import datetime, timedelta

# 基金代码列表
FUND_CODES = [
    '519674', '161039', '110011', '000001', '510300',
    '159919', '510500', '159915', '001052', '000300',
    '159920', '530020', '000751', '110022', '481012',
    '163406', '161725', '005918', '006113', '001878'
]

# 数据存储目录
DATA_DIR = 'fund_data'
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# 加载单个基金数据
def load_fund_data(fund_code):
    print(f"正在加载基金 {fund_code} 的数据...")
    
    try:
        # 获取基金基本信息
        fund_info = ak.fund_open_fund_info_em(symbol=fund_code, indicator='单位净值走势', period='近3年')
        
        if fund_info.empty:
            print(f"基金 {fund_code} 无数据")
            return False
        
        # 计算收益率、波动率、夏普比率等指标
        returns = fund_info['日增长率'].dropna() / 100
        if len(returns) > 0:
            annual_return = returns.mean() * 252 * 100
            annual_volatility = returns.std() * (252 ** 0.5) * 100
            sharpe_ratio = annual_return / annual_volatility if annual_volatility > 0 else 0
            
            # 计算卡玛比率
            cumulative = (1 + returns).cumprod()
            rolling_max = cumulative.expanding().max()
            drawdown = (cumulative - rolling_max) / rolling_max
            max_drawdown = drawdown.min() * 100
            calmar_ratio = annual_return / abs(max_drawdown) if max_drawdown != 0 else 0
        else:
            annual_return = 0
            annual_volatility = 0
            sharpe_ratio = 0
            calmar_ratio = 0
            max_drawdown = 0
        
        # 获取最新净值
        latest_nav = fund_info.iloc[-1]['单位净值']
        latest_date = fund_info.iloc[-1]['净值日期']
        change_pct = fund_info.iloc[-1]['日增长率'] if '日增长率' in fund_info.columns else 0
        
        # 构建基本信息
        info_dict = {
            '基金代码': fund_code,
            '单位净值': f"{latest_nav:.4f}",
            '净值日期': str(latest_date),
            '日增长率': f"{change_pct:.2f}%",
            '年化收益率': f"{annual_return:.2f}%",
            '年化波动率': f"{annual_volatility:.2f}%",
            '夏普比率': f"{sharpe_ratio:.2f}",
            '卡玛比率': f"{calmar_ratio:.2f}",
            '最大回撤': f"{max_drawdown:.2f}%"
        }
        
        # 获取基金经理信息
        try:
            manager_df = ak.fund_manager_em()
            manager_info = manager_df[manager_df['现任基金代码'] == fund_code]
            if not manager_info.empty:
                managers = []
                for _, m in manager_info.iterrows():
                    managers.append(f"{m['姓名']} ({m['累计从业时间']}天)")
                info_dict['基金经理'] = ', '.join(managers[:2])
        except Exception as e:
            print(f"获取基金经理信息失败: {e}")
        
        # 获取行业配置信息
        try:
            industry_df = ak.fund_portfolio_industry_allocation_em(symbol=fund_code)
            if not industry_df.empty:
                latest = industry_df.iloc[0]
                info_dict['第一大行业'] = latest['行业类别']
                info_dict['行业占比'] = f"{latest['占净值比例']:.2f}%"
                
                # 基金定性分析
                def analyze_fund_style(industry_df):
                    if industry_df.empty:
                        return {'style': '未知', 'description': '无法分析'}
                    
                    latest = industry_df.head(5)
                    industries = latest['行业类别'].tolist()
                    weights = latest['占净值比例'].tolist()
                    
                    tech_keywords = ['软件', '信息技术', '计算机', '电子', '通信', '互联网', '芯片', '半导体']
                    finance_keywords = ['金融', '银行', '证券', '保险', '信托']
                    consumer_keywords = ['消费', '食品', '饮料', '家电', '纺织', '服装', '商贸', '零售']
                    medical_keywords = ['医药', '医疗', '生物', '保健', '疫苗']
                    energy_keywords = ['能源', '电力', '石油', '煤炭', '新能源', '光伏', '锂电', '电池']
                    industrial_keywords = ['制造', '工业', '设备', '机械', '汽车', '化工', '材料']
                    
                    style_scores = {'科技': 0, '金融': 0, '消费': 0, '医药': 0, '新能源': 0, '制造': 0, '其他': 0}
                    
                    for ind, weight in zip(industries, weights):
                        tech_match = any(kw in ind for kw in tech_keywords)
                        finance_match = any(kw in ind for kw in finance_keywords)
                        consumer_match = any(kw in ind for kw in consumer_keywords)
                        medical_match = any(kw in ind for kw in medical_keywords)
                        energy_match = any(kw in ind for kw in energy_keywords)
                        industrial_match = any(kw in ind for kw in industrial_keywords)
                        
                        if tech_match:
                            style_scores['科技'] += weight
                        elif finance_match:
                            style_scores['金融'] += weight
                        elif consumer_match:
                            style_scores['消费'] += weight
                        elif medical_match:
                            style_scores['医药'] += weight
                        elif energy_match:
                            style_scores['新能源'] += weight
                        elif industrial_match:
                            style_scores['制造'] += weight
                        else:
                            style_scores['其他'] += weight
                    
                    main_style = max(style_scores, key=style_scores.get)
                    score = style_scores[main_style]
                    
                    if main_style == '制造' and score > 50 and all(style_scores[style] < score * 0.3 for style in style_scores if style != '制造'):
                        return {'style': '先进制造', 'description': '投资于制造业升级转型，关注中国制造2025'}
                    elif score < 30:
                        return {'style': '均衡配置', 'description': '行业配置相对均衡，分散投资风险'}
                    elif main_style == '科技':
                        return {'style': '科技成长', 'description': '主要投资于科技创新领域，把握科技发展红利'}
                    elif main_style == '金融':
                        return {'style': '金融权重', 'description': '重仓金融板块，受益于金融市场发展'}
                    elif main_style == '消费':
                        return {'style': '消费成长', 'description': '聚焦消费行业，分享消费升级机遇'}
                    elif main_style == '医药':
                        return {'style': '医药健康', 'description': '专注于医药健康领域，受益于人口老龄化和医疗需求'}
                    elif main_style == '新能源':
                        return {'style': '新能源主题', 'description': '聚焦新能源领域，把握碳中和背景下的产业机遇'}
                    else:
                        return {'style': '均衡配置', 'description': '行业配置相对均衡'}
                
                industry_analysis = analyze_fund_style(industry_df)
                info_dict['基金风格'] = industry_analysis['style']
                info_dict['风格描述'] = industry_analysis['description']
        except Exception as e:
            print(f"获取行业配置信息失败: {e}")
        
        # 获取前十大持仓
        try:
            hold_df = ak.fund_portfolio_hold_em(symbol=fund_code)
            if not hold_df.empty:
                top10 = hold_df.head(10)
                holdings = []
                for _, row in top10.iterrows():
                    holdings.append({
                        '股票代码': row['股票代码'],
                        '股票名称': row['股票名称'],
                        '占净值比例': f"{row['占净值比例']:.2f}%"
                    })
                info_dict['前十大持仓'] = holdings
                info_dict['持仓集中度'] = f"{top10['占净值比例'].sum():.2f}%"
        except Exception as e:
            print(f"获取前十大持仓失败: {e}")
        
        # 保存数据
        data_file = os.path.join(DATA_DIR, f"{fund_code}.json")
        with open(data_file, 'w', encoding='utf-8') as f:
            json.dump(info_dict, f, ensure_ascii=False, indent=2)
        
        print(f"基金 {fund_code} 数据加载成功")
        return True
    except Exception as e:
        print(f"加载基金 {fund_code} 数据失败: {e}")
        return False

# 批量加载所有基金数据
def load_all_fund_data():
    print("开始批量加载基金数据...")
    print(f"总共需要加载 {len(FUND_CODES)} 只基金")
    
    success_count = 0
    failure_count = 0
    
    for fund_code in FUND_CODES:
        if load_fund_data(fund_code):
            success_count += 1
        else:
            failure_count += 1
    
    print(f"\n加载完成！")
    print(f"成功: {success_count} 只基金")
    print(f"失败: {failure_count} 只基金")
    
    # 保存加载时间
    load_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(os.path.join(DATA_DIR, 'load_time.json'), 'w', encoding='utf-8') as f:
        json.dump({'last_load_time': load_time}, f, ensure_ascii=False, indent=2)

if __name__ == '__main__':
    load_all_fund_data()
