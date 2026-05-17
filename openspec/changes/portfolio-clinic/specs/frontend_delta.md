# Delta: Frontend Report Template

**Change ID:** `portfolio-clinic`
**Affects:** templates/portfolio_eval.html (modified)

---

## ADDED

### UI Component: 诊断报告模板

在现有 portfolio_eval.html 的分析结果区域新增诊断报告视图。

#### 组合健康分仪表盘
- GIVEN 分析结果包含 health_score
- WHEN 渲染报告顶部
- THEN 显示圆形仪表盘（0-100分），颜色渐变（红<40 → 黄40-70 → 绿>70）
- AND 显示评分等级标签（如"良好/一般/需改善"）

#### 指标摘要卡片
- GIVEN 分析结果包含 metrics
- WHEN 渲染健康分下方
- THEN 以2x2网格展示：年化收益（正红负绿）、最大回撤、夏普比率、组合规模（基金数）
- AND 每个指标带简短说明文字

#### 持仓诊断列表
- GIVEN 分析结果包含 holdings
- WHEN 渲染指标下方
- THEN 每只基金显示：名称+代码、权重（带进度条）、年化收益（正红负绿）、最大回撤、4P评分
- AND 标记"拖后腿"基金（收益为负且权重>15%）

#### 回测曲线
- GIVEN 分析结果包含 backtest_result
- WHEN 渲染持仓下方
- THEN 用 Plotly 绘制组合净值曲线（主色）和沪深300曲线（灰色虚线）
- AND 高亮最大回撤区间（浅红背景）
- AND 图例标注

### UI Component: 方法卡

- GIVEN 报告已生成
- WHEN 用户点击"这些分数怎么算的？"折叠按钮
- THEN 展开显示：组合健康分公式（4维度权重）、各维度评分标准、数据来源说明
- AND 不要求用户理解金融术语

### UI Component: 调仓模拟器

- GIVEN 报告已生成且含 recommendations
- WHEN 用户进入"调仓模拟"tab
- THEN 显示各基金权重拖拽滑块
- AND 用户拖动时前端即时计算预期效果（健康分变化、回撤变化、收益变化）
- AND 显示"调整前 vs 调整后"对比指标
- AND 开发模式下自动调用验证端点比对JS结果

### UI Component: 分享链接

- GIVEN 报告已渲染完成
- WHEN 用户点击"分享"按钮
- THEN 生成唯一URL（UUID v4）并显示
- AND 提供"复制链接"按钮
- AND 已分享的链接显示"删除分享"按钮

### UI Component: LLM摘要（异步加载）

- GIVEN 报告已渲染
- WHEN 用户查看"AI诊断"区域
- THEN 显示加载中占位符
- AND 异步请求LLM摘要
- AND 完成后展示三段式：发现问题 → 建议方案 → 预期效果

---

## MODIFIED

### 持仓确认界面

- GIVEN 图片提取完成
- WHEN 进入持仓确认
- THEN 展示提取结果列表，每行包含：基金代码、名称、权重、金额
- AND 基金代码高亮显示（2秒）：若非6位数字则标记红色"需编辑"（输出端验证）
- AND 用户可修改任何字段或删除行
- AND 底部"添加基金"按钮可手动输入

### 渐进式加载

- GIVEN 用户上传并点击分析
- THEN 立即显示加载动画
- AND 指标数据到达后逐步渲染（不等待全部完成）
- AND 每部分渲染时更新进度指示

---

## REMOVED

(None — 现有上传/手动编辑界面保留)
