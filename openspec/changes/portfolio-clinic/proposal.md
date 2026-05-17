# Proposal: 独立组合诊所 (Portfolio Clinic)

**Change ID:** `portfolio-clinic`
**Created:** 2026-05-17
**Status:** Archived
**Completed:** 2026-05-17
**Archived:** 2026-05-17

---

## Problem Statement

基金投资小白（如"小李"：自学理财、上过课、有点认知但没实操能力）跟风大V买了组合，10万亏了2万，跑来问"怎么调仓挽回损失"。他面对的不是"选哪只基金好"的问题，而是"我的组合整体到底行不行、哪只拖后腿、怎么换"的问题。

现有系统（天天基金、好买、晨星）能查单只基金评分，但没有人能回答「我的这个组合整体好不好」——用户要的不是另一份基金排行榜，而是一个能告诉他"亏在哪、换什么、换了能好多少"的诊断工具。

**痛点验证：**
- 至少3名粉丝在评论区留言"求带"（真实需求信号）
- 用户跟风买入后亏损来问怎么办——这个场景反复出现
- 创始人自己在做基金投资，切身体会到缺乏组合级诊断工具的痛点

## Proposed Solution

三步走方案：

**Step 0（验证阶段）— 先跑通再动手：** 配置豆包API密钥，用真实截图跑通现有 routes_portfolio_eval.py 全链路，验证图片提取准确率（>70%）、净值数据准确性（<5%偏差）、akshare并发能力和总延迟（<120s）。有明确退出条件。

**Step 1（构建阶段）— 打磨到可演示：** 将现有779行单体路由文件中的16个私有函数抽取为独立 `PortfolioClinic` 类（dataclass报告结构，三层接口），设计结构化诊断报告HTML模板（客户端渲染），包含组合健康分仪表盘、回测曲线（含沪深300基准对比）、持仓明细+4P评分、方法卡（评分公式透明）、调仓模拟器（前端即时计算+JS/Python公式验证端点）、可分享URL（UUID v4，可删除）、LLM诊断摘要（异步加载）。

**Step 2（增强阶段）— 有条件进入：** LLM摘要增强、组合对比。只有在Step 1获得正面用户反馈后才进入。

## Scope

### In Scope
- 豆包API密钥配置与图片提取集成
- PortfolioClinic 类抽取（从 routes_portfolio_eval.py 解耦）
- 结构化诊断报告HTML模板（客户端渲染）
- 组合健康分指标体系（非4P，自定义4维度评分）
- 回测曲线 + 沪深300基准对比
- 方法卡（评分公式透明展示）
- 调仓模拟器（前端权重拖拽 + 实时指标计算 + 公式验证端点）
- 分享URL（UUID v4，可删除）
- 手动编辑兜底（提取结果确认/修改）
- LLM诊断摘要（异步加载，用豆包API）
- 单元测试（Step 1完成后补）

### Out of Scope
- PDF导出（直到有付费信号）
- 微信支付/收费系统（同上）
- SEO/抖音推广（同上）
- 独立缓存层（零用户，过早优化）
- 复合基准支持（CSI300+中证债券，记入TODOS）
- 组合对比（Step 2，有条件进入）
- E2E测试基础设施（项目级缺失，非此计划范围）

## Impact Analysis

| Component | Change Required | Details |
|-----------|-----------------|---------|
| Database | No | 不需要数据库变更。报告数据在session内存中，不持久化 |
| API | Yes | 新增 `portfolio_clinic.py` 模块；修改 `portfolio_eval_bp` 路由层保持薄层；新增公式验证端点 |
| State | No | 不需要全局状态管理。截图哈希作为匿名session键 |
| UI | Yes | 修改 `portfolio_eval.html`（515行）增加诊断报告模板、分享URLUI、方法卡、调仓模拟器 |
| Config | Yes | 需要配置豆包API环境变量（DOUBAO_API_KEY, DOUBAO_MODEL） |

## Architecture Considerations

**模块结构：**
```
routes_portfolio_eval.py  → 薄路由层（仅HTTP适配）
portfolio_clinic.py       → 新模块（核心引擎，dataclass输出）
fund_crawler.py           → 增加CSI300净值获取函数
portfolio_eval.html       → 增加报告模板、模拟器、方法卡
```

**关键设计决策：**
- ClinicReport 用 dataclass + to_dict() 实现类型安全和JSON兼容
- LLM摘要作为独立步骤异步加载（不阻塞主报告）
- 调仓模拟器前端即时计算 + 隐藏端点进行公式校验
- 分享URL用UUID v4，服务端不存储截图

**依赖关系：**
- 豆包API（火山引擎视觉模型）—— Step 0 必须完成，否则全链路用mock
- akshare（金融数据源）—— 已有，但需验证并发能力
- 无需新增第三方依赖

## Success Criteria

- [ ] 上传截图 → 提取持仓 → 出诊断报告，全链路 ≤ 60秒
- [ ] 诊断报告包含：组合健康分 + 回测曲线(含基准) + 每只基金贡献分析 + 调仓建议 + 方法卡
- [ ] 创始人在自己的组合上跑通，得到一个"有道理、能执行"的诊断结果
- [ ] 找2-3个真实粉丝试用，获取"有用/没用"的反馈
- [ ] 调仓模拟器 JS 计算与 Python 计算结果一致（公式验证端点通过）

## Risks & Mitigations

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| 豆包API图片提取准确率<70% | Medium | High | Step 0有退出条件；提取结果可手动编辑 |
| akshare并发限制导致延迟超120s | Medium | Medium | Step 0验证并发能力；渐进式渲染先显示已就绪数据 |
| 前端JS公式与Python公式不一致 | Medium | High | 公式验证端点 + 开发模式自动比对 |
| 净值回测数据与官方偏差>5% | Low | High | Step 0交叉验证3只已知基金 |
| 用户看不懂技术指标 | Medium | Medium | 方法卡+LLM摘要解决透明度问题 |
