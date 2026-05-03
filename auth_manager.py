# -*- coding: utf-8 -*-
"""
用户认证与权限管理模块
功能：用户注册/登录/登出、会话管理、首次登录强制改密、用户级订阅控制
"""
import hashlib
import os
import re
import json
from datetime import datetime, timedelta
from functools import wraps
from flask import request, jsonify, session
from db import db_execute



# ══════════════════════════════════════════════════════════════
# 数据库表
# ══════════════════════════════════════════════════════════════

_MYSQL_USERS = '''
    CREATE TABLE IF NOT EXISTS users (
        id INT AUTO_INCREMENT PRIMARY KEY,
        username VARCHAR(50) NOT NULL UNIQUE,
        password_hash VARCHAR(255) NOT NULL,
        display_name VARCHAR(100),
        email VARCHAR(100),
        wechat VARCHAR(50),
        must_change_password TINYINT(1) DEFAULT 1,
        is_active TINYINT(1) DEFAULT 1,
        is_admin TINYINT(1) DEFAULT 0,
        account_expiry DATETIME,
        created_by_subscription TINYINT(1) DEFAULT 0,
        last_login DATETIME,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
'''

_MYSQL_USER_SUBSCRIPTIONS = '''
    CREATE TABLE IF NOT EXISTS user_subscriptions (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        plan_type VARCHAR(20) NOT NULL,
        amount DECIMAL(10,2) NOT NULL,
        payment_method VARCHAR(30),
        payment_status VARCHAR(20) DEFAULT 'pending',
        out_trade_no VARCHAR(64),
        start_date DATETIME,
        end_date DATETIME,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
'''

_MYSQL_LOGIN_LOGS = '''
    CREATE TABLE IF NOT EXISTS login_logs (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT,
        username VARCHAR(50),
        ip_address VARCHAR(45),
        success TINYINT(1) DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
'''

_SQLITE_USERS = '''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        display_name TEXT,
        email TEXT,
        wechat TEXT,
        must_change_password INTEGER DEFAULT 1,
        is_active INTEGER DEFAULT 1,
        is_admin INTEGER DEFAULT 0,
        account_expiry TEXT,
        created_by_subscription INTEGER DEFAULT 0,
        last_login TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        updated_at TEXT DEFAULT (datetime('now','localtime'))
    )
'''

_SQLITE_USER_SUBSCRIPTIONS = '''
    CREATE TABLE IF NOT EXISTS user_subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        plan_type TEXT NOT NULL,
        amount REAL NOT NULL,
        payment_method TEXT,
        payment_status TEXT DEFAULT 'pending',
        out_trade_no TEXT,
        start_date TEXT,
        end_date TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )
'''

_SQLITE_LOGIN_LOGS = '''
    CREATE TABLE IF NOT EXISTS login_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT,
        ip_address TEXT,
        success INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )
'''




def init_auth_tables():
    """初始化认证相关数据库表"""
    tables = {
        'users': (_MYSQL_USERS, _SQLITE_USERS),
        'user_subscriptions': (_MYSQL_USER_SUBSCRIPTIONS, _SQLITE_USER_SUBSCRIPTIONS),
        'login_logs': (_MYSQL_LOGIN_LOGS, _SQLITE_LOGIN_LOGS),
    }
    for name, (mysql_sql, _) in tables.items():
        try:
            db_execute(mysql_sql, fetch=False)
            print(f"[auth] MySQL table '{name}' OK")
        except Exception as e:
            print(f"[auth] MySQL table '{name}' failed: {e}")
    # 兼容旧表：添加可能缺失的新列
    _migrate_users_table()
    # SQLite fallback
    import app as _app
    if _app.SQLITE_DB_PATH:
        import sqlite3 as _sq
        try:
            conn = _sq.connect(_app.SQLITE_DB_PATH)
            for name, (_, sqlite_sql) in tables.items():
                try:
                    conn.execute(sqlite_sql)
                    conn.commit()
                except Exception as e:
                    print(f"[auth] SQLite table '{name}' failed: {e}")
            conn.close()
            print("[auth] SQLite tables initialized")
        except Exception as e:
            print(f"[auth] SQLite init failed: {e}")
    _ensure_default_admin()


# ══════════════════════════════════════════════════════════════
# 密码处理
# ══════════════════════════════════════════════════════════════

def _hash_password(password):
    """SHA-256 密码哈希（加盐）"""
    salt = os.urandom(16).hex()
    h = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
    return f"{salt}:{h}"


def _verify_password(password, password_hash):
    """验证密码"""
    try:
        salt, h = password_hash.split(':', 1)
        expected = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
        return h == expected
    except Exception:
        return False


def _validate_password_strength(password):
    """验证密码强度：至少8位，含大小写字母和数字"""
    if len(password) < 8:
        return False, '密码至少8位'
    if not re.search(r'[a-z]', password):
        return False, '密码需包含小写字母'
    if not re.search(r'[A-Z]', password):
        return False, '密码需包含大写字母'
    if not re.search(r'\d', password):
        return False, '密码需包含数字'
    return True, ''


# ══════════════════════════════════════════════════════════════
# 用户管理
# ══════════════════════════════════════════════════════════════

def _migrate_users_table():
    """兼容旧表：添加可能缺失的列"""
    new_columns = {
        'email': 'VARCHAR(100)',
        'wechat': 'VARCHAR(50)',
        'account_expiry': 'DATETIME',
        'created_by_subscription': 'TINYINT(1) DEFAULT 0',
    }
    for col, col_type in new_columns.items():
        try:
            db_execute(f"ALTER TABLE users ADD COLUMN {col} {col_type}", fetch=False)
            print(f"[auth] Added column users.{col}")
        except Exception:
            pass  # 列已存在
    try:
        db_execute("ALTER TABLE user_subscriptions ADD COLUMN contact VARCHAR(200)", fetch=False)
    except Exception:
        pass
    try:
        db_execute("ALTER TABLE user_subscriptions ADD COLUMN poll_token VARCHAR(64)", fetch=False)
    except Exception:
        pass


def _ensure_default_admin():
    """确保默认管理员账户存在"""
    existing = db_execute("SELECT id FROM users WHERE username='admin'", fetch=True)
    if not existing or len(existing) == 0:
        pw_hash = _hash_password('Admin@123')
        db_execute(
            "INSERT INTO users (username, password_hash, display_name, must_change_password, is_admin) "
            "VALUES (%s, %s, %s, %s, %s)",
            ('admin', pw_hash, '系统管理员', 1, 1), fetch=False)
        print("[auth] Default admin user created (admin / Admin@123)")


def get_user_by_id(user_id):
    rows = db_execute(
        "SELECT * FROM users WHERE id=%s",
        (user_id,), fetch=True)
    return rows[0] if rows else None


def get_user_by_username(username):
    rows = db_execute(
        "SELECT * FROM users WHERE username=%s AND is_active=1",
        (username,), fetch=True)
    return rows[0] if rows else None


# ══════════════════════════════════════════════════════════════
# 认证装饰器
# ══════════════════════════════════════════════════════════════

# ── 套餐配置 ────────────────────────────────────────────────
PLANS = {
    'monthly': {'name': '月度会员', 'price': 19.90, 'days': 30},
    'quarterly': {'name': '季度会员', 'price': 49.90, 'days': 90},
    'annual': {'name': '年度会员', 'price': 169.00, 'days': 365},
}


def login_required(f):
    """要求登录的装饰器"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'success': False, 'error': '请先登录', 'code': 'login_required'})
        return f(*args, **kwargs)
    return decorated


def portfolio_access_required(f):
    """要求登录+订阅的装饰器（仅用于组合功能）"""
    @wraps(f)
    def decorated(*args, **kwargs):
        # 1. 检查登录
        if 'user_id' not in session:
            return jsonify({'success': False, 'error': '请先登录', 'code': 'login_required'})
        # 2. 检查收费开关
        settings = _get_portfolio_settings()
        if not settings.get('payment_enabled'):
            return f(*args, **kwargs)  # 免费模式，直接放行
        if settings.get('user_free_access'):
            return f(*args, **kwargs)  # 白名单
        # 3. 检查用户订阅
        user_id = session['user_id']
        sub = _get_user_active_subscription(user_id)
        if sub:
            return f(*args, **kwargs)  # 有效订阅
        # 4. 检查试用
        trial_start = settings.get('trial_start_date')
        if trial_start:
            try:
                ts = datetime.strptime(str(trial_start)[:10], '%Y-%m-%d')
                days_used = (datetime.now() - ts).days
                if days_used <= settings.get('free_trial_days', 7):
                    return f(*args, **kwargs)
            except Exception:
                pass
        # 返回友好的订阅到期提示，而非纯JSON错误
        return jsonify({
            'success': False,
            'error': '您的订阅已到期，请续费',
            'code': 'subscription_expired',
            'show_subscription_page': True,
        })
    return decorated


def _get_portfolio_settings():
    try:
        rows = db_execute("SELECT * FROM portfolio_settings WHERE id=1", fetch=True)
        if rows and len(rows) > 0:
            r = rows[0]
            return {
                'payment_enabled': bool(r.get('payment_enabled', 0)),
                'free_trial_days': r.get('free_trial_days', 7),
                'user_free_access': bool(r.get('user_free_access', 1)),
                'trial_start_date': r.get('trial_start_date'),
            }
    except Exception:
        pass
    return {'payment_enabled': True, 'free_trial_days': 7, 'user_free_access': False, 'trial_start_date': None}


def _get_user_active_subscription(user_id):
    """获取用户当前有效订阅"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    rows = db_execute(
        "SELECT * FROM user_subscriptions WHERE user_id=%s AND payment_status='paid' "
        "AND end_date > %s ORDER BY end_date DESC LIMIT 1",
        (user_id, now), fetch=True)
    return rows[0] if rows else None


# ══════════════════════════════════════════════════════════════
# 账户过期检查
# ══════════════════════════════════════════════════════════════

def check_and_lock_expired_accounts():
    """检查并锁定所有已过期账户"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db_execute(
        "UPDATE users SET is_active=0 WHERE account_expiry IS NOT NULL "
        "AND account_expiry < %s AND is_active=1 AND created_by_subscription=1",
        (now,), fetch=False)


def check_account_expiry(user_id):
    """检查单个用户是否过期，过期则锁定"""
    user = get_user_by_id(user_id)
    if not user:
        return False
    if user.get('created_by_subscription') and user.get('account_expiry'):
        try:
            expiry = datetime.strptime(str(user['account_expiry'])[:19], '%Y-%m-%d %H:%M:%S')
            if datetime.now() > expiry:
                db_execute("UPDATE users SET is_active=0 WHERE id=%s", (user_id,), fetch=False)
                return False  # 已过期
        except Exception:
            pass
    return bool(user.get('is_active', 1))


def create_subscription_user(email_or_wechat, plan_type, plan_end_date):
    """订阅成功后自动创建用户，返回用户名和密码"""
    import uuid
    # 生成用户名：sub_随机6位
    username = f"sub_{uuid.uuid4().hex[:6]}"
    while db_execute("SELECT id FROM users WHERE username=%s", (username,), fetch=True):
        username = f"sub_{uuid.uuid4().hex[:6]}"
    # 生成随机密码
    password = uuid.uuid4().hex[:8] + "A1"
    pw_hash = _hash_password(password)

    # 判断是邮箱还是微信
    is_email = '@' in (email_or_wechat or '')
    email = email_or_wechat if is_email else ''
    wechat = email_or_wechat if not is_email else ''

    db_execute(
        "INSERT INTO users (username, password_hash, display_name, email, wechat, "
        "must_change_password, is_active, is_admin, account_expiry, created_by_subscription) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (username, pw_hash, f"订阅用户{username[-4:]}", email, wechat,
         0, 1, 0, plan_end_date, 1), fetch=False)

    return {
        'username': username,
        'password': password,
        'display_name': f"订阅用户{username[-4:]}",
        'plan_type': plan_type,
        'expiry_date': plan_end_date,
    }


# ══════════════════════════════════════════════════════════════
# API 路由处理函数
# ══════════════════════════════════════════════════════════════

def handle_login():
    """处理登录请求"""
    data = request.get_json() or {}
    username = (data.get('username', '') or '').strip()
    password = data.get('password', '') or ''
    if not username or not password:
        return jsonify({'success': False, 'error': '请输入用户名和密码'})
    user = get_user_by_username(username)
    ip = request.remote_addr or '127.0.0.1'
    if not user:
        db_execute(
            "INSERT INTO login_logs (username, ip_address, success) VALUES (%s, %s, 0)",
            (username, ip), fetch=False)
        return jsonify({'success': False, 'error': '用户名或密码错误'})
    if not _verify_password(password, user['password_hash']):
        db_execute(
            "INSERT INTO login_logs (user_id, username, ip_address, success) VALUES (%s, %s, %s, 0)",
            (user['id'], username, ip), fetch=False)
        return jsonify({'success': False, 'error': '用户名或密码错误'})
    # 检查账户是否过期
    if not check_account_expiry(user['id']):
        return jsonify({'success': False, 'error': '账户已过期，请重新订阅'})
    # 登录成功
    session['user_id'] = user['id']
    session['username'] = user['username']
    session['display_name'] = user.get('display_name', '') or user['username']
    session['is_admin'] = bool(user.get('is_admin', 0))
    session.permanent = True
    db_execute(
        "UPDATE users SET last_login=%s WHERE id=%s",
        (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), user['id']), fetch=False)
    db_execute(
        "INSERT INTO login_logs (user_id, username, ip_address, success) VALUES (%s, %s, %s, 1)",
        (user['id'], username, ip), fetch=True)
    return jsonify({
        'success': True,
        'user': {
            'id': user['id'],
            'username': user['username'],
            'display_name': user.get('display_name', '') or user['username'],
            'must_change_password': bool(user.get('must_change_password', 0)),
            'is_admin': bool(user.get('is_admin', 0)),
        },
        'message': '登录成功' + ('，请修改初始密码' if user.get('must_change_password') else ''),
    })


def handle_logout():
    session.clear()
    return jsonify({'success': True, 'message': '已退出登录'})


def handle_change_password():
    """修改密码"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '请先登录'})
    data = request.get_json() or {}
    old_password = data.get('old_password', '')
    new_password = data.get('new_password', '')
    if not old_password or not new_password:
        return jsonify({'success': False, 'error': '请输入旧密码和新密码'})
    valid, msg = _validate_password_strength(new_password)
    if not valid:
        return jsonify({'success': False, 'error': msg})
    user = get_user_by_id(session['user_id'])
    if not user:
        return jsonify({'success': False, 'error': '用户不存在'})
    if not _verify_password(old_password, user['password_hash']):
        return jsonify({'success': False, 'error': '旧密码错误'})
    new_hash = _hash_password(new_password)
    db_execute(
        "UPDATE users SET password_hash=%s, must_change_password=0 WHERE id=%s",
        (new_hash, session['user_id']), fetch=False)
    return jsonify({
        'success': True,
        'message': '密码修改成功'
    })


def handle_user_info():
    """获取当前用户信息"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录', 'logged_in': False})
    user = get_user_by_id(session['user_id'])
    if not user:
        session.clear()
        return jsonify({'success': False, 'error': '用户不存在', 'logged_in': False})
    # 检查订阅状态
    sub = _get_user_active_subscription(session['user_id'])
    return jsonify({
        'success': True,
        'logged_in': True,
        'user': {
            'id': user['id'],
            'username': user['username'],
            'display_name': user.get('display_name', '') or user['username'],
            'must_change_password': bool(user.get('must_change_password', 0)),
            'is_admin': bool(user.get('is_admin', 0)),
            'has_subscription': sub is not None,
            'subscription': {
                'plan_type': sub['plan_type'],
                'end_date': str(sub['end_date'])[:10],
            } if sub else None,
        },
    })


# ══════════════════════════════════════════════════════════════
# 邮箱订阅自动开通
# ══════════════════════════════════════════════════════════════

import string
import random

def _generate_random_username():
    """生成随机用户名：fund_YYYYMMDD_xxxx（8位随机字母数字）"""
    now_str = datetime.now().strftime('%Y%m%d')
    rand_part = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"fund_{now_str}_{rand_part}"


def _generate_random_password():
    """生成随机密码：12位，含大小写字母+数字"""
    lowercase = random.choices(string.ascii_lowercase, k=4)
    uppercase = random.choices(string.ascii_uppercase, k=4)
    digits = random.choices(string.digits, k=4)
    all_chars = lowercase + uppercase + digits
    random.shuffle(all_chars)
    return ''.join(all_chars)


def handle_contact_subscribe():
    """订阅接口：创建支付订单，返回支付信息给前端
    支付成功后由支付网关异步通知生成账号密码"""
    data = request.get_json() or {}
    contact = (data.get('contact', '') or '').strip()
    plan_type = (data.get('plan_type', '') or '').strip()
    payment_method = (data.get('payment_method', '') or 'alipay').strip()

    # 参数校验
    if not contact:
        return jsonify({'success': False, 'error': '请输入邮箱或微信号'})
    if len(contact) < 4:
        return jsonify({'success': False, 'error': '联系信息至少4个字符'})
    if plan_type not in PLANS:
        return jsonify({'success': False, 'error': f'无效的套餐类型: {plan_type}'})
    if payment_method not in ('alipay', 'wechat'):
        return jsonify({'success': False, 'error': '支付方式仅支持 alipay 或 wechat'})

    from payment_gateway import create_alipay_order, create_wechat_order

    if payment_method == 'wechat':
        result = create_wechat_order(contact, plan_type)
    else:
        result = create_alipay_order(contact, plan_type)

    if result.get('success'):
        return jsonify({
            'success': True,
            'message': f'支付订单已创建，请完成支付',
            'payment_method': payment_method,
            'pay_url': result.get('pay_url', ''),
            'code_url': result.get('code_url', ''),
            'out_trade_no': result.get('out_trade_no', ''),
            'poll_token': result.get('poll_token', ''),
            'amount': result.get('amount', 0),
            'plan_name': result.get('plan_name', ''),
        })
    elif result.get('poll_token') and result.get('mock_pay_url'):
        # 支付网关返回失败但有mock降级（如微信沙箱签名错误）
        return jsonify({
            'success': True,
            'message': f'支付订单已创建（模拟模式）',
            'payment_method': payment_method,
            'pay_url': '',
            'code_url': result.get('mock_pay_url', ''),
            'out_trade_no': result.get('out_trade_no', ''),
            'poll_token': result.get('poll_token', ''),
            'amount': result.get('amount', 0) or PLANS[plan_type]['price'],
            'plan_name': result.get('plan_name', '') or PLANS[plan_type]['name'],
        })
    else:
        return jsonify(result)


def handle_subscription_status():
    """获取当前登录用户的订阅状态"""
    if 'user_id' not in session:
        return jsonify({
            'success': True,
            'logged_in': False,
            'has_subscription': False,
            'subscription': None,
        })
    user_id = session['user_id']
    sub = _get_user_active_subscription(user_id)
    if sub:
        return jsonify({
            'success': True,
            'logged_in': True,
            'has_subscription': True,
            'subscription': {
                'plan_type': sub['plan_type'],
                'end_date': str(sub['end_date'])[:10],
            },
            'expire_days': (datetime.strptime(str(sub['end_date'])[:10], '%Y-%m-%d') - datetime.now()).days,
        })
    else:
        return jsonify({
            'success': True,
            'logged_in': True,
            'has_subscription': False,
            'subscription': None,
        })
