# -*- coding: utf-8 -*-
"""主页 + 基金搜索 Blueprint"""
from flask import Blueprint, render_template, request, jsonify
from datetime import datetime
import json
from app import (
    generate_cache_key, get_cache, set_cache, delete_cache,
    get_mysql_pool,
    CACHE_CONFIG, memory_cache, REDIS_AVAILABLE, r,
    SQLITE_DB_PATH, FUND_NAME_MAP,
    _eastmoney_get
)
from routes_fund import get_fund_name

main_bp = Blueprint('main', __name__)

# 搜索结果缓存（短期，5分钟）
search_cache = {}
search_cache_timestamps = {}
SEARCH_CACHE_TTL = 300  # 5分钟


@main_bp.route('/')
def index():
    return render_template('index.html')


# ── 基金列表缓存管理 ──────────────────────────────────────

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


@main_bp.route('/api/fund/search', methods=['GET'])
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
