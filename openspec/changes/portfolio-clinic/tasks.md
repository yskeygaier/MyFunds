# Implementation Tasks: 独立组合诊所 (Portfolio Clinic)

**Change ID:** `portfolio-clinic`

---

## Phase 1: Foundation — 验证 (Step 0) ✅

- [x] 1.1 申请并配置豆包API密钥 ✅
- [x] 1.2 两张真实截图测试通过（12只等权重 + 21只长图提取）✅
- [x] 1.3 净值数据交叉验证（3只基金，用户接受误差）✅
- [x] 1.4 akshare并发能力测试（10只1.87s）✅
- [x] 1.5 验证结果记录，退出条件全部通过 ✅

**Quality Gate:** PASSED

---

## Phase 2: 核心引擎抽取 (Step 1) ✅

- [x] 2.1 portfolio_clinic.py（310行，PortfolioClinic + ClinicReport dataclass）
- [x] 2.2 analyze() + 计算函数迁移到新模块
- [x] 2.3 backtest() + CSI300基准
- [x] 2.4 CSI300 获取实现
- [x] 2.5 LLM诊断摘要（独立端点异步加载）
- [x] 2.6 图片提取输出端验证
- [x] 2.7 长图自动拆分识别合并
- [x] 2.8 公式验证端点

**Quality Gate:** PASSED

---

## Phase 3: 诊断报告前端 (Step 1) ✅

- [x] 3.1 诊断报告HTML模板（健康分仪表盘 + 指标摘要 + 持仓分析）✅
- [x] 3.2 回测曲线 + 沪深300基准叠加（Plotly）✅
- [x] 3.3 方法卡（评分公式透明展示）✅
- [x] 3.4 分享URL功能（UUID v4 + 复制/删除）✅
- [x] 3.5 调仓模拟器（权重拖拽滑块 + 前端即时计算）✅
- [x] 3.6 渐进式加载（先指标数据，LLM摘要异步后到）✅
- [x] 3.7 手动编辑兜底界面（确认/修改提取结果 + 代码格式校验）✅

**Quality Gate:** 全链路端到端测试通过

---

## Phase 4: 增强与收尾 ✅

- [x] 4.1 PortfolioClinic 单元测试（17个测试全部通过）✅
- [x] 4.2 LLM摘要prompt调优 ✅
- [x] 4.3 结构化日志（log_step 函数 + 替换 print）✅
- [x] 4.4 遗留代码质量修复（bare except → 具体异常类型）✅

---

## 完成情况

| Phase | 状态 |
|-------|------|
| Phase 1 (验证) | ✅ Step 0 全部通过 |
| Phase 2 (引擎) | ✅ portfolio_clinic.py |
| Phase 3 (前端) | ✅ 诊断报告模板 |
| Phase 4 (收尾) | ✅ 测试+日志+质量修复 |
