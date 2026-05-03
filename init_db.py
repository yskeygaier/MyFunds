import sqlite3
import os

# 数据库文件路径
DB_FILE = 'fund_data.db'

# 初始化数据库
def init_db():
    print("开始初始化数据库...")
    
    # 连接数据库
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 创建基金基本信息表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS fund_basic (
        fund_code TEXT PRIMARY KEY,
        fund_name TEXT,
        net_value TEXT,
        nav_date TEXT,
        day_growth TEXT,
        annual_return TEXT,
        annual_volatility TEXT,
        sharpe_ratio TEXT,
        calmar_ratio TEXT,
        max_drawdown TEXT,
        fund_manager TEXT,
        first_industry TEXT,
        industry_ratio TEXT,
        fund_style TEXT,
        style_description TEXT,
        holdings_concentration TEXT,
        created_at TEXT,
        updated_at TEXT
    )''')
    
    # 创建基金持仓表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS fund_holdings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fund_code TEXT,
        stock_code TEXT,
        stock_name TEXT,
        weight TEXT,
        FOREIGN KEY (fund_code) REFERENCES fund_basic (fund_code)
    )''')
    
    # 创建加载时间表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS load_time (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        last_load_time TEXT
    )''')
    
    # 提交并关闭连接
    conn.commit()
    conn.close()
    
    print("数据库初始化完成！")

if __name__ == '__main__':
    init_db()
