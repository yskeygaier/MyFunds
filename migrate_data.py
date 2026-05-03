import sqlite3
import json
import os
from datetime import datetime

# 数据库文件路径
DB_FILE = 'fund_data.db'
# 基金数据目录
DATA_DIR = 'fund_data'

# 连接数据库
def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

# 迁移单个基金数据
def migrate_fund_data(fund_code):
    print(f"正在迁移基金 {fund_code} 的数据...")
    
    # 读取本地文件数据
    data_file = os.path.join(DATA_DIR, f"{fund_code}.json")
    if not os.path.exists(data_file):
        print(f"基金 {fund_code} 的数据文件不存在")
        return False
    
    try:
        with open(data_file, 'r', encoding='utf-8') as f:
            fund_data = json.load(f)
    except Exception as e:
        print(f"读取数据文件失败: {e}")
        return False
    
    # 连接数据库
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # 插入或更新基金基本信息
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute('''
        INSERT OR REPLACE INTO fund_basic 
        (fund_code, fund_name, net_value, nav_date, day_growth, annual_return, annual_volatility, 
         sharpe_ratio, calmar_ratio, max_drawdown, fund_manager, first_industry, industry_ratio, 
         fund_style, style_description, holdings_concentration, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            fund_code,
            fund_data.get('基金简称', ''),
            fund_data.get('单位净值', ''),
            fund_data.get('净值日期', ''),
            fund_data.get('日增长率', ''),
            fund_data.get('年化收益率', ''),
            fund_data.get('年化波动率', ''),
            fund_data.get('夏普比率', ''),
            fund_data.get('卡玛比率', ''),
            fund_data.get('最大回撤', ''),
            fund_data.get('基金经理', ''),
            fund_data.get('第一大行业', ''),
            fund_data.get('行业占比', ''),
            fund_data.get('基金风格', ''),
            fund_data.get('风格描述', ''),
            fund_data.get('持仓集中度', ''),
            current_time,
            current_time
        ))
        
        # 删除旧的持仓数据
        cursor.execute('DELETE FROM fund_holdings WHERE fund_code = ?', (fund_code,))
        
        # 插入新的持仓数据
        holdings = fund_data.get('前十大持仓', [])
        for holding in holdings:
            cursor.execute('''
            INSERT INTO fund_holdings (fund_code, stock_code, stock_name, weight)
            VALUES (?, ?, ?, ?)
            ''', (
                fund_code,
                holding.get('股票代码', ''),
                holding.get('股票名称', ''),
                holding.get('占净值比例', '')
            ))
        
        # 提交事务
        conn.commit()
        print(f"基金 {fund_code} 的数据迁移成功")
        return True
    except Exception as e:
        print(f"迁移基金 {fund_code} 数据失败: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

# 批量迁移所有基金数据
def migrate_all_fund_data():
    print("开始批量迁移基金数据...")
    
    # 获取所有基金数据文件
    fund_files = [f for f in os.listdir(DATA_DIR) if f.endswith('.json') and f != 'load_time.json']
    fund_codes = [f.replace('.json', '') for f in fund_files]
    
    print(f"总共需要迁移 {len(fund_codes)} 只基金的数据")
    
    success_count = 0
    failure_count = 0
    
    for fund_code in fund_codes:
        if migrate_fund_data(fund_code):
            success_count += 1
        else:
            failure_count += 1
    
    # 更新加载时间
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute('DELETE FROM load_time')
        cursor.execute('INSERT INTO load_time (last_load_time) VALUES (?)', (current_time,))
        conn.commit()
    except Exception as e:
        print(f"更新加载时间失败: {e}")
    finally:
        conn.close()
    
    print(f"\n迁移完成！")
    print(f"成功: {success_count} 只基金")
    print(f"失败: {failure_count} 只基金")

if __name__ == '__main__':
    migrate_all_fund_data()
