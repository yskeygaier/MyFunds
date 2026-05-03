import sqlite3

# 数据库文件路径
DB_FILE = 'fund_data.db'

# 连接数据库
def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

# 展示基金基本信息
def show_fund_basic():
    print("=== 基金基本信息 ===")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 查询所有基金基本信息
    cursor.execute('''
    SELECT fund_code, fund_name, net_value, nav_date, fund_style FROM fund_basic
    ORDER BY fund_code
    ''')
    
    funds = cursor.fetchall()
    print(f"共 {len(funds)} 只基金")
    print("-" * 80)
    print(f"{'基金代码':<10} {'基金名称':<20} {'单位净值':<10} {'净值日期':<12} {'基金风格':<10}")
    print("-" * 80)
    
    for fund in funds:
        print(f"{fund['fund_code']:<10} {fund['fund_name'][:19]:<20} {fund['net_value']:<10} {fund['nav_date']:<12} {fund['fund_style']:<10}")
    
    conn.close()
    print("-" * 80)

# 展示基金持仓信息
def show_fund_holdings(fund_code):
    print(f"\n=== 基金 {fund_code} 的持仓信息 ===")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 查询基金持仓信息
    cursor.execute('''
    SELECT stock_code, stock_name, weight FROM fund_holdings
    WHERE fund_code = ? ORDER BY id
    ''', (fund_code,))
    
    holdings = cursor.fetchall()
    if not holdings:
        print("该基金暂无持仓信息")
        conn.close()
        return
    
    print(f"共 {len(holdings)} 只持仓股票")
    print("-" * 60)
    print(f"{'股票代码':<10} {'股票名称':<20} {'占净值比例':<10}")
    print("-" * 60)
    
    for holding in holdings:
        print(f"{holding['stock_code']:<10} {holding['stock_name'][:19]:<20} {holding['weight']:<10}")
    
    conn.close()
    print("-" * 60)

# 展示加载时间
def show_load_time():
    print("\n=== 数据加载时间 ===")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 查询加载时间
    cursor.execute('SELECT last_load_time FROM load_time ORDER BY id DESC LIMIT 1')
    load_time = cursor.fetchone()
    
    if load_time:
        print(f"最后加载时间: {load_time['last_load_time']}")
    else:
        print("暂无加载时间记录")
    
    conn.close()

if __name__ == '__main__':
    # 展示基金基本信息
    show_fund_basic()
    
    # 展示指定基金的持仓信息（示例）
    test_funds = ['519674', '161039', '163406']
    for fund_code in test_funds:
        show_fund_holdings(fund_code)
    
    # 展示加载时间
    show_load_time()
