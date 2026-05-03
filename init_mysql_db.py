import mysql.connector
from mysql.connector import errorcode

# MySQL数据库配置
CONFIG = {
    'user': 'yskey',
    'password': 'yskey',
    'host': '127.0.0.1',
    'port': '3306'
}

# 数据库名称
DB_NAME = 'fund_data'

def init_mysql_db():
    print("开始初始化MySQL数据库...")
    
    # 连接MySQL服务器
    try:
        cnx = mysql.connector.connect(**CONFIG)
        cursor = cnx.cursor()
        
        # 创建数据库
        try:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS {DB_NAME} DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            print(f"数据库 {DB_NAME} 创建成功")
        except mysql.connector.Error as err:
            print(f"创建数据库失败: {err}")
            return
        
        # 切换到创建的数据库
        cnx.database = DB_NAME
        
        # 创建基金基本信息表
        fund_basic_table = """
        CREATE TABLE IF NOT EXISTS fund_basic (
            fund_code VARCHAR(10) PRIMARY KEY,
            fund_name VARCHAR(100),
            net_value VARCHAR(20),
            nav_date VARCHAR(20),
            day_growth VARCHAR(20),
            annual_return VARCHAR(20),
            annual_volatility VARCHAR(20),
            sharpe_ratio VARCHAR(20),
            calmar_ratio VARCHAR(20),
            max_drawdown VARCHAR(20),
            fund_manager VARCHAR(200),
            first_industry VARCHAR(100),
            industry_ratio VARCHAR(20),
            fund_style VARCHAR(50),
            style_description VARCHAR(200),
            holdings_concentration VARCHAR(20),
            created_at DATETIME,
            updated_at DATETIME
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
        
        # 创建基金持仓表
        fund_holdings_table = """
        CREATE TABLE IF NOT EXISTS fund_holdings (
            id INT AUTO_INCREMENT PRIMARY KEY,
            fund_code VARCHAR(10),
            stock_code VARCHAR(20),
            stock_name VARCHAR(100),
            weight VARCHAR(20),
            FOREIGN KEY (fund_code) REFERENCES fund_basic (fund_code) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
        
        # 创建加载时间表
        load_time_table = """
        CREATE TABLE IF NOT EXISTS load_time (
            id INT AUTO_INCREMENT PRIMARY KEY,
            last_load_time DATETIME
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
        
        # 执行创建表的SQL语句
        tables = {
            'fund_basic': fund_basic_table,
            'fund_holdings': fund_holdings_table,
            'load_time': load_time_table
        }
        
        for table_name, table_sql in tables.items():
            try:
                cursor.execute(table_sql)
                print(f"表 {table_name} 创建成功")
            except mysql.connector.Error as err:
                if err.errno == errorcode.ER_TABLE_EXISTS_ERROR:
                    print(f"表 {table_name} 已存在")
                else:
                    print(f"创建表 {table_name} 失败: {err}")
        
        # 提交并关闭连接
        cnx.commit()
        cursor.close()
        cnx.close()
        
        print("MySQL数据库初始化完成！")
        
    except mysql.connector.Error as err:
        print(f"连接MySQL失败: {err}")

if __name__ == '__main__':
    init_mysql_db()
