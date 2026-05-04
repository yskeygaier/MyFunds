# -*- coding: utf-8 -*-
from flask import Flask, render_template, request, jsonify
import json
from datetime import datetime, timedelta, date
import os
import sys
import pymysql

# ── 分析报告生成锁（防止同一基金重复生成）──────────────────────────────────
# 键：基金代码，值：生成任务状态 'generating' 或 None
REPORT_GENERATING = {}

# 将项目目录加入import路径，以便加载fund_analyzer模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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
        host='127.0.0.1',
        port=6379,
        db=0,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
        retry_on_timeout=True,
        max_connections=50
    )
    r.ping()
    REDIS_AVAILABLE = True
    print("Redis连接成功")
except Exception as e:
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
fund_name_cache = {}
FUND_NAME_CACHE_TTL = 86400

# 基金数据刷新配置
FUND_DATA_REFRESH_HOURS = 24
fund_refresh_times = {}
refresh_lock = __import__('threading').Lock()

# 缓存配置
CACHE_CONFIG = {
    'fund_info': {
        'expiry': 3600,
        'prefix': 'fund:info'
    },
    'fund_analysis_report': {
        'expiry': 3600,
        'prefix': 'fund:analysis_report'
    },
}

# ── 分析报告历史库配置（方案B+C）──────────────────────────────
ANALYSIS_HISTORY_WEEKS = 4
TOP_FUNDS_WARMUP_COUNT = 30

CACHE_CONFIG['fund_backtest'] = {
    'expiry': 7200,
    'prefix': 'fund:backtest'
}
CACHE_CONFIG['fund_dca'] = {
    'expiry': 7200,
    'prefix': 'fund:dca'
}
CACHE_CONFIG['fund_list'] = {
    'expiry': 86400,
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
app.config['PERMANENT_SESSION_LIFETIME'] = 86400

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
        # 注册分析报告蓝图（4P三性）
        from routes_analysis import analysis_bp, init_analysis_module
        app.register_blueprint(analysis_bp)
        init_analysis_module()
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
        # 注册公开路由（无需登录）+ 教练向导
        from routes_public import public_bp
        app.register_blueprint(public_bp)
        # 教练向导页面
        @app.route('/guide')
        def guide_page():
            return render_template('guide.html')
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
