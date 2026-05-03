# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**基金分析工具** (Fund Analysis Tool) - A Flask web application providing Chinese fund information lookup, historical backtesting, dollar-cost averaging (DCA) calculations, real-time valuation tracking, and AI-powered investment analysis reports using the "4P三性" methodology.

- **Port**: 5001
- **Host**: 127.0.0.1
- **venv**: `/media/yskey/文档/work/mytest/venv/bin/python`
- **MySQL**: Connected (host=127.0.0.1, port=3306, database=fund_data)
- **Redis**: Connected
- **SQLite**: Fallback for fund_basic/fund_holdings data

## Project Structure

```
/media/yskey/文档/work/mytest/
├── app.py                          # Main Flask application (3469 lines)
├── templates/index.html             # Single-page frontend (2227 lines)
├── fund_data.db                     # SQLite fallback database
├── requirements.txt                 # Dependencies
├── CLAUDE.md                        # This file
└── fund_data/                       # JSON fund data files
    └── {fund_code}.json
```

## Commands

```bash
# Start the Flask application
cd /media/yskey/文档/work/mytest && nohup venv2/bin/python app.py > /tmp/app.log 2>&1 &

# Verify syntax
venv2/bin/python -m py_compile app.py

# Check if running
ps aux | grep app.py | grep -v grep
```

## Health Check (2026-04-25)

All endpoints verified OK:
- `/` → 200
- `/api/fund/info?fund_code=000001` → ✅ data.经理_details完整（刘睿聪/郑晓辉）
- `/api/fund/manager?fund_code=000001` → ✅ OK
- `/api/fund/search?keyword=000001` → ✅ 1 result
- `/api/fund/analysis_report?fund_code=000001` → ✅ 4P评分64分，redis_cache缓存命中

**000001分析结论：** 谨慎关注（最大回撤59%过大，夏普0.53偏低，量化初筛未通过）

## 性能优化记录 (2026-04-25)

### 问题：分析报告生成 >15秒
**根因：** `fetch_manager_info_with_timeout` 每次请求都从网络下载 `ak.fund_manager_em()` 全量34K行（~11秒），且串行在 fetch 流程里。

### 解决：A+B 双措
1. **方案A — 全局基金经理缓存**：启动时一次性加载 34K 行到 `MANAGER_DF` 全局变量，后续查询直接内存过滤（11s → 0.01s）
2. **方案B — 报告复用 info 数据**：`get_analysis_report` 优先从 Redis/内存缓存读取，`fetch_fund_info` 已通过 ThreadPoolExecutor 并发获取 manager+industry+holdings

### 效果（冷启动，无缓存）：
- 000001/162201/519772：1.0~1.5s（原 >15s）
- 缓存命中：<1ms
- 启动时 manager cache 加载：~12.5s（仅一次）

### 方案B+C（2026-04-26）：分析报告历史库 + 热点基金预热

**问题：** 即使 info 缓存命中，分析报告仍需回源计算 FundScreener.screen()（~0.5-1s），且历史数据无法积累。

**方案B — 热点基金启动预热：**
- 启动时后台异步批量生成 Top30 热点基金分析报告缓存（`_warmup_top_funds_report`）
- 覆盖 SQLite fund_list_cache 中前30只基金

**方案C — MySQL历史报告持久化：**
- 新建 `fund_analysis_history` 表（fund_code, week_number, report_data, generated_at）
- 每次生成报告后异步写入 MySQL（`ON DUPLICATE KEY UPDATE` 实现 Upsert）
- 读取优先级：Redis缓存 → MySQL历史库 → 回源计算
- 每周日凌晨2点自动刷新历史库（`_schedule_weekly_refresh`）

**新增函数（app.py ~line 2060）：**
- `_init_analysis_history_table()` — MySQL建表
- `_save_report_to_mysql()` — Upsert写入
- `_get_report_from_mysql()` — 按基金读最新周报告
- `_warmup_top_funds_report()` — 启动时批量预热（后台线程）
- `_schedule_weekly_refresh()` — sched调度每周刷新
- `_weekly_refresh_history_reports()` — 实际刷新逻辑
- `get_analysis_report()` — v3版，3层降级：缓存→MySQL历史→回源

**效果：**
- 热点基金（Top30）：启动预热后，报告直接命中 MySQL历史库，<50ms
- 非热点基金：info缓存命中时走DB路径 <1s
- MySQL不可用时：自动降级到纯缓存模式（fund_list_cache无数据则跳过预热）

### 代码改动：
- `app.py` 全局新增 `MANAGER_DF` / `_ensure_manager_cache()` / `_reload_manager_cache_async()`
- `fetch_manager_info_with_timeout()` 优先读缓存，无缓存才网络获取
- `_get_akshare_timeout()` 保持 15s（industry/holdings 仍有网络延迟）

## API Endpoints

| Endpoint | Purpose | Cache TTL |
|----------|---------|-----------|
| `GET /` | Render main page | - |
| `GET /api/fund/info` | Fund basic info + holdings + risk metrics | 1 hour |
| `GET /api/fund/manager?fund_code=xxx` | 基金经理详情（独立异步接口，不含在 info 里）| 未缓存 |
| `GET /api/fund/analysis_report?fund_code=xxx` | 4P三性分析报告 | 3层降级：Redis缓存→MySQL历史→回源（TTL 1小时）|

**分析报告优化（2026-04-25）：**
- 优先复用 `/fund/info` 缓存数据（避免14秒3年NAV重复下载）
- 分析报告自身独立缓存1小时，重复查询<1秒
- info+report 双缓存：info缓存命中时冷启动约15秒，report缓存命中时<1秒
| `GET /api/fund/backtest?fund_code=xxx&start_date=xxx&end_date=xxx` | Historical NAV chart | 2 hours |
| `GET /api/fund/dca?fund_code=xxx&amount=xxx&frequency=xxx` | DCA calculation | 2 hours |
| `GET /api/fund/search?keyword=xxx` | Search funds by code/name | - |
| `GET /api/fund/valuation?fund_code=xxx` | Real-time valuation (never cached) | - |

## Data Flow (Fund Info)

Priority order:
1. Redis cache (REDIS_AVAILABLE=False, falls back to memory_cache)
2. MySQL database (connection refused, falls back to SQLite)
3. SQLite `fund_data.db`
4. Local JSON files `fund_data/{code}.json`
5. **Live fetch via akshare** (last resort, ~1-17s latency)

## Key Data Structures

### Fund Info Response (`/api/fund/info`)

```python
{
    '基金代码': str,
    '基金简称': str,
    '单位净值': str,        # e.g. "3.4521"
    '净值日期': str,
    '日增长率': str,        # e.g. "2.35%"
    '年化收益率': str,       # e.g. "18.52%"
    '年化波动率': str,       # e.g. "22.15%"
    '夏普比率': str,         # e.g. "0.85"
    '卡玛比率': str,         # e.g. "1.23"
    '最大回撤': str,          # e.g. "-28.45%"
    '基金经理': str,          # e.g. "张坤"
    '基金经理公司': str,
    '从业年限': str,          # e.g. "8.5年"
    '从业天数': int,
    '管理基金数量': int,
    '管理基金总规模': str,    # e.g. "520.35亿"
    '最佳回报率': str,        # e.g. "156.32%"
    'manager_details': [     # List of all funds managed by this manager
        {
            'name': str,
            'company': str,
            'days': int,
            'fund_code': str,
            'fund_name': str,
            'scale': float,
            'best_return': float
        }
    ],
    '第一大行业': str,
    '行业占比': str,
    '基金风格': str,          # e.g. "大盘成长"
    '风格描述': str,
    '持仓集中度': str,         # e.g. "68.5%"
    '前十大持仓': [
        {
            '股票代码': str,
            '股票名称': str,
            '细分行业': str,
            '占净值比例': str   # e.g. "9.52%"
        }
    ]
}
```

### 4P三性 Analysis Report (`/api/fund/analysis_report`)

```python
{
    'success': True,
    'fund_code': str,
    'fund_name': str,
    'source': str,           # 'redis_cache' | 'mysql_database' | 'sqlite_database' | 'akshare'
    'generated_at': str,
    'four_p': {
        'performance': {'score': int, 'max': 25, 'verdict': str, 'detail': str},
        'philosophy':  {'score': int, 'max': 25, 'verdict': str, 'detail': str},
        'people':      {'score': int, 'max': 30, 'verdict': str, 'detail': str},
        'process':     {'score': int, 'max': 20, 'verdict': str, 'detail': str},
        'total':       {'score': int, 'max': 100}
    },
    'three_natures': {
        '一致性': {'result': str, 'detail': str},
        '稳定性': {'result': str, 'detail': str},
        '有效性': {'result': str, 'detail': str}
    },
    'recommendation': str,   # '强烈推荐' | '建议持有' | '谨慎关注' | '不建议投资'
    'recommendation_color': str,
    'risk_level': str,        # '低风险' | '中等风险' | '中高风险' | '高风险'
    'holdings_analysis': [...],
    'top_sectors': [{'sector': str, 'weight': str}],
    'metrics': [...],
    'summary': [str, ...]    # Paragraph array for display
}
```

## Frontend Sections (renderFundInfoParts in index.html)

The info tab renders these sections in order (each is a skeleton placeholder that gets populated independently):

1. `#skeleton-main` - Fund name, style badge, analysis report button
2. `#skeleton-valuation` - Real-time estimation (loaded separately via `/api/fund/valuation`)
3. `#skeleton-basic` - Basic info grid (code, manager, NAV, date, day growth)
4. `#skeleton-manager` - **基金经理评估卡片** (see below)
5. `#skeleton-performance` - Risk/return metrics (annual return, volatility, Sharpe, Calmar, max drawdown)
6. `#skeleton-industry` - Industry allocation (top industry, weight, concentration)
7. `#skeleton-holdings` - Top 10 holdings table

### 基金经理评估卡片 (Manager Evaluation Card)

Already implemented in frontend JS (lines 1507-1602 of index.html):

**Displayed Fields:**
- Manager name + company
- 从业年限 (experience level badge: 新手/进阶/成熟/资深)
- 管理基金数量 + 总规模
- 最佳回报率
- 稳定性评级 (based on number of funds managed: 良好/一般/频繁跳槽)
- 管理基金详情列表 (scrollable, showing each fund's best return)

**Backend Data Source:**
- `fetch_manager_info_with_timeout()` in app.py (lines 795-842)
- Uses `ak.fund_manager_em()` API
- Returns `manager_details` array with all funds managed by this person

## 4P三性 Methodology (好买基金选基法)

Implemented in app.py (lines 1760-1948):

### 4P Weights:
- Performance (业绩): 25/100
- Philosophy (理念): 25/100  
- People (管理人): 30/100
- Process (流程): 20/100

### 三性 (一票否决):
- 一致性 (Consistency): Investment style consistency over time
- 稳定性 (Stability): Performance stability across cycles
- 有效性 (Effectiveness): Ability to generate alpha

**Recommendation Levels:**
- 强烈推荐: total_4p >= 80 AND all three natures pass
- 建议持有: total_4p >= 60 AND all three natures pass
- 谨慎关注: total_4p >= 45
- 不建议投资: total_4p < 45

## Key Functions in app.py

| Function | Line | Purpose |
|----------|------|---------|
| `get_fund_info()` | 585 | Main info endpoint |
| `fetch_fund_info()` | 1757 | Raw akshare fetch |
| `fetch_manager_info_with_timeout()` | 795 | Manager data via akshare |
| `fetch_industry_info()` | 844 | Industry allocation |
| `fetch_holdings_info()` | ~880 | Top 10 holdings |
| `get_analysis_report()` | 1760 | 4P三性 report |
| `_score_performance()` | ~1140 | 4P performance scoring |
| `_score_philosophy()` | ~1200 | 4P philosophy scoring |
| `_score_people()` | ~1260 | 4P people scoring |
| `_score_process()` | ~1320 | 4P process scoring |
| `_three_natures_check()` | ~1380 | 三性 validation |
| `get_fund_backtest()` | 1950 | NAV history + chart |
| `calculate_dca()` | 2105 | DCA simulation |
| `search_fund()` | 2486 | Fund search |
| `get_fund_valuation()` | 2591 | Real-time valuation |
| `get_fund_name()` | 199 | Multi-source name resolution |

## Caching

```python
CACHE_CONFIG = {
    'fund_info':    {'expiry': 3600,  'prefix': 'fund:info'},    # 1 hour
    'fund_backtest': {'expiry': 7200,  'prefix': 'fund:backtest'}, # 2 hours
    'fund_dca':     {'expiry': 7200,  'prefix': 'fund:dca'},     # 2 hours
    'fund_list':    {'expiry': 86400, 'prefix': 'fund:list'},    # 24 hours
}
```

Note: Real-time valuation (`/api/fund/valuation`) is **never cached** - always fresh.

## Stock Sector Tags

app.py contains a large `STOCK_SECTOR_TAGS` dict (lines 862-1280+) mapping stock names to Chinese sector descriptions. Used for holdings analysis to tag each stock with its industry.

## Database Schema

### fund_basic table
```sql
fund_code, fund_name, net_value, nav_date, day_growth, annual_return,
annual_volatility, sharpe_ratio, calmar_ratio, max_drawdown,
fund_manager, first_industry, industry_ratio, fund_style, holdings_concentration,
update_time
```

### fund_holdings table
```sql
fund_code, holding_seq, stock_code, stock_name, sector, weight, update_time
```

### Common Fund Codes (for testing)

| Code | Name |
|------|------|
| 161039 | 易方达中小盘混合 |
| 519674 | 银河创新成长混合 |
| 110011 | 易方达消费行业股票 |
| 163406 | 兴全合润混合 |
| 161725 | 招商中证白酒指数 |
| 005918 | 易方达蓝筹精选混合 |
| 510300 | 华夏沪深300ETF |

## UI 颜色规范（全局约定，无需重复提示）

**收益/增长率颜色规则**：
- 正数（盈利/上涨）→ 红色 `var(--danger)` 或 `#EF4444`
- 负数（亏损/下跌）→ 绿色 `var(--success)` 或 `#10B981`

此规则适用于所有涉及金额、收益率、回报率的展示场景，包括但不限于：
- 日增长率
- 年化收益率
- 最佳回报率
- 基金详情列表中各基金的回报率
- 回测收益、定投收益等

## Dependencies

Key packages in venv2:
- `flask` - Web framework
- `akshare` - Chinese financial data (akshare>=1.10.0)
- `pandas` - Data processing
- `plotly` - Charts
- `redis` - Caching (optional)
- `mysql.connector` - MySQL support (optional)
|- `sqlite3` - SQLite fallback (stdlib)

## gstack Skills

gstack transforms Claude Code into a virtual engineering team. Use these slash commands in Claude Code:

### Plan-mode reviews
- `/office-hours` — Start here. Reframes your product idea before you write code.
- `/plan-ceo-review` — CEO-level review: find the 10-star product in the request.
- `/plan-eng-review` — Lock architecture, data flow, edge cases, and tests.
- `/plan-design-review` — Rate each design dimension 0-10, explain what a 10 looks like.
- `/plan-devex-review` — DX-mode review: TTHW, magical moments, friction points, persona traces.
- `/autoplan` — One command runs CEO → design → eng → DX review.

### Implementation + review
- `/review` — Pre-landing PR review. Finds bugs that pass CI but break in prod.
- `/investigate` — Systematic root-cause debugging. No fixes without investigation.
- `/qa` — Open a real browser, find bugs, fix them, re-verify.
- `/cso` — OWASP Top 10 + STRIDE security audit.
- `/browse` — All web browsing MUST use this skill. Do NOT use MCP browser tools.

### Release + deploy
- `/ship` — Run tests, review, push, open PR.
- `/retro` — Weekly retro with per-person breakdowns and shipping streaks.