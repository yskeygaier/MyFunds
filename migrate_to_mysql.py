import sqlite3
import mysql.connector
from datetime import datetime

# SQLite数据库配置
SQLITE_DB = 'fund_data.db'

# MySQL数据库配置
MYSQL_CONFIG = {
    'user': 'yskey',
    'password': 'yskey',
    'host': '127.0.0.1',
    'port': '3306',
    'database': 'fund_data'
}

def migrate_data():
    print("开始将数据从SQLite迁移到MySQL...")
    
    # 连接SQLite数据库
    sqlite_conn = sqlite3.connect(SQLITE_DB)
    sqlite_conn.row_factory = sqlite3.Row
    sqlite_cursor = sqlite_conn.cursor()
    
    # 连接MySQL数据库
    mysql_conn = mysql.connector.connect(**MYSQL_CONFIG)
    mysql_cursor = mysql_conn.cursor()
    
    try:
        # 迁移基金基本信息
        print("迁移基金基本信息...")
        sqlite_cursor.execute('SELECT * FROM fund_basic')
        fund_basic_rows = sqlite_cursor.fetchall()
        
        fund_count = 0
        for row in fund_basic_rows:
            # 构建插入语句
            insert_sql = """
            INSERT INTO fund_basic 
            (fund_code, fund_name, net_value, nav_date, day_growth, annual_return, 
             annual_volatility, sharpe_ratio, calmar_ratio, max_drawdown, fund_manager, 
             first_industry, industry_ratio, fund_style, style_description, 
             holdings_concentration, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            
            # 准备数据
            data = (
                row['fund_code'],
                row['fund_name'],
                row['net_value'],
                row['nav_date'],
                row['day_growth'],
                row['annual_return'],
                row['annual_volatility'],
                row['sharpe_ratio'],
                row['calmar_ratio'],
                row['max_drawdown'],
                row['fund_manager'],
                row['first_industry'],
                row['industry_ratio'],
                row['fund_style'],
                row['style_description'],
                row['holdings_concentration'],
                row['created_at'] or datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                row['updated_at'] or datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            )
            
            # 执行插入
            mysql_cursor.execute(insert_sql, data)
            fund_count += 1
        
        print(f"成功迁移 {fund_count} 只基金的基本信息")
        
        # 迁移基金持仓信息
        print("迁移基金持仓信息...")
        sqlite_cursor.execute('SELECT * FROM fund_holdings')
        fund_holdings_rows = sqlite_cursor.fetchall()
        
        holdings_count = 0
        for row in fund_holdings_rows:
            # 构建插入语句
            insert_sql = """
            INSERT INTO fund_holdings 
            (fund_code, stock_code, stock_name, weight)
            VALUES (%s, %s, %s, %s)
            """
            
            # 准备数据
            data = (
                row['fund_code'],
                row['stock_code'],
                row['stock_name'],
                row['weight']
            )
            
            # 执行插入
            mysql_cursor.execute(insert_sql, data)
            holdings_count += 1
        
        print(f"成功迁移 {holdings_count} 条基金持仓信息")
        
        # 迁移加载时间信息
        print("迁移加载时间信息...")
        sqlite_cursor.execute('SELECT * FROM load_time')
        load_time_rows = sqlite_cursor.fetchall()
        
        for row in load_time_rows:
            # 构建插入语句
            insert_sql = """
            INSERT INTO load_time 
            (last_load_time)
            VALUES (%s)
            """
            
            # 准备数据
            data = (row['last_load_time'],)
            
            # 执行插入
            mysql_cursor.execute(insert_sql, data)
        
        # 提交事务
        mysql_conn.commit()
        print("数据迁移完成！")
        
    except Exception as e:
        print(f"数据迁移失败: {e}")
        mysql_conn.rollback()
    finally:
        # 关闭连接
        sqlite_cursor.close()
        sqlite_conn.close()
        mysql_cursor.close()
        mysql_conn.close()

if __name__ == '__main__':
    migrate_data()
