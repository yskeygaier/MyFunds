# Delta: PortfolioClinic Core Engine

**Change ID:** `portfolio-clinic`
**Affects:** portfolio_clinic.py (new), routes_portfolio_eval.py (modified)

---

## ADDED

### Component: PortfolioClinic (new module)

独立组合诊断引擎，从 routes_portfolio_eval.py 的16个私有函数抽取。

#### `PortfolioClinic.analyze(holdings) -> ClinicReport`
- GIVEN 用户已确认持仓列表
- WHEN 调用 analyze()
- THEN 并行获取每只基金数据，成功时合并到报告
- AND 部分基金获取失败时在报告中标记"数据暂缺"横幅
- AND 全部失败时返回错误

#### `PortfolioClinic.backtest(holdings, years=3) -> BacktestResult`
- GIVEN 有效的持仓信息和回测周期
- WHEN 调用 backtest()
- THEN 并行获取每只基金的历史净值
- AND 合成组合净值曲线
- AND 计算沪深300基准对比（新增）
- AND 计算总收益/年化/最大回撤/夏普/波动率
- AND 净值数据不足10天时跳过回测

#### `PortfolioClinic.generate_recommendations(analysis) -> Recommendations`
- GIVEN 组合分析结果（含metrics/style/sectors/risk）
- WHEN 调用 generate_recommendations()
- THEN 按集中度/风险适配/行业分散/数量优化/风格平衡逐项检查
- AND 每项建议含数据支撑 + 具体动作 + 预期效果
- AND 预期效果展示计算逻辑（非黑盒）

### Data Structure: ClinicReport (dataclass)

```
ClinicReport:
  holdings: list[FundHolding]        # 持仓明细（含每只4P评分）
  metrics: PortfolioMetrics           # 组合指标
  style: StyleBreakdown               # 风格分析
  sectors: SectorBreakdown            # 行业分布
  risk: RiskAssessment                # 风险评估
  recommendations: Recommendations    # 调仓建议
  llm_summary: str | None             # LLM摘要（异步，可能为空）
  share_url: str | None               # 分享链接
  missing_funds: list[str]            # 数据暂缺的基金
  health_score: float                 # 组合健康分 0-100
```

### Data Structure: BacktestResult (dataclass)

```
BacktestResult:
  dates: list[str]                   # 日期序列
  navs: list[float]                  # 组合净值序列
  benchmark_navs: list[float] | None # 沪深300净值序列
  total_return: float
  annualized_return: float
  max_drawdown: float
  max_dd_date: str
  volatility: float
  sharpe_ratio: float
  recovery_days: int
  data_points: int
  period_years: float
```

---

## MODIFIED

### Component: routes_portfolio_eval.py

**现有路由函数保持不变**（upload_and_extract_portfolio, analyze_portfolio, backtest_portfolio），但实现改为调用 PortfolioClinic 而非内联逻辑。

#### `analyze_portfolio()` — 改为委托
- GIVEN POST 请求携带 holdings
- WHEN 调用 analyze_portfolio()
- THEN 验证输入后委托 `PortfolioClinic.analyze(holdings)` 处理
- AND 返回 ClinicReport 的 JSON 序列化结果

#### `backtest_portfolio()` — 改为委托
- GIVEN POST 请求携带 holdings + years
- WHEN 调用 backtest_portfolio()
- THEN 委托 `PortfolioClinic.backtest(holdings, years)` 处理
- AND 返回 BacktestResult 的 JSON 序列化结果

### Component: fund_crawler.py

#### 新增函数: `crawl_csi300_nav(years=3)`
- GIVEN 回测需要基准对比
- WHEN 调用 crawl_csi300_nav(years)
- THEN 通过akshare获取沪深300指数历史净值
- AND 返回与 `crawl_fund_nav_df` 相同格式的数据

---

## REMOVED

(None — 所有现有功能保留，内部实现重构)
