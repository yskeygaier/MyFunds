# -*- coding: utf-8 -*-
"""统一数据库模块 — 线程安全连接池 + MySQL/SQLite 降级"""
import queue
import sqlite3
import pymysql

# ── 配置（由 app 启动时设置）──────────────────────────────────
SQLITE_DB_PATH = None
_MYSQL_CONFIG = None


# ── 内部连接包装 ──────────────────────────────────────────────

class _PooledConn:
    """包装 pymysql 连接，close() 时归还到池"""

    def __init__(self, conn, pool_queue):
        self._conn = conn
        self._pool_queue = pool_queue
        self._invalid = False

    def cursor(self):
        return self._conn.cursor()

    def commit(self):
        self._conn.commit()

    def rollback(self):
        try:
            self._conn.rollback()
        except Exception:
            pass

    def invalidate(self):
        """标记此连接为无效，close() 时将真正关闭而不是归还到池"""
        self._invalid = True

    def close(self):
        """归还连接到池（如果有效），否则真正关闭"""
        if self._invalid or self._pool_queue is None:
            try:
                self._conn.close()
            except Exception:
                pass
            return
        try:
            self._pool_queue.put_nowait(self._conn)
        except queue.Full:
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


# ── 连接池 ────────────────────────────────────────────────────

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
                    conn.invalidate()
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
        had_error = False
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            for idx_name, table, col in indexes:
                try:
                    cur.execute(
                        f"CREATE INDEX {idx_name} ON {table}({col})"
                    )
                    conn.commit()
                    print(f"[db] Index {idx_name} created on {table}({col})")
                except Exception:
                    pass  # 索引已存在
            cur.close()
        except Exception as e:
            had_error = True
            print(f"[db] Index creation skipped: {e}")
        finally:
            if conn:
                try:
                    if had_error:
                        conn.invalidate()
                    conn.close()
                except Exception:
                    pass

    def ensure_sqlite_schema(self):
        """SQLite 列对齐：添加缺失的列"""
        if not SQLITE_DB_PATH:
            return
        try:
            conn = sqlite3.connect(SQLITE_DB_PATH)
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


def get_pool():
    """获取当前连接池单例（总是返回最新值，不捕获导入时的 None）"""
    return pool


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
    """获取原生 MySQL 连接（复杂操作用，用完后必须 close()）"""
    if pool is None:
        raise RuntimeError("db pool not initialized")
    return pool.get_connection()


def _sqlite_execute(sql, params=None, fetch=True):
    """内部 SQLite 执行器（集中处理所有 SQL 方言差异）"""
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
            rows = cur.rowcount
        else:
            conn.commit()
            rows = cur.rowcount
        cur.close()
        return rows
    finally:
        conn.close()
