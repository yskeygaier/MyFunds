import mysql.connector
import akshare as ak
import json
import os
from datetime import datetime, timedelta

# MySQL数据库配置
MYSQL_CONFIG = {
    'user': 'yskey',
    'password': os.environ.get('DB_PASSWORD', 'yskey'),
    'host': '127.0.0.1',
    'port': '3306',
    'database': 'fund_data'
}

# 基金代码列表
FUND_CODES = [
    # 常用基金
    '161039', '519674', '110011', '000001', '510300',
    '159919', '510500', '159915', '001052', '000300',
    '159920', '530020', '000751', '110022', '481012',
    '163406', '161725', '005918', '006113', '001878',
    # 指数基金
    '510300', '510500', '159919', '159915', '159920',
    '510880', '510900', '159901', '159902', '510180',
    # 主动型基金
    '000001', '000002', '000003', '000004', '000005',
    '000006', '000007', '000008', '000009', '000010',
    '000011', '000012', '000013', '000014', '000015',
    '000016', '000017', '000018', '000019', '000020',
    '000021', '000022', '000023', '000024', '000025',
    '000026', '000027', '000028', '000029', '000030',
    # 行业基金
    '161725', '160222', '161024', '001475', '001593',
    '001618', '001714', '001740', '001896', '002079',
    # 混合基金
    '163406', '163402', '163412', '163417', '163419',
    '166002', '166005', '166006', '166009', '166011'
]

def analyze_fund_style(industry_df):
    """根据行业配置分析基金风格"""
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
        for kw in tech_keywords:
            if kw in ind:
                style_scores['科技'] += weight
                break
        for kw in finance_keywords:
            if kw in ind:
                style_scores['金融'] += weight
                break
        for kw in consumer_keywords:
            if kw in ind:
                style_scores['消费'] += weight
                break
        for kw in medical_keywords:
            if kw in ind:
                style_scores['医药'] += weight
                break
        for kw in energy_keywords:
            if kw in ind:
                style_scores['新能源'] += weight
                break
        for kw in industrial_keywords:
            if kw in ind:
                style_scores['制造'] += weight
                break
    
    main_style = max(style_scores, key=style_scores.get)
    score = style_scores[main_style]
    
    if score < 20:
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
    elif main_style == '制造':
        return {'style': '先进制造', 'description': '投资于制造业升级转型，关注中国制造2025'}
    else:
        return {'style': '均衡配置', 'description': '行业配置相对均衡'}

def update_fund_data(fund_code):
    """更新单只基金数据"""
    print(f"开始更新基金 {fund_code} 的数据...")
    
    # 连接MySQL数据库
    conn = mysql.connector.connect(**MYSQL_CONFIG)
    cursor = conn.cursor()
    
    try:
        # 获取基金基本信息
        df = ak.fund_open_fund_info_em(symbol=fund_code, indicator='单位净值走势', period='近3年')
        
        if df.empty:
            print(f"基金 {fund_code} 无数据")
            return False
        
        latest_nav = df.iloc[-1]['单位净值']
        latest_date = df.iloc[-1]['净值日期']
        change_pct = df.iloc[-1]['日增长率'] if '日增长率' in df.columns else 0
        
        # 计算收益率、波动率、夏普比率
        returns = df['日增长率'].dropna() / 100
        if len(returns) > 0:
            annual_return = returns.mean() * 252 * 100
            annual_volatility = returns.std() * (252 ** 0.5) * 100
            sharpe_ratio = annual_return / annual_volatility if annual_volatility > 0 else 0
            
            # 计算卡玛比率 (年化收益 / 最大回撤)
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
        
        # 获取基金经理信息
        fund_manager = ''
        try:
            manager_df = ak.fund_manager_em()
            manager_info = manager_df[manager_df['现任基金代码'] == fund_code]
            if not manager_info.empty:
                managers = []
                for _, m in manager_info.iterrows():
                    managers.append(f"{m['姓名']} ({m['累计从业时间']}天)")
                fund_manager = ', '.join(managers[:2])
        except Exception as e:
            print(f"获取基金经理信息失败: {e}")
        
        # 获取行业配置信息
        first_industry = ''
        industry_ratio = ''
        fund_style = '未知'
        style_description = '无法分析'
        try:
            industry_df = ak.fund_portfolio_industry_allocation_em(symbol=fund_code)
            if not industry_df.empty:
                latest = industry_df.iloc[0]
                first_industry = latest['行业类别']
                industry_ratio = f"{latest['占净值比例']:.2f}%"
                
                # 基金定性分析
                industry_analysis = analyze_fund_style(industry_df)
                fund_style = industry_analysis['style']
                style_description = industry_analysis['description']
        except Exception as e:
            print(f"获取行业配置信息失败: {e}")
        
        # 获取前十大持仓
        holdings = []
        holdings_concentration = '0.00%'
        try:
            hold_df = ak.fund_portfolio_hold_em(symbol=fund_code)
            if not hold_df.empty:
                top10 = hold_df.head(10)
                for _, row in top10.iterrows():
                    holdings.append({
                        'stock_code': row['股票代码'],
                        'stock_name': row['股票名称'],
                        'weight': f"{row['占净值比例']:.2f}%"
                    })
                holdings_concentration = f"{top10['占净值比例'].sum():.2f}%"
        except Exception as e:
            print(f"获取前十大持仓失败: {e}")
        
        # 获取基金名称
        fund_name = ''
        try:
            fund_info = ak.fund_open_fund_info_em(symbol=fund_code, indicator='基本信息')
            if not fund_info.empty:
                fund_name = fund_info.iloc[0].get('基金名称', '')
        except Exception as e:
            print(f"获取基金名称失败: {e}")
        
        # 构建基本信息
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # 插入或更新基金基本信息
        update_sql = """
        INSERT INTO fund_basic 
        (fund_code, fund_name, net_value, nav_date, day_growth, annual_return, 
         annual_volatility, sharpe_ratio, calmar_ratio, max_drawdown, fund_manager, 
         first_industry, industry_ratio, fund_style, style_description, 
         holdings_concentration, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
        fund_name = VALUES(fund_name),
        net_value = VALUES(net_value),
        nav_date = VALUES(nav_date),
        day_growth = VALUES(day_growth),
        annual_return = VALUES(annual_return),
        annual_volatility = VALUES(annual_volatility),
        sharpe_ratio = VALUES(sharpe_ratio),
        calmar_ratio = VALUES(calmar_ratio),
        max_drawdown = VALUES(max_drawdown),
        fund_manager = VALUES(fund_manager),
        first_industry = VALUES(first_industry),
        industry_ratio = VALUES(industry_ratio),
        fund_style = VALUES(fund_style),
        style_description = VALUES(style_description),
        holdings_concentration = VALUES(holdings_concentration),
        updated_at = VALUES(updated_at)
        """
        
        # 准备数据
        data = (
            fund_code,
            fund_name,
            f"{latest_nav:.4f}",
            str(latest_date),
            f"{change_pct:.2f}%",
            f"{annual_return:.2f}%",
            f"{annual_volatility:.2f}%",
            f"{sharpe_ratio:.2f}",
            f"{calmar_ratio:.2f}",
            f"{max_drawdown:.2f}%",
            fund_manager,
            first_industry,
            industry_ratio,
            fund_style,
            style_description,
            holdings_concentration,
            current_time,
            current_time
        )
        
        # 执行更新
        cursor.execute(update_sql, data)
        
        # 删除旧的持仓信息
        cursor.execute("DELETE FROM fund_holdings WHERE fund_code = %s", (fund_code,))
        
        # 插入新的持仓信息
        if holdings:
            insert_holdings_sql = """
            INSERT INTO fund_holdings (fund_code, stock_code, stock_name, weight)
            VALUES (%s, %s, %s, %s)
            """
            for holding in holdings:
                holdings_data = (
                    fund_code,
                    holding['stock_code'],
                    holding['stock_name'],
                    holding['weight']
                )
                cursor.execute(insert_holdings_sql, holdings_data)
        
        # 提交事务
        conn.commit()
        print(f"基金 {fund_code} 数据更新成功")
        return True
        
    except Exception as e:
        print(f"更新基金 {fund_code} 数据失败: {e}")
        conn.rollback()
        return False
    finally:
        # 关闭连接
        cursor.close()
        conn.close()

def update_all_funds():
    """更新所有基金数据"""
    print("开始更新所有基金数据...")
    print(f"更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    success_count = 0
    failure_count = 0
    
    for fund_code in FUND_CODES:
        if update_fund_data(fund_code):
            success_count += 1
        else:
            failure_count += 1
        
        # 避免请求过于频繁
        import time
        time.sleep(1)
    
    print(f"\n更新完成！")
    print(f"成功: {success_count} 只基金")
    print(f"失败: {failure_count} 只基金")
    
    # 更新加载时间
    conn = mysql.connector.connect(**MYSQL_CONFIG)
    cursor = conn.cursor()
    try:
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        # 检查是否已有记录
        cursor.execute("SELECT COUNT(*) FROM load_time")
        count = cursor.fetchone()[0]
        
        if count > 0:
            # 更新现有记录
            cursor.execute("UPDATE load_time SET last_load_time = %s", (current_time,))
        else:
            # 插入新记录
            cursor.execute("INSERT INTO load_time (last_load_time) VALUES (%s)", (current_time,))
        
        conn.commit()
    except Exception as e:
        print(f"更新加载时间失败: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

def optimize_database():
    """优化数据库性能"""
    print("开始优化数据库性能...")
    
    conn = mysql.connector.connect(**MYSQL_CONFIG)
    cursor = conn.cursor()
    
    try:
        # 为fund_basic表添加索引
        cursor.execute("CREATE INDEX idx_fund_basic_code ON fund_basic (fund_code)")
        cursor.execute("CREATE INDEX idx_fund_basic_name ON fund_basic (fund_name)")
        
        # 为fund_holdings表添加索引
        cursor.execute("CREATE INDEX idx_fund_holdings_fund_code ON fund_holdings (fund_code)")
        
        # 优化表
        cursor.execute("OPTIMIZE TABLE fund_basic")
        cursor.execute("OPTIMIZE TABLE fund_holdings")
        cursor.execute("OPTIMIZE TABLE load_time")
        
        conn.commit()
        print("数据库优化完成！")
        
    except Exception as e:
        print(f"数据库优化失败: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

if __name__ == '__main__':
    # 优化数据库
    optimize_database()
    
    # 更新所有基金数据
    update_all_funds()
