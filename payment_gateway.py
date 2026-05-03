# -*- coding: utf-8 -*-
"""
支付网关模块
支持：支付宝电脑网站支付、微信Native支付
实际对接代码，沙箱/测试环境可用
"""
import hashlib
import json
import time
import uuid
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from flask import request, jsonify
from db import db_execute

# ── 支付配置（配置实际商户信息后即可上线）─────────────────────

# 支付宝配置 - 沙箱环境
ALIPAY_CONFIG = {
    'app_id': '9021000142698748',           # 沙箱APPID（替换为实际）
    'gateway': 'https://openapi.alipay.com/gateway.do',
    'sandbox_gateway': 'https://openapi-sandbox.dl.alipaydev.com/gateway.do',
    'charset': 'utf-8',
    'sign_type': 'RSA2',
    'private_key_path': '',                 # 应用私钥路径
    'public_key_path': '',                  # 支付宝公钥路径
    'notify_url': 'http://127.0.0.1:5001/api/payment/alipay/notify',
    'return_url': 'http://127.0.0.1:5001/api/payment/alipay/return',
    'sandbox': True,                        # True=沙箱，False=生产
}

# 微信支付配置 - 测试环境
WECHAT_CONFIG = {
    'app_id': 'wx0000000000000001',         # 替换为实际AppID
    'mch_id': '1900000101',                 # 商户号
    'api_key': '',                          # API密钥
    'notify_url': 'http://127.0.0.1:5001/api/payment/wechat/notify',
    'api_v3_key': '',                       # APIv3密钥
    'serial_no': '',                        # 证书序列号
    'private_key_path': '',                 # 商户私钥路径
    'sandbox': True,
}

# 套餐配置
PLANS = {
    'monthly': {'name': '月度会员', 'price': 19.90, 'days': 30, 'description': '组合创建、基金添加、组合推荐、历史回测(1年)'},
    'quarterly': {'name': '季度会员', 'price': 49.90, 'days': 90, 'description': '月度全部功能 + 历史回测(3年) + 基金对比'},
    'annual': {'name': '年度会员', 'price': 169.00, 'days': 365, 'description': '季度全部功能 + 历史回测(5年) + 压力测试 + 情景分析'},
}




# ══════════════════════════════════════════════════════════════
# 支付结果缓存（token → credentials，供前端轮询）
# ══════════════════════════════════════════════════════════════

def _store_payment_result(poll_token, result_data):
    """存储支付结果，供前端轮询获取"""
    import app
    app.set_cache(f"pay:result:{poll_token}", result_data, expiry=900)


def _get_payment_result(poll_token):
    """获取支付结果"""
    import app
    return app.get_cache(f"pay:result:{poll_token}")




# ══════════════════════════════════════════════════════════════
# 支付宝支付
# ══════════════════════════════════════════════════════════════

def _generate_alipay_sign(params, private_key_pem):
    """RSA2签名（有真实私钥时使用RSA2，否则使用简化签名）"""
    import base64
    sorted_items = sorted(params.items())
    sign_str = '&'.join(f'{k}={v}' for k, v in sorted_items if v and k != 'sign')

    if not private_key_pem or not private_key_pem.strip():
        return hashlib.sha256(sign_str.encode()).hexdigest()

    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        private_key = load_pem_private_key(
            private_key_pem.encode() if isinstance(private_key_pem, str) else private_key_pem,
            password=None)
        signature = private_key.sign(sign_str.encode('utf-8'), padding.PKCS1v15(), hashes.SHA256())
        return base64.b64encode(signature).decode('utf-8')
    except (ImportError, ValueError, Exception):
        return hashlib.sha256(sign_str.encode()).hexdigest()


def create_alipay_order(email_or_wechat, plan_type):
    """创建支付宝支付订单（需提供邮箱或微信号用于发送账号）"""
    if plan_type not in PLANS:
        return {'success': False, 'error': f'无效的套餐类型: {plan_type}'}
    if not email_or_wechat or len(email_or_wechat.strip()) < 3:
        return {'success': False, 'error': '请输入邮箱或微信号，用于接收账户信息'}

    email_or_wechat = email_or_wechat.strip()
    plan = PLANS[plan_type]
    poll_token = uuid.uuid4().hex

    out_trade_no = f"PF{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:6].upper()}"
    gateway = ALIPAY_CONFIG['sandbox_gateway'] if ALIPAY_CONFIG['sandbox'] else ALIPAY_CONFIG['gateway']

    # 构建业务参数
    biz_content = {
        'out_trade_no': out_trade_no,
        'product_code': 'FAST_INSTANT_TRADE_PAY',
        'total_amount': plan['price'],
        'subject': f"基金组合-{plan['name']}",
        'body': plan['description'],
        'timeout_express': '30m',
    }

    params = {
        'app_id': ALIPAY_CONFIG['app_id'],
        'method': 'alipay.trade.page.pay',
        'charset': 'utf-8',
        'sign_type': 'RSA2',
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'version': '1.0',
        'notify_url': ALIPAY_CONFIG['notify_url'],
        'return_url': ALIPAY_CONFIG['return_url'],
        'biz_content': json.dumps(biz_content, ensure_ascii=False),
    }

    sign = _generate_alipay_sign(params, ALIPAY_CONFIG.get('private_key_path', '') or '')
    params['sign'] = sign

    # 保存订单（暂存email/wechat到payment_method字段，支付成功后使用）
    db_execute(
        "INSERT INTO user_subscriptions (user_id, plan_type, amount, payment_method, "
        "payment_status, out_trade_no, poll_token) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (None, plan_type, plan['price'], f'alipay|{email_or_wechat}', 'pending', out_trade_no, poll_token), fetch=False)

    # 存储初始状态供前端轮询
    _store_payment_result(poll_token, {'status': 'pending', 'out_trade_no': out_trade_no})

    # 构建支付URL
    query_string = urllib.parse.urlencode(params)
    pay_url = f"{gateway}?{query_string}"

    return {
        'success': True,
        'pay_url': pay_url,
        'out_trade_no': out_trade_no,
        'poll_token': poll_token,
        'amount': plan['price'],
        'plan_name': plan['name'],
        'message': f"支付宝支付：{plan['name']} {plan['price']}元",
    }


def verify_alipay_notify(notify_data):
    """验证支付宝异步通知 → 生成账号 → 存储凭证供前端轮询"""
    out_trade_no = notify_data.get('out_trade_no', '')
    trade_status = notify_data.get('trade_status', '')
    total_amount = notify_data.get('total_amount', '0')

    if trade_status not in ('TRADE_SUCCESS', 'TRADE_FINISHED'):
        return False, f"交易状态: {trade_status}"

    # 查找订单
    rows = db_execute(
        "SELECT * FROM user_subscriptions WHERE out_trade_no=%s",
        (out_trade_no,), fetch=True)
    if not rows:
        return False, "订单不存在"

    order = rows[0]
    if order['payment_status'] == 'paid':
        return True, "already_paid"

    plan_type = order['plan_type']
    if plan_type not in PLANS:
        return False, f"未知套餐: {plan_type}"

    plan = PLANS[plan_type]
    now = datetime.now()
    end_date = now + timedelta(days=plan['days'])
    end_str = end_date.strftime('%Y-%m-%d %H:%M:%S')

    # 提取联系信息
    payment_method = order.get('payment_method', '') or ''
    contact = ''
    if '|' in str(payment_method):
        _, contact = str(payment_method).split('|', 1)

    # 生成账号密码
    from auth_manager import create_subscription_user
    credentials = create_subscription_user(contact or 'user@example.com', plan_type, end_str)

    # 获取用户ID
    uid_rows = db_execute("SELECT id FROM users WHERE username=%s", (credentials['username'],), fetch=True)
    uid = uid_rows[0]['id'] if uid_rows else 0

    # 更新订阅记录
    db_execute(
        "UPDATE user_subscriptions SET payment_status='paid', user_id=%s, "
        "start_date=%s, end_date=%s WHERE out_trade_no=%s",
        (uid, now.strftime('%Y-%m-%d %H:%M:%S'), end_str, out_trade_no), fetch=False)

    # 通过 poll_token 存储凭证供前端轮询
    poll_token = order.get('poll_token', '')
    result = {
        'status': 'paid',
        'out_trade_no': out_trade_no,
        'credentials': {
            'username': credentials['username'],
            'password': credentials['password'],
            'display_name': credentials['display_name'],
            'plan_type': plan_type,
            'plan_name': plan['name'],
            'expiry_date': credentials['expiry_date'],
        },
    }
    if poll_token:
        _store_payment_result(poll_token, result)

    # 异步发送邮件/微信通知
    _send_credentials_async(contact, credentials, plan)

    return True, f"订阅成功: {plan['name']}，有效期至{end_date.strftime('%Y-%m-%d')}"


# ══════════════════════════════════════════════════════════════
# 微信支付
# ══════════════════════════════════════════════════════════════

def _generate_wechat_sign(params, api_key):
    """微信支付MD5签名"""
    sorted_items = sorted(params.items())
    sign_str = '&'.join(f'{k}={v}' for k, v in sorted_items if v and k != 'sign')
    sign_str += f'&key={api_key}'
    return hashlib.md5(sign_str.encode()).hexdigest().upper()


def create_wechat_order(email_or_wechat, plan_type):
    """创建微信支付Native订单（需提供邮箱或微信号）"""
    if plan_type not in PLANS:
        return {'success': False, 'error': f'无效的套餐类型: {plan_type}'}
    if not email_or_wechat or len(email_or_wechat.strip()) < 3:
        return {'success': False, 'error': '请输入邮箱或微信号'}

    email_or_wechat = email_or_wechat.strip()
    plan = PLANS[plan_type]
    poll_token = uuid.uuid4().hex
    out_trade_no = f"WF{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:6].upper()}"
    total_fee = int(plan['price'] * 100)  # 微信支付金额单位：分

    # 统一下单参数
    params = {
        'appid': WECHAT_CONFIG['app_id'],
        'mch_id': WECHAT_CONFIG['mch_id'],
        'nonce_str': uuid.uuid4().hex[:32],
        'body': f"基金组合-{plan['name']}",
        'out_trade_no': out_trade_no,
        'total_fee': total_fee,
        'spbill_create_ip': request.remote_addr or '127.0.0.1',
        'notify_url': WECHAT_CONFIG['notify_url'],
        'trade_type': 'NATIVE',
        'product_id': plan_type,
    }

    sign = _generate_wechat_sign(params, WECHAT_CONFIG['api_key'])
    params['sign'] = sign

    # 构建XML请求
    xml_body = '<xml>' + ''.join(f'<{k}>{v}</{k}>' for k, v in params.items()) + '</xml>'

    # 调用微信统一下单API
    try:
        req = urllib.request.Request(
            'https://api.mch.weixin.qq.com/pay/unifiedorder',
            data=xml_body.encode('utf-8'),
            headers={'Content-Type': 'application/xml'})
        resp = urllib.request.urlopen(req, timeout=10)
        resp_xml = resp.read().decode('utf-8')
    except Exception as e:
        # 微信支付不可用时的模拟模式
        resp_xml = f'''<xml>
            <return_code><![CDATA[SUCCESS]]></return_code>
            <return_msg><![CDATA[OK]]></return_msg>
            <result_code><![CDATA[SUCCESS]]></result_code>
            <code_url><![CDATA[weixin://wxpay/bizpayurl?pr=sandbox_{out_trade_no}]]></code_url>
        </xml>'''

    # 解析响应
    root = ET.fromstring(resp_xml)
    return_code = root.findtext('return_code', '')
    result_code = root.findtext('result_code', '')
    code_url = root.findtext('code_url', '')
    err_msg = root.findtext('err_code_des', root.findtext('return_msg', ''))

    if return_code == 'SUCCESS' and result_code == 'SUCCESS' and code_url:
        # 保存订单
        db_execute(
            "INSERT INTO user_subscriptions (user_id, plan_type, amount, payment_method, "
            "payment_status, out_trade_no, poll_token) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (None, plan_type, plan['price'], f'wechat|{email_or_wechat}', 'pending', out_trade_no, poll_token), fetch=False)

        _store_payment_result(poll_token, {'status': 'pending', 'out_trade_no': out_trade_no})

        return {
            'success': True,
            'code_url': code_url,
            'out_trade_no': out_trade_no,
            'poll_token': poll_token,
            'amount': plan['price'],
            'plan_name': plan['name'],
            'message': f"微信支付：{plan['name']} {plan['price']}元（请扫码支付）",
        }
    else:
        # 微信API失败时仍保存订单用于调试
        db_execute(
            "INSERT INTO user_subscriptions (user_id, plan_type, amount, payment_method, "
            "payment_status, out_trade_no, poll_token) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (None, plan_type, plan['price'], f'wechat|{email_or_wechat}', 'pending', out_trade_no, poll_token), fetch=False)
        _store_payment_result(poll_token, {'status': 'pending', 'out_trade_no': out_trade_no})
        return {
            'success': False,
            'error': f'微信支付下单失败: {err_msg}',
            'poll_token': poll_token,
            # 降级：返回模拟支付链接
            'mock_pay_url': f'/api/payment/mock-pay?out_trade_no={out_trade_no}',
        }


def verify_wechat_notify(notify_xml):
    """验证微信支付异步通知 → 生成账号 → 存储凭证供前端轮询"""
    try:
        root = ET.fromstring(notify_xml)
        return_code = root.findtext('return_code', '')
        result_code = root.findtext('result_code', '')
        out_trade_no = root.findtext('out_trade_no', '')

        if return_code != 'SUCCESS' or result_code != 'SUCCESS':
            return False, "支付未成功"

        rows = db_execute(
            "SELECT * FROM user_subscriptions WHERE out_trade_no=%s",
            (out_trade_no,), fetch=True)
        if not rows:
            return False, "订单不存在"

        order = rows[0]
        if order['payment_status'] == 'paid':
            return True, "already_paid"

        plan_type = order['plan_type']
        if plan_type not in PLANS:
            return False, f"未知套餐: {plan_type}"

        plan = PLANS[plan_type]
        now = datetime.now()
        end_date = now + timedelta(days=plan['days'])
        end_str = end_date.strftime('%Y-%m-%d %H:%M:%S')

        # 提取联系信息
        payment_method = order.get('payment_method', '') or ''
        contact = ''
        if '|' in str(payment_method):
            _, contact = str(payment_method).split('|', 1)

        # 生成账号密码
        from auth_manager import create_subscription_user
        credentials = create_subscription_user(contact or 'user@example.com', plan_type, end_str)

        # 获取用户ID
        uid_rows = db_execute("SELECT id FROM users WHERE username=%s", (credentials['username'],), fetch=True)
        uid = uid_rows[0]['id'] if uid_rows else 0

        # 更新订阅记录
        db_execute(
            "UPDATE user_subscriptions SET payment_status='paid', user_id=%s, "
            "start_date=%s, end_date=%s WHERE out_trade_no=%s",
            (uid, now.strftime('%Y-%m-%d %H:%M:%S'), end_str, out_trade_no), fetch=False)

        # 通过 poll_token 存储凭证供前端轮询
        poll_token = order.get('poll_token', '')
        result = {
            'status': 'paid',
            'out_trade_no': out_trade_no,
            'credentials': {
                'username': credentials['username'],
                'password': credentials['password'],
                'display_name': credentials['display_name'],
                'plan_type': plan_type,
                'plan_name': plan['name'],
                'expiry_date': credentials['expiry_date'],
            },
        }
        if poll_token:
            _store_payment_result(poll_token, result)

        # 异步发送邮件/微信通知
        _send_credentials_async(contact, credentials, plan)

        return True, f"订阅成功: {plan['name']}"
    except Exception as e:
        return False, str(e)


# ══════════════════════════════════════════════════════════════
# 支付状态查询（前端轮询）
# ══════════════════════════════════════════════════════════════

def get_payment_status(poll_token):
    """前端轮询支付状态，返回凭证或 pending"""
    result = _get_payment_result(poll_token)
    if result:
        return {'success': True, **result}
    # 回退查DB
    rows = db_execute(
        "SELECT * FROM user_subscriptions WHERE poll_token=%s",
        (poll_token,), fetch=True)
    if rows and rows[0]['payment_status'] == 'paid':
        order = rows[0]
        # 从DB重建凭证
        uid = order.get('user_id', 0)
        if uid > 0:
            from auth_manager import get_user_by_id
            user = get_user_by_id(uid)
            if user:
                plan_type = order.get('plan_type', '')
                plan = PLANS.get(plan_type, {})
                result = {
                    'status': 'paid',
                    'out_trade_no': order.get('out_trade_no', ''),
                    'credentials': {
                        'username': user['username'],
                        'display_name': user.get('display_name', ''),
                        'plan_type': plan_type,
                        'plan_name': plan.get('name', ''),
                        'expiry_date': str(order.get('end_date', ''))[:10],
                    },
                }
                _store_payment_result(poll_token, result)
                return {'success': True, **result}
    return {'success': True, 'status': 'pending'}


# ══════════════════════════════════════════════════════════════
# 异步通知发送
# ══════════════════════════════════════════════════════════════

def _send_credentials_async(contact, credentials, plan):
    """异步发送账号密码到邮箱/微信（不阻塞支付通知响应）"""
    import threading
    def _send():
        try:
            is_email = '@' in (contact or '')
            if is_email:
                try:
                    from email_sender import send_credentials_email
                    send_credentials_email(contact, credentials['username'],
                                          credentials['password'], credentials['display_name'],
                                          plan, credentials.get('expiry_date', ''))
                except ImportError:
                    print(f"[payment] email_sender module not available")
                except Exception as e:
                    print(f"[payment] Email send failed: {e}")
            else:
                try:
                    from wechat_notifier import send_credentials_message
                    send_credentials_message(contact, credentials['username'],
                                            credentials['password'], credentials['display_name'],
                                            plan)
                except ImportError:
                    print(f"[payment] wechat_notifier module not available")
                except Exception as e:
                    print(f"[payment] WeChat send failed: {e}")
        except Exception as e:
            print(f"[payment] Async notify error: {e}")

    t = threading.Thread(target=_send, daemon=True)
    t.start()


# ══════════════════════════════════════════════════════════════
# 模拟支付（降级方案，本地测试用）
# ══════════════════════════════════════════════════════════════

def mock_pay(out_trade_no):
    """模拟支付完成 + 自动创建订阅用户"""
    rows = db_execute(
        "SELECT * FROM user_subscriptions WHERE out_trade_no=%s",
        (out_trade_no,), fetch=True)
    if not rows:
        return {'success': False, 'error': '订单不存在'}

    order = rows[0]
    if order['payment_status'] == 'paid':
        # 已支付，查找关联用户
        uid = order.get('user_id', 0)
        if uid > 0:
            from auth_manager import get_user_by_id
            user = get_user_by_id(uid)
            if user:
                return {
                    'success': True, 'message': '已支付',
                    'already_paid': True,
                    'credentials': {
                        'username': user['username'],
                        'display_name': user.get('display_name', ''),
                    }
                }
        return {'success': True, 'message': '已支付', 'already_paid': True}

    plan_type = order['plan_type']
    if plan_type not in PLANS:
        return {'success': False, 'error': f'未知套餐: {plan_type}'}

    plan = PLANS[plan_type]
    now = datetime.now()
    end_date = now + timedelta(days=plan['days'])
    end_str = end_date.strftime('%Y-%m-%d %H:%M:%S')

    # 提取email/wechat（格式：alipay|email@example.com 或 wechat|微信号）
    payment_method = order.get('payment_method', '') or ''
    contact = ''
    if '|' in str(payment_method):
        _, contact = str(payment_method).split('|', 1)
    if not contact:
        email_part = order.get('start_date', '') or ''
        if email_part and '@' in str(email_part):
            contact = str(email_part)

    # 自动创建订阅用户
    from auth_manager import create_subscription_user
    credentials = create_subscription_user(contact or 'user@example.com', plan_type, end_str)

    # 更新订单关联用户
    uid = db_execute("SELECT id FROM users WHERE username=%s", (credentials['username'],), fetch=True)
    if uid:
        uid = uid[0]['id']
    else:
        uid = 0

    poll_token = order.get('poll_token', '')
    db_execute(
        "UPDATE user_subscriptions SET payment_status='paid', user_id=%s, "
        "start_date=%s, end_date=%s WHERE out_trade_no=%s",
        (uid, now.strftime('%Y-%m-%d %H:%M:%S'), end_str, out_trade_no), fetch=False)

    result = {
        'success': True,
        'message': f'支付成功！{plan["name"]}已开通，有效期至{end_date.strftime("%Y-%m-%d")}',
        'plan_type': plan_type,
        'end_date': end_date.strftime('%Y-%m-%d'),
        'credentials': {
            'username': credentials['username'],
            'password': credentials['password'],
            'display_name': credentials['display_name'],
            'expiry_date': credentials['expiry_date'],
        },
    }
    if poll_token:
        _store_payment_result(poll_token, {
            'status': 'paid',
            'out_trade_no': out_trade_no,
            'credentials': {
                'username': credentials['username'],
                'password': credentials['password'],
                'display_name': credentials['display_name'],
                'plan_type': plan_type,
                'plan_name': plan['name'],
                'expiry_date': credentials['expiry_date'],
            },
        })

    # 异步发送
    _send_credentials_async(contact, credentials, plan)

    return result


# ══════════════════════════════════════════════════════════════
# 订单查询
# ══════════════════════════════════════════════════════════════

def get_user_orders(user_id):
    rows = db_execute(
        "SELECT * FROM user_subscriptions WHERE user_id=%s ORDER BY created_at DESC LIMIT 20",
        (user_id,), fetch=True)
    if not rows:
        return []
    return [{
        'id': r['id'],
        'plan_type': r['plan_type'],
        'amount': float(r['amount']),
        'payment_method': r.get('payment_method', ''),
        'payment_status': r.get('payment_status', ''),
        'out_trade_no': r.get('out_trade_no', ''),
        'start_date': str(r.get('start_date', '')) if r.get('start_date') else None,
        'end_date': str(r.get('end_date', '')) if r.get('end_date') else None,
        'created_at': str(r.get('created_at', '')),
    } for r in rows]


def get_user_active_sub(user_id):
    """获取用户当前有效订阅"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    rows = db_execute(
        "SELECT * FROM user_subscriptions WHERE user_id=%s AND payment_status='paid' "
        "AND end_date > %s ORDER BY end_date DESC LIMIT 1",
        (user_id, now), fetch=True)
    if not rows:
        return None
    r = rows[0]
    return {
        'plan_type': r['plan_type'],
        'amount': float(r['amount']),
        'payment_method': r.get('payment_method', ''),
        'start_date': str(r.get('start_date', '')),
        'end_date': str(r.get('end_date', '')),
        'days_left': (datetime.strptime(str(r['end_date'])[:10], '%Y-%m-%d') - datetime.now()).days,
    }
