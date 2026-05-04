from flask import Flask, render_template, request, jsonify
import pandas as pd
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

# ── 导入 routes_fund 中的函数（re-export 供 routes_main.py 使用）──
from routes_fund import fetch_fund_info, get_fund_info_from_db, get_fund_name

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
        # 注册回测 + 定投蓝图
        from routes_backtest import backtest_bp
        app.register_blueprint(backtest_bp)
        # 注册主页 + 搜索蓝图
        from routes_main import main_bp
        app.register_blueprint(main_bp)
        # 注册基金信息 + 经理 + 估值蓝图
        from routes_fund import fund_bp
        app.register_blueprint(fund_bp)
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