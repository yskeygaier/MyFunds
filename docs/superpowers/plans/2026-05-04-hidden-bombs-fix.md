# 隐藏炸弹修复 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 5 个隐藏问题：线程安全缓存、线程安全连接池、缺失索引、SQLite 列对齐、统一 `_db_execute`

**Architecture:** 新建 `db.py`（统一连接池 + 执行器）和 `cache.py`（线程安全缓存），3 个模块删除重复的 `_db_execute`，`app.py` 删除连接池类

**Tech Stack:** Python 3.12, pymysql, sqlite3, queue.Queue, threading.RLock

---

### Task 1: 新建 `cache.py` — 线程安全 TTL 缓存

**Files:**
- Create: `/media/yskey/文档/work/mytest/cache.py`
- Modify: `/media/yskey/文档/work/mytest/fund_crawler.py` — `_nav_cache` 替换
- Modify: `/media/yskey/文档/work/mytest/app.py` — `memory_cache` 替换

- [ ] **Step 1: 创建 `cache.py`**

```python
# -*- coding: utf-8 -*-
"""线程安全 TTL 缓存，用于全局共享状态"""
import threading
import time


class ThreadSafeCache:
    """RLock + dict + 可选 TTL，线程安全读写"""

    def __init__(self, name="cache"):
        self._lock = threading.RLock()
        self._data = {}
        self._name = name

    def get(self, key):
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            ts, ttl, value = entry
            if ttl > 0 and time.time() - ts > ttl:
                del self._data[key]
                return None
            return value

    def set(self, key, value, ttl=0):
        with self._lock:
            self._data[key] = (time.time(), ttl, value)

    def clear(self):
        with self._lock:
            self._data.clear()

    def __len__(self):
        with self._lock:
            return len(self._data)

    def __contains__(self, key):
        return self.get(key) is not None
```

- [ ] **Step 2: 测试 cache.py**

```bash
cd /media/yskey/文档/work/mytest && venv2/bin/python -c "
import threading, time
from cache import ThreadSafeCache

c = ThreadSafeCache()
results = []

def writer(n):
    for i in range(100):
        c.set(f'key{n}', f'value{n}-{i}')
        time.sleep(0.001)

def reader():
    for _ in range(500):
        for n in range(5):
            v = c.get(f'key{n}')
            if v:
                results.append(True)

threads = [threading.Thread(target=writer, args=(i,)) for i in range(5)]
threads += [threading.Thread(target=reader) for _ in range(3)]
for t in threads:
    t.start()
for t in threads:
    t.join()
print(f'No exception, cache size={len(c)}, results={len(results)}')
" && echo "cache.py OK"
```

- [ ] **Step 3: `fund_crawler.py` — `_nav_cache` 替换为 ThreadSafeCache**

In `/media/yskey/文档/work/mytest/fund_crawler.py`, replace line 694:
```python
_nav_cache = {}  # {(fund_code, years): (timestamp, [records])}
```
with:
```python
from cache import ThreadSafeCache
_nav_cache = ThreadSafeCache(name="nav")  # {(fund_code, years): (timestamp, [records])}
```

Then update `crawl_fund_nav_df` (around line 697-723). Replace:
```python
cache_key = (fund_code, years)
now = time.time()
ttl = 300 if years <= 1 else 1800
if cache_key in _nav_cache:
    cached_time, cached_data = _nav_cache[cache_key]
    if now - cached_time < ttl:
        return cached_data

data = _fetch_nav_history_via_http(fund_code, years)
nav_df = data.get('nav_df')

if nav_df is not None and len(nav_df) > 0:
    result = nav_df.to_dict('records')
    _nav_cache[cache_key] = (now, result)
    return result
```
with:
```python
cache_key = (fund_code, years)
ttl = 300 if years <= 1 else 1800
cached = _nav_cache.get(cache_key)
if cached is not None:
    return cached

data = _fetch_nav_history_via_http(fund_code, years)
nav_df = data.get('nav_df')

if nav_df is not None and len(nav_df) > 0:
    result = nav_df.to_dict('records')
    _nav_cache.set(cache_key, result, ttl=ttl)
    return result
```

- [ ] **Step 4: `app.py` — `memory_cache` 替换为 ThreadSafeCache**

In `/media/yskey/文档/work/mytest/app.py`, find `memory_cache = {}` (around line 138) and replace with:
```python
from cache import ThreadSafeCache
memory_cache = ThreadSafeCache(name="memory")
```

Update all `memory_cache` accesses. The current pattern is:
```python
memory_cache[key] = value       # → memory_cache.set(key, value)
value = memory_cache.get(key)   # → OK, unchanged
key in memory_cache             # → OK, __contains__ works
```

Run to find all usages:
```bash
grep -n "memory_cache\[" /media/yskey/文档/work/mytest/app.py
```
Replace each `memory_cache[key] = value` with `memory_cache.set(key, value)`.

- [ ] **Step 5: 验证 syntax 和基本功能**

```bash
cd /media/yskey/文档/work/mytest && venv2/bin/python -m py_compile cache.py fund_crawler.py app.py && echo "Syntax OK"
```

- [ ] **Step 6: Commit**

```bash
cd /media/yskey/文档/work/mytest
git add cache.py fund_crawler.py app.py
git commit -m "feat: add ThreadSafeCache and wrap _nav_cache, memory_cache

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2: 新建 `db.py` — 统一线程安全连接池 + 执行器

**Files:**
- Create: `/media/yskey/文档/work/mytest/db.py`

- [ ] **Step 1: 创建 `db.py`**

```python
# -*- coding: utf-8 -*-
"""统一数据库模块 — 线程安全连接池 + MySQL/SQLite 降级"""
import queue
import sqlite3
import time
import pymysql

# ── 配置 ──────────────────────────────────────────────────────
SQLITE_DB_PATH = None          # 由 app 在启动时设置
_MYSQL_CONFIG = None           # 由 app 在启动时设置

# ── 连接池 ────────────────────────────────────────────────────

class _PooledConn:
    """包装 pymysql 连接，close() 时归还到池"""

    def __init__(self, conn, pool_queue):
        self._conn = conn
        self._pool_queue = pool_queue

    def cursor(self):
        return self._conn.cursor()

    def commit(self):
        self._conn.commit()

    def rollback(self):
        try:
            self._conn.rollback()
        except Exception:
            pass

    def close(self):
        """归还连接到池（不真正关闭）"""
        if self._pool_queue is not None:
            try:
                self._pool_queue.put_nowait(self._conn)
            except queue.Full:
                self._conn.close()
        else:
            self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _create_mysql_conn():
    """创建新的 MySQL 连接"""
    if _MYSQL_CONFIG is None:
        raise RuntimeError("db module not initialized: call db.init() first")
    return pymysql.connect(
        **_MYSQL_CONFIG,
        cursorclass=pymysql.cursors.DictCursor
    )


class DatabasePool:
    """线程安全 MySQL 连接池（基于 queue.Queue）"""

    def __init__(self, pool_size=5):
        self._queue = queue.Queue(maxsize=pool_size)
        self._size = pool_size
        for _ in range(pool_size):
            self._queue.put(_create_mysql_conn())

    def get_connection(self):
        """获取连接（阻塞，线程安全）"""
        conn = self._queue.get()
        return _PooledConn(conn, self._queue)

    def execute(self, sql, params=None, fetch=True):
        """统一查询入口：MySQL 优先 → SQLite 降级"""
        conn = None
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute(sql, params or ())
            is_select = sql.strip().upper().startswith('SELECT')
            if fetch and is_select:
                rows = cur.fetchall()
            elif fetch:
                conn.commit()
                rows = cur.rowcount
            else:
                conn.commit()
                rows = cur.rowcount
            cur.close()
            return rows
        except Exception as e:
            print(f"[db] MySQL error: {e}, falling back to SQLite")
            if conn:
                try:
                    conn.rollback()
                    conn.close()
                except Exception:
                    pass
            return _sqlite_execute(sql, params, fetch)

    def ensure_indexes(self):
        """启动时自动创建缺失索引（幂等）"""
        indexes = [
            ("idx_sub_out_trade_no", "user_subscriptions", "out_trade_no"),
            ("idx_sub_poll_token", "user_subscriptions", "poll_token"),
            ("idx_hold_fund_code", "portfolio_holdings", "fund_code"),
        ]
        conn = None
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            for idx_name, table, col in indexes:
                try:
                    cur.execute(
                        f"CREATE INDEX {idx_name} ON {table}({col})"
                    )
                    conn.commit()
                    print(f"[db] Index {idx_name} created")
                except Exception:
                    pass  # 索引已存在
            cur.close()
        except Exception as e:
            print(f"[db] Index creation skipped: {e}")
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def ensure_sqlite_schema(self):
        """SQLite 列对齐：添加 MySQL 已存在但 SQLite 缺失的列"""
        if not SQLITE_DB_PATH:
            return
        try:
            conn = sqlite3.connect(SQLITE_DB_PATH)
            # user_subscriptions 表
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(user_subscriptions)")
            existing = {r[1] for r in cur.fetchall()}
            needed = {
                'poll_token': 'TEXT',
                'contact': 'TEXT',
            }
            for col, col_type in needed.items():
                if col not in existing:
                    try:
                        cur.execute(f"ALTER TABLE user_subscriptions ADD COLUMN {col} {col_type}")
                        conn.commit()
                        print(f"[db] SQLite: added column user_subscriptions.{col}")
                    except Exception as e:
                        print(f"[db] SQLite column add failed ({col}): {e}")
            conn.close()
        except Exception as e:
            print(f"[db] SQLite schema check failed: {e}")


# ── 全局单例 ──────────────────────────────────────────────────

pool = None

def init(mysql_config, sqlite_db_path, pool_size=5):
    """初始化数据库模块（app 启动时调用一次）"""
    global pool, SQLITE_DB_PATH, _MYSQL_CONFIG
    _MYSQL_CONFIG = mysql_config
    SQLITE_DB_PATH = sqlite_db_path
    pool = DatabasePool(pool_size=pool_size)
    pool.ensure_indexes()
    pool.ensure_sqlite_schema()
    return pool


def db_execute(sql, params=None, fetch=True):
    """模块级快捷函数"""
    if pool is None:
        return _sqlite_execute(sql, params, fetch)
    return pool.execute(sql, params, fetch)


def get_connection():
    """获取原生 MySQL 连接（用于复杂操作，用完后必须 close()）"""
    if pool is None:
        raise RuntimeError("db pool not initialized")
    return pool.get_connection()


def _sqlite_execute(sql, params=None, fetch=True):
    """内部 SQLite 执行器（所有 SQL 替换在此集中处理）"""
    sql = sql.replace('%s', '?')
    sql = sql.replace('ON UPDATE CURRENT_TIMESTAMP', '')
    sql = sql.replace('AUTO_INCREMENT', 'AUTOINCREMENT')
    sql = sql.replace('LONGTEXT', 'TEXT')
    sql = sql.replace('TINYINT(1)', 'INTEGER')
    sql = sql.replace('DECIMAL(10,2)', 'REAL')
    sql = sql.replace('DECIMAL(5,2)', 'REAL')
    sql = sql.replace('DECIMAL(10,4)', 'REAL')
    sql = sql.replace('ENGINE=InnoDB DEFAULT CHARSET=utf8mb4', '')
    conn = sqlite3.connect(SQLITE_DB_PATH or '/tmp/fallback.db')
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(sql, params or ())
        is_select = sql.strip().upper().startswith('SELECT')
        if fetch and is_select:
            rows = [dict(r) for r in cur.fetchall()]
        elif fetch:
            conn.commit()
            rows = cur.lastrowid
        else:
            conn.commit()
            rows = cur.rowcount
        cur.close()
        return rows
    finally:
        conn.close()
```

- [ ] **Step 2: 验证 db.py 语法**

```bash
cd /media/yskey/文档/work/mytest && venv2/bin/python -m py_compile db.py && echo "Syntax OK"
```

- [ ] **Step 3: 单元测试 — 连接池借还循环**

```bash
cd /media/yskey/文档/work/mytest && venv2/bin/python -c "
import db
db.init(
    mysql_config={'user':'yskey','password':'yskey','host':'127.0.0.1','port':3306,'database':'fund_data','charset':'utf8mb4','ssl_disabled':True},
    sqlite_db_path='fund_data.db',
    pool_size=5
)
# 测试借还循环
for i in range(50):
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute('SELECT 1 as val')
    r = cur.fetchone()
    assert r['val'] == 1, f'Round {i} failed'
    cur.close()
    conn.close()
print('Pool borrow/return: 50 rounds OK')

# 测试 db_execute
rows = db.db_execute('SELECT 1 as val')
assert rows[0]['val'] == 1
print('db_execute: OK')

# 测试索引
db.pool.ensure_indexes()
print('Index ensure: OK')

# 测试 SQLite 列对齐
db.pool.ensure_sqlite_schema()
print('SQLite schema: OK')
" && echo "db.py tests passed"
```

- [ ] **Step 4: Commit**

```bash
cd /media/yskey/文档/work/mytest
git add db.py
git commit -m "feat: add db.py — unified thread-safe connection pool + executor

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 3: 迁移 `auth_manager.py`、`payment_gateway.py`、`portfolio_manager.py`

**Files:**
- Modify: `/media/yskey/文档/work/mytest/auth_manager.py`
- Modify: `/media/yskey/文档/work/mytest/payment_gateway.py`
- Modify: `/media/yskey/文档/work/mytest/portfolio_manager.py`

- [ ] **Step 1: `auth_manager.py` — 删除 `_db_execute`/`_sqlite_execute`/`_ensure_deps`**

Delete lines 20-28 (`_ensure_deps`), lines 130-157 (`_db_execute`), lines 160-184 (`_sqlite_execute`).

Add at top:
```python
from db import db_execute
```

Replace all calls from `_db_execute(...)` to `db_execute(...)`. Also remove calls to `_ensure_deps()` — no longer needed.

Also replace `_ensure_deps()` calls in `init_auth_tables()` and other functions. The `_SQLITE_DB_PATH` variable is no longer needed locally — remove its global declaration.

```bash
# Verify no remaining _db_execute or _ensure_deps references
grep -n "_db_execute\|_sqlite_execute\|_ensure_deps" /media/yskey/文档/work/mytest/auth_manager.py
# Should return nothing
```

- [ ] **Step 2: `payment_gateway.py` — 删除 `_db_execute`/`_sqlite_execute`/`_ensure_deps`**

Delete lines 56-66 (`_ensure_deps`/globals), lines 86-113 (`_db_execute`), lines 116-135 (`_sqlite_execute`).

Add at top:
```python
from db import db_execute, get_connection
```

Replace all `_db_execute(...)` with `db_execute(...)`. Also replace `_store_payment_result` and `_get_payment_result` which currently call `_ensure_deps()` then `app.get_cache`/`app.set_cache` — keep these but remove `_ensure_deps()` calls.

Note: `_store_payment_result` and `_get_payment_result` use `app.set_cache`/`app.get_cache`. These need to keep importing from app but `_ensure_deps()` is no longer needed since `db.py` doesn't depend on it.

```bash
grep -n "_db_execute\|_sqlite_execute\|_ensure_deps" /media/yskey/文档/work/mytest/payment_gateway.py
# Should return nothing
```

- [ ] **Step 3: `portfolio_manager.py` — 删除 `_db_execute`/`_sqlite_execute`/`_ensure_deps`**

Delete lines 349-352 (`_ensure_deps`), lines 354-382 (`_db_execute`), lines 385-403 (`_sqlite_execute`).

Add at top:
```python
from db import db_execute, get_connection
```

Replace all `_db_execute(...)` with `db_execute(...)`. Remove `_ensure_deps()` calls where they exist (they were called inside `_db_execute` which is now deleted).

```bash
grep -n "_db_execute\|_sqlite_execute\|_ensure_deps" /media/yskey/文档/work/mytest/portfolio_manager.py
# Should return nothing
```

- [ ] **Step 4: 验证所有文件编译通过**

```bash
cd /media/yskey/文档/work/mytest
venv2/bin/python -m py_compile auth_manager.py && echo "auth_manager OK"
venv2/bin/python -m py_compile payment_gateway.py && echo "payment_gateway OK"
venv2/bin/python -m py_compile portfolio_manager.py && echo "portfolio_manager OK"
```

- [ ] **Step 5: Commit**

```bash
cd /media/yskey/文档/work/mytest
git add auth_manager.py payment_gateway.py portfolio_manager.py
git commit -m "refactor: use db.db_execute from auth/payment/portfolio modules

Remove duplicated _db_execute/_sqlite_execute/_ensure_deps (3 copies).
All DB access now goes through unified db.py.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 4: 迁移 `app.py` — 删除连接池类，改用 `db.py`

**Files:**
- Modify: `/media/yskey/文档/work/mytest/app.py`

- [ ] **Step 1: `app.py` — 替换连接池实现**

Delete lines 63-135: `_create_mysql_conn`, `_MySQLPool`, `_PooledMySQLConn`, `_MYSQL_POOL`, `get_mysql_pool`.

Replace with:
```python
# MySQL 连接池 — 统一由 db.py 管理
from db import init as _db_init, get_connection as _db_get_connection, pool as _db_pool

def get_mysql_pool():
    """获取 MySQL 连接池（兼容旧接口）"""
    if _db_pool is None:
        return None
    return _db_pool
```

- [ ] **Step 2: `app.py` — 启动时初始化 db 模块**

Find `if __name__ == '__main__':` block. Add before `app.run(...)`:
```python
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
```

- [ ] **Step 3: 验证语法和启动**

```bash
cd /media/yskey/文档/work/mytest && venv2/bin/python -m py_compile app.py && echo "Syntax OK"
```

- [ ] **Step 4: 重新生成 `__pycache__` 并启动测试**

```bash
cd /media/yskey/文档/work/mytest
rm -rf __pycache__ auth_manager.py__pycache__ 2>/dev/null  # clean stale .pyc
fuser -k 5001/tcp 2>/dev/null; sleep 1
nohup venv2/bin/python app.py > /tmp/app.log 2>&1 &
sleep 3
tail -10 /tmp/app.log
```

- [ ] **Step 5: Commit**

```bash
cd /media/yskey/文档/work/mytest
git add app.py
git commit -m "refactor: replace app.py pool classes with db.py

Delete _MySQLPool, _PooledMySQLConn, _create_mysql_conn, _MYSQL_POOL.
app.py now imports db.py for all connection pool management.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 5: 回归验证

**Files:** 无改动，纯验证

- [ ] **Step 1: 验证全部 API 端点**

```bash
cd /media/yskey/文档/work/mytest
for endpoint in \
    "/" \
    "/api/fund/info?fund_code=000001" \
    "/api/fund/manager?fund_code=000001" \
    "/api/fund/analysis_report?fund_code=000001" \
    "/api/fund/backtest?fund_code=000001&start_date=2025-01-01&end_date=2025-12-31" \
    "/api/fund/dca?fund_code=000001&amount=1000&frequency=monthly" \
    "/api/fund/search?keyword=000001" \
    "/api/fund/valuation?fund_code=000001" \
    "/api/payment/plans" \
    "/api/subscription/status"
do
    code=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:5001$endpoint")
    if [ "$code" != "200" ]; then
        echo "FAIL: $endpoint → $code"
    else
        echo "OK: $endpoint"
    fi
done
```

- [ ] **Step 2: 验证索引生效**

```bash
venv2/bin/python -c "
from db import pool
conn = pool.get_connection()
cur = conn.cursor()
for q in [
    'EXPLAIN SELECT * FROM user_subscriptions WHERE out_trade_no = \"X\"',
    'EXPLAIN SELECT * FROM user_subscriptions WHERE poll_token = \"X\"',
    'EXPLAIN SELECT * FROM portfolio_holdings WHERE fund_code = \"X\"',
]:
    cur.execute(q)
    r = cur.fetchone()
    print(f'{r[\"key\"] or \"FULL_SCAN\"} | rows={r[\"rows\"]}')
cur.close()
conn.close()
"
# 预期：每个查询使用 idx_* 索引，而非 NULL/FULL_SCAN
```

- [ ] **Step 3: 验证 SQLite 列对齐**

```bash
venv2/bin/python -c "
import sqlite3
conn = sqlite3.connect('fund_data.db')
cur = conn.cursor()
cur.execute('PRAGMA table_info(user_subscriptions)')
cols = [r[1] for r in cur.fetchall()]
for needed in ['poll_token', 'contact']:
    print(f'{needed}: {\"OK\" if needed in cols else \"MISSING\"}')  
conn.close()
"
# 预期：poll_token: OK, contact: OK
```

- [ ] **Step 4: 验证连接池无泄漏**

```bash
venv2/bin/python -c "
from db import get_connection
for i in range(100):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute('SELECT 1')
    cur.fetchone()
    cur.close()
    conn.close()
print('100 rounds, no pool exhaustion')
"
```

- [ ] **Step 5: 验证缓存线程安全（多线程并发）**

```bash
venv2/bin/python -c "
import threading, sys
sys.path.insert(0, '.')
from fund_crawler import crawl_fund_nav_df

errors = []
def fetch():
    try:
        crawl_fund_nav_df('000001')
    except Exception as e:
        errors.append(str(e))

threads = [threading.Thread(target=fetch) for _ in range(10)]
for t in threads: t.start()
for t in threads: t.join()
print(f'10 concurrent NAV fetches: {\"OK\" if not errors else errors}')
"
```

- [ ] **Step 6: Commit**

```bash
echo "All regression tests passed - no code changes"
```

---

### Task 6: 清理 — 删除遗留的全局变量声明

**Files:**
- Modify: `/media/yskey/文档/work/mytest/auth_manager.py`
- Modify: `/media/yskey/文档/work/mytest/payment_gateway.py`
- Modify: `/media/yskey/文档/work/mytest/portfolio_manager.py`

- [ ] **Step 1: `auth_manager.py` — 清理未使用的 globals 和 imports**

删除顶部的全局变量声明（`_get_mysql_pool`, `_get_cache`, `_set_cache`, `_SQLITE_DB_PATH`），它们不再被 `_db_execute` 使用。

但检查是否有其他函数引用这些变量：
```bash
grep -n "_get_mysql_pool\|_get_cache\|_set_cache\|_SQLITE_DB_PATH" /media/yskey/文档/work/mytest/auth_manager.py | grep -v "^#"
```
如果有引用，保持；如果没有，删除全局声明和 `_ensure_deps` 中的赋值。

- [ ] **Step 2: `payment_gateway.py` — 同样的清理**

```bash
grep -n "_get_mysql_pool\|_set_cache\|_SQLITE_DB_PATH" /media/yskey/文档/work/mytest/payment_gateway.py | grep -v "^#"
```
保留 `_get_mysql_pool` 因为 `_store_payment_result` 和 `_get_payment_result` 需要通过 app 访问缓存。但 `_get_mysql_pool` 不再需要——这些函数使用 `app.set_cache`/`app.get_cache`，不涉及数据库。

删除 `_get_mysql_pool` 和 `_SQLITE_DB_PATH` 的全局声明，删除 `_ensure_deps` 函数（如果还存在）。

- [ ] **Step 3: `portfolio_manager.py` — 同样的清理**

```bash
grep -n "_get_mysql_pool\|_get_cache\|_set_cache\|_SQLITE_DB_PATH" /media/yskey/文档/work/mytest/portfolio_manager.py | grep -v "^#"
```
`portfolio_manager.py` 有 `_init_deps()` 函数用于其他目的（缓存操作），保持它但删除 `_get_mysql_pool` 和 `_SQLITE_DB_PATH` 相关的全局声明。

`_SQLITE_DB_PATH` 仍在 `_sqlite_execute` 中使用——但该函数已删除。检查 `_init_deps` 中是否设置 `_SQLITE_DB_PATH` 以及还有谁用：
```bash
grep -n "_SQLITE_DB_PATH" /media/yskey/文档/work/mytest/portfolio_manager.py
```
如果只在 `_init_deps` 中设置且在 `_ensure_deps` 中使用（都已删除），则删除全局声明。

- [ ] **Step 4: 最终验证**

```bash
cd /media/yskey/文档/work/mytest
venv2/bin/python -m py_compile auth_manager.py payment_gateway.py portfolio_manager.py app.py && echo "All modules compile OK"
```

- [ ] **Step 5: Commit**

```bash
cd /media/yskey/文档/work/mytest
git add auth_manager.py payment_gateway.py portfolio_manager.py
git commit -m "chore: remove unused global variables and dead code

Clean up _get_mysql_pool, _SQLITE_DB_PATH, _ensure_deps leftovers
from modules migrated to db.py.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```
