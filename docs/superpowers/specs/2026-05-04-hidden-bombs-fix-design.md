# 隐藏炸弹修复 — 线程安全 + 索引 + DB 一致性

日期：2026-05-04  
状态：设计已批准，待实现

## 背景

深度性能诊断发现 5 个严重问题（"隐藏炸弹"），当前数据量小未引发故障，但增长后会出问题：

1. `_nav_cache` 无线程锁 — ThreadPoolExecutor 多线程并发读写
2. `_MySQLPool._pool` — `list.pop()` 非线程安全
3. 3 个缺失 MySQL 索引 — 全表扫描
4. SQLite/MySQL 列不一致 — 部分列只在 MySQL 存在
5. `_db_execute` 在 4 个文件中重复实现，细微差异导致 bug

## 设计方案（方案 B — 结构加固）

### 第一部分：`db.py` — 统一数据库模块

新建文件，替代 4 个模块中各自为政的 `_db_execute`。

```
db.py
├── class DatabasePool     — 线程安全连接池（queue.Queue）
│   ├── get()             — 从队列取连接
│   ├── put(conn)         — 归还连接
│   ├── execute(sql, ...) — 统一入口：MySQL 优先 → SQLite 降级
│   ├── _ensure_indexes() — 启动时建缺失索引
│   └── _ensure_sqlite_schema() — SQLite 列对齐
└── db_execute()           — 模块级快捷函数
```

关键设计：
- `queue.Queue` 替代 `list.pop()`，天然线程安全
- 所有写操作统一返回 `rowcount`（修复 `payment_gateway` 返回 None 的 bug）
- 异常时自动降级：MySQL 失败 → SQLite，都失败才上抛
- 启动时自动检测并创建缺失索引

### 第二部分：`cache.py` — 线程安全缓存

新建文件，统一包装全局缓存。

```python
class ThreadSafeCache:
    # RLock + dict，支持可选 TTL
    def get(key)
    def set(key, value, ttl=0)
    def clear()
```

需要包装的缓存：

| 缓存 | 位置 | 风险 | TTL |
|------|------|------|-----|
| `_nav_cache` | fund_crawler.py | ThreadPoolExecutor 并发写 | 300s/1800s |
| `MANAGER_DF` | app.py | 变量替换非原子 | 86400s |
| `memory_cache` | app.py | 后台线程并发写 | 随场景 |

### 第三部分：启动自动修复

3 个缺失索引在 `ensure_indexes()` 中自动创建：
```sql
CREATE INDEX IF NOT EXISTS idx_sub_out_trade_no ON user_subscriptions(out_trade_no);
CREATE INDEX IF NOT EXISTS idx_sub_poll_token ON user_subscriptions(poll_token);
CREATE INDEX IF NOT EXISTS idx_hold_fund_code ON portfolio_holdings(fund_code);
```

SQLite 列对齐在 `ensure_sqlite_schema()` 中执行：
- 检测 `PRAGMA table_info` 缺失的列（poll_token、contact 等）
- 自动 `ALTER TABLE ADD COLUMN`

### 受影响文件

| 文件 | 改动 | 行数变化 |
|------|------|---------|
| 新建 `db.py` | 连接池 + 执行器 + 索引/迁移 | +120 |
| 新建 `cache.py` | ThreadSafeCache | +40 |
| `app.py` | 删除连接池类和 `_db_execute`，改用 `db.py`/`cache.py` | -80 / +10 |
| `auth_manager.py` | 删除 `_db_execute`/`_sqlite_execute` | -50 / +3 |
| `payment_gateway.py` | 删除 `_db_execute`/`_sqlite_execute`/`_ensure_deps` | -45 / +3 |
| `portfolio_manager.py` | 删除 `_db_execute`/`_sqlite_execute`/`_ensure_deps` | -50 / +3 |
| `fund_crawler.py` | `_nav_cache` → ThreadSafeCache | +5 |

净效果：删除 ~200 行重复代码，新增 ~170 行基础设施。

## 验证方式

1. 启动后 `EXPLAIN` 确认索引生效（type=ref, key=新索引名）
2. 多线程并发测试：100 并发 `crawl_fund_nav_df` 无数据竞争
3. SQLite 确认列对齐：`PRAGMA table_info(user_subscriptions)` 含 poll_token
4. 全部 API 端点回归：响应时间不退化
5. MySQL 连接池借还循环 1000 次无连接泄漏

## 不移入范围

- 方案 C 的监控能力（logging、/api/health、慢查询日志）— 后续按需追加
- app.py 拆分 — 不在本次范围
- 前端优化 — 不在本次范围
- DCA/Manager 冷启动优化 — 不在本次范围
