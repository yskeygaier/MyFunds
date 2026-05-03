from flask import Flask, render_template, request, jsonify
import pandas as pd
import plotly.graph_objects as go
import plotly.utils
import json
from datetime import datetime, timedelta, date
import time
import sched
import base64
from io import BytesIO
import concurrent.futures
import threading
import os
import sys
import atexit

# ── 分析报告生成锁（防止同一基金重复生成）──────────────────────────────────
# 键：基金代码，值：生成任务状态 'generating' 或 None
REPORT_GENERATING = {}

# 将项目目录加入import路径，以便加载fund_analyzer模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pymysql
from fund_analyzer import FundScreener, ReportGenerator
from fund_crawler import crawl_fund_full

# ══════════════════════════════════════════════════════════════
# 东方财富 HTTP 请求头（所有直调接口共用）
# ══════════════════════════════════════════════════════════════
_EASTMONEY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://fund.eastmoney.com/",
    "Accept": "*/*",
}

# SQLite数据库路径（使用绝对路径，避免工作目录问题）
_SQLITE_DIR = os.path.dirname(os.path.abspath(__file__))
SQLITE_DB_PATH = os.path.join(_SQLITE_DIR, 'fund_data.db')

# 初始化Redis连接
REDIS_AVAILABLE = False
r = None
try:
    import redis
    r = redis.Redis(
        host='127.0.0.1',  # 使用指定的地址
        port=6379,         # 使用指定的端口
        db=0,
        decode_responses=True,
        socket_connect_timeout=5,  # 连接超时设置
        socket_timeout=5,          # 读写超时设置
        retry_on_timeout=True,     # 超时重试
        max_connections=50         # 最大连接数
    )
    r.ping()  # 测试连接
    REDIS_AVAILABLE = True
    print("Redis连接成功")
except Exception as e:
    # 如果Redis不可用，设置为False，仍然可以正常运行
    REDIS_AVAILABLE = False
    print(f"Redis不可用，使用内存缓存: {e}")

# MySQL 连接池 — 统一由 db.py 管理
from db import init as _db_init, get_pool

def get_mysql_pool():
    """获取 MySQL 连接池（兼容旧接口）"""
    return get_pool()

# 内存缓存作为备选
from cache import ThreadSafeCache
memory_cache = ThreadSafeCache(name="memory")

# 搜索结果缓存（短期，5分钟）
search_cache = {}
search_cache_timestamps = {}
SEARCH_CACHE_TTL = 300  # 5分钟

# 基金名称内存缓存（24小时过期）
fund_name_cache = {}  # {fund_code: (name, timestamp)}
FUND_NAME_CACHE_TTL = 86400  # 24小时

# 基金数据刷新配置
FUND_DATA_REFRESH_HOURS = 24  # 数据24小时刷新一次
fund_refresh_times = {}  # 记录每个基金的上次刷新时间 {fund_code: timestamp}
refresh_lock = threading.Lock()  # 防止多线程同时刷新

# 缓存配置
CACHE_CONFIG = {
    'fund_info': {
        'expiry': 3600,  # 1小时
        'prefix': 'fund:info'
    },
    'fund_analysis_report': {
        'expiry': 3600,  # 1小时
        'prefix': 'fund:analysis_report'
    },
}

# ── 分析报告历史库配置（方案B+C）──────────────────────────────
ANALYSIS_HISTORY_WEEKS = 4  # 保留4周历史
TOP_FUNDS_WARMUP_COUNT = 30  # 启动时预热Top30基金报告

CACHE_CONFIG['fund_backtest'] = {
    'expiry': 7200,  # 2小时
    'prefix': 'fund:backtest'
}
CACHE_CONFIG['fund_dca'] = {
    'expiry': 7200,  # 2小时
    'prefix': 'fund:dca'
}
CACHE_CONFIG['fund_list'] = {
    'expiry': 86400,  # 24小时
    'prefix': 'fund:list'
}

# 生成缓存键
def generate_cache_key(prefix, *args):
    """生成缓存键"""
    key_parts = [prefix]
    key_parts.extend(str(arg) for arg in args)
    return ':'.join(key_parts)

# 缓存操作函数
def get_cache(key):
    """从缓存获取数据"""
    try:
        if REDIS_AVAILABLE:
            data = r.get(key)
            if data:
                return json.loads(data)
        else:
            return memory_cache.get(key)
    except Exception:
        return None
    return None

def set_cache(key, data, expiry=3600):
    """设置缓存数据"""
    try:
        def json_serial(obj):
            if isinstance(obj, (datetime.date, datetime.datetime)):
                return obj.isoformat()
            raise TypeError(f"Type {type(obj)} not serializable")

        serialized = json.dumps(data, default=json_serial)
        if REDIS_AVAILABLE:
            r.setex(key, expiry, serialized)
        else:
            memory_cache.set(key, data)
    except Exception:
        # 缓存写入失败不影响主流程，降级到内存
        try:
            memory_cache.set(key, data)
        except Exception:
            pass

def delete_cache(key):
    """删除缓存数据"""
    try:
        if REDIS_AVAILABLE:
            r.delete(key)
        else:
            memory_cache.delete(key)
    except Exception as e:
        print(f"删除缓存失败: {e}")


app = Flask(__name__)
# ── Session 配置 ─────────────────────────────────────────────
app.secret_key = os.urandom(32).hex()
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = 86400  # 24小时

FUND_NAME_MAP = {
    '161039': '易方达中小盘混合',
    '519674': '银河创新成长混合',
    '110011': '易方达消费行业股票',
    '000001': '平安成长收益混合',
    '510300': '华夏沪深300ETF',
    '159919': '华夏上证50ETF',
    '510500': '华夏中证500ETF',
    '159915': '易方达创业板ETF',
    '001052': '华夏上证50ETF联接',
    '000300': '沪深300指数',
    '159920': '华夏上证50ETF',
    '530020': '建信优选成长混合',
    '000751': '嘉实沪深300指数',
    '110022': '华夏上证50ETF联接',
    '481012': '中银中证100指数',
    '163406': '兴全合润混合',
    '161725': '招商中证白酒指数',
    '005918': '易方达蓝筹精选混合',
    '006113': '易方达创新驱动混合',
    '001878': '嘉实沪港深精选股票',
    '510500': '华夏中证500ETF',
    '159915': '易方达创业板ETF',
    '512760': '国泰CES半导体芯片ETF',
    '512480': '华夏半导体ETF',
    '515050': '华夏5G ETF',
    '513050': '易方达中概互联ETF',
    '159920': '华夏上证50ETF',
    '510050': '华夏上证50ETF',
    '512000': '华宝券商ETF',
    '512880': '国泰证券公司ETF',
    '515000': '华安媒体互联网ETF',
    '159992': '兴全中证800ETF',
    '159867': '华夏中证新能源ETF',
    '515790': '华泰柏瑞光伏ETF',
    '588000': '华夏科创50ETF',
    '588050': '工银瑞信科创50ETF',
}

def get_fund_name(fund_code):
    """从多个来源获取基金名称，优先级：缓存 > FUND_NAME_MAP > crawl_fund_full > 数据库"""
    now = time.time()

    # 1. 先从内存缓存获取
    if fund_code in fund_name_cache:
        cached_name, cached_ts = fund_name_cache[fund_code]
        if now - cached_ts < FUND_NAME_CACHE_TTL:
            return cached_name

    # 2. 先从本地映射表获取
    if fund_code in FUND_NAME_MAP:
        fund_name_cache[fund_code] = (FUND_NAME_MAP[fund_code], now)
        return FUND_NAME_MAP[fund_code]

    # 3. 尝试从天天基金网爬虫获取（~0.2s vs akshare 3-17s）
    try:
        data = crawl_fund_full(fund_code)
        if data and data.get('fund_name'):
            name = data['fund_name']
            fund_name_cache[fund_code] = (name, now)
            return name
    except Exception:
        pass

    # 4. 返回默认名称
    default_name = f'基金{fund_code}'
    fund_name_cache[fund_code] = (default_name, now)
    return default_name

def save_fund_info_to_db(info_dict):
    """保存基金信息到MySQL数据库"""
    fund_code = info_dict.get('基金代码')
    if not fund_code:
        return False

    try:
        pool = get_mysql_pool()
        if pool is None:
            return save_fund_info_to_sqlite(info_dict)
        conn = pool.get_connection()
        cursor = conn.cursor()

        # 更新或插入 fund_basic 表
        cursor.execute('''
            INSERT INTO fund_basic (
                fund_code, fund_name, net_value, nav_date, day_growth, annual_return,
                annual_volatility, sharpe_ratio, calmar_ratio, max_drawdown,
                fund_manager, first_industry, industry_ratio, fund_style, holdings_concentration,
                update_time
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
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
                holdings_concentration = VALUES(holdings_concentration),
                update_time = VALUES(update_time)
        ''', (
            fund_code,
            info_dict.get('基金简称', ''),
            info_dict.get('单位净值', ''),
            info_dict.get('净值日期', ''),
            info_dict.get('日增长率', ''),
            info_dict.get('年化收益率', ''),
            info_dict.get('年化波动率', ''),
            info_dict.get('夏普比率', ''),
            info_dict.get('卡玛比率', ''),
            info_dict.get('最大回撤', ''),
            info_dict.get('基金经理', ''),
            info_dict.get('第一大行业', ''),
            info_dict.get('行业占比', ''),
            info_dict.get('基金风格', ''),
            info_dict.get('持仓集中度', ''),
            datetime.now()
        ))

        # 更新前十大持仓
        if '前十大持仓' in info_dict and info_dict['前十大持仓']:
            # 先删除旧持仓
            cursor.execute('DELETE FROM fund_holdings WHERE fund_code = %s', (fund_code,))

            # 插入新持仓
            for holding in info_dict['前十大持仓']:
                cursor.execute('''
                    INSERT INTO fund_holdings (fund_code, stock_code, stock_name, weight, sector_tag)
                    VALUES (%s, %s, %s, %s, %s)
                ''', (
                    fund_code,
                    holding.get('股票代码', ''),
                    holding.get('股票名称', ''),
                    holding.get('占净值比例', ''),
                    holding.get('细分行业', '')
                ))

        conn.commit()
        conn.close()
        print(f"基金 {fund_code} 数据已保存到MySQL")
        return True
    except Exception as e:
        print(f"保存基金数据到MySQL失败: {e}")
        # 如果MySQL失败，尝试保存到SQLite
        try:
            save_fund_info_to_sqlite(info_dict)
        except Exception as e2:
            print(f"保存基金数据到SQLite也失败: {e2}")
        return False

def save_fund_info_to_sqlite(info_dict):
    """保存基金信息到SQLite数据库（备选）"""
    fund_code = info_dict.get('基金代码')
    if not fund_code:
        return False

    try:
        import sqlite3
        conn = sqlite3.connect(SQLITE_DB_PATH)
        cursor = conn.cursor()

        cursor.execute('''
            INSERT OR REPLACE INTO fund_basic (
                fund_code, net_value, nav_date, day_growth, annual_return,
                annual_volatility, sharpe_ratio, calmar_ratio, max_drawdown,
                fund_manager, industry_ratio, fund_style, holdings_concentration,
                update_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            fund_code,
            info_dict.get('单位净值', ''),
            info_dict.get('净值日期', ''),
            info_dict.get('日增长率', ''),
            info_dict.get('年化收益率', ''),
            info_dict.get('年化波动率', ''),
            info_dict.get('夏普比率', ''),
            info_dict.get('卡玛比率', ''),
            info_dict.get('最大回撤', ''),
            info_dict.get('基金经理', ''),
            info_dict.get('行业占比', ''),
            info_dict.get('基金风格', ''),
            info_dict.get('持仓集中度', ''),
            datetime.now().isoformat()
        ))

        if '前十大持仓' in info_dict and info_dict['前十大持仓']:
            cursor.execute('DELETE FROM fund_holdings WHERE fund_code = ?', (fund_code,))
            for holding in info_dict['前十大持仓']:
                cursor.execute('''
                    INSERT INTO fund_holdings (fund_code, stock_code, stock_name, weight, sector_tag)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    fund_code,
                    holding.get('股票代码', ''),
                    holding.get('股票名称', ''),
                    holding.get('占净值比例', ''),
                    holding.get('细分行业', '')
                ))

        conn.commit()
        conn.close()
        print(f"基金 {fund_code} 数据已保存到SQLite")
        return True
    except Exception as e:
        print(f"保存基金数据到SQLite失败: {e}")
        return False

def get_fund_info_from_db(fund_code):
    """从MySQL数据库获取基金信息"""
    try:
        pool = get_mysql_pool()
        if pool is None:
            return get_fund_info_from_sqlite(fund_code)
        conn = pool.get_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM fund_basic WHERE fund_code = %s', (fund_code,))
        fund_row = cursor.fetchone()

        if not fund_row:
            conn.close()
            return None

        fund_name = get_fund_name(fund_code)

        cursor.execute('SELECT * FROM fund_holdings WHERE fund_code = %s ORDER BY id LIMIT 10', (fund_code,))
        holdings_rows = cursor.fetchall()
        top_industry, industry_desc = get_industry_from_holdings(holdings_rows)

        info_dict = {
            '基金代码': fund_row['fund_code'],
            '基金简称': fund_name,
            '单位净值': fund_row['net_value'],
            '净值日期': fund_row['nav_date'],
            '日增长率': fund_row['day_growth'],
            '年化收益率': fund_row['annual_return'],
            '年化波动率': fund_row['annual_volatility'],
            '夏普比率': fund_row['sharpe_ratio'],
            '卡玛比率': fund_row['calmar_ratio'],
            '最大回撤': fund_row['max_drawdown'],
            '基金经理': fund_row['fund_manager'],
            '第一大行业': top_industry,
            '行业占比': fund_row['industry_ratio'],
            '基金风格': fund_row['fund_style'],
            '风格描述': industry_desc,
            '持仓集中度': fund_row['holdings_concentration'],
            'update_time': fund_row['update_time'].isoformat() if fund_row.get('update_time') else None
        }

        if holdings_rows:
            holdings = [
                {'股票代码': row['stock_code'], '股票名称': row['stock_name'], '占净值比例': row['weight'], '细分行业': row.get('sector_tag', '')}
                for row in holdings_rows
            ]
            info_dict['前十大持仓'] = holdings
            top_industry, industry_desc = get_industry_from_holdings(holdings)

        conn.close()
        return info_dict
    except Exception as e:
        print(f"从MySQL获取基金数据失败: {e}")
        return get_fund_info_from_sqlite(fund_code)

def get_fund_info_from_sqlite(fund_code):
    """从SQLite数据库获取基金信息（备选）"""
    try:
        import sqlite3
        conn = sqlite3.connect(SQLITE_DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM fund_basic WHERE fund_code = ?', (fund_code,))
        fund_row = cursor.fetchone()

        if not fund_row:
            conn.close()
            return None

        fund_name = get_fund_name(fund_code)

        cursor.execute('SELECT * FROM fund_holdings WHERE fund_code = ? ORDER BY rowid LIMIT 10', (fund_code,))
        holdings_rows = cursor.fetchall()

        info_dict = {
            '基金代码': dict(fund_row)['fund_code'],
            '基金简称': fund_name,
            '单位净值': dict(fund_row)['net_value'],
            '净值日期': dict(fund_row)['nav_date'],
            '日增长率': dict(fund_row)['day_growth'],
            '年化收益率': dict(fund_row)['annual_return'],
            '年化波动率': dict(fund_row)['annual_volatility'],
            '夏普比率': dict(fund_row)['sharpe_ratio'],
            '卡玛比率': dict(fund_row)['calmar_ratio'],
            '最大回撤': dict(fund_row)['max_drawdown'],
            '基金经理': dict(fund_row)['fund_manager'],
            '行业占比': dict(fund_row)['industry_ratio'],
            '基金风格': dict(fund_row)['fund_style'],
            '持仓集中度': dict(fund_row)['holdings_concentration'],
            'update_time': dict(fund_row).get('update_time')
        }

        if holdings_rows:
            holdings = []
            for row in holdings_rows:
                row_dict = dict(row)
                holdings.append({
                    '股票代码': row_dict.get('stock_code', ''),
                    '股票名称': row_dict.get('stock_name', ''),
                    '占净值比例': row_dict.get('weight', ''),
                    '细分行业': row_dict.get('sector_tag', '')
                })
            info_dict['前十大持仓'] = holdings
            info_dict['第一大行业'], info_dict['风格描述'] = get_industry_from_holdings(holdings)
            
            # 从持仓数据计算持仓集中度和行业占比
            if holdings:
                total_weight = sum(float(h.get('占净值比例', '0').replace('%', '')) for h in holdings)
                info_dict['持仓集中度'] = f"{total_weight:.1f}%"
                
                # 计算行业占比（第一大行业）
                first_sector = info_dict.get('第一大行业', '')
                if first_sector and holdings:
                    sector_weight = sum(
                        float(h.get('占净值比例', '0').replace('%', ''))
                        for h in holdings if first_sector in str(h.get('细分行业', ''))
                    )
                    info_dict['行业占比'] = f"{sector_weight:.1f}%" if sector_weight > 0 else ''
            else:
                info_dict['持仓集中度'] = info_dict.get('持仓集中度') or ''
                info_dict['行业占比'] = info_dict.get('行业占比') or ''

        conn.close()
        return info_dict
    except Exception as e:
        print(f"从SQLite获取基金数据失败: {e}")
        return None

def needs_refresh(fund_code):
    """检查基金数据是否需要刷新（超过24小时）"""
    if fund_code not in fund_refresh_times:
        return True

    last_refresh = fund_refresh_times[fund_code]
    elapsed = datetime.now() - last_refresh
    return elapsed.total_seconds() > (FUND_DATA_REFRESH_HOURS * 3600)

def mark_refreshed(fund_code):
    """标记基金数据已刷新"""
    fund_refresh_times[fund_code] = datetime.now()

def get_industry_from_holdings(holdings_rows):
    """根据前10大重仓股的行业占比计算基金行业属性"""
    if not holdings_rows:
        return '综合', '投资于多个行业的混合基金'

    # 行业关键词映射（扩展版本）
    industry_keywords = {
        '制造': ['制造', '工业', '机械', '电气', '汽车', '新能源', '技术', '装备', '军工', '航天', '航空'],
        '科技': ['科技', '软件', '互联网', '通信', '电子', '半导体', '芯片', '5G', '人工智能', '云计算', '大数据', '网络安全'],
        '消费': ['消费', '食品', '饮料', '白酒', '啤酒', '酒', '家电', '纺织', '服装', '餐饮', '旅游', '酒店', '传媒', '娱乐', '零售', '商贸', '汾', '茅', '窖', '贡', '粮', '液', '缘', '舍得', '迎驾', '口子'],
        '医药': ['医药', '医疗', '生物', '健康', '中药', '疫苗', '医院', '器械', '制药', '化药', '生物药'],
        '金融': ['银行', '保险', '证券', '金融', '信托', '基金', '期货', '租凭'],
        '地产': ['地产', '房地产', '建筑', '建材', '物业', '园林', '装饰'],
        '能源': ['能源', '石油', '煤炭', '电力', '光伏', '风电', '水电', '核电', '燃气', '电池', '锂', '储能'],
    }

    # 统计各行业权重
    industry_weights = {}
    for row in holdings_rows:
        stock_name = str(row.get('stock_name', ''))
        weight = float(str(row.get('weight', '0')).replace('%', '')) if row.get('weight') else 0

        # 简单行业识别（基于股票名称关键词，优先级匹配）
        classified = False
        # 按优先级检查行业
        for industry, keywords in industry_keywords.items():
            for keyword in keywords:
                if keyword in stock_name:
                    industry_weights[industry] = industry_weights.get(industry, 0) + weight
                    classified = True
                    break
            if classified:
                break

        if not classified:
            industry_weights['其他'] = industry_weights.get('其他', 0) + weight

    if not industry_weights:
        return '综合', '投资于多个行业的混合基金'

    # 找出占比最高的行业
    top_industry = max(industry_weights, key=industry_weights.get)
    top_weight = industry_weights[top_industry]

    # 生成行业描述
    industry_descriptions = {
        '制造': f'先进制造 - 投资于制造业升级转型，关注中国制造2025相关企业，合计占比{top_weight:.1f}%',
        '科技': f'科技创新 - 投资于科技前沿领域，包括半导体、5G、软件等，合计占比{top_weight:.1f}%',
        '消费': f'消费升级 - 投资于消费行业龙头企业，合计占比{top_weight:.1f}%',
        '医药': f'医药健康 - 投资于医药医疗健康领域，合计占比{top_weight:.1f}%',
        '金融': f'金融地产 - 投资于金融和房地产板块，合计占比{top_weight:.1f}%',
        '地产': f'房地产 - 投资于房地产开发和相关产业链，合计占比{top_weight:.1f}%',
        '能源': f'新能源 - 投资于光伏、风电、锂电等清洁能源，合计占比{top_weight:.1f}%',
        '其他': f'综合配置 - 投资于多个行业分散配置，合计占比{top_weight:.1f}%',
    }

    return top_industry, industry_descriptions.get(top_industry, f'行业配置 - 合计占比{top_weight:.1f}%')

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/fund/info', methods=['GET'])
def get_fund_info():
    fund_code = request.args.get('fund_code', '').strip()

    if not fund_code:
        return jsonify({'error': '请输入基金代码'})

    cache_config = CACHE_CONFIG['fund_info']
    cache_key = generate_cache_key(cache_config['prefix'], fund_code)

    # 1. 先查Redis缓存
    cached_data = get_cache(cache_key)
    if cached_data:
        if not needs_refresh(fund_code):
            # Redis缓存快速路径：检查基金经理字段是否完整，缺失则异步补充（不阻塞主请求）
            if not cached_data.get('manager_details'):
                def _async_update_manager(code, data, ck, expiry):
                    try:
                        extra = fetch_manager_info_with_timeout(code, timeout=15)
                        if extra:
                            data.update(extra)
                            set_cache(ck, data, expiry)
                    except Exception:
                        pass
                threading.Thread(target=_async_update_manager,
                                 args=(fund_code, cached_data.copy(), cache_key, cache_config['expiry']),
                                 daemon=True).start()
            # 预生成分析报告缓存（后台，不阻塞）
            threading.Thread(target=_pregenerate_analysis_report, args=(fund_code, cached_data.copy()), daemon=True).start()
            return jsonify({
                'success': True,
                'data': cached_data,
                'from_cache': True,
                'source': 'redis_cache'
            })

    # 2. 缓存未命中，查MySQL数据库
    db_data = get_fund_info_from_db(fund_code)
    if db_data:
        # 从数据库读取后，异步补充基金经理扩展信息（不阻塞主请求）
        set_cache(cache_key, db_data, cache_config['expiry'])
        mark_refreshed(fund_code)

        if not needs_refresh(fund_code):
            # 快速路径：基金经理扩展字段缺失则异步补充（不阻塞）
            if not db_data.get('manager_details'):
                def _async_db_manager(code, data, ck, expiry):
                    try:
                        extra = fetch_manager_info_with_timeout(code, timeout=15)
                        if extra:
                            data.update(extra)
                            set_cache(ck, data, expiry)
                    except Exception:
                        pass
                threading.Thread(target=_async_db_manager,
                                 args=(fund_code, db_data.copy(), cache_key, cache_config['expiry']),
                                 daemon=True).start()
            # 预生成分析报告缓存（后台，不阻塞）
            threading.Thread(target=_pregenerate_analysis_report, args=(fund_code, db_data.copy()), daemon=True).start()
            return jsonify({
                'success': True,
                'data': db_data,
                'from_cache': True,
                'source': 'mysql_database'
            })
        threading.Thread(target=refresh_fund_data_background, args=(fund_code, db_data.copy()), daemon=True).start()
        threading.Thread(target=_pregenerate_analysis_report, args=(fund_code, db_data.copy()), daemon=True).start()
        return jsonify({
            'success': True,
            'data': db_data,
            'from_cache': True,
            'source': 'mysql_database',
            'refreshing': True
        })

    # 3. 数据库也没有，从天天基金网爬虫获取（~0.2s vs akshare 3-17s）
    try:
        data = crawl_fund_full(fund_code)
        if not data or not data.get('net_value'):
            return jsonify({'error': '未找到基金数据'})

        info_dict = {
            '基金代码': fund_code,
            '基金简称': data.get('fund_name', get_fund_name(fund_code)),
            '单位净值': data.get('net_value', ''),
            '净值日期': data.get('nav_date', ''),
            '日增长率': data.get('day_growth', '0%'),
            '年化收益率': data.get('annual_return', '0%'),
            '年化波动率': data.get('annual_volatility', '0%'),
            '夏普比率': data.get('sharpe_ratio', '0'),
            '卡玛比率': data.get('calmar_ratio', '0'),
            '最大回撤': data.get('max_drawdown', '0%'),
            '基金经理': data.get('基金经理', ''),
            '基金经理任职年限': data.get('基金经理任职年限') or data.get('manager_tenure', ''),
            '基金风格': data.get('基金风格', ''),
            '基金类型': data.get('基金类型', ''),
            '基金规模': data.get('基金规模', ''),
            '基金公司': data.get('基金公司', ''),
            '从业天数': data.get('从业天数', ''),
            '持仓集中度': '',
            '行业占比': '',
            '第一大行业': '',
            '风格描述': '',
        }
        # 行业配置
        first_industry = data.get('第一大行业', '')
        info_dict['第一大行业'] = first_industry
        if first_industry and first_industry not in ('股票占净比', '债券占净比'):
            info_dict['第一行业'] = first_industry
        # 持仓信息
        holdings_raw = data.get('前十大持仓', [])
        if holdings_raw:
            top10 = []
            total_weight = 0.0
            for h in holdings_raw[:10]:
                weight_str = h.get('占净值比例', '0%')
                weight = float(str(weight_str).replace('%', ''))
                total_weight += weight
                top10.append({
                    '股票代码': h.get('股票代码', ''),
                    '股票名称': h.get('股票名称', ''),
                    '占净值比例': weight_str,
                    '细分行业': h.get('细分行业', ''),
                })
            info_dict['前十大持仓'] = top10
            info_dict['持仓集中度'] = f"{total_weight:.1f}%"
            # 行业占比：用第一大行业的持仓占比
            if first_industry:
                sector_weight = sum(
                    float(str(h.get('占净值比例', '0%')).replace('%', ''))
                    for h in holdings_raw[:10] if first_industry in str(h.get('细分行业', ''))
                )
                info_dict['行业占比'] = f"{sector_weight:.1f}%" if sector_weight > 0 else ''
            # 风格描述
            _, style_desc = get_industry_from_holdings([
                {'stock_name': h.get('股票名称', ''), 'weight': h.get('占净值比例', '0%')}
                for h in holdings_raw[:10]
            ])
            info_dict['风格描述'] = style_desc
        # crawl_fund_full 已包含：净值/经理/持仓/行业/风格，无需再调ThreadPoolExecutor

        set_cache(cache_key, info_dict, cache_config['expiry'])
        save_fund_info_to_db(info_dict)
        mark_refreshed(fund_code)

        # 预生成分析报告缓存（后台，不阻塞）
        threading.Thread(target=_pregenerate_analysis_report, args=(fund_code, info_dict.copy()), daemon=True).start()

        return jsonify({
            'success': True,
            'data': info_dict,
            'from_cache': False,
            'source': 'crawler'
        })
    except Exception as e:
        print(f"获取基金信息失败: {e}")
        return jsonify({'error': f'获取基金信息失败: {str(e)}'})

def refresh_fund_data_background(fund_code, old_data):
    """后台刷新基金数据（天天基金网爬虫，~0.2s）"""
    with refresh_lock:
        try:
            data = crawl_fund_full(fund_code)
            if not data or not data.get('net_value'):
                return

            info_dict = {
                '基金代码': fund_code,
                '基金简称': data.get('fund_name', get_fund_name(fund_code)),
                '单位净值': data.get('net_value', ''),
                '净值日期': data.get('nav_date', ''),
                '日增长率': data.get('day_growth', '0%'),
                '年化收益率': data.get('annual_return', '0%'),
                '年化波动率': data.get('annual_volatility', '0%'),
                '夏普比率': data.get('sharpe_ratio', '0'),
                '卡玛比率': data.get('calmar_ratio', '0'),
                '最大回撤': data.get('max_drawdown', '0%'),
                '基金经理': data.get('基金经理', ''),
                '基金经理任职年限': data.get('基金经理任职年限') or data.get('manager_tenure', ''),
            }
            first_industry = data.get('第一大行业', '')
            if first_industry and first_industry not in ('股票占净比', '债券占净比'):
                info_dict['第一行业'] = first_industry
            holdings_raw = data.get('前十大持仓', [])
            if holdings_raw:
                top10 = []
                for h in holdings_raw[:10]:
                    top10.append({
                        '股票代码': h.get('股票代码', ''),
                        '股票名称': h.get('股票名称', ''),
                        '占净值比例': h.get('占净值比例', ''),
                        '细分行业': h.get('细分行业', ''),
                    })
                info_dict['前十大持仓'] = top10

            cache_key = generate_cache_key(CACHE_CONFIG['fund_info']['prefix'], fund_code)
            set_cache(cache_key, info_dict, CACHE_CONFIG['fund_info']['expiry'])
            save_fund_info_to_db(info_dict)
            mark_refreshed(fund_code)
            print(f"基金 {fund_code} 后台刷新完成（crawler）")
        except Exception as e:
            print(f"后台刷新基金 {fund_code} 失败: {e}")
def _get_akshare_timeout():
    """获取akshare操作全局超时时间（秒）"""
    return 15


def _akshare_with_timeout(func, *args, timeout=None, **kwargs):
    """用ThreadPoolExecutor给任意akshare函数加全局超时，永不永久挂起"""
    if timeout is None:
        timeout = _get_akshare_timeout()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(func, *args, **kwargs)
        return future.result(timeout=timeout)

def fetch_manager_info_with_timeout(fund_code, timeout=None):
    """获取基金经理详细信息（并发抓取，str.find 替代回溯正则，<2s）"""
    try:
        import concurrent.futures
        from fund_crawler import crawl_manager_fund_list

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            f_basic = executor.submit(crawl_fund_full, fund_code)
            f_manager = executor.submit(crawl_manager_fund_list, fund_code)

            basic_data = f_basic.result(timeout=8)
            manager_data = f_manager.result(timeout=5)

        if not basic_data:
            return None

        mgr = manager_data or {}
        return {
            '基金经理': mgr.get('基金经理') or basic_data.get('基金经理', ''),
            '基金经理公司': mgr.get('基金经理公司') or basic_data.get('基金经理公司', ''),
            '基金经理任职年限': mgr.get('基金经理任职年限') or basic_data.get('基金经理任职年限', ''),
            '基金经理星级': '',
            '管理基金规模': mgr.get('管理基金规模') or basic_data.get('管理规模', ''),
            '基金经理评分': '',
            '管理基金数量': mgr.get('管理基金数量', 0),
            '最佳回报率': mgr.get('最佳回报率', ''),
            'manager_details': mgr.get('manager_details', []),
        }
    except Exception as e:
        print(f"fetch_manager_info_with_timeout失败: {e}")
        try:
            data = crawl_fund_full(fund_code)
            if data:
                return {
                    '基金经理': data.get('基金经理', ''),
                    '基金经理公司': data.get('基金经理公司', ''),
                    '基金经理任职年限': data.get('基金经理任职年限', ''),
                    '基金经理星级': '',
                    '管理基金规模': data.get('管理规模', ''),
                    '基金经理评分': '',
                    '管理基金数量': 0,
                    '最佳回报率': '',
                    'manager_details': [],
                }
        except Exception:
            pass
        return None

TIER1_COMPANIES = ['易方达', '华夏', '广发', '汇添富', '富国', '南方', '嘉实', '招商', '工银', '中欧', '兴全', '景顺', '博时', '华安', '鹏华', '平安', '交银']
TIER2_COMPANIES = ['银华', '天弘', '国泰', '建信', '农银', '泓德', '国投瑞银', '光大', '上投摩根', '华泰柏瑞', '长信', '东方', '万家', '华商', '金鹰', '宝盈', '前海开源', '财通', '浙商', '诺安', '国联安', '泰康', '太平', '人保', '中信保诚', '长城', '金元顺安', '民生加银', '浦银安盛', '中银', '融通']

def _score_manager_company(company_name):
    """公司背景评分（15分）"""
    if not company_name:
        return 6, '未知'
    for t1 in TIER1_COMPANIES:
        if t1 in company_name:
            return 15, '一线知名'
    for t2 in TIER2_COMPANIES:
        if t2 in company_name:
            return 12, '二线知名'
    return 6, '其他'

def _score_manager_experience(days):
    """从业年限评分（20分）"""
    if not days or days <= 0:
        return 4, '新手'
    years = days / 365
    if years < 2:
        return 6, '新手'
    elif years < 4:
        return 12, '进阶'
    elif years < 7:
        return 16, '成熟'
    else:
        return 20, '资深'

def _score_manager_performance(best_return):
    """业绩表现评分（30分）"""
    if best_return is None or best_return == '未知' or (isinstance(best_return, float) and (best_return != best_return)):
        return 10, '未知'
    try:
        r = float(str(best_return).replace('%', ''))
    except:
        return 10, '未知'
    if r >= 150:
        return 30, '卓越'
    elif r >= 100:
        return 26, '优秀'
    elif r >= 60:
        return 22, '良好'
    elif r >= 30:
        return 16, '一般'
    elif r >= 0:
        return 10, '偏弱'
    else:
        return 6, '亏损'

def _score_manager_scale(total_scale_str):
    """管理规模评分（20分）"""
    if not total_scale_str or total_scale_str == '未知':
        return 8, '未知'
    try:
        scale = float(str(total_scale_str).replace('亿', '').strip())
    except:
        return 8, '未知'
    if scale >= 500:
        return 20, '超大'
    elif scale >= 200:
        return 16, '大'
    elif scale >= 50:
        return 12, '中'
    elif scale >= 10:
        return 8, '小'
    else:
        return 5, '微型'

def _score_manager_stability(fund_count, manager_details):
    """团队稳定性评分（15分）——基于管理基金数量"""
    if not manager_details:
        return 6, '一般'
    if fund_count >= 5:
        return 15, '优秀'
    elif fund_count >= 3:
        return 12, '良好'
    elif fund_count >= 2:
        return 9, '一般'
    else:
        return 7, '新手'

def generate_manager_evaluation(data):
    """根据基金经理数据生成评估报告"""
    if not data:
        return None

    manager_name = data.get('基金经理', '')
    company = data.get('基金经理公司', '')
    # 从 "6年114天" 格式提取天数
    tenure_str = data.get('基金经理任职年限', '')
    import re as _re
    days = 0
    years_match = _re.match(r'(\d+)年(\d+)天', str(tenure_str))
    if years_match:
        days = int(years_match.group(1)) * 365 + int(years_match.group(2))
    else:
        days = data.get('从业天数', 0)
    fund_count = data.get('管理基金数量', 0)
    total_scale_str = data.get('管理基金规模') or data.get('管理基金总规模', '未知')
    best_return = data.get('最佳回报率', '未知')
    details = data.get('manager_details', [])

    # 各维度评分
    exp_score, exp_level = _score_manager_experience(days)
    perf_score, perf_level = _score_manager_performance(best_return)
    scale_score, scale_level = _score_manager_scale(total_scale_str)
    comp_score, comp_level = _score_manager_company(company)
    stab_score, stab_level = _score_manager_stability(fund_count, details)

    total_score = exp_score + perf_score + scale_score + comp_score + stab_score

    # 综合评述
    years_str = data.get('基金经理任职年限') or data.get('从业年限', '未知')
    summary_parts = []
    summary_parts.append(f"该基金经理为{manager_name}，任职{years_str}，目前管理{fund_count}只基金。")
    if fund_count > 0:
        summary_parts.append(f"合计管理规模{total_scale_str}，最佳回报率为{best_return}。")
    else:
        summary_parts.append("目前暂无在管基金信息。")
    summary_parts.append(f"所在公司{company}为{comp_level}基金公司，整体投研实力较强。")
    summary_parts.append(f"从业稳定性评级为{stab_level}，{'基金经理管理经验丰富，团队配置成熟' if stab_score >= 12 else '基金经理处于成长期，需关注团队稳定性'}。")
    summary = "".join(summary_parts)

    # 优势
    advantages = []
    if comp_score >= 15:
        advantages.append(f"所属{company}，为{comp_level}基金公司，平台投研实力雄厚")
    if exp_score >= 16:
        advantages.append(f"从业{years_str}，投资经验丰富，应对市场周期能力强")
    if best_return and best_return != '未知':
        try:
            r = float(str(best_return).replace('%', ''))
            if r >= 60:
                advantages.append(f"历史最佳回报率{best_return}，业绩表现{perf_level}，具备显著超额收益能力")
        except:
            pass
    if scale_score >= 12:
        advantages.append(f"管理总规模{total_scale_str}，市场影响力较强，流动性管理压力大但资源充足")
    if len(advantages) < 2:
        advantages.append("投资策略有一定特色，风格灵活")
    if len(advantages) < 2:
        advantages.append("近期业绩呈现增长趋势，可保持关注")

    # 风险提示
    risks = []
    if exp_score < 12:
        risks.append(f"从业年限较短（{years_str}），投资经验有待更多市场周期验证")
    if best_return and best_return != '未知':
        try:
            r = float(str(best_return).replace('%', ''))
            if r < 0:
                risks.append("历史回报率为负，需关注本金亏损风险")
            elif r < 30:
                risks.append(f"最佳回报率{best_return}相对有限，超额收益能力一般")
        except:
            pass
    if fund_count >= 5:
        risks.append(f"同时管理{fund_count}只基金，精力分散风险较高，需关注是否真正参与所有产品管理")
    if comp_score < 12:
        risks.append(f"所在公司{company}品牌影响力一般，投研资源相对有限")
    if len(risks) < 2:
        risks.append("市场系统性风险难以预测，需关注极端行情下的回撤控制能力")
    if len(risks) < 2:
        risks.append("基金过往业绩不代表未来表现，投资需谨慎")

    # 雷达图数据
    radar_labels = ['从业年限', '业绩表现', '管理规模', '公司背景', '团队稳定性']
    radar_scores = [exp_score, perf_score, scale_score, comp_score, stab_score]
    radar_levels = [exp_level, perf_level, scale_level, comp_level, stab_level]

    trust_index = total_score  # 信赖指数 = 综合评分（满分100）
    trust_level = 'A' if trust_index >= 80 else 'B' if trust_index >= 60 else 'C' if trust_index >= 40 else 'D'
    return {
        '综合评分': total_score,
        '综合评级': '优秀' if total_score >= 80 else '良好' if total_score >= 60 else '一般' if total_score >= 40 else '较差',
        '信赖指数': trust_index,
        '信赖等级': trust_level,
        '各项评分': {
            '从业年限': {'得分': exp_score, '满分': 20, '等级': exp_level, 'label': '从业年限'},
            '业绩表现': {'得分': perf_score, '满分': 30, '等级': perf_level, 'label': '业绩表现'},
            '管理规模': {'得分': scale_score, '满分': 20, '等级': scale_level, 'label': '管理规模'},
            '公司背景': {'得分': comp_score, '满分': 15, '等级': comp_level, 'label': '公司背景'},
            '团队稳定性': {'得分': stab_score, '满分': 15, '等级': stab_level, 'label': '团队稳定性'},
        },
        '综合评述': summary,
        '优势': advantages[:4],
        '风险提示': risks[:4],
        '雷达图': {
            'labels': radar_labels,
            'scores': radar_scores,
            'levels': radar_levels,
        }
    }

def fetch_industry_info(fund_code):
    """获取行业配置信息（从crawl_fund_full缓存数据，无额外网络请求）"""
    try:
        cache_key = generate_cache_key(CACHE_CONFIG['fund_info']['prefix'], fund_code)
        cached = get_cache(cache_key)
        if cached and cached.get('第一行业'):
            return {
                '第一大行业': cached.get('第一行业', ''),
                '行业占比': cached.get('行业占比', ''),
                '基金风格': cached.get('基金风格', ''),
                '风格描述': cached.get('风格描述', '')
            }
        # 无缓存时从爬虫获取
        data = crawl_fund_full(fund_code)
        if data:
            return {
                '第一大行业': data.get('first_industry', ''),
                '行业占比': data.get('industry_ratio', ''),
                '基金风格': data.get('fund_style', ''),
                '风格描述': data.get('style_description', '')
            }
    except Exception as e:
        print(f"获取行业配置信息失败: {e}")
    return {}

STOCK_SECTOR_TAGS = {
    # 白酒
    '贵州茅台': '高端白酒 / 酱香型绝对龙头',
    '五粮液': '高端浓香白酒 / 次高端龙头',
    '泸州老窖': '高端浓香白酒 / 历史名酒',
    '山西汾酒': '清香型白酒 / 全国化扩张中',
    '洋河股份': '绵柔浓香白酒 / 苏酒龙头',
    '古井贡酒': '年份原浆 / 徽酒龙头',
    '今世缘': '江苏区域白酒 / 婚宴市场',
    '口子窖': '安徽白酒 / 兼香型代表',
    '舍得酒业': '川酒六朵金花 / 老酒概念',
    '水井坊': '四川成都 / 外资控股名酒',
    '酒鬼酒': '湘酒之王 / 馥郁香型',
    '迎驾贡酒': '安徽六安 / 生态洞藏系列',
    '金徽酒': '西北白酒 / 陇酒龙头',
    # 啤酒
    '青岛啤酒': '啤酒 / 国内三强',
    '重庆啤酒': '啤酒 / 嘉士伯控股',
    '华润啤酒': '啤酒 / 国内份额第一',
    '燕京啤酒': '啤酒 / 北京区域龙头',
    # 医药
    '恒瑞医药': '创新药 / 国内药企研发一哥',
    '药明康德': 'CXO医药外包 / 全球龙头',
    '药明生物': '生物药CDMO / 全球领先',
    '泰格医药': '临床CRO / 国内龙头',
    '凯莱英': '小分子CDMO / 辉瑞新冠药供应商',
    '康龙化成': '实验室服务 / 全球布局',
    '爱尔眼科': '专科眼科医院 / 连锁龙头',
    '通策医疗': '口腔连锁医院 / 杭口集团',
    '爱博医疗': '眼科高值耗材 / 人工晶体',
    '欧普康视': '角膜塑形镜 / 近视防控',
    '片仔癀': '中药保密配方 / 肝病神药',
    '云南白药': '中药保密配方 / 伤科圣药',
    '同仁堂': '老字号中药 / 安宫牛黄丸',
    '华润三九': '中药OTC / 品牌中药',
    '以岭药业': '中药创新药 / 连花清瘟',
    '步长制药': '心脑血管中药 / 独家品种',
    '天士力': '中药现代化 / 生物药布局',
    '我武生物': '脱敏治疗 / 粉尘螨滴剂',
    '长春高新': '生长激素 / 垄断地位',
    '智飞生物': '疫苗代理+自研 / 默沙东独家',
    '沃森生物': 'mRNA疫苗 / 新冠布局',
    '康泰生物': '疫苗 / 乙肝疫苗龙头',
    '华兰生物': '血制品+疫苗 / 河南龙头',
    '山东药玻': '药用玻璃 / 中性硼硅管',
    '九洲药业': '原料药+CDMO / 诺华合作',
    '司太立': '造影剂原料药 / 碘海醇',
    '奥泰生物': '体外诊断 / 毒品检测',
    '诺唯赞': '生物试剂 / 分子酶技术',
    '义翘神州': '重组蛋白 / 病毒类蛋白',
    # 半导体/芯片
    '中芯国际': '晶圆代工 / 国内最先进制程',
    '华虹半导体': '晶圆代工 / 特色工艺',
    '北方华创': '半导体设备 / 国产替代龙头',
    '中微公司': '刻蚀机 / 5nm先进刻蚀',
    '华润微': '功率半导体 / IDM模式',
    '斯达半导': 'IGBT模块 / 新能源汽车',
    '士兰微': '功率半导体 / IDM全产业链',
    '韦尔股份': 'CIS图像传感器 / 全球前三',
    '卓胜微': '射频前端芯片 / 5G核心器件',
    '圣邦股份': '模拟芯片 / 信号链龙头',
    '思瑞浦': '模拟芯片 / 信号链专家',
    '纳芯微': '车规级模拟芯片 / 国产替代',
    '帝奥微': '模拟芯片 / 精密运算放大器',
    '芯朋微': 'AC-DC芯片 / 快充方案',
    '晶方科技': '先进封装 / TSV技术',
    '通富微电': '封装测试 / AMD大客户',
    '长电科技': '封装测试 / 全球第三',
    '华天科技': '封装测试 / 国内前三',
    '深科技': '存储封装 / 先进封装',
    '兆易创新': 'MCU+NorFlash / 国产替代',
    '北京君正': '车规级存储 / ISSI品牌',
    '澜起科技': '内存接口芯片 / DDR5升级',
    '聚辰股份': 'EEPROM / 摄像头模组',
    '安集科技': 'CMP抛光液 / 国产替代',
    '鼎龙股份': 'CMP抛光垫 / 打破垄断',
    '沪硅产业': '大硅片 / 12英寸量产',
    '立昂微': '硅片+射频芯片 / 全产业链',
    '神工股份': '刻蚀用硅电极 / 晶圆刻蚀',
    '和有微': '蓝牙音频芯片 / 物联网',
    # 光伏/新能源
    '隆基绿能': '光伏一体化 / 组件出货第一',
    '通威股份': '硅料+电池片 / 成本最低',
    '晶澳科技': '光伏组件 / 全球TOP3',
    '天合光能': '光伏组件+支架 / 全球化',
    '晶盛机电': '光伏硅片设备 / 订单充足',
    '迈为股份': 'HJT电池设备 / 技术路线',
    '奥特维': '光伏组件设备 / 串焊机龙头',
    '高测股份': '金刚线切割 / 切片代工',
    '双良节能': '硅片+换热器 / 节能设备',
    'TCL中环': '光伏硅片 / 210mm大硅片',
    '福斯特': '光伏胶膜 / 全球市占过半',
    '福莱特': '光伏玻璃 / 双寡头之一',
    '信义光能': '光伏玻璃 / 行业龙头',
    '阳光电源': '光伏逆变器 / 全球第一',
    '锦浪科技': '组串式逆变器 / 海外占比高',
    '固德威': '户用逆变器 / 欧洲市场',
    '德业股份': '储能逆变器 / 微逆布局',
    '上能电气': '集中式逆变器 / 大电站',
    '禾迈股份': '微型逆变器 / MLPE方案',
    '昱能科技': '微型逆变器 / 组件级关断',
    '阿特斯': '光伏组件 / 美股回A',
    '金博股份': '碳基复合材料 / 热场龙头',
    '天宜上佳': '碳基复合材料 / 制动闸片',
    '岱勒新材': '金刚线 / 钨丝线研发',
    # 锂电/电池
    '宁德时代': '动力电池 / 全球市占第一',
    '比亚迪': '锂电池+整车 / 垂直整合',
    '亿纬锂能': '锂电池 / 全技术路线',
    '国轩高科': '动力电池 / 大众合作',
    '欣旺达': '消费电池PACK / 动力切入',
    '珠海冠宇': '消费电芯 / 聚合物锂电',
    '鹏辉能源': '储能电池 / 户储切入',
    '派能科技': '储能电池系统 / 欧洲户储',
    '南都电源': '铅酸+储能 / 老牌电池',
    '废旧电池': '电池回收 / 循环利用',
    '赣锋锂业': '锂资源 / 盐湖+矿石',
    '天齐锂业': '锂资源 / 全球最大锂辉石',
    '盐湖股份': '盐湖提锂 / 钾肥+碳酸锂',
    '藏格矿业': '盐湖钾锂 / 青海矿权',
    '雅化集团': '锂盐 / 氢氧化锂',
    '中矿资源': '铯铷盐+锂盐 / 海外矿',
    '盛新锂能': '锂盐 / 锂辉石项目',
    '融捷股份': '锂矿+锂盐 / 甲基卡矿',
    '江特电机': '锂云母提锂 / 江西宜春',
    '永兴材料': '云母提锂+特钢 / 双主业',
    '西藏矿业': '盐湖提锂 / 扎布耶盐湖',
    '西藏城投': '盐湖提锂 / 结则茶卡',
    '龙蟠科技': '磷酸铁锂 / 收购贝特瑞',
    '德方纳米': '磷酸铁锂正极 / 液相法',
    '湖南裕能': '磷酸铁锂正极 / 液相法',
    '富临精工': '磷酸铁锂 / 草酸亚铁',
    '万润新能': '磷酸铁锂 / A股上市',
    '湖北宜化': '磷酸铁锂 / 磷化工切入',
    '中伟股份': '前驱体 / 三元前驱体龙头',
    '格林美': '前驱体+回收 / 三元前驱体',
    '华友钴业': '钴+三元前驱体 / 资源布局',
    '寒锐钴业': '钴铜矿 / 刚果金布局',
    '洛阳钼业': '铜钴+铌磷 / 全球矿业',
    '腾远钴业': '钴盐 / 硫酸钴龙头',
    '科达利': '电池结构件 / 宁德时代主供',
    '震裕科技': '电机铁芯 / 精密冲压',
    '恩捷股份': '湿法隔膜 / 全球市占过半',
    '星源材质': '干法隔膜 / 湿法切入',
    '中材科技': '隔膜+风电叶片 / 央企背景',
    '沧州明珠': 'PE管道+隔膜 / 传统业务',
    '铜陵有色': '铜箔 / 锂电铜箔扩张',
    '诺德股份': '锂电铜箔 / 极薄化趋势',
    '嘉元科技': '锂电铜箔 / 6μm龙头',
    '超华科技': '铜箔+覆铜板 / PCB全产业链',
    '壹石通': '勃姆石 / 涂覆材料',
    '天奈科技': '碳纳米管导电剂 / 碳管龙头',
    '道氏技术': '碳纳米管导电剂 / 三元前驱体',
    '黑猫股份': '碳纳米管导电剂 / 炭黑切入',
    '多氟多': '六氟磷酸锂 / 电解液龙头',
    '天赐材料': '电解液+六氟 / 一体化',
    '新宙邦': '电解液 / 海外客户占高',
    '永太科技': '含氟精细化工 / 钠电电解液',
    '瑞泰新材': '电解液添加剂 / LG主供',
    # 消费电子
    '立讯精密': '消费电子代工 / 苹果核心',
    '歌尔股份': '声学元器件 / AirPods供应商',
    '蓝思科技': '玻璃盖板 / 苹果+特斯拉',
    '工业富联': '电子代工 / AI服务器',
    '鹏鼎控股': 'PCB / 苹果软板主供',
    '东山精密': '精密制造 / 软板+通信',
    '京东方A': '面板 / LCD全球第一',
    'TCL科技': '面板 / LCD+硅基OLED',
    '深天马A': '中小尺寸面板 / 车载屏',
    '维信诺': 'OLED面板 / 屏下摄像',
    '三安光电': 'LED芯片+三代半 / 化合物半导体',
    '兆驰股份': 'LED封装+电视ODM / 江西基地',
    '利亚德': 'LED显示 / MicroLED布局',
    '洲明科技': 'LED显示 / 智慧城市',
    '艾比森': 'LED显示屏 / 海外市场',
    '长阳科技': '光学反射膜 / 面板材料',
    '激智科技': '光学膜 / 扩散膜龙头',
    '斯迪克': '功能性涂层材料 / OCA光学胶',
    '安克创新': '消费电子品牌 / 亚马逊渠道',
    '石头科技': '扫地机器人 / 小米生态链',
    '科沃斯': '服务机器人 / 添可品牌',
    '极米科技': '投影仪 / 国内投影龙头',
    # 新能源汽车
    '长城汽车': '整车 / 哈佛+欧拉+坦克',
    '吉利汽车': '整车 / 极氪+领克',
    '理想汽车': '造车新势力 / 增程式SUV',
    '蔚来汽车': '造车新势力 / BaaS换电',
    '小鹏汽车': '造车新势力 / XNGP智驾',
    '广汽集团': '整车+电池 / 埃安+丰田',
    '上汽集团': '整车 / 智己+五菱',
    '长安汽车': '整车 / 阿维塔+深蓝',
    '北汽蓝谷': '极狐ARCFOX / 华为合作',
    '小康股份': '赛力斯SF5 / 华为智选',
    '赛力斯': 'AITO问界 / 华为深度合作',
    '江淮汽车': '代工蔚来+思皓 / 轻卡',
    '海马汽车': '海南本地 / 代工+氢能源',
    '宇通客车': '新能源客车 / 龙头地位',
    '中通客车': '新能源客车 / 山东聊城',
    '比亚迪电子': '电子代工 / 苹果+汽车电子',
    '拓普集团': '汽车零部件 / 轻量化+热管理',
    '德赛西威': '智能座舱+智驾 / 英伟达方案',
    '华阳集团': '汽车电子 / HUD抬头显示',
    '均胜电子': '汽车安全 / 全球并购',
    '保隆科技': '汽车传感器 / ADAS布局',
    '伯特利': '线控制动 / 国产替代',
    '万安科技': '底盘制动 / ABS供应商',
    '万润股份': '环保材料+尾气处理 / 沸石分子筛',
    # 金融
    '中国平安': '综合金融+保险 / A+H股',
    '中国太保': '保险 / 太保寿险+产险',
    '中国人寿': '寿险 / 行业龙头',
    '新华保险': '寿险 / 银保渠道强',
    '中国人保': '保险 / 财险为基',
    '招商银行': '零售银行 / 银行标杆',
    '宁波银行': '城商行 / 资产质量最优',
    '杭州银行': '城商行 / 区域经济强',
    '成都银行': '城商行 / 成渝双城',
    '江苏银行': '城商行 / 资产规模领先',
    '南京银行': '城商行 / 债购基金主力',
    '兴业银行': '股份行 / 银银平台',
    '中信证券': '券商 / 投行龙头',
    '中金公司': '券商 / 国际化投行',
    '华泰证券': '券商 / 财富管理转型',
    '国泰君安': '券商 / 机构业务强',
    '招商证券': '券商 / 研究实力强',
    '东方证券': '券商 / 资管业务强',
    '中信建投': '券商 / 科创板保荐最多',
    '中国中免': '免税零售 / 牌照垄断',
    '华致酒行': '酒类流通 / 保真连锁',
    # 互联网/科技
    '腾讯控股': '互联网社交+游戏 / 港股龙头',
    '阿里巴巴': '电商+云计算 / 中概股航母',
    '美团-W': '本地生活 / 外卖龙头',
    '京东集团-SW': '电商+物流 / 自营模式',
    '拼多多': '电商 / 低价下沉市场',
    '网易-S': '游戏 / 研发实力强',
    '百度集团-SW': 'AI+搜索 / 文心一言',
    '快手-W': '短视频 / 直播电商',
    '哔哩哔哩-SW': '中长视频 / Z世代社区',
    '小米集团-W': '手机+IoT / 新能源汽车',
    '小米': '手机+IoT / 新能源汽车',
    '海康威视': '安防监控 / AI视觉龙头',
    '科大讯飞': 'AI语音 / 大模型布局',
    '三六零': '网络安全 / 浏览器+搜索',
    '金山办公': '办公软件 / WPS会员',
    '用友网络': '企业软件 / 云ERP',
    '宝信软件': '工业软件 / 宝武集团背景',
    '海光信息': 'CPU芯片 / x86服务器处理器',
    '寒武纪': 'AI芯片 / 神经网络处理器NPU',
    '中科曙光': '服务器+超算 / 海光CPU',
    '浪潮信息': '服务器 / Intel方案',
    '紫光股份': '网络设备+云 / 新华三',
    '中兴通讯': '通信设备 / 5G基站',
    '亨通光电': '光纤光缆 / 海缆业务',
    '中天科技': '光纤+海缆 / 新能源布局',
    '长飞光纤': '光纤预制棒 / 行业龙头',
    '移远通信': '物联网模组 / 全球第一',
    '广和通': '物联网模组 / 笔电+车联网',
    '有方科技': '物联网通信 / 智能电网',
    '中国联通': '运营商 / 混改标杆',
    '中国电信': '运营商 / 回A上市',
    '光环新网': 'IDC / 亚马逊AWS合作',
    '宝信软件': 'IDC+工业软件 / 宝武集团',
    '数据港': 'IDC / 阿里+字节客户',
    '奥飞数据': 'IDC / 华南布局',
    '新易盛': '光模块 / 5G光通信',
    '中际旭创': '光模块 / 数通市场',
    '剑桥科技': '光模块 / 微软合作',
    # 房地产/基建/建材
    '万科A': '房地产开发 / 行业优等生',
    '保利发展': '房地产开发 / 央企背景',
    '招商蛇口': '房地产开发 / 园区运营',
    '金地集团': '房地产开发 / 稳健经营',
    '龙湖集团': '房地产开发 / 民营优等生',
    '滨江集团': '杭州地产 / 区域龙头',
    '华发股份': '珠海地产 / 湾区布局',
    '建发股份': '供应链+地产 / 厦门国企',
    '海螺水泥': '水泥 / 华东+中南龙头',
    '华新水泥': '水泥 / 西南+海外',
    '天山股份': '水泥 / 西北+华北',
    '祁连山': '水泥 / 甘青藏区域',
    '宁夏建材': '水泥 / 宁夏市场',
    '北新建材': '石膏板+防水 / 垄断地位',
    '东方雨虹': '防水材料 / 建筑建材龙头',
    '科顺股份': '防水材料 / 行业第二',
    '凯伦股份': '防水材料 / 高分子卷材',
    '三棵树': '涂料 / 建筑涂料龙头',
    '亚士创能': '保温装饰板 / 功能型建筑涂料',
    '伟星新材': 'PPR管道 / 隐蔽工程专家',
    '中国巨石': '玻纤 / 全球产能第一',
    '中材科技': '风电叶片+玻纤 / 央企背景',
    '旗滨集团': '浮法玻璃+光伏玻璃 / 节能玻璃',
    '南玻A': '浮法玻璃+光伏 / 电子玻璃',
    '福耀玻璃': '汽车玻璃 / 全球份额领先',
    '坚朗五金': '建筑五金 / 门窗配件集成',
    '顶固集创': '定制家居 / 品牌定制',
    # 化工/材料
    '万华化学': 'MDI+石化 / 化工龙头',
    '华鲁恒升': '煤化工 / 醋酸+DMF',
    '宝丰能源': '煤化工 / 绿氢布局',
    '鲁西化工': '煤化工+新材料 / 园区化',
    '卫星化学': '丙烯酸+乙烯 / C2/C3龙头',
    '荣盛石化': '炼化+化工 / PX-PTA链',
    '恒力石化': '炼化+化工 / 全球最大PTA',
    '东方盛虹': '炼化+光伏EVA / 斯尔邦',
    '桐昆股份': '涤纶长丝 / 行业龙头',
    '新凤鸣': '涤纶长丝 / 嘉兴基地',
    '恒逸石化': '涤纶+锦纶 / 文莱炼化',
    '三友化工': '粘胶短纤+氯碱 / 循环经济',
    '华峰化学': '氨纶+聚氨酯 / 行业龙头',
    '泰和新材': '氨纶+芳纶 / 防护纤维',
    '万润股份': '功能性材料 / 沸石分子筛',
    '奥来德': 'OLED材料 / 蒸发源设备',
    '濮阳惠成': '顺酐酸酐衍生物 / 变压器绝缘',
    '康达新材': '胶粘剂 / 风电叶片胶',
    '回天新材': '胶粘剂 / 太阳能背板胶',
    '天赐材料': '电解液+日化 / 磷化工延伸',
    '石大胜华': '电解液溶剂 / DMC龙头',
    '华鲁恒升': '煤化工 / 醋酸+DMF',
    '兴发集团': '磷化工+湿电子 / 磷矿资源',
    '云天化': '磷化工 / 磷矿+氮肥',
    '湖北宜化': '磷化工 / 磷酸二铵龙头',
    '川发龙蟒': '磷化工 / 工业级磷酸一铵',
    '六国化工': '磷复肥 / 华东市场',
    '川恒股份': '磷化工 / 饲料级磷酸氢钙',
    '司尔特': '磷复合肥 / 硫铁矿制酸',
    '金诚信': '矿山服务 / 有色金属',
    # 农业/养殖
    '海大集团': '饲料+养殖 / 水产饲料龙头',
    '新希望': '饲料+猪养殖 / 农牧龙头',
    '温氏股份': '肉鸡+肉猪养殖 / 行业龙头',
    '牧原股份': '生猪养殖 / 成本最低',
    '正邦科技': '生猪养殖 / 饲料+屠宰',
    '天康生物': '饲料+疫苗 / 新疆区域',
    '天邦食品': '饲料+养殖 / 食品转型',
    '傲农生物': '饲料+养殖 / 福建区域',
    '巨星农牧': '饲料+养殖 / 四川区域',
    '唐人神': '饲料+养殖 / 湖南区域',
    '中牧股份': '动物疫苗 / 政府招采',
    '生物股份': '动物疫苗 / 口蹄疫龙头',
    '普莱柯': '动物疫苗 / 产学研结合',
    '科前生物': '动物疫苗 / 猪苗产品',
    # 传媒/互联网内容
    '分众传媒': '电梯广告 / 楼宇媒体龙头',
    '兆讯传媒': '高铁广告 / 媒体资源',
    '芒果超媒': '流媒体 / 湖南广电新媒体',
    '捷成股份': '影视版权 / 内容运营',
    '华策影视': '影视制作 / 电视剧龙头',
    '光线传媒': '影视制作 / 动画电影',
    '万达电影': '电影院线 / 行业龙头',
    '中国电影': '电影全产业链 / 中影品牌',
    '横店影视': '电影院线 / 资产联结型',
    '金逸影视': '电影院线 / 华南优势',
    '分众传媒': '电梯广告 / 江南春创办',
    '视觉中国': '图片版权 / 全媒体布局',
    '中文在线': '数字阅读 / IP培育',
    '掌趣科技': '游戏 / 移动游戏研发',
    '完美世界': '游戏+影视 / 影游联动',
    '三七互娱': '游戏 / 买量运营模式',
    '吉比特': '游戏 / 问道IP常青树',
    '姚记科技': '休闲游戏+扑克 / 抖音买量',
    '星辉娱乐': '游戏+体育 / 西班牙人俱乐部',
    # 轻工/家居
    '顾家家居': '软体家具 / 沙发龙头',
    '敏华控股': '功能沙发 /芝华仕品牌',
    '欧派家居': '定制橱柜 / 行业龙头',
    '索菲亚': '定制衣柜 / 行业龙头',
    '尚品宅配': '全屋定制 / C2B模式',
    '志邦家居': '定制橱柜 / 衣柜扩张',
    '金牌厨柜': '定制橱柜 / 高端定制',
    '好莱客': '全屋定制 / 无醛添加',
    '曲美家居': '成品+定制 / 曲美品牌',
    '喜临门': '床垫 / 蜜月喜临门',
    '慕思股份': '床垫 / 高端健康睡眠',
    '梦百合': '记忆绵床垫 / 跨境电商',
    '江山欧派': '木门 / 工程渠道强',
    '王力安防': '安全门锁 / 智能门锁',
    '奥普家居': '浴霸+集成吊顶 / 品牌优势',
    '浙江美大': '集成灶 / 行业开创者',
    '火星人': '集成灶 / 电商渠道强',
    '亿田智能': '集成灶 / 高端定位',
    '帅丰电器': '集成灶 / 蒸烤一体',
    '老板电器': '油烟机+灶具 / 高端厨电',
    '方太集团': '油烟机+灶具 / 高端厨电（非上市）',
    # 军工
    '中航沈飞': '军机制造 / 歼击机龙头',
    '中航西飞': '军机制造 / 运输机+轰炸机',
    '中航直升机': '直升机 / AC313高原型',
    '中航光电': '军工连接器 / 军用配套',
    '中航机电': '航空机电系统 / 垄断配套',
    '中航电子': '航电系统 / 系统级配套',
    '航发动力': '航空发动机 / 唯一总装',
    '航发控制': '发动机控制系统 / 垄断',
    '航发科技': '发动机零部件 / 法斯特罗',
    '钢研高纳': '高温合金 / 航空发动机',
    '图南股份': '精密铸件 / 两机叶片',
    '西部超导': '钛合金+高温合金 / 超导线材',
    '宝钛股份': '钛合金 / 军用钛材龙头',
    '三角防务': '模锻件 / 军机结构件',
    '中简科技': '碳纤维 / ZT7/ZT9系列',
    '光威复材': '碳纤维 / 全产业链布局',
    '中复神鹰': '碳纤维 / 民用龙头',
    '楚江新材': '碳纤维+热工设备 / 顶立科技',
    '北摩高科': '刹车系统 / 军机+民机',
    '爱乐达': '航空零部件 / 精密制造',
    '利君股份': '辊压机+航空零件 / 特材加工',
    '银邦股份': '铝合金复合材料 / 军工配套',
    '中船防务': '军舰制造 / 护卫舰+导弹艇',
    '中国船舶': '船舶制造 / LNG船+航母',
    '中国重工': '船舶制造 / 全产业链',
    '中国海防': '水声通信 / 水下信息',
    '海兰信': '航海电子 / 舰船综合导航',
    '天奥电子': '时间频率 / 铷原子钟',
    '振华科技': '军工电子 / MLCC+IGBT',
    '紫光国微': '特种芯片 / FPGA+安全芯片',
    '复旦微电': '特种芯片 / FPGA行业',
    '臻雷科技': '军工电子 / 模拟芯片',
    '高德红外': '红外热成像 / 精确制导',
    '大立科技': '红外热成像 / 军品整机和民品',
    '睿创微纳': '红外MEMS / 非制冷红外',
    '广哈通信': '军民用通信 / 指挥调度',
    '七一二': '无线通信 / 军用专网通信',
    '海格通信': '军事通信 / 北斗+5G',
    '杰赛科技': '通信规划设计 / 远东新秀',
    '航天电器': '军用连接器 / 宇航级',
    '航天电子': '航天电子对抗 / 测控通导',
    '北方导航': '制导系统 / 惯性导航',
    '光电股份': '光电对抗 / 精确制导',
    '中兵红箭': '特种装备 / 超硬材料',
    '东土科技': '工业互联网 / 自主可控',
    '景嘉微': 'GPU芯片 / 国产显控',
    '雷科防务': '毫米波雷达 / 汽车+军工',
    '上海瀚讯': '军用宽带通信 / 专网设备',
    '红相股份': '电力+军工 / 雷电防护',
    # 环保/公用事业
    '伟明环保': '固废处理 / 垃圾焚烧发电',
    '瀚蓝环境': '固废处理 / 南海模式',
    '绿色动力': '垃圾焚烧 / 国资背景',
    '上海环境': '垃圾焚烧+污水 / 城投背景',
    '首创股份': '水务处理 / 北京首钢',
    '碧水源': '膜技术水处理 / 自主膜',
    '津膜科技': '膜技术水处理 / 分离膜',
    '万邦达': '工业水处理 / 煤化工废水',
    '中持股份': '中小城市水务 / 区域布局',
    '维尔利': '渗滤液+有机垃圾 / 德国技术',
    '高能环境': '土壤修复+危废处理 / 修复龙头',
    '建工修复': '土壤修复 / 场地修复',
    # 物流/供应链
    '顺丰控股': '快递物流 / 高端时效',
    '京东物流': '仓储物流 / 一体化供应链',
    '中通快递-SW': '快递 / 市场份额第一',
    '圆通速递': '快递 / 航空机队',
    '韵达股份': '快递 / 阿里参股',
    '申通快递': '快递 / 阿里控股',
    '德邦股份': '大件快递 / 京东收购',
    '中国外运': '跨境物流 / 招商局集团',
    '华贸物流': '跨境物流 / 空运货代',
    '中谷物流': '内贸集装箱 / 散改集',
    '兴通股份': '内贸化学品船 / 液体化工',
    '密尔克卫': '化工物流 / 一站式履约',
    '宏川智慧': '石化仓储 / 并购扩张',
    '恒通股份': 'LNG物流 / 轻资产运营',
    # 纺织服装
    '波司登': '羽绒服 / 全球第一羽绒服品牌',
    '雅戈尔': '男装+地产 / 品牌服装',
    '比音勒芬': '高端运动休闲 / 传奇F4代言',
    '报喜鸟': '西装+HAZZYS / 代理韩国品牌',
    '七匹狼': '男装 / 茄克市场领先',
    '九牧王': '男裤 / 专注男裤领域',
    '海澜之家': '男装 / 国民男装品牌',
    '红豆股份': '男装+内衣 / 老牌国产',
    '太平鸟': '休闲装 / 年轻化转型',
    '森马服饰': '休闲装+童装 / 巴拉巴拉龙头',
    '安踏体育': '体育用品 / 国产运动龙头',
    '李宁': '体育用品 / 国潮崛起',
    '特步国际': '体育用品 / 马拉松跑鞋',
    '361度': '体育用品 / 三四线市场',
    '申洲国际': '运动服装代工 / 优衣库+Nike',
    '华利集团': '运动鞋代工 / Nike+Puma+UA',
    # 食品饮料
    '伊利股份': '乳制品 / 液态奶龙头',
    '蒙牛乳业': '乳制品 / 雅士利+现代牧业',
    '光明乳业': '乳制品 / 华东低温奶',
    '新乳业': '低温鲜奶 / 并购整合',
    '妙可蓝多': '奶酪棒 / 儿童奶酪市场',
    '澳优': '羊奶粉 / 佳贝艾特全球第一',
    '中国飞鹤': '婴幼儿奶粉 / 高端市场',
    'H&H国际控股': '婴幼儿奶粉+宠物营养 / 澳洲',
    '海天味业': '酱油 / 调味品龙头',
    '千禾味业': '零添加酱油 / 差异化竞争',
    '中炬高新': '酱油+园区 / 厨邦品牌',
    '恒顺醋业': '香醋 / 镇江香醋代表',
    '安琪酵母': '酵母 / 全球第三',
    '涪陵榨菜': '榨菜 / 乌江品牌绝对龙头',
    '安井食品': '速冻食品 / 火锅料龙头',
    '三全食品': '速冻水饺 / 速冻食品先驱',
    '广州酒家': '月饼+餐饮 / 陶陶居品牌',
    '桃李面包': '短保面包 / 东北起家全国扩张',
    '绝味食品': '休闲卤味 / 门店数最多',
    '周黑鸭': '休闲卤味 / 自营模式',
    '煌上煌': '休闲卤味 / 江西区域',
    '巴比食品': '早餐连锁 / 包子馒头赛道',
    '味知香': '预制菜 / 行业先驱',
    '千味央厨': '速冻食品B端 / 定制研发',
    '立高食品': '烘焙原料 / 奶油+酱料',
    '南侨股份': '烘焙油脂 / 行业龙头',
    '阳光乳业': '低温鲜奶 / 江西区域',
    # 农业种植/种子
    '隆平高科': '种子 / 水稻+玉米双龙头',
    '登海种业': '玉米种子 / 登海系列',
    '荃银高科': '水稻种子 / 背靠先正达',
    '农发种业': '小麦+玉米种子 / 中农发集团',
    '敦煌种业': '种子 / 玉米脱水机',
    '万向德农': '玉米种子 / 东北市场',
    # 造纸/包装
    '太阳纸业': '造纸 / 文化纸+溶解浆',
    '晨鸣纸业': '造纸 / 白卡纸龙头',
    '博汇纸业': '白卡纸 / APP控股',
    '山鹰国际': '包装纸+造纸 / 产业互联',
    '玖龙纸业': '包装纸 / 全球废纸造纸',
    '裕同科技': '包装印刷 / 消费电子包装',
    '合兴包装': '瓦楞纸箱 / 行业整合者',
    '新宏泽': '烟标印刷 / 粤绣品牌',
    '东风股份': '烟标印刷 / 乳液高阻隔膜',
    '恩捷股份': '包装膜+隔膜 / 锂电隔膜',
    # 家电
    '美的集团': '家电 / 空调+机器人',
    '格力电器': '空调 / 单项冠军企业',
    '海尔智家': '家电 / 高端品牌卡萨帝',
    '苏泊尔': '小家电 / 炊具龙头',
    '九阳股份': '小家电 / 豆浆机发明者',
    '小熊电器': '创意小家电 / 电商渠道',
    '新宝股份': '小家电代工 / 东菱品牌',
    '小熊电器': '创意小家电 / 年轻人市场',
    # 其他
    '公牛集团': '插座+电工 / 转换器龙头',
    '老板电器': '厨卫电器 / 油烟机高端市场',
    '华帝股份': '厨卫电器 / 油烟机+灶具',
    '浙江美大': '集成灶 / 行业开创者',
    '火星人': '集成灶 / 电商渠道强',
}

def get_stock_sector_tag(stock_name):
    """根据股票名称返回细分行业标签"""
    if not stock_name:
        return '其他'
    for name, tag in STOCK_SECTOR_TAGS.items():
        if name in stock_name:
            return tag
    # 尝试关键词匹配
    keywords = {
        '医': '医疗健康', '药': '医药生物', '科': '科技', '半': '半导体/芯片',
        '光': '光电/光伏', '芯': '芯片/集成电路', '软': '软件/IT', '网': '网络/通信',
        '银': '银行', '保': '保险', '券': '证券', '房': '房地产',
        '车': '汽车/零部件', '电': '电力/新能源', '气': '燃气/公用事业',
        '石': '石油/化工', '冶': '有色金属', '矿': '矿业/资源',
        '航': '航空/国防', '天': '航天/卫星', '船': '船舶/海工',
        '水': '水务/环保', '交': '交通运输', '建': '建筑/建材',
        '农': '农业/种植', '牧': '养殖/畜牧', '林': '林业/种业',
        '传': '传媒/娱乐', '教': '教育', '旅': '旅游/酒店',
        '食': '食品/餐饮', '穿': '纺织服装', '美': '美容/化妆品',
    }
    for kw, sector in keywords.items():
        if kw in stock_name:
            return sector
    return '综合/其他'


def fetch_holdings_info(fund_code):
    """获取持仓信息（从crawl_fund_full缓存数据，无额外网络请求）"""
    try:
        cache_key = generate_cache_key(CACHE_CONFIG['fund_info']['prefix'], fund_code)
        cached = get_cache(cache_key)
        if cached and cached.get('前十大持仓'):
            return {'前十大持仓': cached.get('前十大持仓', [])}
        # 无缓存时从爬虫获取
        data = crawl_fund_full(fund_code)
        if data and data.get('前十大持仓_raw'):
            holdings = []
            for h in data['前十大持仓_raw'][:10]:
                holdings.append({
                    '股票代码': h.get('股票代码', ''),
                    '股票名称': h.get('股票名称', ''),
                    '占净值比例': h.get('占净值比', ''),
                    '细分行业': h.get('sector_tag', ''),
                })
            return {'前十大持仓': holdings}
    except Exception as e:
        print(f"获取持仓信息失败: {e}")
    return {}

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

# ============================================================
# 基金分析报告（4P三性选基方法论）
# ============================================================

def _score_performance(info: dict) -> tuple:
    """
    评估业绩表现（满分25分）
    好买标准：同类排名前1/3 + 夏普>1.0 + 信息比率>0.8 + 超额连续为正
    """
    score = 0
    detail = []
    annual = info.get('年化收益率', '0%')
    num = float(str(annual).replace('%', '').replace('nan', '0') or 0)
    sharpe = float(str(info.get('夏普比率', '0')).replace('nan', '0') or 0)
    info_ratio = float(str(info.get('信息比率', '0')).replace('nan', '0') or 0)
    max_draw = abs(float(str(info.get('最大回撤', '0%')).replace('%', '').replace('nan', '0') or 0))
    excess_return = float(str(info.get('超额收益', '0')).replace('nan', '0') or 0)

    # 硬门槛标记
    hard_fail = []
    if sharpe <= 1.0:
        hard_fail.append(f"夏普{sharpe:.2f}<1.0")
    if info_ratio < 0.8 and info_ratio > 0:
        hard_fail.append(f"信息比率{info_ratio:.2f}<0.8")

    # 近1年收益率（8分）
    if num >= 20:   score += 8; detail.append(f'近1年{num:.1f}%，同类前部')
    elif num >= 10: score += 6; detail.append(f'近1年{num:.1f}%，同类中部')
    elif num >= 0:  score += 4; detail.append(f'近1年{num:.1f}%，同类偏后')
    else:           score += 1; detail.append(f'近1年{num:.1f}%，亏损')

    # 夏普比率（6分）
    if sharpe >= 2.0:     score += 6; detail.append(f'夏普{sharpe:.2f}，极佳')
    elif sharpe >= 1.2:   score += 5; detail.append(f'夏普{sharpe:.2f}，优秀')
    elif sharpe >= 1.0:   score += 3; detail.append(f'夏普{sharpe:.2f}，达标')
    else:                 score += 1; detail.append(f'夏普{sharpe:.2f}，未达1.0门槛')

    # 信息比率（5分）
    if info_ratio >= 1.5:     score += 5; detail.append(f'信息比率{info_ratio:.2f}，超额稳定')
    elif info_ratio >= 1.0:   score += 4; detail.append(f'信息比率{info_ratio:.2f}，良好')
    elif info_ratio >= 0.8:   score += 2; detail.append(f'信息比率{info_ratio:.2f}，达标')
    else:                     score += 0; detail.append(f'信息比率{info_ratio:.2f}<0.8未达标')

    # 最大回撤（3分）
    if max_draw <= 15:    score += 3; detail.append(f'最大回撤{max_draw:.1f}%，优秀')
    elif max_draw <= 25:  score += 2; detail.append(f'最大回撤{max_draw:.1f}%，良好')
    elif max_draw <= 40:  score += 1; detail.append(f'最大回撤{max_draw:.1f}%，一般')
    else:                 score += 0; detail.append(f'最大回撤{max_draw:.1f}%，较大')

    # 超额收益持续性（3分）
    if excess_return > 10:
        score += 3; detail.append(f'超额{excess_return:.1f}%，显著')
    elif excess_return > 5:
        score += 2; detail.append(f'超额{excess_return:.1f}%，良好')
    elif excess_return > 0:
        score += 1; detail.append(f'超额{excess_return:.1f}%，正超额')
    else:
        score += 0; detail.append(f'超额{excess_return:.1f}%，无超额')

    verdict = '高分(20-25)' if score >= 20 else '达标(10-19)' if score >= 10 else '剔除(0-9)'
    prefix = f'[{verdict}] '
    if hard_fail:
        prefix += '⚠️硬门槛: ' + '; '.join(hard_fail) + ' | '
    return score, verdict, prefix + '; '.join(detail)


def _score_philosophy(info: dict, holdings: list) -> tuple:
    """
    评估投资理念（满分25分）
    好买标准：理念清晰自洽 + 风格定位明确 + 策略可复制 + 非押注式投资
    """
    score = 0
    detail = []
    style = info.get('基金风格', '')
    first_ind = info.get('第一大行业', '')
    ind_ratio = float(str(info.get('行业占比', '0%')).replace('%', '') or 0)
    conc = float(str(info.get('持仓集中度', '0%')).replace('%', '') or 0)

    # 风格定位清晰（10分）
    clear_styles = {'价值', '成长', '均衡', '大盘价值', '大盘成长', '小盘成长', '小盘价值',
                    '消费', '医药', '科技', '制造', '周期', '金融'}
    if style in clear_styles:
        score += 10; detail.append(f'风格定位清晰：{style}')
    elif style:
        score += 6; detail.append(f'风格：{style}（定位不够明确）')
    else:
        score += 2; detail.append('风格定位模糊')

    # 策略可复制、非押注（8分）
    if ind_ratio <= 40:
        score += 8; detail.append('行业分散，策略可复制性强')
    elif ind_ratio <= 60:
        score += 6; detail.append('行业适度集中，策略有方向')
    elif ind_ratio <= 80:
        score += 4; detail.append(f'行业集中{ind_ratio:.0f}%，赛道型策略')
    else:
        score += 1; detail.append(f'行业高度集中{ind_ratio:.0f}%，押注式风险高')

    # 投资逻辑验证（7分）：持仓与风格匹配
    if style in ('价值', '大盘价值') and conc >= 50:
        score += 7; detail.append('价值风格与适度集中持仓逻辑一致')
    elif style in ('成长', '大盘成长', '科技') and ind_ratio >= 40:
        score += 7; detail.append('成长风格与行业聚焦逻辑一致')
    elif style in ('均衡',) and 30 <= ind_ratio <= 60:
        score += 6; detail.append('均衡风格与分散配置逻辑一致')
    elif first_ind and style:
        score += 4; detail.append(f'风格「{style}」与持仓「{first_ind}」基本一致')
    else:
        score += 2; detail.append('投资逻辑验证数据不足')

    verdict = '高分(20-25)' if score >= 20 else '达标(10-19)' if score >= 10 else '剔除(0-9)'
    return score, verdict, '; '.join(detail)


def _score_people(info: dict) -> tuple:
    """
    评估管理人（满分30分）
    好买标准：从业≥5年经历牛熊（10分）+ 任职该基金≥3年（10分）+ 基金公司实力（10分）
    """
    score = 0
    detail = []
    manager = info.get('基金经理', '')
    company = info.get('基金公司', '')
    tenure = float(str(info.get('从业年限', '0')).replace('年', '').replace('又', '.').replace('天', '0').replace('nan', '0') or 0)

    # 基金经理从业年限（10分）
    if manager and manager not in ('', 'None', 'nan'):
        score += 2; detail.append(f'基金经理：{manager}')
        if tenure >= 8:
            score += 8; detail.append(f'从业{tenure:.1f}年，经历多轮牛熊')
        elif tenure >= 5:
            score += 6; detail.append(f'从业{tenure:.1f}年，经历完整周期')
        elif tenure >= 3:
            score += 4; detail.append(f'从业{tenure:.1f}年，经历部分周期')
        elif tenure >= 1:
            score += 2; detail.append(f'从业{tenure:.1f}年，尚需验证')
        else:
            score += 0; detail.append(f'从业{tenure:.1f}年，经验不足')
    else:
        score += 0; detail.append('基金经理信息缺失')

    # 任职该基金稳定性（10分）
    if manager and manager not in ('', 'None', 'nan'):
        if tenure >= 5:
            score += 10; detail.append(f'任职该基金{tenure:.1f}年，深度绑定')
        elif tenure >= 3:
            score += 8; detail.append(f'任职该基金{tenure:.1f}年，稳定')
        elif tenure >= 1:
            score += 5; detail.append(f'任职该基金{tenure:.1f}年，尚可')
        else:
            score += 1; detail.append(f'任职该基金{tenure:.1f}年，需跟踪')
    else:
        score += 0; detail.append('任职信息缺失')

    # 基金公司实力（10分）
    if company and company not in ('', 'None', 'nan'):
        score += 4; detail.append(f'基金公司：{company}')
        top_companies = ['易方达','华夏','广发','富国','嘉实','南方','汇添富','工银','招商','中欧','兴证全球','景顺长城','鹏华','华安','博时','银华','交银施罗德']
        if any(c in company for c in top_companies):
            score += 6; detail.append('头部基金公司，投研团队实力强')
        else:
            score += 3; detail.append('中小型基金公司，投研实力待验证')
    else:
        score += 3; detail.append('基金公司信息缺失（使用行业默认）')

    verdict = '高分(24-30)' if score >= 24 else '达标(15-23)' if score >= 15 else '剔除(0-14)'
    return score, verdict, '; '.join(detail)


def _score_process(info: dict, holdings: list) -> tuple:
    """
    评估决策流程（满分20分）
    好买标准：投研决策体系完善（8分）+ 选股/择时流程标准化（6分）+ 风控机制（6分）
    """
    score = 0
    detail = []
    conc = float(str(info.get('持仓集中度', '0%')).replace('%', '') or 0)
    ind_ratio = float(str(info.get('行业占比', '0%')).replace('%', '') or 0)

    # 持仓结构合理性（8分）
    if 30 <= conc <= 65:
        score += 8; detail.append(f'持仓集中度{conc:.0f}%，合理分散')
    elif 65 < conc <= 80:
        score += 6; detail.append(f'持仓集中度{conc:.0f}%，偏重仓')
    elif conc > 80:
        score += 3; detail.append(f'持仓集中度{conc:.0f}%，高度集中风险大')
    else:
        score += 5; detail.append(f'持仓集中度{conc:.0f}%，极度分散')

    # 行业配置纪律（7分）
    if 25 <= ind_ratio <= 55:
        score += 7; detail.append(f'行业配置均衡{ind_ratio:.0f}%，纪律良好')
    elif 55 < ind_ratio <= 70:
        score += 5; detail.append(f'行业配置偏集中{ind_ratio:.0f}%，关注赛道风险')
    elif ind_ratio > 70:
        score += 2; detail.append(f'行业高度集中{ind_ratio:.0f}%，无分散化纪律')
    else:
        score += 4; detail.append(f'行业分散{ind_ratio:.0f}%，配置无明显方向')

    # 风险控制（5分）
    if 30 <= conc <= 65 and ind_ratio <= 55:
        score += 5; detail.append('持仓与行业双分散，风控意识强')
    elif conc <= 70 and ind_ratio <= 60:
        score += 3; detail.append('分散控制合理，风控意识一般')
    else:
        score += 1; detail.append('持仓集中度高，风控需关注')

    verdict = '高分(16-20)' if score >= 16 else '达标(10-15)' if score >= 10 else '剔除(0-9)'
    return score, verdict, '; '.join(detail)


def _three_natures_check(info: dict, holdings: list) -> dict:
    """
    三性校验（一票否决制）
    好买标准：一致性（理念风格持仓匹配）+ 稳定性（牛熊周期回撤可控）+ 有效性（持续超额收益）
    任意一项不通过 → 直接剔除
    """
    results = {}
    style = info.get('基金风格', '')
    first_ind = info.get('第一大行业', '')
    ind_ratio = float(str(info.get('行业占比', '0%')).replace('%', '') or 0)
    conc = float(str(info.get('持仓集中度', '0%')).replace('%', '') or 0)

    # ── 一致性 ──
    if style and first_ind:
        if style in ('均衡',) and ind_ratio > 50:
            results['一致性'] = {'result': '不通过', 'detail': f'风格为均衡但第一大行业{ind_ratio:.0f}%，配置偏集中'}
        elif style in ('价值', '大盘价值') and conc < 30:
            results['一致性'] = {'result': '不通过', 'detail': f'价值风格但持仓极度分散{conc:.0f}%，与价值投资理念不一致'}
        else:
            results['一致性'] = {'result': '通过', 'detail': f'风格「{style}」与持仓「{first_ind}」{ind_ratio:.0f}%匹配，逻辑自洽'}
    else:
        results['一致性'] = {'result': '不通过', 'detail': '风格或持仓数据不完整，无法验证一致性'}

    # ── 稳定性 ──
    vol = float(str(info.get('年化波动率', '0%')).replace('%', '') or 0)
    max_draw = abs(float(str(info.get('最大回撤', '0%')).replace('%', '') or 0))
    if max_draw <= 20 and vol <= 25:
        results['稳定性'] = {'result': '通过', 'detail': f'最大回撤{max_draw:.1f}%、波动率{vol:.1f}%，风险可控'}
    elif max_draw <= 30 and vol <= 30:
        results['稳定性'] = {'result': '通过', 'detail': f'回撤{max_draw:.1f}%、波动率{vol:.1f}%，处于合理范围'}
    elif max_draw <= 40:
        results['稳定性'] = {'result': '不通过', 'detail': f'回撤{max_draw:.1f}%略高，需关注'}
    else:
        results['稳定性'] = {'result': '不通过', 'detail': f'回撤{max_draw:.1f}%过大，稳定性不达标'}

    # ── 有效性 ──
    annual = float(str(info.get('年化收益率', '0%')).replace('%', '') or 0)
    sharpe = float(str(info.get('夏普比率', '0')) or 0)
    excess_return = float(str(info.get('超额收益', '0')).replace('nan', '0') or 0)
    if annual > 10 and sharpe > 1.0 and excess_return > 0:
        results['有效性'] = {'result': '通过', 'detail': f'年化{annual:.1f}%、夏普{sharpe:.2f}、超额{excess_return:.1f}%，超额收益持续为正'}
    elif annual > 5 and sharpe > 0.5:
        results['有效性'] = {'result': '通过', 'detail': f'年化{annual:.1f}%、夏普{sharpe:.2f}，正收益'}
    elif annual > 0:
        results['有效性'] = {'result': '不通过', 'detail': f'年化{annual:.1f}%，勉强正收益，超额不显著'}
    else:
        results['有效性'] = {'result': '不通过', 'detail': f'年化{annual:.1f}%，亏损，有效性存疑'}

    return results


def fetch_fund_info(fund_code):
    """从天天基金网爬虫获取基金信息（供analysis_report使用，~0.2s）"""
    try:
        data = crawl_fund_full(fund_code)
        if not data or not data.get('net_value'):
            return None

        returns = data.get('nav_growth_raw', [])
        if len(returns) > 0:
            annual_return = data.get('annual_return', 0)
            annual_volatility = data.get('annual_volatility', 0)
            sharpe_ratio = data.get('sharpe_ratio', 0)
            max_drawdown = data.get('max_drawdown', 0)
            calmar_ratio = data.get('calmar_ratio', 0)
        else:
            annual_return = annual_volatility = sharpe_ratio = max_drawdown = calmar_ratio = 0

        # 提取前十大持仓
        holdings_raw = data.get('前十大持仓', [])
        holdings = []
        if holdings_raw:
            for h in holdings_raw[:10]:
                holdings.append({
                    '股票代码': h.get('股票代码', ''),
                    '股票名称': h.get('股票名称', ''),
                    '占净值比例': h.get('占净值比例', ''),
                    '细分行业': h.get('细分行业', ''),
                })

        info_dict = {
            '基金代码': fund_code,
            '基金简称': data.get('fund_name', ''),
            '单位净值': data.get('net_value', ''),
            '净值日期': data.get('nav_date', ''),
            '日增长率': data.get('day_growth', '0%'),
            '年化收益率': data.get('annual_return', '0%'),
            '年化波动率': data.get('annual_volatility', '0%'),
            '夏普比率': data.get('sharpe_ratio', '0'),
            '信息比率': data.get('info_ratio', '0'),
            '卡玛比率': data.get('calmar_ratio', '0'),
            '最大回撤': data.get('max_drawdown', '0%'),
            '超额收益': data.get('excess_return', '0'),
            '基金经理': data.get('基金经理', ''),
            '基金经理公司': data.get('基金经理公司', ''),
            '基金公司': data.get('基金经理公司', ''),
            '从业年限': data.get('从业年限', data.get('基金经理任职年限', '0')),
            '从业天数': data.get('从业天数', 0),
            '管理基金数量': data.get('管理基金数量', 0),
            '管理基金总规模': data.get('管理基金总规模', '0'),
            '基金风格': data.get('基金风格', ''),
            '风格描述': data.get('风格描述', ''),
            '第一大行业': data.get('第一大行业', ''),
            '行业占比': data.get('行业占比', '0%'),
            '持仓集中度': data.get('持仓集中度', '0%'),
            '前十大持仓': holdings,
        }
        return info_dict
    except Exception as e:
        print(f"fetch_fund_info失败: {e}")
        return None


def _pregenerate_analysis_report(fund_code: str, info: dict):
    """后台预生成分析报告缓存（异步，不阻塞主请求）"""
    try:
        from fund_analyzer import FundScreener, ReportGenerator
        screener = FundScreener(fund_info=info, holdings={"前十大持仓": info.get("前十大持仓", [])})
        result = screener.screen()
        report = ReportGenerator.generate(result)
        report["source"] = 'pregenerated'
        report["cached"] = False
        report_cache_key = generate_cache_key(CACHE_CONFIG['fund_analysis_report']['prefix'], fund_code)
        set_cache(report_cache_key, report, CACHE_CONFIG['fund_analysis_report']['expiry'])
        print(f"[pregenerate] {fund_code} 分析报告已预生成")
    except Exception as e:
        print(f"[pregenerate] {fund_code} 预生成失败: {e}")


# ══════════════════════════════════════════════════════════════
# 方案B+C：分析报告历史库（MySQL持久化 + 热点基金预热）
# ══════════════════════════════════════════════════════════════

def _init_analysis_history_table():
    """创建分析报告历史表（仅MySQL可用时执行）"""
    pool = get_mysql_pool()
    if pool is None:
        return False
    try:
        conn = pool.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS fund_analysis_history (
                id INT AUTO_INCREMENT PRIMARY KEY,
                fund_code VARCHAR(10) NOT NULL,
                week_number INT NOT NULL,          -- 周数编号（如202601）
                report_data LONGTEXT NOT NULL,     -- JSON序列化报告
                generated_at DATETIME NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uk_fund_week (fund_code, week_number)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        conn.commit()
        cursor.close()
        conn.close()
        print("[history] fund_analysis_history 表就绪")
        return True
    except Exception as e:
        print(f"[history] 建表失败: {e}")
        return False


def _get_latest_week_number():
    """获取当前周数编号（如202617 = 2026年第17周）"""
    now = datetime.now()
    year, week, _ = now.isocalendar()
    return year * 100 + week


def _save_report_to_mysql(fund_code: str, report: dict, week_number: int):
    """保存分析报告到MySQL历史库（Upsert）"""
    pool = get_mysql_pool()
    if pool is None:
        return
    try:
        import json
        conn = pool.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO fund_analysis_history (fund_code, week_number, report_data, generated_at)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE report_data = VALUES(report_data), generated_at = VALUES(generated_at)
        """, (fund_code, week_number, json.dumps(report, ensure_ascii=False), datetime.now()))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"[history] 保存报告失败 {fund_code}: {e}")


def _get_report_from_mysql(fund_code: str, week_number: int = None) -> dict:
    """从MySQL历史库读取指定周的报告，week_number为None时读最新周"""
    pool = get_mysql_pool()
    if pool is None:
        return None
    try:
        import json
        conn = pool.get_connection()
        cursor = conn.cursor()
        if week_number is None:
            # 读最新一条
            cursor.execute("""
                SELECT report_data, week_number, generated_at FROM fund_analysis_history
                WHERE fund_code = %s ORDER BY week_number DESC LIMIT 1
            """, (fund_code,))
        else:
            cursor.execute("""
                SELECT report_data, week_number, generated_at FROM fund_analysis_history
                WHERE fund_code = %s AND week_number = %s LIMIT 1
            """, (fund_code, week_number))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if row:
            report = json.loads(row['report_data'])
            report['history_week'] = row['week_number']
            report['history_generated_at'] = str(row['generated_at'])
            report['source'] = 'mysql_history'
            return report
        return None
    except Exception as e:
        print(f"[history] 读取报告失败 {fund_code}: {e}")
        return None


def _warmup_top_funds_report():
    """方案B：启动时批量预热Top30热点基金分析报告（后台异步）"""
    def _do_warmup():
        try:
            import sqlite3
            conn = sqlite3.connect(SQLITE_DB_PATH)
            cursor = conn.cursor()
            # 取成交量最大的基金（用fund_list_cache前30个作为热点池）
            cursor.execute("SELECT code FROM fund_list_cache LIMIT ?", (TOP_FUNDS_WARMUP_COUNT,))
            codes = [r[0] for r in cursor.fetchall()]
            conn.close()
            if not codes:
                print("[warmup] 无热点基金数据，跳过")
                return
            print(f"[warmup] 开始预热 {len(codes)} 只基金分析报告...")
            for code in codes:
                try:
                    # 优先复用info缓存
                    info_cache_key = generate_cache_key(CACHE_CONFIG['fund_info']['prefix'], code)
                    info = get_cache(info_cache_key)
                    source = 'redis_cache'
                    if not info:
                        info = get_fund_info_from_db(code)
                        source = 'mysql_database'
                    if not info:
                        continue
                    # 生成报告
                    screener = FundScreener(fund_info=info, holdings={"前十大持仓": info.get("前十大持仓", [])})
                    result = screener.screen()
                    report = ReportGenerator.generate(result)
                    report["source"] = source + "_warmup"
                    # 写内存/Redis缓存
                    report_cache_key = generate_cache_key(CACHE_CONFIG['fund_analysis_report']['prefix'], code)
                    set_cache(report_cache_key, report, CACHE_CONFIG['fund_analysis_report']['expiry'])
                    # 写MySQL历史库
                    _save_report_to_mysql(code, report, _get_latest_week_number())
                    print(f"[warmup] {code} 预热完成")
                except Exception as e:
                    print(f"[warmup] {code} 预热失败: {e}")
            print("[warmup] 热点基金预热全部完成")
        except Exception as e:
            print(f"[warmup] 预热流程异常: {e}")

    t = threading.Thread(target=_do_warmup, daemon=True)
    t.start()


def _schedule_weekly_refresh():
    """每周日凌晨2点自动刷新历史报告库"""
    import sched
    import time as time_module

    scheduler = sched.scheduler(time_module.time, time_module.sleep)

    def _next_sunday_2am():
        """计算下周日凌晨2点的unix时间戳"""
        now = datetime.now()
        days_until_sunday = (6 - now.weekday()) % 7  # 0=Sunday
        if days_until_sunday == 0 and now.hour >= 2:
            days_until_sunday = 7
        next_sunday = now + timedelta(days=days_until_sunday)
        next_sunday_2am = next_sunday.replace(hour=2, minute=0, second=0, microsecond=0)
        return next_sunday_2am.timestamp()

    def _run_and_reschedule():
        print("[scheduler] 触发每周历史报告刷新...")
        _weekly_refresh_history_reports()
        # 重新调度到下周日
        delay = _next_sunday_2am() - time_module.time()
        if delay > 0:
            scheduler.enter(delay, 0, _run_and_reschedule)

    delay = _next_sunday_2am() - time_module.time()
    if delay > 0:
        scheduler.enter(delay, 0, _run_and_reschedule)
        t = threading.Thread(target=scheduler.run, daemon=True)
        t.start()
        print(f"[scheduler] 每周刷新已调度，距下次执行 {delay/3600:.1f} 小时")


def _weekly_refresh_history_reports():
    """方案C：每周定时刷新MySQL历史报告库（增量更新）"""
    def _do_refresh():
        try:
            import sqlite3
            conn = sqlite3.connect(SQLITE_DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT code FROM fund_list_cache LIMIT ?", (TOP_FUNDS_WARMUP_COUNT,))
            codes = [r[0] for r in cursor.fetchall()]
            conn.close()
            week = _get_latest_week_number()
            count = 0
            for code in codes:
                try:
                    info_cache_key = generate_cache_key(CACHE_CONFIG['fund_info']['prefix'], code)
                    info = get_cache(info_cache_key)
                    if not info:
                        info = get_fund_info_from_db(code)
                    if not info:
                        continue
                    screener = FundScreener(fund_info=info, holdings={"前十大持仓": info.get("前十大持仓", [])})
                    result = screener.screen()
                    report = ReportGenerator.generate(result)
                    report["source"] = 'weekly_refresh'
                    _save_report_to_mysql(code, report, week)
                    # 同时更新缓存
                    report_cache_key = generate_cache_key(CACHE_CONFIG['fund_analysis_report']['prefix'], code)
                    set_cache(report_cache_key, report, CACHE_CONFIG['fund_analysis_report']['expiry'])
                    count += 1
                except Exception as e:
                    print(f"[weekly_refresh] {code} 刷新失败: {e}")
            print(f"[weekly_refresh] 周{week}历史报告刷新完成，共{count}只")
        except Exception as e:
            print(f"[weekly_refresh] 刷新流程异常: {e}")

    t = threading.Thread(target=_do_refresh, daemon=True)
    t.start()


# ══════════════════════════════════════════════════════════════


def _generate_report_async(fund_code: str, info: dict, source: str):
    """后台异步生成并缓存分析报告（冷启动降级路径）"""
    global REPORT_GENERATING
    try:
        screener = FundScreener(fund_info=info, holdings={"前十大持仓": info.get("前十大持仓", [])})
        result = screener.screen()
        report = ReportGenerator.generate(result)
        report["source"] = source
        report["cached"] = False
        report["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # 写入缓存
        report_cache_key = generate_cache_key(CACHE_CONFIG['fund_analysis_report']['prefix'], fund_code)
        set_cache(report_cache_key, report, CACHE_CONFIG['fund_analysis_report']['expiry'])
        # 异步写入MySQL历史库
        threading.Thread(
            target=_save_report_to_mysql,
            args=(fund_code, report, _get_latest_week_number()),
            daemon=True
        ).start()
        print(f"[report_async] {fund_code} 报告生成完成（{source}）")
    except Exception as e:
        print(f"[report_async] {fund_code} 生成失败: {e}")
    finally:
        REPORT_GENERATING.pop(fund_code, None)


@app.route('/api/fund/analysis_report', methods=['GET'])
def get_analysis_report():
    """生成基金投资分析报告（v4 — 冷启动立即返回，后台异步生成）

    数据获取优先级：
      1. 内存/Redis缓存（最快，TTL 1小时）
      2. MySQL历史库（持久化，最新周数据）
      3. 后台异步生成（info缓存/DB → FundScreener，客户端轮询）
    """
    import time
    t0 = time.time()
    fund_code = request.args.get('fund_code', '').strip()
    if not fund_code:
        return jsonify({'success': False, 'message': '请输入基金代码'})

    # ── Step 1：内存/Redis缓存（最快路径）──────────────────────
    report_cache_key = generate_cache_key(CACHE_CONFIG['fund_analysis_report']['prefix'], fund_code)
    t1 = time.time()
    cached_report = get_cache(report_cache_key)
    t2 = time.time()
    if cached_report:
        cached_report['source'] = 'redis_cache'
        cached_report['cached'] = True
        print(f"[report timing] {fund_code} 缓存命中 total={t2-t0:.3f}s cache_get={t2-t1:.3f}s")
        return jsonify(cached_report)

    # ── Step 2：MySQL历史库（持久化，次快）───────────────────
    t3 = time.time()
    mysql_report = _get_report_from_mysql(fund_code)
    t4 = time.time()
    if mysql_report:
        mysql_report['cached'] = False
        # 回填缓存
        set_cache(report_cache_key, mysql_report, CACHE_CONFIG['fund_analysis_report']['expiry'])
        print(f"[report timing] {fund_code} MySQL命中 total={t4-t0:.3f}s mysql={t4-t3:.3f}s")
        return jsonify(mysql_report)

    # ── Step 3：同步实时计算（info已缓存，纯内存计算 <1s）──
    info_cache_key = generate_cache_key(CACHE_CONFIG['fund_info']['prefix'], fund_code)
    t5 = time.time()
    info = get_cache(info_cache_key)
    t6 = time.time()
    source = 'redis_cache'

    if not info:
        info = get_fund_info_from_db(fund_code)
        source = 'mysql_database'

    if not info:
        # 数据库也没有，从天天基金网爬虫回源（~0.2s）
        info = fetch_fund_info(fund_code)
        source = 'crawler'

    if not info:
        return jsonify({'success': False, 'message': f'无法获取基金 {fund_code} 的信息'})

    # 同步计算报告（info数据完整，纯计算 <500ms）
    try:
        t7 = time.time()
        screener = FundScreener(
            fund_info=info,
            holdings={"前十大持仓": info.get("前十大持仓", [])}
        )
        t8 = time.time()
        result = screener.screen()
        t9 = time.time()
        report = ReportGenerator.generate(result)
        t10 = time.time()
        report["source"] = source
        report["cached"] = False
        report["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 回填缓存
        report_cache_key = generate_cache_key(CACHE_CONFIG['fund_analysis_report']['prefix'], fund_code)
        set_cache(report_cache_key, report, CACHE_CONFIG['fund_analysis_report']['expiry'])
        t11 = time.time()

        # 异步写入MySQL历史库（不阻塞响应）
        threading.Thread(
            target=_save_report_to_mysql,
            args=(fund_code, report, _get_latest_week_number()),
            daemon=True
        ).start()

        print(f"[report timing] {fund_code} 实时计算 total={t11-t0:.3f}s | cache_get={t2-t1:.3f}s mysql={t4-t3:.3f}s info_get={t6-t5:.3f}s screener={t9-t7:.3f}s reportgen={t10-t9:.3f}s")
        return jsonify(report)
    except Exception as e:
        print(f"[report] {fund_code} 同步计算失败: {e}")
        return jsonify({'success': False, 'message': f'报告生成失败: {e}'})


@app.route('/api/fund/analysis_report_status', methods=['GET'])
def get_analysis_report_status():
    """轮询分析报告生成状态"""
    fund_code = request.args.get('fund_code', '').strip()
    if not fund_code:
        return jsonify({'success': False, 'message': '请输入基金代码'})

    report_cache_key = generate_cache_key(CACHE_CONFIG['fund_analysis_report']['prefix'], fund_code)
    cached_report = get_cache(report_cache_key)
    if cached_report:
        cached_report['source'] = 'redis_cache'
        cached_report['cached'] = True
        return jsonify(cached_report)

    mysql_report = _get_report_from_mysql(fund_code)
    if mysql_report:
        mysql_report['cached'] = False
        set_cache(report_cache_key, mysql_report, CACHE_CONFIG['fund_analysis_report']['expiry'])
        return jsonify(mysql_report)

    generating = fund_code in REPORT_GENERATING
    return jsonify({
        'success': True,
        'pending': generating,
        'fund_code': fund_code,
        'message': '报告正在生成中，请稍后刷新' if generating else '报告不存在'
    })


@app.route('/api/fund/screen', methods=['GET'])
def screen_funds():
    """
    批量筛选基金 — 执行6步筛选流程
    参数:
      codes: 逗号分隔的基金代码列表，如 "110022,000001,161725"
      min_score: 最小4P总分（默认0）
      min_sharpe: 最小夏普比率（默认0）
      max_drawdown: 最大回撤上限%（默认100）
      risk_level: 风险等级过滤（低/中/中高/高，不传则不过滤）
    返回: 筛选结果列表 + 各阶段统计
    """
    codes_str = request.args.get('codes', '').strip()
    if not codes_str:
        return jsonify({'success': False, 'message': '请提供基金代码列表（codes参数）'})

    codes = [c.strip() for c in codes_str.split(',') if c.strip()]
    if not codes:
        return jsonify({'success': False, 'message': '基金代码列表为空'})

    min_score = int(request.args.get('min_score', 0))
    min_sharpe = float(request.args.get('min_sharpe', 0))
    max_drawdown = float(request.args.get('max_drawdown', 100))
    risk_filter = request.args.get('risk_level', '').strip()

    # 阶段统计
    stage_counts = {
        "合规初筛池": 0,
        "量化初筛优质池": 0,
        "4P评估通过池": 0,
        "三性校验通过池": 0,
        "精选池": 0,
        "已剔除": 0,
    }

    results = []
    errors = []

    for code in codes:
        try:
            # 尝试从缓存/数据库获取数据
            cache_key = generate_cache_key(CACHE_CONFIG['fund_info']['prefix'], code)
            info = get_cache(cache_key)
            source = 'redis_cache'

            if not info:
                info = get_fund_info_from_db(code)
                if info:
                    source = 'mysql_database' if info.get('_db_source') == 'mysql' else 'sqlite_database'
                    info.pop('_db_source', None)

            if not info:
                # 从直爬接口获取
                info = fetch_fund_info(code)
                source = 'crawler'

            if not info:
                errors.append({'code': code, 'error': '无法获取基金数据'})
                continue

            screener = FundScreener(fund_info=info)
            result = screener.screen()
            report = ReportGenerator.generate(result)
            report['source'] = source

            # 过滤
            total_4p = report['four_p']['total']['score'] if report.get('four_p') else 0
            sharpe = 0.0
            drawdown = 0.0
            for m in (report.get('metrics') or []):
                if m['label'] == '夏普比率':
                    try: sharpe = float(m['value'])
                    except: pass
                if m['label'] == '最大回撤':
                    try: drawdown = abs(float(m['value'].replace('%', '')))
                    except: pass

            if total_4p < min_score:
                continue
            if sharpe < min_sharpe:
                continue
            if drawdown > max_drawdown:
                continue
            if risk_filter and report.get('risk_level') != risk_filter:
                continue

            stage_counts[report.get('stage', '已剔除')] += 1
            results.append(report)

        except Exception as e:
            errors.append({'code': code, 'error': str(e)})

    # 按4P总分降序
    results.sort(key=lambda x: x.get('four_p', {}).get('total', {}).get('score', 0), reverse=True)

    return jsonify({
        'success': True,
        'total_input': len(codes),
        'total_passed': len(results),
        'total_errors': len(errors),
        'stage_counts': stage_counts,
        'filters_applied': {
            'min_score': min_score,
            'min_sharpe': min_sharpe,
            'max_drawdown': max_drawdown,
            'risk_level': risk_filter or '不过滤',
        },
        'results': results,
        'errors': errors,
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    })


def _generate_summary(name, total_4p, all_pass, three_natures, info, risk):
    """生成综合评述文字"""
    annual = float(str(info.get('年化收益率', '0%')).replace('%', '') or 0)
    sharpe = float(str(info.get('夏普比率', '0')).replace('nan', '0') or 0)
    max_draw = abs(float(str(info.get('最大回撤', '0%')).replace('%', '') or 0))
    style = info.get('基金风格', '未知')
    first_ind = info.get('第一大行业', '综合')
    conc = info.get('持仓集中度', 'N/A')

    paras = []

    paras.append(f"【{name}】（{info.get('基金代码', '')}）投资分析综述：")

    # 业绩总评
    if annual >= 15 and sharpe >= 1.5:
        paras.append(f"该基金年化收益率{annual:.1f}%、夏普比率{sharpe:.2f}，风险调整后收益表现出色，具备较强的主动管理能力。")
    elif annual >= 10:
        paras.append(f"该基金年化收益率{annual:.1f}%，长期趋势向上，但夏普比率{sharpe:.2f}显示风险收益比仍有提升空间。")
    elif annual >= 0:
        paras.append(f"该基金年化收益率{annual:.1f}%，整体正收益但超额收益不明显，需结合市场环境综合判断。")
    else:
        paras.append(f"该基金年化收益{annual:.1f}%，需关注收益为负的原因，谨慎评估其投资价值。")

    # 风险特征
    if max_draw <= 15:
        paras.append(f"历史最大回撤{max_draw:.1f}%，风险控制能力较强，适合风险偏好适中的投资者。")
    elif max_draw <= 25:
        paras.append(f"最大回撤{max_draw:.1f}%，处于主流偏股基金正常区间，需关注极端行情下的风险承受能力。")
    else:
        paras.append(f"最大回撤{max_draw:.1f}%，波动较大，投资者需具备较高的风险承受能力。")

    # 投资风格
    if style != '未知':
        paras.append(f"基金风格定位为「{style}」，重点配置「{first_ind}」行业（占比{info.get('行业占比','N/A')}），持仓集中度{conc}。")

    # 三性总评
    passed = [k for k, v in three_natures.items() if v['result'].startswith('通过')]
    if len(passed) == 3:
        paras.append(f"三性校验全部通过（一致性、稳定性、有效性），投资逻辑清晰，可追溯性强。")
    elif len(passed) >= 2:
        paras.append(f"三性校验{len(passed)}/3项通过（{'、'.join(passed)}），整体可接受，建议持续跟踪。")
    else:
        paras.append(f"三性校验仅{len(passed)}/3项通过，投资逻辑需进一步验证，建议谨慎。")

    # 综合建议
    if total_4p >= 80 and len(passed) == 3:
        paras.append(f"综合4P评分{total_4p}/100分，建议【强烈推荐】——该基金在收益、风险、风格一致性等方面均表现优秀，适合作为核心持仓配置。")
    elif total_4p >= 60:
        paras.append(f"综合4P评分{total_4p}/100分，建议【建议持有】——中长期持有可期，建议结合个人风险偏好决定。")
    elif total_4p >= 45:
        paras.append(f"综合4P评分{total_4p}/100分，建议【谨慎关注】——适合风险偏好较高的投资者，不宜重仓。")
    else:
        paras.append(f"综合4P评分{total_4p}/100分，建议【不建议投资】——当前各项指标未达优，建议等待更好时机或寻找更优标的。")

    return paras

@app.route('/api/fund/backtest', methods=['GET'])
def get_fund_backtest():
    fund_code = request.args.get('fund_code', '').strip()
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    
    if not fund_code:
        return jsonify({'error': '请输入基金代码'})
    
    if not start_date:
        start_date = (datetime.now() - timedelta(days=365)).strftime('%Y%m%d')
    if not end_date:
        end_date = datetime.now().strftime('%Y%m%d')
    
    # 解析用户指定的时间范围
    start_date_obj = datetime.strptime(start_date, '%Y%m%d')
    end_date_obj = datetime.strptime(end_date, '%Y%m%d')
    
    try:
        from fund_crawler import crawl_fund_nav_df
        fund_hist = crawl_fund_nav_df(fund_code)
        import pandas as _pd
        if isinstance(fund_hist, list):
            fund_hist = _pd.DataFrame(fund_hist)

        if not fund_hist.empty:
            fund_hist['日期'] = _pd.to_datetime(fund_hist['净值日期'])
            fund_hist = fund_hist.rename(columns={'单位净值': '收盘'})
            # 过滤用户指定的时间范围
            fund_hist = fund_hist[(fund_hist['日期'] >= start_date_obj) & (fund_hist['日期'] <= end_date_obj)]
        else:
            # 东方财富直调 ETF/LOF 历史净值（尝试上交所+深交所）
            fund_hist = _http_fetch_etf_lof_nav(fund_code, start_date, end_date)
            if not fund_hist.empty:
                fund_hist['日期'] = _pd.to_datetime(fund_hist['净值日期'])

        if fund_hist.empty:
            return jsonify({'error': '未找到基金数据'})

        if '日期' in fund_hist.columns:
            fund_hist['日期'] = _pd.to_datetime(fund_hist['日期'])
        else:
            fund_hist['日期'] = _pd.to_datetime(fund_hist.index)

        # 再次确保数据在时间范围内
        fund_hist = fund_hist[(fund_hist['日期'] >= start_date_obj) & (fund_hist['日期'] <= end_date_obj)]
        fund_hist = fund_hist.sort_values('日期')

        if fund_hist.empty:
            return jsonify({'error': '指定时间段内无基金数据'})

        prices = fund_hist['收盘'].tolist()
        dates = fund_hist['日期'].dt.strftime('%Y-%m-%d').tolist()

        if len(prices) >= 2:
            total_return = (prices[-1] - prices[0]) / prices[0] * 100
            max_price = max(prices)
            min_price = min(prices)
            volatility = (max_price - min_price) / prices[0] * 100
        else:
            total_return = 0
            max_price = prices[0] if prices else 0
            min_price = prices[0] if prices else 0
            volatility = 0

        fig = go.Figure()

        # 渐变色面积图效果
        fig.add_trace(go.Scatter(
            x=dates,
            y=prices,
            mode='lines',
            name='净值',
            line=dict(
                color='#F59E0B',
                width=2.5,
                shape='spline',
                smoothing=0.3
            ),
            fill='tonexty',
            fillcolor='rgba(245, 158, 11, 0.15)',
            hovertemplate='<b>日期</b>: %{x}<br><b>净值</b>: %{y:.4f}<extra></extra>'
        ))

        # 添加价格区间范围（可选，用于显示波动）
        fig.update_layout(
            title=dict(
                text=f'<b style="color:#F59E0B">📈</b> {fund_code} 历史净值走势 <span style="font-size:12px;color:#94A3B8">({start_date} ~ {end_date})</span>',
                font=dict(size=16, family='IBM Plex Sans', color='#F8FAFC'),
                x=0.5,
                xanchor='center'
            ),
            xaxis=dict(
                title='',
                showgrid=True,
                gridcolor='rgba(245, 158, 11, 0.1)',
                linecolor='rgba(148, 163, 184, 0.3)',
                rangeslider=dict(visible=True, thickness=0.05),
                range=[dates[0] if dates else None, dates[-1] if dates else None],
                tickfont=dict(size=11, color='#94A3B8'),
                hoverformat='%Y-%m-%d'
            ),
            yaxis=dict(
                title='单位净值',
                showgrid=True,
                gridcolor='rgba(245, 158, 11, 0.1)',
                linecolor='rgba(148, 163, 184, 0.3)',
                tickfont=dict(size=11, color='#94A3B8'),
                hoverformat='.4f'
            ),
            paper_bgcolor='rgba(30, 41, 59, 0.95)',
            plot_bgcolor='rgba(15, 23, 42, 0.4)',
            margin=dict(l=50, r=30, t=60, b=50),
            height=380,
            hovermode='x unified',
            hoverlabel=dict(
                bgcolor='rgba(15, 23, 42, 0.95)',
                bordercolor='rgba(245, 158, 11, 0.5)',
                font=dict(color='white', family='IBM Plex Sans')
            ),
            annotations=[
                dict(
                    text=f'总收益率: <b>{"+" if total_return >= 0 else ""}{total_return:.2f}%</b>',
                    x=0.02, y=0.98, xref='paper', yref='paper',
                    showarrow=False,
                    font=dict(size=13, color='#EF4444' if total_return >= 0 else '#10B981'),
                    bgcolor='rgba(30, 41, 59, 0.9)',
                    borderpad=4
                )
            ]
        )
        
        chart_html = fig.to_html(full_html=False, include_plotlyjs='cdn')
        
        return jsonify({
            'success': True,
            'data': {
                'dates': dates,
                'prices': prices,
                'total_return': round(total_return, 2),
                'volatility': round(volatility, 2),
                'start_price': prices[0],
                'end_price': prices[-1],
                'max_price': max_price,
                'min_price': min_price,
                'chart': chart_html
            }
        })
    except Exception as e:
        return jsonify({'error': f'获取回测数据失败: {str(e)}'})

@app.route('/api/fund/dca', methods=['GET'])
def calculate_dca():
    fund_code = request.args.get('fund_code', '').strip()
    amount = request.args.get('amount', 1000)
    frequency = request.args.get('frequency', 'weekly')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    
    try:
        amount = float(amount)
    except:
        amount = 1000
    
    if not fund_code:
        return jsonify({'error': '请输入基金代码'})
    
    if not start_date:
        start_date = (datetime.now() - timedelta(days=365)).strftime('%Y%m%d')
    if not end_date:
        end_date = datetime.now().strftime('%Y%m%d')
    
    # 解析用户指定的时间范围
    start_date_obj = datetime.strptime(start_date, '%Y%m%d')
    end_date_obj = datetime.strptime(end_date, '%Y%m%d')
    
    try:
        from fund_crawler import crawl_fund_nav_df
        fund_hist = crawl_fund_nav_df(fund_code)
        if isinstance(fund_hist, list):
            fund_hist = pd.DataFrame(fund_hist)

        if not fund_hist.empty:
            fund_hist['日期'] = pd.to_datetime(fund_hist['净值日期'])
            fund_hist = fund_hist.rename(columns={'单位净值': '收盘'})
            # 过滤用户指定的时间范围
            fund_hist = fund_hist[(fund_hist['日期'] >= start_date_obj) & (fund_hist['日期'] <= end_date_obj)]
        else:
            # 东方财富直调 ETF/LOF 历史净值（尝试上交所+深交所）
            fund_hist = _http_fetch_etf_lof_nav(fund_code, start_date, end_date)
            if not fund_hist.empty:
                fund_hist['日期'] = pd.to_datetime(fund_hist['净值日期'])

        if fund_hist.empty:
            return jsonify({'error': '未找到基金数据'})
        
        if '日期' in fund_hist.columns:
            fund_hist['日期'] = pd.to_datetime(fund_hist['日期'])
        else:
            fund_hist['日期'] = pd.to_datetime(fund_hist.index)
        
        # 再次确保数据在时间范围内
        fund_hist = fund_hist[(fund_hist['日期'] >= start_date_obj) & (fund_hist['日期'] <= end_date_obj)]
        fund_hist = fund_hist.sort_values('日期')
        
        if fund_hist.empty:
            return jsonify({'error': '指定时间段内无基金数据'})
        
        if frequency == 'daily':
            invest_dates = fund_hist['日期'].tolist()
        elif frequency == 'weekly':
            fund_hist['week'] = fund_hist['日期'].dt.isocalendar().week
            fund_hist['year'] = fund_hist['日期'].dt.year
            invest_dates = fund_hist.groupby(['year', 'week']).first()['日期'].tolist()
        else:
            fund_hist['month'] = fund_hist['日期'].dt.month
            fund_hist['year'] = fund_hist['日期'].dt.year
            invest_dates = fund_hist.groupby(['year', 'month']).first()['日期'].tolist()
        
        # 确保投资日期在用户指定的时间范围内
        invest_dates = [date for date in invest_dates if start_date_obj <= date <= end_date_obj]
        
        total_invested = 0
        total_shares = 0
        investment_records = []
        
        for date in invest_dates:
            row = fund_hist[fund_hist['日期'] == date]
            if not row.empty:
                price = row['收盘'].iloc[0]
                shares = amount / price
                total_invested += amount
                total_shares += shares
                investment_records.append({
                    'date': date.strftime('%Y-%m-%d'),
                    'price': round(price, 4),
                    'amount': amount,
                    'shares': round(shares, 4),
                    'total_invested': round(total_invested, 2),
                    'total_shares': round(total_shares, 4)
                })
        
        if total_shares > 0:
            # 使用用户指定的结束日期的价格
            end_price_row = fund_hist[fund_hist['日期'] <= end_date_obj].tail(1)
            if not end_price_row.empty:
                current_price = end_price_row['收盘'].iloc[0]
            else:
                current_price = fund_hist['收盘'].iloc[-1]
            current_value = total_shares * current_price
            profit = current_value - total_invested
            profit_rate = (profit / total_invested) * 100 if total_invested > 0 else 0
        else:
            current_value = 0
            profit = 0
            profit_rate = 0
        
        portfolio_values = []
        if investment_records and len(investment_records) > 0 and len(fund_hist) > 0:
            # 按用户指定的时间范围生成组合价值
            for idx, row in fund_hist.iterrows():
                if start_date_obj <= row['日期'] <= end_date_obj:
                    value = total_shares * row['收盘']
                    portfolio_values.append({
                        'date': row['日期'].strftime('%Y-%m-%d'),
                        'value': round(value, 2),
                        'invested': round(total_invested, 2)
                    })
        
        invested_values = []
        invested_dates = []
        if investment_records:
            invested_values = [r['total_invested'] for r in investment_records]
            invested_dates = [r['date'] for r in investment_records]
        
        fig = go.Figure()

        # 累计投入曲线（虚线）
        fig.add_trace(go.Scatter(
            x=invested_dates,
            y=invested_values,
            mode='lines',
            name='累计投入',
            line=dict(
                color='#8B5CF6',
                width=2.5,
                dash='dash',
                shape='spline',
                smoothing=0.3
            ),
            hovertemplate='<b>日期</b>: %{x}<br><b>累计投入</b>: ¥%{y:,.2f}<extra></extra>'
        ))

        portfolio_dates = []
        portfolio_values_list = []
        if portfolio_values:
            sample_portfolio = portfolio_values[::max(1, len(portfolio_values)//20)]
            for item in sample_portfolio:
                if item and 'date' in item and 'value' in item:
                    portfolio_dates.append(item['date'])
                    portfolio_values_list.append(item['value'])

        # 账户价值曲线（面积图）
        profit_color = '#EF4444' if profit >= 0 else '#10B981'
        fig.add_trace(go.Scatter(
            x=portfolio_dates,
            y=portfolio_values_list,
            mode='lines',
            name='账户价值',
            fill='tonexty',
            fillcolor=f'rgba(239, 68, 68, 0.2)' if profit >= 0 else 'rgba(16, 185, 129, 0.2)',
            line=dict(
                color=profit_color,
                width=2.5,
                shape='spline',
                smoothing=0.3
            ),
            hovertemplate='<b>日期</b>: %{x}<br><b>账户价值</b>: ¥%{y:,.2f}<extra></extra>'
        ))

        fig.update_layout(
            title=dict(
                text=f'<b style="color:#8B5CF6">💰</b> {fund_code} 定投收益走势 <span style="font-size:12px;color:#94A3B8">(收益率: <b style="color:{profit_color}">{"+" if profit_rate >= 0 else ""}{profit_rate:.2f}%</b>)</span>',
                font=dict(size=16, family='IBM Plex Sans', color='#F8FAFC'),
                x=0.5,
                xanchor='center'
            ),
            xaxis=dict(
                title='',
                showgrid=True,
                gridcolor='rgba(139, 92, 246, 0.1)',
                linecolor='rgba(148, 163, 184, 0.3)',
                tickfont=dict(size=11, color='#94A3B8'),
                hoverformat='%Y-%m-%d'
            ),
            yaxis=dict(
                title='金额 (元)',
                showgrid=True,
                gridcolor='rgba(139, 92, 246, 0.1)',
                linecolor='rgba(148, 163, 184, 0.3)',
                tickfont=dict(size=11, color='#94A3B8'),
                hoverformat=',.2f'
            ),
            paper_bgcolor='rgba(30, 41, 59, 0.95)',
            plot_bgcolor='rgba(15, 23, 42, 0.4)',
            margin=dict(l=50, r=30, t=70, b=50),
            height=380,
            hovermode='x unified',
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1,
                bgcolor='rgba(30, 41, 59, 0.9)',
                bordercolor='rgba(148, 163, 184, 0.3)',
                borderwidth=1,
                font=dict(color='#F8FAFC')
            ),
            hoverlabel=dict(
                bgcolor='rgba(15, 23, 42, 0.95)',
                bordercolor='rgba(139, 92, 246, 0.5)',
                font=dict(color='white', family='IBM Plex Sans')
            ),
            annotations=[
                dict(
                    text=f'总收益: <b style="color:{profit_color}">{"+" if profit >= 0 else ""}¥{profit:,.2f}</b>',
                    x=0.02, y=0.98, xref='paper', yref='paper',
                    showarrow=False,
                    font=dict(size=14, color=profit_color),
                    bgcolor='rgba(30, 41, 59, 0.9)',
                    borderpad=6,
                    bordercolor='rgba(148, 163, 184, 0.3)'
                )
            ]
        )
        
        chart_html = fig.to_html(full_html=False, include_plotlyjs='cdn')
        
        return jsonify({
            'success': True,
            'data': {
                'total_invested': round(total_invested, 2),
                'current_value': round(current_value, 2),
                'profit': round(profit, 2),
                'profit_rate': round(profit_rate, 2),
                'total_shares': round(total_shares, 4),
                'investment_count': len(investment_records),
                'chart': chart_html,
                'records': investment_records[-10:] if len(investment_records) > 10 else investment_records
            }
        })
    except Exception as e:
        return jsonify({'error': f'定投计算失败: {str(e)}'})

# 基金列表内存缓存
fund_list_cache = {}  # {code: name}
# 内存搜索索引：按名称和代码的首字母分组，加速模糊搜索
fund_list_index = {}  # {char: [(code, name), ...]}

def rebuild_fund_list_index(fund_list):
    """重建内存搜索索引"""
    global fund_list_index
    fund_list_index = {}
    for code, name in fund_list.items():
        if name is None:
            name = ''
        # 索引名称中的每个字符
        for char in str(name):
            if char not in fund_list_index:
                fund_list_index[char] = []
            fund_list_index[char].append((code, name))
        # 索引代码中的每个字符
        for char in code:
            if char not in fund_list_index:
                fund_list_index[char] = []
            fund_list_index[char].append((code, name))

def get_fund_list_from_cache():
    """从缓存获取全量基金列表"""
    global fund_list_index
    cache_key = generate_cache_key(CACHE_CONFIG['fund_list']['prefix'], 'all')

    # 1. 先查Redis
    if REDIS_AVAILABLE:
        try:
            data = r.get(cache_key)
            if data:
                fund_list = json.loads(data)
                if not fund_list_index:
                    rebuild_fund_list_index(fund_list)
                return fund_list
        except:
            pass

    # 2. 再查内存缓存
    if cache_key in memory_cache:
        fund_list = memory_cache.get(cache_key)
        if not fund_list_index:
            rebuild_fund_list_index(fund_list)
        return fund_list

    return None

def save_fund_list_to_cache(fund_list):
    """保存基金列表到缓存"""
    global fund_list_index
    cache_key = generate_cache_key(CACHE_CONFIG['fund_list']['prefix'], 'all')
    expiry = CACHE_CONFIG['fund_list']['expiry']

    # 保存到内存缓存
    memory_cache.set(cache_key, fund_list)

    # 重建索引
    rebuild_fund_list_index(fund_list)

    # 保存到Redis
    if REDIS_AVAILABLE:
        try:
            r.setex(cache_key, expiry, json.dumps(fund_list))
        except:
            pass

    # 保存到SQLite数据库
    try:
        import sqlite3
        conn = sqlite3.connect(SQLITE_DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS fund_list_cache (
                code TEXT PRIMARY KEY,
                name TEXT
            )
        ''')
        for code, name in fund_list.items():
            cursor.execute('INSERT OR REPLACE INTO fund_list_cache VALUES (?, ?)', (code, name))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"保存到SQLite失败: {e}")

    return fund_list

def load_fund_list_from_db():
    """从数据库加载基金列表到缓存"""
    cache_key = generate_cache_key(CACHE_CONFIG['fund_list']['prefix'], 'all')
    fund_list = {}

    # 从MySQL（可能失败，继续往下走）
    mysql_failed = False
    try:
        pool = get_mysql_pool()
        if pool:
            conn = pool.get_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT fund_code, fund_name FROM fund_basic LIMIT 5000')
            for row in cursor.fetchall():
                fund_list[str(row['fund_code'])] = row['fund_name']
            conn.close()
        else:
            mysql_failed = True
    except Exception as e:
        print(f"MySQL加载失败: {e}")
        mysql_failed = True

    # 从SQLite（当MySQL没有返回足够数据时，加载Redis缓存的完整列表）
    if mysql_failed or len(fund_list) < 100:
        try:
            import sqlite3
            conn = sqlite3.connect(SQLITE_DB_PATH)
            cursor = conn.cursor()
            cursor.execute('SELECT code, name FROM fund_list_cache LIMIT 50000')
            for row in cursor.fetchall():
                fund_list[str(row[0])] = row[1]
            conn.close()
        except Exception as e:
            print(f"SQLite加载失败: {e}")

    # 添加FUND_NAME_MAP
    for code, name in FUND_NAME_MAP.items():
        if code not in fund_list:
            fund_list[code] = name

    if fund_list:
        memory_cache.set(cache_key, fund_list)

    return fund_list

@app.route('/api/fund/manager', methods=['GET'])
def get_fund_manager():
    """独立接口：获取基金经理详情（异步加载，不阻塞主信息渲染）"""
    fund_code = request.args.get('fund_code', '').strip()
    if not fund_code:
        return jsonify({'success': False, 'message': '请输入基金代码'})

    # 1. 查缓存
    cache_key = generate_cache_key('fund:manager', fund_code)
    if REDIS_AVAILABLE:
        try:
            data = r.get(cache_key)
            if data:
                return jsonify({'success': True, 'data': json.loads(data), 'from_cache': True})
        except Exception:
            pass
    elif cache_key in memory_cache and (datetime.now() - memory_cache.get(cache_key)['ts']).seconds < 3600:
        return jsonify({'success': True, 'data': memory_cache.get(cache_key)['data'], 'from_cache': True})

    # 2. 回源
    manager_info = fetch_manager_info_with_timeout(fund_code, timeout=8)
    if manager_info:
        evaluation = generate_manager_evaluation(manager_info)
        manager_info['evaluation'] = evaluation
        # 缓存 1 小时
        if REDIS_AVAILABLE:
            try:
                r.setex(cache_key, 3600, json.dumps(manager_info, ensure_ascii=False))
            except Exception:
                pass
        else:
            memory_cache.set(cache_key, {'data': manager_info, 'ts': datetime.now()})
        return jsonify({'success': True, 'data': manager_info})
    return jsonify({'success': False, 'message': '暂无基金经理数据'})

@app.route('/api/fund/search', methods=['GET'])
def search_fund():
    keyword = request.args.get('keyword', '').strip()

    if not keyword or len(keyword) < 2:
        return jsonify({'success': True, 'data': []})

    # 清理过期缓存
    now = datetime.now()
    expired_keys = [k for k, t in search_cache_timestamps.items()
                   if (now - t).total_seconds() > SEARCH_CACHE_TTL]
    for k in expired_keys:
        search_cache.pop(k, None)
        search_cache_timestamps.pop(k, None)

    # 检查搜索结果缓存
    keyword_lower = keyword.lower()
    if keyword_lower in search_cache:
        return jsonify({'success': True, 'data': search_cache[keyword_lower], 'cached': True})

    results = []

    # 1. 优先从基金列表缓存搜索（使用索引加速）
    fund_list = get_fund_list_from_cache()
    if fund_list:
        # 使用索引快速查找
        # 获取第一个字符对应的候选集
        first_char = keyword_lower[0] if keyword_lower else ''
        candidates = set()
        if first_char in fund_list_index:
            for code, name in fund_list_index[first_char]:
                candidates.add((code, name))

        for code, name in candidates:
            if keyword_lower in code.lower() or (name and keyword_lower in name.lower()):
                results.append({'code': code, 'name': name})
                if len(results) >= 10:
                    # 缓存结果
                    search_cache[keyword_lower] = results
                    search_cache_timestamps[keyword_lower] = now
                    return jsonify({'success': True, 'data': results})

        # 如果索引没找到足够的，使用完整列表
        if len(results) < 10:
            for code, name in fund_list.items():
                if keyword_lower in code.lower() or (name and keyword_lower in name.lower()):
                    if not any(item['code'] == code for item in results):
                        results.append({'code': code, 'name': name})
                    if len(results) >= 10:
                        break

        if results:
            # 缓存结果
            search_cache[keyword_lower] = results
            search_cache_timestamps[keyword_lower] = now
            return jsonify({'success': True, 'data': results})

    # 2. 缓存为空，从数据库加载
    fund_list = load_fund_list_from_db()
    if fund_list:
        rebuild_fund_list_index(fund_list)
        for code, name in fund_list.items():
            if keyword_lower in code.lower() or (name and keyword_lower in name.lower()):
                results.append({'code': code, 'name': name})
                if len(results) >= 10:
                    break
        # 缓存结果
        search_cache[keyword_lower] = results
        search_cache_timestamps[keyword_lower] = now
        return jsonify({'success': True, 'data': results})

    # 3. 数据库也没有，直接调天天基金接口获取
    try:
        new_fund_list = _http_fetch_fund_list_via_eastmoney()
        if new_fund_list and len(new_fund_list) > 100:
            save_fund_list_to_cache(new_fund_list)
            rebuild_fund_list_index(new_fund_list)

        for code, name in new_fund_list.items():
            if keyword_lower in code.lower() or (name and keyword_lower in name.lower()):
                if not any(item['code'] == code for item in results):
                    results.append({'code': code, 'name': name})
                if len(results) >= 10:
                    break
    except Exception as e:
        print(f"从天天基金接口搜索失败: {e}")

    # 缓存结果
    search_cache[keyword_lower] = results
    search_cache_timestamps[keyword_lower] = now

    return jsonify({
        'success': True,
        'data': results
    })

@app.route('/api/fund/valuation', methods=['GET'])
def get_fund_valuation():
    fund_code = request.args.get('fund_code', '').strip()
    if not fund_code:
        return jsonify({'error': '请输入基金代码'})
    try:
        from datetime import datetime, time as dtime
        from fund_crawler import _fetch_realtime_nav, crawl_fund_nav_df

        fund_name = get_fund_name(fund_code)
        now = datetime.now()
        is_weekend = now.weekday() >= 5
        trading_start = dtime(9, 30)
        trading_end = dtime(15, 0)
        is_trading_time = not is_weekend and trading_start <= now.time() <= trading_end
        is_after_close = not is_weekend and now.time() >= trading_end

        if is_after_close:
            trading_status = '已收盘'
        elif is_trading_time:
            trading_status = '交易中'
        else:
            trading_status = '非交易时间'

        # 策略1：调用天天基金实时估值接口（fundgz.1234567.com.cn）
        realtime = _fetch_realtime_nav(fund_code)
        if realtime and realtime.get('net_value'):
            net_value = realtime.get('net_value', 0)
            est_value = realtime.get('estimated_value') or net_value
            day_growth = realtime.get('day_growth', 0)
            nav_date = realtime.get('nav_date', '')
            est_time = realtime.get('estimated_time', '')

            # 计算涨跌额：用上一日净值
            change_amount = 0
            try:
                history = crawl_fund_nav_df(fund_code, years=1)
                if history and len(history) >= 2:
                    prev_nav = float(history[-2].get('单位净值', 0))
                    if prev_nav > 0:
                        change_amount = round(net_value - prev_nav, 4)
            except Exception:
                pass

            return jsonify({
                'success': True,
                'data': {
                    '基金代码': fund_code,
                    '基金名称': fund_name,
                    '实时估值': est_value,
                    '估算涨跌幅': day_growth,
                    '估算涨跌额': change_amount,
                    '单位净值': net_value,
                    '净值日期': nav_date,
                    '估值时间': est_time or now.strftime('%Y-%m-%d %H:%M:%S'),
                    '计算方式': '天天基金实时估值接口',
                    '交易状态': trading_status
                },
                'from_cache': False
            })

        # 策略2：fallback - 用历史净值最新一条
        try:
            history = crawl_fund_nav_df(fund_code, years=1)
            if history:
                latest = history[-1]
                latest_nav = float(latest.get('单位净值', 0))
                latest_date = str(latest.get('净值日期', ''))[:10]
                change_pct = 0
                change_amount = 0
                if len(history) >= 2:
                    prev = float(history[-2].get('单位净值', 0))
                    if prev > 0:
                        change_pct = round((latest_nav - prev) / prev * 100, 2)
                        change_amount = round(latest_nav - prev, 4)
                return jsonify({
                    'success': True,
                    'data': {
                        '基金代码': fund_code,
                        '基金名称': fund_name,
                        '实时估值': latest_nav,
                        '估算涨跌幅': change_pct,
                        '估算涨跌额': change_amount,
                        '单位净值': latest_nav,
                        '净值日期': latest_date,
                        '估值时间': now.strftime('%Y-%m-%d %H:%M:%S'),
                        '计算方式': '最新公布净值',
                        '交易状态': trading_status
                    },
                    'from_cache': False
                })
        except Exception as e2:
            print(f'历史净值获取失败: {e2}')

        return jsonify({'error': '无法获取基金估值数据'})
    except Exception as e:
        print(f'get_fund_valuation失败: {e}')
        return jsonify({'error': f'获取实时估值失败: {str(e)}'})

# ══════════════════════════════════════════════════════════════
# 天天基金/东方财富直调接口（替代 akshare）
# ══════════════════════════════════════════════════════════════

def _eastmoney_get(url: str, headers: dict = None, timeout: int = 15) -> str:
    """GET 请求天天基金/东方财富接口"""
    import urllib.request
    h = dict(_EASTMONEY_HEADERS)
    if headers:
        h.update(headers)
    try:
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode('utf-8', errors='replace')
    except Exception:
        return ""


def _http_fetch_etf_lof_nav(fund_code: str, start_date: str, end_date: str):
    """
    通过东方财富 push2his 接口获取 ETF/LOF 历史净值。
    尝试上交所(secid=1.)和深交所(secid=0.)两种格式。
    返回 DataFrame: {净值日期, 单位净值, 日增长率} 或空 DataFrame。
    """
    import pandas as _pd2
    for market_prefix in ('1', '0'):
        secid = f"{market_prefix}.{fund_code}"
        url = (
            f"https://push2his.eastmoney.com/api/qt/stock/lszc/get"
            f"?fields1=f1,f2,f3,f4,f5,f6"
            f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
            f"&ut=7eea3edcaed734bea9cbfc24409ed989"
            f"&klt=01&fqt=1&secid={secid}"
            f"&beg={start_date}&end={end_date}&smplmt=460&lmt=1000000"
        )
        raw = _eastmoney_get(url)
        if not raw:
            continue
        try:
            import json
            data = json.loads(raw)
            klines = data.get('data', {}).get('lszc', [])
            if not klines:
                continue
        except Exception:
            continue

        dates, navs, changes = [], [], []
        for item in klines:
            date_str = item.get('f51', '')
            if not date_str:
                continue
            try:
                nav = float(item.get('f53', 0))
                change = float(item.get('f54', 0))
            except (ValueError, TypeError):
                continue
            dates.append(date_str)
            navs.append(nav)
            changes.append(change / 100)

        if len(navs) >= 5:
            df = _pd2.DataFrame({'净值日期': dates, '单位净值': navs, '日增长率': changes})
            return df

    return _pd2.DataFrame()


def _http_fetch_realtime_valuation(fund_code: str) -> dict:
    """
    通过 fundgz.1234567.com.cn 获取单只基金实时估值。
    返回 dict: {fund_code, fund_name, estimated_value, day_growth, net_value, nav_date, gztime}
    失败返回空 dict。
    """
    import time, re, json
    ts = int(time.time() * 1000)
    url = f"https://fundgz.1234567.com.cn/js/{fund_code}.js?rt={ts}"
    raw = _eastmoney_get(url)
    if not raw:
        return {}
    m = re.search(r"jsonpgz\((.+)\)", raw)
    if not m:
        return {}
    try:
        data = json.loads(m.group(1))
    except Exception:
        return {}
    return {
        'fund_code': data.get('fundcode', ''),
        'fund_name': data.get('name', ''),
        'net_value': float(data.get('dwjz', 0)),
        'nav_date': data.get('jzrq', ''),
        'day_growth': float(data.get('gszzl', 0)),
        'estimated_value': float(data.get('gsz', 0)),
        'gztime': data.get('gztime', ''),
    }


def _http_fetch_fund_list_via_eastmoney() -> dict:
    """
    直接调天天基金排行榜接口获取全市场基金列表。
    返回 dict: {fund_code: fund_name, ...}
    """
    url = "https://fund.eastmoney.com/data/rankhandler.aspx"
    params = (
        "op=ph&dt=kf&ft=gp&rs=&gs=0&sc=1nzf&st=desc"
        "&sd=2024-04-01&ed=2025-04-01&qdii=|&tabRate=0"
        "&status=0&pi=1&pn=10000&dx=1&v="
    )
    full_url = f"{url}?{params}"
    raw = _eastmoney_get(full_url, headers={"Referer": "https://fund.eastmoney.com/data/fundranking.html"})
    if not raw:
        return {}

    # 返回的是 JS 变量赋值语句: var rank_datas = [{...}, ...];
    import re, json
    m = re.search(r'var rank_datas\s*=\s*(\[.+?\]);', raw, re.DOTALL)
    if not m:
        return {}
    try:
        items = json.loads(m.group(1))
    except Exception:
        return {}

    result = {}
    for item in items:
        # item 格式: "基金代码,基金简称,..."
        parts = item.split(',')
        if len(parts) >= 2:
            code = parts[0].strip()
            name = parts[1].strip().strip('"')
            if code and name:
                result[code] = name
    return result


if __name__ == '__main__':
    print('Starting Flask application...')
    try:
        # 初始化数据库连接池
        _db_init(
            mysql_config={
                'user': 'yskey',
                'password': 'yskey',
                'host': '127.0.0.1',
                'port': 3306,
                'database': 'fund_data',
                'charset': 'utf8mb4',
                'ssl_disabled': True,
            },
            sqlite_db_path=SQLITE_DB_PATH,
            pool_size=5
        )
        # 初始化分析报告历史库（MySQL建表）
        _init_analysis_history_table()
        # 启动时预热热点基金分析报告（后台异步）
        _warmup_top_funds_report()
        # 调度每周刷新（每周日凌晨2点执行）
        _schedule_weekly_refresh()
        # 初始化基金组合管理模块
        from portfolio_manager import register_routes, init_portfolio_tables
        init_portfolio_tables()
        register_routes(app)
        # 初始化用户认证模块
        from auth_manager import init_auth_tables, login_required, portfolio_access_required
        init_auth_tables()
        # 注册认证路由
        from auth_manager import handle_login, handle_logout, handle_change_password, handle_user_info
        app.add_url_rule('/api/auth/login', 'auth_login', handle_login, methods=['POST'])
        app.add_url_rule('/api/auth/logout', 'auth_logout', handle_logout, methods=['POST'])
        app.add_url_rule('/api/auth/change-password', 'auth_change_password', handle_change_password, methods=['POST'])
        app.add_url_rule('/api/auth/user-info', 'auth_user_info', handle_user_info, methods=['GET'])
        # 注册订阅路由
        from auth_manager import handle_contact_subscribe, handle_subscription_status
        app.add_url_rule('/api/subscription/email-subscribe', 'sub_email_subscribe', handle_contact_subscribe, methods=['POST'])
        app.add_url_rule('/api/subscription/status', 'sub_status', handle_subscription_status, methods=['GET'])
        # 注册支付路由
        from payment_gateway import (create_alipay_order, create_wechat_order,
                                     verify_alipay_notify, verify_wechat_notify,
                                     mock_pay, get_user_orders, get_user_active_sub,
                                     get_payment_status, PLANS)
        def api_create_alipay():
            data = request.get_json() or {}
            contact = data.get('email', data.get('wechat', ''))
            if not contact or len(str(contact).strip()) < 3:
                return jsonify({'success': False, 'error': '请输入邮箱或微信号，用于接收账户信息'})
            return jsonify(create_alipay_order(str(contact).strip(), data.get('plan_type', 'monthly')))
        def api_create_wechat():
            data = request.get_json() or {}
            contact = data.get('email', data.get('wechat', ''))
            if not contact or len(str(contact).strip()) < 3:
                return jsonify({'success': False, 'error': '请输入邮箱或微信号，用于接收账户信息'})
            return jsonify(create_wechat_order(str(contact).strip(), data.get('plan_type', 'monthly')))
        def api_alipay_notify():
            return jsonify(verify_alipay_notify(dict(request.form)))
        def api_alipay_return():
            return '<html><body><script>window.opener&&window.opener.location.reload();window.close();</script><p>支付完成，请关闭页面</p></body></html>'
        def api_wechat_notify():
            data = request.data.decode('utf-8')
            ok, msg = verify_wechat_notify(data)
            return '<xml><return_code><![CDATA[SUCCESS]]></return_code></xml>' if ok else '<xml><return_code><![CDATA[FAIL]]></return_code></xml>'
        def api_mock_pay():
            out_trade_no = request.args.get('out_trade_no', '')
            return jsonify(mock_pay(out_trade_no))
        def api_user_orders():
            from flask import session
            uid = session.get('user_id')
            if not uid: return jsonify({'success': False, 'error': '请先登录'})
            return jsonify({'success': True, 'data': get_user_orders(uid)})
        def api_user_sub():
            from flask import session
            uid = session.get('user_id')
            if not uid: return jsonify({'success': False, 'error': '请先登录'})
            sub = get_user_active_sub(uid)
            return jsonify({'success': True, 'has_subscription': sub is not None, 'subscription': sub})
        def api_payment_status():
            poll_token = request.args.get('token', '')
            if not poll_token:
                return jsonify({'success': False, 'error': '缺少token参数'})
            return jsonify(get_payment_status(poll_token))
        def api_plans():
            return jsonify({'success': True, 'data': PLANS})

        app.add_url_rule('/api/payment/alipay/create', 'pay_alipay_create', api_create_alipay, methods=['POST'])
        app.add_url_rule('/api/payment/wechat/create', 'pay_wechat_create', api_create_wechat, methods=['POST'])
        app.add_url_rule('/api/payment/alipay/notify', 'pay_alipay_notify', api_alipay_notify, methods=['POST'])
        app.add_url_rule('/api/payment/alipay/return', 'pay_alipay_return', api_alipay_return, methods=['GET'])
        app.add_url_rule('/api/payment/wechat/notify', 'pay_wechat_notify', api_wechat_notify, methods=['POST'])
        app.add_url_rule('/api/payment/mock-pay', 'pay_mock', api_mock_pay, methods=['GET'])
        app.add_url_rule('/api/payment/orders', 'pay_orders', api_user_orders, methods=['GET'])
        app.add_url_rule('/api/payment/subscription', 'pay_subscription', api_user_sub, methods=['GET'])
        app.add_url_rule('/api/payment/status', 'pay_status', api_payment_status, methods=['GET'])
        app.add_url_rule('/api/payment/plans', 'pay_plans', api_plans, methods=['GET'])

        app.run(debug=False, host='0.0.0.0', port=5001)
    except Exception as e:
        print(f'Error starting Flask application: {e}')
        import traceback
        traceback.print_exc()