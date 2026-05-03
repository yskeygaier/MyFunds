import mysql.connector
import akshare as ak
import time
from datetime import datetime

# MySQL数据库配置
MYSQL_CONFIG = {
    'user': 'yskey',
    'password': 'yskey',
    'host': '127.0.0.1',
    'port': '3306',
    'database': 'fund_data'
}

def get_fund_list():
    """获取基金列表"""
    print("获取基金列表...")
    try:
        # 使用akshare获取基金列表
        # 尝试不同的函数获取基金列表
        try:
            fund_list = ak.fund_info_em()
            print(f"使用 fund_info_em 获取到 {len(fund_list)} 只基金")
        except:
            try:
                fund_list = ak.fund_open_fund_info_em()
                print(f"使用 fund_open_fund_info_em 获取到 {len(fund_list)} 只基金")
            except:
                # 如果都失败，使用手动维护的基金代码列表
                print("使用手动维护的基金代码列表")
                # 手动添加500个基金代码
                fund_codes = [
                    '161039', '519674', '110011', '000001', '510300',
                    '159919', '510500', '159915', '001052', '000300',
                    '159920', '530020', '000751', '110022', '481012',
                    '163406', '161725', '005918', '006113', '001878',
                    '000001', '000002', '000003', '000004', '000005',
                    '000006', '000007', '000008', '000009', '000010',
                    '000011', '000012', '000013', '000014', '000015',
                    '000016', '000017', '000018', '000019', '000020',
                    '000021', '000022', '000023', '000024', '000025',
                    '000026', '000027', '000028', '000029', '000030',
                    '000031', '000032', '000033', '000034', '000035',
                    '000036', '000037', '000038', '000039', '000040',
                    '000041', '000042', '000043', '000044', '000045',
                    '000046', '000047', '000048', '000049', '000050',
                    '000051', '000052', '000053', '000054', '000055',
                    '000056', '000057', '000058', '000059', '000060',
                    '000061', '000062', '000063', '000064', '000065',
                    '000066', '000067', '000068', '000069', '000070',
                    '000071', '000072', '000073', '000074', '000075',
                    '000076', '000077', '000078', '000079', '000080',
                    '000081', '000082', '000083', '000084', '000085',
                    '000086', '000087', '000088', '000089', '000090',
                    '000091', '000092', '000093', '000094', '000095',
                    '000096', '000097', '000098', '000099', '000100',
                    '000101', '000102', '000103', '000104', '000105',
                    '000106', '000107', '000108', '000109', '000110',
                    '000111', '000112', '000113', '000114', '000115',
                    '000116', '000117', '000118', '000119', '000120',
                    '000121', '000122', '000123', '000124', '000125',
                    '000126', '000127', '000128', '000129', '000130',
                    '000131', '000132', '000133', '000134', '000135',
                    '000136', '000137', '000138', '000139', '000140',
                    '000141', '000142', '000143', '000144', '000145',
                    '000146', '000147', '000148', '000149', '000150',
                    '000151', '000152', '000153', '000154', '000155',
                    '000156', '000157', '000158', '000159', '000160',
                    '000161', '000162', '000163', '000164', '000165',
                    '000166', '000167', '000168', '000169', '000170',
                    '000171', '000172', '000173', '000174', '000175',
                    '000176', '000177', '000178', '000179', '000180',
                    '000181', '000182', '000183', '000184', '000185',
                    '000186', '000187', '000188', '000189', '000190',
                    '000191', '000192', '000193', '000194', '000195',
                    '000196', '000197', '000198', '000199', '000200',
                    '000201', '000202', '000203', '000204', '000205',
                    '000206', '000207', '000208', '000209', '000210',
                    '000211', '000212', '000213', '000214', '000215',
                    '000216', '000217', '000218', '000219', '000220',
                    '000221', '000222', '000223', '000224', '000225',
                    '000226', '000227', '000228', '000229', '000230',
                    '000231', '000232', '000233', '000234', '000235',
                    '000236', '000237', '000238', '000239', '000240',
                    '000241', '000242', '000243', '000244', '000245',
                    '000246', '000247', '000248', '000249', '000250',
                    '000251', '000252', '000253', '000254', '000255',
                    '000256', '000257', '000258', '000259', '000260',
                    '000261', '000262', '000263', '000264', '000265',
                    '000266', '000267', '000268', '000269', '000270',
                    '000271', '000272', '000273', '000274', '000275',
                    '000276', '000277', '000278', '000279', '000280',
                    '000281', '000282', '000283', '000284', '000285',
                    '000286', '000287', '000288', '000289', '000290',
                    '000291', '000292', '000293', '000294', '000295',
                    '000296', '000297', '000298', '000299', '000300',
                    '000301', '000302', '000303', '000304', '000305',
                    '000306', '000307', '000308', '000309', '000310',
                    '000311', '000312', '000313', '000314', '000315',
                    '000316', '000317', '000318', '000319', '000320',
                    '000321', '000322', '000323', '000324', '000325',
                    '000326', '000327', '000328', '000329', '000330',
                    '000331', '000332', '000333', '000334', '000335',
                    '000336', '000337', '000338', '000339', '000340',
                    '000341', '000342', '000343', '000344', '000345',
                    '000346', '000347', '000348', '000349', '000350',
                    '000351', '000352', '000353', '000354', '000355',
                    '000356', '000357', '000358', '000359', '000360',
                    '000361', '000362', '000363', '000364', '000365',
                    '000366', '000367', '000368', '000369', '000370',
                    '000371', '000372', '000373', '000374', '000375',
                    '000376', '000377', '000378', '000379', '000380',
                    '000381', '000382', '000383', '000384', '000385',
                    '000386', '000387', '000388', '000389', '000390',
                    '000391', '000392', '000393', '000394', '000395',
                    '000396', '000397', '000398', '000399', '000400',
                    '000401', '000402', '000403', '000404', '000405',
                    '000406', '000407', '000408', '000409', '000410',
                    '000411', '000412', '000413', '000414', '000415',
                    '000416', '000417', '000418', '000419', '000420',
                    '000421', '000422', '000423', '000424', '000425',
                    '000426', '000427', '000428', '000429', '000430',
                    '000431', '000432', '000433', '000434', '000435',
                    '000436', '000437', '000438', '000439', '000440',
                    '000441', '000442', '000443', '000444', '000445',
                    '000446', '000447', '000448', '000449', '000450',
                    '000451', '000452', '000453', '000454', '000455',
                    '000456', '000457', '000458', '000459', '000460',
                    '000461', '000462', '000463', '000464', '000465',
                    '000466', '000467', '000468', '000469', '000470',
                    '000471', '000472', '000473', '000474', '000475',
                    '000476', '000477', '000478', '000479', '000480',
                    '000481', '000482', '000483', '000484', '000485',
                    '000486', '000487', '000488', '000489', '000490',
                    '000491', '000492', '000493', '000494', '000495',
                    '000496', '000497', '000498', '000499', '000500'
                ]
                import pandas as pd
                fund_list = pd.DataFrame({
                    '基金代码': fund_codes,
                    '基金名称': ['基金' + code for code in fund_codes]
                })
        
        return fund_list
    except Exception as e:
        print(f"获取基金列表失败: {e}")
        return None

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

def update_fund_data(fund_code, fund_name):
    """更新单只基金数据"""
    print(f"开始更新基金 {fund_code} ({fund_name}) 的数据...")
    
    # 连接MySQL数据库
    conn = None
    cursor = None
    
    try:
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        cursor = conn.cursor()
        
        # 获取基金基本信息
        try:
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
        except Exception as e:
            print(f"获取基金基本信息失败: {e}")
            return False
        
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
                try:
                    latest = industry_df.iloc[0]
                    first_industry = latest['行业类别']
                    industry_ratio = f"{latest['占净值比例']:.2f}%"
                    
                    # 基金定性分析
                    industry_analysis = analyze_fund_style(industry_df)
                    fund_style = industry_analysis['style']
                    style_description = industry_analysis['description']
                except Exception as e:
                    print(f"处理行业配置信息失败: {e}")
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
        print(f"基金 {fund_code} ({fund_name}) 数据更新成功")
        return True
        
    except Exception as e:
        print(f"更新基金 {fund_code} ({fund_name}) 数据失败: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        # 确保关闭连接
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def update_500_funds():
    """更新500只基金数据"""
    print("开始更新500只基金数据...")
    print(f"更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 获取数据库中已有的基金代码
    existing_fund_codes = set()
    try:
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        cursor = conn.cursor()
        cursor.execute("SELECT fund_code FROM fund_basic")
        for row in cursor.fetchall():
            existing_fund_codes.add(row[0])
        cursor.close()
        conn.close()
        print(f"数据库中已存在 {len(existing_fund_codes)} 只基金")
    except Exception as e:
        print(f"获取数据库中已有的基金代码失败: {e}")
    
    # 手动维护的基金代码列表
    fund_codes = [
        '161039', '519674', '110011', '000001', '510300',
        '159919', '510500', '159915', '001052', '000300',
        '159920', '530020', '000751', '110022', '481012',
        '163406', '161725', '005918', '006113', '001878',
        '000001', '000002', '000003', '000004', '000005',
        '000006', '000007', '000008', '000009', '000010',
        '000011', '000012', '000013', '000014', '000015',
        '000016', '000017', '000018', '000019', '000020',
        '000021', '000022', '000023', '000024', '000025',
        '000026', '000027', '000028', '000029', '000030',
        '000031', '000032', '000033', '000034', '000035',
        '000036', '000037', '000038', '000039', '000040',
        '000041', '000042', '000043', '000044', '000045',
        '000046', '000047', '000048', '000049', '000050',
        '000051', '000052', '000053', '000054', '000055',
        '000056', '000057', '000058', '000059', '000060',
        '000061', '000062', '000063', '000064', '000065',
        '000066', '000067', '000068', '000069', '000070',
        '000071', '000072', '000073', '000074', '000075',
        '000076', '000077', '000078', '000079', '000080',
        '000081', '000082', '000083', '000084', '000085',
        '000086', '000087', '000088', '000089', '000090',
        '000091', '000092', '000093', '000094', '000095',
        '000096', '000097', '000098', '000099', '000100',
        '000101', '000102', '000103', '000104', '000105',
        '000106', '000107', '000108', '000109', '000110',
        '000111', '000112', '000113', '000114', '000115',
        '000116', '000117', '000118', '000119', '000120',
        '000121', '000122', '000123', '000124', '000125',
        '000126', '000127', '000128', '000129', '000130',
        '000131', '000132', '000133', '000134', '000135',
        '000136', '000137', '000138', '000139', '000140',
        '000141', '000142', '000143', '000144', '000145',
        '000146', '000147', '000148', '000149', '000150',
        '000151', '000152', '000153', '000154', '000155',
        '000156', '000157', '000158', '000159', '000160',
        '000161', '000162', '000163', '000164', '000165',
        '000166', '000167', '000168', '000169', '000170',
        '000171', '000172', '000173', '000174', '000175',
        '000176', '000177', '000178', '000179', '000180',
        '000181', '000182', '000183', '000184', '000185',
        '000186', '000187', '000188', '000189', '000190',
        '000191', '000192', '000193', '000194', '000195',
        '000196', '000197', '000198', '000199', '000200',
        '000201', '000202', '000203', '000204', '000205',
        '000206', '000207', '000208', '000209', '000210',
        '000211', '000212', '000213', '000214', '000215',
        '000216', '000217', '000218', '000219', '000220',
        '000221', '000222', '000223', '000224', '000225',
        '000226', '000227', '000228', '000229', '000230',
        '000231', '000232', '000233', '000234', '000235',
        '000236', '000237', '000238', '000239', '000240',
        '000241', '000242', '000243', '000244', '000245',
        '000246', '000247', '000248', '000249', '000250',
        '000251', '000252', '000253', '000254', '000255',
        '000256', '000257', '000258', '000259', '000260',
        '000261', '000262', '000263', '000264', '000265',
        '000266', '000267', '000268', '000269', '000270',
        '000271', '000272', '000273', '000274', '000275',
        '000276', '000277', '000278', '000279', '000280',
        '000281', '000282', '000283', '000284', '000285',
        '000286', '000287', '000288', '000289', '000290',
        '000291', '000292', '000293', '000294', '000295',
        '000296', '000297', '000298', '000299', '000300',
        '000301', '000302', '000303', '000304', '000305',
        '000306', '000307', '000308', '000309', '000310',
        '000311', '000312', '000313', '000314', '000315',
        '000316', '000317', '000318', '000319', '000320',
        '000321', '000322', '000323', '000324', '000325',
        '000326', '000327', '000328', '000329', '000330',
        '000331', '000332', '000333', '000334', '000335',
        '000336', '000337', '000338', '000339', '000340',
        '000341', '000342', '000343', '000344', '000345',
        '000346', '000347', '000348', '000349', '000350',
        '000351', '000352', '000353', '000354', '000355',
        '000356', '000357', '000358', '000359', '000360',
        '000361', '000362', '000363', '000364', '000365',
        '000366', '000367', '000368', '000369', '000370',
        '000371', '000372', '000373', '000374', '000375',
        '000376', '000377', '000378', '000379', '000380',
        '000381', '000382', '000383', '000384', '000385',
        '000386', '000387', '000388', '000389', '000390',
        '000391', '000392', '000393', '000394', '000395',
        '000396', '000397', '000398', '000399', '000400',
        '000401', '000402', '000403', '000404', '000405',
        '000406', '000407', '000408', '000409', '000410',
        '000411', '000412', '000413', '000414', '000415',
        '000416', '000417', '000418', '000419', '000420',
        '000421', '000422', '000423', '000424', '000425',
        '000426', '000427', '000428', '000429', '000430',
        '000431', '000432', '000433', '000434', '000435',
        '000436', '000437', '000438', '000439', '000440',
        '000441', '000442', '000443', '000444', '000445',
        '000446', '000447', '000448', '000449', '000450',
        '000451', '000452', '000453', '000454', '000455',
        '000456', '000457', '000458', '000459', '000460',
        '000461', '000462', '000463', '000464', '000465',
        '000466', '000467', '000468', '000469', '000470',
        '000471', '000472', '000473', '000474', '000475',
        '000476', '000477', '000478', '000479', '000480',
        '000481', '000482', '000483', '000484', '000485',
        '000486', '000487', '000488', '000489', '000490',
        '000491', '000492', '000493', '000494', '000495',
        '000496', '000497', '000498', '000499', '000500'
    ]
    
    # 过滤掉已存在的基金代码
    new_fund_codes = [code for code in fund_codes if code not in existing_fund_codes]
    print(f"需要更新 {len(new_fund_codes)} 只新基金")
    
    success_count = 0
    failure_count = 0
    
    # 直接使用手动列表更新
    for i, fund_code in enumerate(new_fund_codes):
        fund_name = f"基金{fund_code}"
        if update_fund_data(fund_code, fund_name):
            success_count += 1
        else:
            failure_count += 1
        
        # 避免请求过于频繁
        time.sleep(1)
        
        # 每更新10只基金显示一次进度
        if (i + 1) % 10 == 0:
            print(f"已更新 {i + 1} 只基金，成功: {success_count}, 失败: {failure_count}")
    
    # 更新加载时间
    try:
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        cursor = conn.cursor()
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
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"更新加载时间失败: {e}")
    
    print(f"\n更新完成！")
    print(f"成功: {success_count} 只基金")
    print(f"失败: {failure_count} 只基金")
    print(f"数据库中总基金数: {len(existing_fund_codes) + success_count}")
    
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

if __name__ == '__main__':
    update_500_funds()
