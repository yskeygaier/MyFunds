import mysql.connector

# MySQL数据库配置
MYSQL_CONFIG = {
    'user': 'yskey',
    'password': 'yskey',
    'host': '127.0.0.1',
    'port': '3306',
    'database': 'fund_data'
}

def check_data():
    print("检查MySQL数据库中的基金数据...")
    
    # 连接MySQL数据库
    conn = mysql.connector.connect(**MYSQL_CONFIG)
    cursor = conn.cursor()
    
    try:
        # 检查基金基本信息数量
        cursor.execute("SELECT COUNT(*) FROM fund_basic")
        fund_count = cursor.fetchone()[0]
        print(f"基金基本信息数量: {fund_count}")
        
        # 检查基金持仓信息数量
        cursor.execute("SELECT COUNT(*) FROM fund_holdings")
        holdings_count = cursor.fetchone()[0]
        print(f"基金持仓信息数量: {holdings_count}")
        
        # 检查加载时间
        cursor.execute("SELECT last_load_time FROM load_time ORDER BY id DESC LIMIT 1")
        load_time = cursor.fetchone()
        if load_time:
            print(f"最后加载时间: {load_time[0]}")
        else:
            print("未找到加载时间记录")
        
        # 检查前10条基金数据
        print("\n前10条基金数据:")
        cursor.execute("SELECT fund_code, fund_name, fund_style FROM fund_basic LIMIT 10")
        funds = cursor.fetchall()
        for fund in funds:
            print(f"基金代码: {fund[0]}, 基金名称: {fund[1]}, 基金风格: {fund[2]}")
        
    except Exception as e:
        print(f"检查数据失败: {e}")
    finally:
        cursor.close()
        conn.close()

if __name__ == '__main__':
    check_data()
