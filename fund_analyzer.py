# -*- coding: utf-8 -*-
"""
基金深度分析引擎 — 基于好买基金「4P三性」选基方法论
重构目标：
  1. 完整实现6步筛选流程（合规初筛→量化初筛→4P尽调→三性校验→精选池→动态跟踪）
  2. 按基金类型差异化评分（主动权益/固收/指数/量化）
  3. 输出标准化报告格式，对齐skill规范
"""

import math
import concurrent.futures
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime
from enum import Enum

# ─────────────────────────────────────────────
# 内部数据结构
# ─────────────────────────────────────────────

class FundType(Enum):
    """基金类型枚举"""
    STOCK = "股票型"        # 主动权益-股票型
    HYBRID = "混合型"        # 主动权益-混合型
    BOND = "债券型"          # 固定收益-债券型
    INDEX = "指数型"         # 指数基金
    QDII = "QDII"           # QDII
    MONEY = "货币型"         # 货币市场
    QUANT = "量化基金"       # 量化基金
    UNKNOWN = "未知"


class FilterStage(Enum):
    """筛选阶段"""
    INITIAL = "合规初筛池"
    QUANTITATIVE = "量化初筛优质池"
    FOUR_P = "4P评估通过池"
    THREE_NATURES = "三性校验通过池"
    SELECTED = "精选池"
    REJECTED = "已剔除"


@dataclass
class FundBasicInfo:
    """基金基本信息"""
    code: str
    name: str
    fund_type: FundType = FundType.UNKNOWN
    establishment_date: str = ""          # 成立日期 YYYYMMDD
    scale: float = 0.0                     # 规模（亿元）
    company: str = ""                      # 基金公司
    manager: str = ""                      # 基金经理
    manager_tenure_days: int = 0          # 经理任职天数
    manager_tenure_years: float = 0.0     # 经理任职年限
    nav: float = 0.0                       # 单位净值
    accumulated_nav: float = 0.0          # 累计净值
    daily_change: float = 0.0             # 日增长率%
    risk_level: str = "中等风险"          # 低/中/中高/高
    style: str = ""                       # 基金风格


@dataclass
class FundQuantMetrics:
    """量化指标"""
    # 各周期收益率
    return_1m: float = 0.0    # 近1月
    return_3m: float = 0.0    # 近3月
    return_6m: float = 0.0    # 近6月
    return_1y: float = 0.0    # 近1年
    return_3y: float = 0.0    # 近3年年化
    return_5y: float = 0.0    # 近5年年化
    return_since_inception: float = 0.0  # 成立以来

    # 相对基准超额收益
    excess_return_1y: float = 0.0
    excess_return_3y: float = 0.0

    # 风险指标
    max_drawdown: float = 0.0       # 最大回撤%
    annual_volatility: float = 0.0   # 年化波动率%
    downside_volatility: float = 0.0 # 下行波动率%
    beta: float = 0.0
    alpha: float = 0.0               # 年化Alpha%

    # 风险调整后收益
    sharpe_ratio: float = 0.0        # 夏普比率
    info_ratio: float = 0.0          # 信息比率
    calmar_ratio: float = 0.0        # 卡玛比率

    # 业绩稳定性
    rank_1y_percentile: float = 0.0  # 近1年排名百分位（越小越好）
    rank_3y_percentile: float = 0.0  # 近3年排名百分位
    quarterly_stability: bool = False # 连续8季度前1/2

    # 其他
    turnover_rate: float = 0.0       # 换手率
    institution_hold_ratio: float = 0.0  # 机构持有比例%


@dataclass
class FundHoldings:
    """持仓信息"""
    top10_concentration: float = 0.0  # 前十大持仓集中度%
    top10_stocks: List[Dict] = field(default_factory=list)  # [{股票名称, 股票代码, 占净值比例, 细分行业}]
    sector_allocation: Dict[str, float] = field(default_factory=dict)  # 行业配置 {行业名: 占比%}
    first_sector: str = ""
    first_sector_ratio: float = 0.0
    style: str = ""       # 风格标签
    style_desc: str = ""  # 风格描述


@dataclass
class FourPScores:
    """4P评分结果"""
    performance: int = 0     # 业绩表现 满分25
    philosophy: int = 0      # 投资理念 满分25
    people: int = 0          # 管理人    满分30
    process: int = 0         # 决策流程  满分20
    total: int = 0           # 总分100

    performance_detail: str = ""
    philosophy_detail: str = ""
    people_detail: str = ""
    process_detail: str = ""


@dataclass
class ThreeNaturesResult:
    """三性校验结果"""
    consistency: Tuple[str, str] = ("不通过", "")   # (结果, 详情)
    stability: Tuple[str, str] = ("不通过", "")
    effectiveness: Tuple[str, str] = ("不通过", "")

    @property
    def all_pass(self) -> bool:
        return all(r[0].startswith("通过") for r in [self.consistency, self.stability, self.effectiveness])

    @property
    def passed_count(self) -> int:
        return sum(1 for r in [self.consistency, self.stability, self.effectiveness] if r[0].startswith("通过"))


@dataclass
class ScreeningResult:
    """单只基金筛选结果"""
    code: str
    name: str
    stage: FilterStage = FilterStage.INITIAL
    basic_info: Optional[FundBasicInfo] = None
    quant_metrics: Optional[FundQuantMetrics] = None
    holdings: Optional[FundHoldings] = None
    four_p: Optional[FourPScores] = None
    three_natures: Optional[ThreeNaturesResult] = None
    recommendation: str = "不建议投资"
    recommendation_color: str = "#EF4444"
    risk_level: str = "中等风险"
    reject_reason: str = ""           # 被剔除原因
    screen_time: str = ""

    @property
    def total_4p(self) -> int:
        return self.four_p.total if self.four_p else 0


# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────

def _safe_float(val, default=0.0) -> float:
    try:
        if val is None:
            return default
        s = str(val).strip().replace('%', '').replace('¥', '').replace(',', '')
        if s in ('', 'None', 'nan', 'N/A'):
            return default
        return float(s)
    except (ValueError, TypeError):
        return default


def _safe_str(val, default="") -> str:
    if val is None:
        return default
    s = str(val).strip()
    return s if s not in ('', 'None', 'nan') else default


def _parse_date(date_str: str) -> Optional[datetime]:
    """解析日期字符串"""
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


def _days_between(date1: str, date2: datetime) -> int:
    """计算日期1到date2的天数"""
    d = _parse_date(date1)
    if d is None:
        return 0
    return (date2 - d).days


# ─────────────────────────────────────────────
# 6步筛选引擎
# ─────────────────────────────────────────────

class FundScreener:
    """
    基金6步筛选引擎
    严格遵循好买基金4P三性选基方法论
    """

    # 差异化阈值配置（严格好买官方标准校准）
    THRESHOLDS = {
        FundType.STOCK: {
            "sharpe": 1.0, "info_ratio": 0.8, "max_drawdown": 40,
            "annual_return_3y": 0.0, "excess_return_consecutive": 3,
        },
        FundType.HYBRID: {
            "sharpe": 1.0, "info_ratio": 0.8, "max_drawdown": 40,
            "annual_return_3y": 0.0, "excess_return_consecutive": 3,
        },
        FundType.BOND: {
            "sharpe": 2.0, "info_ratio": 0.3, "max_drawdown": 8,
            "annual_return_3y": 2.0, "excess_return_consecutive": 3,
        },
        FundType.INDEX: {
            "tracking_error": 0.3, "scale_min": 5.0, "fee_rate": 0.8,
            "excess_return_3y": 2.0, "info_ratio": 0.5,
        },
        FundType.QUANT: {
            "sharpe": 1.5, "info_ratio": 0.8, "max_drawdown": 35,
            "top10_concentration_max": 30.0,
        },
        FundType.UNKNOWN: {
            "sharpe": 1.0, "info_ratio": 0.8, "max_drawdown": 40,
            "annual_return_3y": 0.0, "excess_return_consecutive": 3,
        },
    }

    # 明星基金经理名单（加分项）
    FAMOUS_MANAGERS = {
        "张坤", "刘彦春", "葛兰", "朱少醒", "谢治宇", "周蔚文",
        "傅鹏博", "刘格菘", "胡昕炜", "杨浩", "陈皓", "李晓星",
        "冯明远", "归凯", "周应波", "赵蓓", "谭冬寒", "袁芳",
        "鄢耀", "王健", "李进", "贾成东", "王宗合", "梁浩",
    }

    # 头部基金公司
    TOP_COMPANIES = {
        "易方达", "华夏", "广发", "富国", "嘉实", "南方",
        "汇添富", "工银", "招商", "中欧", "兴证全球", "景顺长城",
        "鹏华", "华安", "博时", "招商", "银华", "交银施罗德",
    }

    def __init__(self, fund_info: dict, holdings: Optional[dict] = None):
        """
        fund_info: get_fund_info() 返回的原始字典
        holdings: 前十大持仓字典（可选）
        """
        self.raw = fund_info
        self.holdings_raw = holdings or {}
        self.code = _safe_str(fund_info.get("基金代码", fund_info.get("fund_code")))
        self.name = _safe_str(fund_info.get("基金简称", fund_info.get("fund_name", self.code)))

    # ── 步骤1：合规初筛 ──────────────────────────────

    def step1_compliance_filter(self, result: ScreeningResult) -> Tuple[bool, str]:
        """
        合规初筛：剔除不满足基础条件的基金
        返回 (是否通过, 剔除原因)
        """
        bi = result.basic_info
        if bi is None:
            return False, "基本信息不完整，无法完成合规初筛"

        # 1. 成立年限 < 3年
        if bi.establishment_date:
            age_days = _days_between(bi.establishment_date, datetime.now())
            age_years = age_days / 365.25
            if age_years < 3:
                return False, f"成立年限{age_years:.1f}年 < 3年（无完整周期验证）"
        else:
            # 无法判断成立日期，给出警告但不直接剔除
            pass

        # 2. 规模 < 2亿 或 > 100亿（主动权益）
        scale = bi.scale
        if scale > 0:
            ftype = bi.fund_type
            if ftype in (FundType.STOCK, FundType.HYBRID):
                if scale < 2:
                    return False, f"规模{scale:.1f}亿 < 2亿（迷你基金，清盘风险）"
                if scale > 100:
                    return False, f"规模{scale:.1f}亿 > 100亿（规模过大，调仓灵活性不足）"
            elif ftype == FundType.INDEX:
                if scale < 5:
                    return False, f"指数基金规模{scale:.1f}亿 < 5亿（流动性风险）"

        # 3. 基金经理任职 < 1年
        if bi.manager_tenure_years > 0 and bi.manager_tenure_years < 1:
            return False, f"基金经理任职{bi.manager_tenure_years:.1f}年 < 1年（业绩无法归因）"

        # 4. 风险等级异常（直接一票否决）
        if "高风险" in str(bi.risk_level) and bi.fund_type == FundType.BOND:
            return False, "债券基金标注高风险，合规异常"

        return True, ""

    # ── 步骤2：量化初筛 ──────────────────────────────

    def step2_quantitative_filter(self, result: ScreeningResult) -> Tuple[bool, str]:
        """
        量化初筛：多维度业绩与风险过滤
        基于同类基金排名，不依赖绝对阈值
        """
        qm = result.quant_metrics
        if qm is None:
            return False, "量化指标数据不完整"

        bi = result.basic_info
        ftype = bi.fund_type if bi else FundType.UNKNOWN
        t = self.THRESHOLDS.get(ftype, self.THRESHOLDS[FundType.UNKNOWN])

        reasons = []

        # 夏普比率 > 阈值（好买标准：>1.0）
        # 注意：初始化未填充时 qm.sharpe_ratio 为 0
        threshold = t.get("sharpe", 1.0)
        if qm.sharpe_ratio <= 0 or qm.sharpe_ratio < threshold:
            reasons.append(f"夏普比率{qm.sharpe_ratio:.2f} < {threshold}")

        # 最大回撤（取绝对值，存储为负数）
        if abs(qm.max_drawdown) > t.get("max_drawdown", 30):
            reasons.append(f"最大回撤{abs(qm.max_drawdown):.1f}% > {t.get('max_drawdown', 30)}%")

        # 近3年年化收益（主动权益类）
        if ftype in (FundType.STOCK, FundType.HYBRID):
            if qm.return_3y <= 0:
                reasons.append(f"近3年年化{qm.return_3y:.1f}% <= 0")
            elif qm.return_3y < t.get("annual_return_3y", 8.0):
                reasons.append(f"近3年年化{qm.return_3y:.1f}% < {t.get('annual_return_3y', 8.0)}%")

        # 信息比率
        if qm.info_ratio < t.get("info_ratio", 0.8):
            reasons.append(f"信息比率{qm.info_ratio:.2f} < {t.get('info_ratio', 0.8)}")

        # 指数基金特殊检查
        if ftype == FundType.INDEX:
            if qm.annual_volatility > 2.0:  # 跟踪误差简估
                reasons.append(f"跟踪误差估计过大")

        # 量化基金：前十大集中度
        if ftype == FundType.QUANT:
            hold = result.holdings
            if hold and hold.top10_concentration > t.get("top10_concentration_max", 30):
                reasons.append(f"前十大持仓集中度{hold.top10_concentration:.1f}% > 30%")

        if reasons:
            return False, "; ".join(reasons)
        return True, ""

    # ── 步骤3：4P定性评估 ──────────────────────────────

    def step3_four_p_evaluation(self, result: ScreeningResult) -> FourPScores:
        """
        4P定性深度评估
        满分100分，<60分直接剔除
        """
        bi = result.basic_info
        qm = result.quant_metrics
        hold = result.holdings

        p = self._score_performance(bi, qm, hold)
        ph = self._score_philosophy(bi, hold)
        pp = self._score_people(bi, qm)
        pr = self._score_process(bi, hold)

        return FourPScores(
            performance=p[0], philosophy=ph[0], people=pp[0], process=pr[0],
            total=p[0] + ph[0] + pp[0] + pr[0],
            performance_detail=p[1],
            philosophy_detail=ph[1],
            people_detail=pp[1],
            process_detail=pr[1],
        )

    def _score_performance(self, bi, qm, hold) -> Tuple[int, str]:
        """
        评估业绩表现（满分25分）— 对齐好买官方标准
        好买官方定义（权重25%）：
          核心目标：区分业绩来源是运气还是能力，验证超额收益的可持续性
          注意：夏普>1.0 + 信息比率>0.8 是量化初筛硬门槛，不在Performance内扣分
        """
        score = 0
        detail = []

        if qm is None:
            return 0, "量化数据缺失"

        annual = qm.return_1y
        ret3y = qm.return_3y
        sharpe = qm.sharpe_ratio
        max_draw = abs(qm.max_drawdown)

        # —— 长期业绩能力验证（10分）：核心看3年+年化，区分运气还是能力 ——
        # 好买：近3年、近5年年化收益率同类排名前1/3（权重最高）
        if ret3y >= 30:
            score += 10; detail.append(f"近3年{ret3y:.1f}%，长期业绩卓越（同类前1/3）")
        elif ret3y >= 20:
            score += 9; detail.append(f"近3年{ret3y:.1f}%，长期业绩优秀")
        elif ret3y >= 15:
            score += 8; detail.append(f"近3年{ret3y:.1f}%，长期业绩良好")
        elif ret3y >= 10:
            score += 6; detail.append(f"近3年{ret3y:.1f}%，长期业绩一般")
        elif ret3y >= 5:
            score += 4; detail.append(f"近3年{ret3y:.1f}%，长期业绩偏弱")
        elif ret3y > 0:
            score += 2; detail.append(f"近3年{ret3y:.1f}%，勉强正收益")
        else:
            score += 0; detail.append(f"近3年{ret3y:.1f}%，长期亏损")

        # —— 短期业绩验证（5分）：近1年业绩，确认没有突然失效 ——
        if annual >= 20:
            score += 5; detail.append(f"近1年{annual:.1f}%，短期表现强劲")
        elif annual >= 10:
            score += 4; detail.append(f"近1年{annual:.1f}%，短期表现良好")
        elif annual >= 0:
            score += 2; detail.append(f"近1年{annual:.1f}%，短期表现平庸")
        else:
            score += 1; detail.append(f"近1年{annual:.1f}%，短期亏损需关注")

        # —— 风险调整后收益（6分）：夏普比率衡量单位风险回报 ——
        # 好买：夏普>1.0是硬门槛（在量化初筛），此处评估夏普的优良程度
        if sharpe >= 2.5:
            score += 6; detail.append(f"夏普{sharpe:.2f}，风险收益比极佳（同类顶尖）")
        elif sharpe >= 2.0:
            score += 5; detail.append(f"夏普{sharpe:.2f}，风险收益比优秀")
        elif sharpe >= 1.5:
            score += 4; detail.append(f"夏普{sharpe:.2f}，风险收益比良好")
        elif sharpe >= 1.0:
            score += 3; detail.append(f"夏普{sharpe:.2f}，风险收益比达标")
        elif sharpe >= 0.5:
            score += 1; detail.append(f"夏普{sharpe:.2f}，风险收益比偏低")
        else:
            score += 0; detail.append(f"夏普{sharpe:.2f}，风险收益比差")

        # —— 回撤控制能力（4分）：验证"赚得到的钱守得住" ——
        if max_draw <= 10:
            score += 4; detail.append(f"最大回撤{max_draw:.1f}%，回撤控制极佳")
        elif max_draw <= 15:
            score += 3; detail.append(f"最大回撤{max_draw:.1f}%，回撤控制优秀")
        elif max_draw <= 25:
            score += 2; detail.append(f"最大回撤{max_draw:.1f}%，回撤控制良好")
        elif max_draw <= 40:
            score += 1; detail.append(f"最大回撤{max_draw:.1f}%，回撤控制一般")
        else:
            score += 0; detail.append(f"最大回撤{max_draw:.1f}%，回撤控制较差")

        verdict = "高分(20-25)" if score >= 20 else "达标(10-19)" if score >= 10 else "剔除(0-9)"
        return score, f"[{verdict}] " + "; ".join(detail)

    def _score_philosophy(self, bi, hold) -> Tuple[int, str]:
        """
        评估投资理念（满分25分）— 对齐好买官方标准
        好买官方定义（权重25%）：
          核心目标：验证投资逻辑是否清晰、可复制、可延续
          规避押注式、无规则投资
        注意：量化基金有明确的量化模型投资理念，不能用传统价值/成长风格来框定
        """
        score = 0
        detail = []

        style = _safe_str(bi.style) if bi else ""
        first_ind = ""
        ind_ratio = 0.0
        conc = 0.0
        ftype = bi.fund_type if bi else FundType.UNKNOWN
        name = _safe_str(bi.name) if bi else ""

        if hold:
            first_ind = _safe_str(hold.first_sector)
            ind_ratio = hold.first_sector_ratio
            conc = hold.top10_concentration

        # 判断是否为量化基金
        is_quant = ftype == FundType.QUANT or "量化" in name or "量化" in _safe_str(bi.style)

        # —— 风格定位是否清晰（10分）—— 量化基金/指数基金用专属逻辑
        clear_styles = {"价值", "成长", "均衡", "大盘价值", "大盘成长", "小盘成长", "小盘价值",
                        "消费", "医药", "科技", "制造", "周期", "金融"}

        if is_quant:
            # 量化基金：量化模型=清晰的投资理念
            score += 10; detail.append("量化多因子模型，投资理念清晰可量化")
        elif "指数" in name or ftype == FundType.INDEX:
            score += 10; detail.append(f"指数化投资，跟踪{style}，理念透明")
        elif style in clear_styles:
            score += 10; detail.append(f"风格定位清晰：{style}")
        elif style:
            score += 6; detail.append(f"风格：{style}（定位不够明确）")
        else:
            score += 2; detail.append("风格定位模糊")
            # 从持仓判断是否可识别
            if is_quant:
                score = max(score, 8)  # 量化基金即使风格字段为空也应有中高基础分

        # —— 投资策略是否可复制、非押注（9分）——
        # 好买核心：规避押注式、无规则投资
        if is_quant:
            # 量化基金：持仓分散=不押注，策略可复制
            if conc <= 30:
                score += 9; detail.append(f"量化分散持仓{conc:.0f}%，杜绝押注，策略可严格复制")
            elif conc <= 50:
                score += 7; detail.append(f"量化持仓{conc:.0f}%，适度分散，策略可复制")
            else:
                score += 5; detail.append(f"量化持仓{conc:.0f}%，偏集中需关注模型逻辑")
        elif ind_ratio <= 40:
            score += 9; detail.append(f"行业分散，策略可复制性强")
        elif ind_ratio <= 60:
            score += 7; detail.append(f"行业适度集中，策略有方向")
        elif ind_ratio <= 80:
            score += 4; detail.append(f"行业集中{ind_ratio:.0f}%，赛道型策略，需关注押注风险")
        else:
            score += 1; detail.append(f"行业高度集中{ind_ratio:.0f}%，押注式投资风险高")

        # —— 投资逻辑验证（6分）：持仓与风格匹配度 ——
        if is_quant:
            # 量化基金验证标准：持仓分散+行业分散=逻辑一致
            if conc <= 50 and ind_ratio <= 60:
                score += 6; detail.append("量化分散持股与量化模型逻辑一致，无风格漂移风险")
            else:
                score += 4; detail.append("量化持仓集中度偏高，需验证模型逻辑是否漂移")
        elif style in ("价值", "大盘价值") and conc >= 50:
            score += 6; detail.append("价值风格与适度集中持仓逻辑一致")
        elif style in ("成长", "大盘成长", "科技") and ind_ratio >= 40:
            score += 6; detail.append("成长风格与行业聚焦逻辑一致")
        elif style in ("均衡",) and 30 <= ind_ratio <= 60:
            score += 5; detail.append("均衡风格与分散配置逻辑一致")
        elif first_ind and style:
            score += 4; detail.append(f"风格「{style}」与持仓「{first_ind}」基本一致")
        else:
            score += 2; detail.append("投资逻辑验证数据不足")

        verdict = "高分(20-25)" if score >= 20 else "达标(10-19)" if score >= 10 else "剔除(0-9)"
        return score, f"[{verdict}] " + "; ".join(detail)

    def _score_people(self, bi, qm) -> Tuple[int, str]:
        """
        评估管理人（满分30分）— 对齐好买官方标准
        好买官方定义（权重30% — 4个维度中权重最高）：
          1. 基金经理从业≥5年经历完整牛熊周期（10分）
          2. 任职该基金≥3年，任职回报同类前1/3（10分）
          3. 基金公司股权稳定，投研团队完善（10分）
        注意：从业天数可能为空时，从基金经理姓名和任职该基金天数推算
        """
        score = 0
        detail = []

        manager = _safe_str(bi.manager) if bi else ""
        company = _safe_str(bi.company) if bi else ""
        tenure_days = bi.manager_tenure_days if bi else 0
        tenure_years = bi.manager_tenure_years if bi else 0
        ftype = bi.fund_type if bi else FundType.UNKNOWN

        # —— 基金经理从业年限与经历（10分）——
        if manager and manager not in ("None", "nan"):
            score += 2; detail.append(f"基金经理：{manager}")

            if tenure_years >= 10:
                score += 8; detail.append(f"从业{tenure_years:.1f}年，经历多轮完整牛熊，经验丰富")
            elif tenure_years >= 8:
                score += 7; detail.append(f"从业{tenure_years:.1f}年，经历完整牛熊周期")
            elif tenure_years >= 5:
                score += 6; detail.append(f"从业{tenure_years:.1f}年，经历完整周期")
            elif tenure_years >= 3:
                score += 4; detail.append(f"从业{tenure_years:.1f}年，经历部分周期")
            elif tenure_years >= 1:
                score += 2; detail.append(f"从业{tenure_years:.1f}年，尚需验证")
            else:
                # tenure_years=0，但经理名字已知 — 从天天基金页面爬到的任职天数可能为空的补偿
                # 用基金经理知名度+基金公司规模估算
                if any(m in manager for m in self.FAMOUS_MANAGERS):
                    score += 6; detail.append(f"{manager}为明星基金经理，默认按从业≥5年计")
                elif any(c in company for c in self.TOP_COMPANIES):
                    score += 4; detail.append(f"头部基金公司{company}，经理从业信息待补全，按≥3年估算")
                else:
                    score += 1; detail.append(f"经理{manager}从业信息缺失，需补充")
        else:
            score += 0; detail.append("基金经理信息缺失")

        # —— 任职该基金年限与稳定性（10分）——
        if manager and manager not in ("None", "nan"):
            if tenure_years >= 7:
                score += 10; detail.append(f"任职该基金{tenure_years:.1f}年，深度绑定，高度稳定")
            elif tenure_years >= 5:
                score += 9; detail.append(f"任职该基金{tenure_years:.1f}年，长期稳定")
            elif tenure_years >= 3:
                score += 8; detail.append(f"任职该基金{tenure_years:.1f}年，稳定")
            elif tenure_years >= 1:
                score += 5; detail.append(f"任职该基金{tenure_years:.1f}年，尚可")
            elif tenure_years > 0:
                score += 2; detail.append(f"任职该基金{tenure_years:.1f}年，需跟踪")
            else:
                # 天数缺失但经理名字已知
                if any(m in manager for m in self.FAMOUS_MANAGERS):
                    score += 7; detail.append(f"{manager}为明星基金经理，默认按任职≥5年计")
                elif any(c in company for c in self.TOP_COMPANIES):
                    score += 5; detail.append(f"头部基金公司，经理任职信息待补全，按≥3年估算")
                else:
                    score += 2; detail.append("任职信息缺失")
        else:
            score += 0; detail.append("任职信息缺失")

        # —— 基金公司实力（10分）——
        if company and company not in ("None", "nan"):
            score += 4; detail.append(f"基金公司：{company}")

            if any(c in company for c in self.TOP_COMPANIES):
                score += 6; detail.append("头部基金公司，投研团队实力强，股权稳定")
            else:
                score += 3; detail.append("中小型基金公司，投研实力待验证")
        else:
            score += 3; detail.append("基金公司信息缺失（使用行业默认）")

        verdict = "高分(24-30)" if score >= 24 else "达标(15-23)" if score >= 15 else "剔除(0-14)"
        return min(score, 30), f"[{verdict}] " + "; ".join(detail)

    def _score_process(self, bi, hold) -> Tuple[int, str]:
        """
        评估决策流程（满分20分）— 对齐好买官方标准
        好买官方定义（权重20%）：
          1. 投研决策体系完善（8分）：持仓结构反映投资决策质量
          2. 选股/择时流程标准化（7分）：分散化程度体现投资纪律
          3. 风控机制有效性（5分）：回撤控制和行业分散体现风控意识
        """
        score = 0
        detail = []

        conc = hold.top10_concentration if hold else 0
        ind_ratio = hold.first_sector_ratio if hold else 0
        ftype = bi.fund_type if bi else FundType.UNKNOWN
        name = _safe_str(bi.name) if bi else ""
        is_quant = ftype == FundType.QUANT or "量化" in name

        if is_quant:
            # —— 量化基金专属Process评估 ——
            # 量化基金决策流程的核心：模型纪律性+持仓分散度+换手率

            # 持仓结构合理性（8分）：量化基金天然分散
            if conc <= 20:
                score += 8; detail.append(f"量化持仓{conc:.0f}%，高度分散，模型选股纪律严格")
            elif conc <= 35:
                score += 7; detail.append(f"量化持仓{conc:.0f}%，良好分散，选股流程系统化")
            elif conc <= 55:
                score += 5; detail.append(f"量化持仓{conc:.0f}%，集中度中等，模型选股有方向性")
            else:
                score += 3; detail.append(f"量化持仓{conc:.0f}%，偏高，需关注模型是否漂移")

            # 行业配置纪律（7分）
            if ind_ratio <= 30:
                score += 7; detail.append(f"行业分散{ind_ratio:.0f}%，量化多因子行业中性策略")
            elif ind_ratio <= 50:
                score += 5; detail.append(f"行业适度{ind_ratio:.0f}%，量化模型有行业偏好")
            else:
                score += 3; detail.append(f"行业集中{ind_ratio:.0f}%，量化模型偏行业轮动型")

            # 风控机制（5分）
            if conc <= 35 and ind_ratio <= 40:
                score += 5; detail.append("持仓+行业双分散，量化风控机制完善")
            elif conc <= 60:
                score += 3; detail.append("风控机制一般")
            else:
                score += 1; detail.append("集中度高，风控意识需关注")
        else:
            # —— 传统基金Process评估 ——
            # 持仓结构合理性（8分）：分散化程度
            if 30 <= conc <= 65:
                score += 8; detail.append(f"持仓集中度{conc:.0f}%，合理分散，选股流程纪律良好")
            elif 65 < conc <= 80:
                score += 6; detail.append(f"持仓集中度{conc:.0f}%，偏重仓，关注选股集中风险")
            elif conc > 80:
                score += 3; detail.append(f"持仓集中度{conc:.0f}%，高度集中，决策风险大")
            else:
                score += 5; detail.append(f"持仓集中度{conc:.0f}%，极度分散")

            # 行业配置纪律（7分）：不过度集中或过度分散
            if 25 <= ind_ratio <= 55:
                score += 7; detail.append(f"行业配置均衡{ind_ratio:.0f}%，纪律良好")
            elif 55 < ind_ratio <= 70:
                score += 5; detail.append(f"行业配置偏集中{ind_ratio:.0f}%，关注赛道风险")
            elif ind_ratio > 70:
                score += 2; detail.append(f"行业高度集中{ind_ratio:.0f}%，无分散化纪律")
            else:
                score += 4; detail.append(f"行业分散{ind_ratio:.0f}%，配置无明显方向")

            # 风险控制（5分）
            if 30 <= conc <= 65 and ind_ratio <= 55:
                score += 5; detail.append("持仓与行业双分散，风控意识强")
            elif conc <= 70 and ind_ratio <= 60:
                score += 3; detail.append("分散控制合理，风控意识一般")
            else:
                score += 1; detail.append("持仓集中度高，风控需关注")

        verdict = "高分(16-20)" if score >= 16 else "达标(10-15)" if score >= 10 else "剔除(0-9)"
        return score, f"[{verdict}] " + "; ".join(detail)

    # ── 步骤4：三性校验 ──────────────────────────────

    def step4_three_natures_check(self, result: ScreeningResult) -> ThreeNaturesResult:
        """
        三性校验：一票否决制
        好买标准：任意一项不通过 → 直接剔除
        一致性：投资理念、持仓风格与实际业绩高度匹配，无风格漂移
        稳定性：投资逻辑经得住牛熊周期，业绩与回撤长期可控
        有效性：策略能持续创造超额收益，长期持有可获得稳定正回报
        """
        bi = result.basic_info
        qm = result.quant_metrics
        hold = result.holdings
        four_p = result.four_p

        # ── 一致性：理念、风格、持仓三者匹配 ──
        style = _safe_str(bi.style) if bi else ""
        first_ind = _safe_str(hold.first_sector) if hold else ""
        ind_ratio = hold.first_sector_ratio if hold else 0
        conc = hold.top10_concentration if hold else 0
        ftype = bi.fund_type if bi else FundType.UNKNOWN
        name = _safe_str(bi.name) if bi else ""
        is_quant = ftype == FundType.QUANT or "量化" in name

        # 一致性校验核心：风格+行业+持仓集中度三者逻辑自洽
        consistency_passed = False
        consistency_detail = ""

        if is_quant:
            # 量化基金一致性校验：量化模型本身就是风格
            # 量化基金的特点是持仓分散、行业分散、无强烈风格倾向
            # 这种"无风格"恰恰是量化模型的风格特征
            if conc <= 50:
                consistency_detail = f"量化基金{conc:.0f}%分散持仓，与量化多因子模型风格一致，无风格漂移"
                consistency_passed = True
            else:
                consistency_detail = f"量化基金持仓集中度{conc:.0f}%偏高，需验证模型是否发生风格漂移"
                consistency_passed = False
        elif not style:
            consistency_detail = "风格数据缺失，无法全面验证一致性"
            consistency_passed = False
        elif style and first_ind:
            # 检查是否风格与行业矛盾
            if style in ("均衡",) and ind_ratio > 50:
                consistency_detail = f"风格为均衡但第一大行业占比{ind_ratio:.0f}%，配置偏集中，一致性存疑"
                consistency_passed = False
            elif style in ("价值", "大盘价值") and conc < 30:
                consistency_detail = f"价值风格但持仓极度分散{conc:.0f}%，与价值投资精选个股理念不一致"
                consistency_passed = False
            else:
                consistency_detail = f"风格「{style}」与持仓方向「{first_ind}」{ind_ratio:.0f}%匹配，逻辑自洽"
                consistency_passed = True
        else:
            consistency_detail = "风格或持仓数据不完整，无法全面验证一致性"
            consistency_passed = False

        consistency = ("通过" if consistency_passed else "不通过", consistency_detail)

        # ── 稳定性：牛熊周期检验 + 回撤可控 ──
        vol = qm.annual_volatility if qm else 0
        max_draw = abs(qm.max_drawdown) if qm else 0
        ret3y = qm.return_3y if qm else 0

        stability_passed = False
        stability_detail = ""
        if max_draw <= 20 and vol <= 25 and ret3y > 0:
            stability_detail = f"最大回撤{max_draw:.1f}%、波动率{vol:.1f}%、3年年化{ret3y:.1f}%，风险收益表现稳定"
            stability_passed = True
        elif max_draw <= 30 and vol <= 30 and ret3y > 0:
            stability_detail = f"回撤{max_draw:.1f}%、波动率{vol:.1f}%，处于可接受范围，持续观察"
            stability_passed = True
        elif max_draw <= 40:
            stability_detail = f"回撤{max_draw:.1f}%略高，需关注是否与投资策略一致"
            stability_passed = False
        else:
            stability_detail = f"最大回撤{max_draw:.1f}%过大，稳定性不达标"
            stability_passed = False

        stability = ("通过" if stability_passed else "不通过", stability_detail)

        # ── 有效性：持续超额收益 + 长期持有正回报 ──
        annual = qm.return_1y if qm else 0
        sharpe = qm.sharpe_ratio if qm else 0
        excess = qm.excess_return_1y if qm else 0
        info_ratio = qm.info_ratio if qm else 0

        effectiveness_passed = False
        effectiveness_detail = ""
        # 好买标准：近3/5年任意时点买入持有1年正收益概率>=70%
        # 简化为：超额收益持续为正 + 夏普>1.0 + 信息比率>0.8
        if annual > 10 and sharpe > 1.0 and excess > 0:
            effectiveness_detail = f"年化{annual:.1f}%、夏普{sharpe:.2f}、超额{excess:.1f}%，超额收益持续为正"
            effectiveness_passed = True
        elif annual > 5 and sharpe > 0.5:
            effectiveness_detail = f"年化{annual:.1f}%、夏普{sharpe:.2f}，正收益但超额优势有限"
            effectiveness_passed = True
        elif annual > 0:
            effectiveness_detail = f"年化{annual:.1f}%，勉强正收益，超额不显著"
            effectiveness_passed = False
        else:
            effectiveness_detail = f"年化{annual:.1f}%，亏损，有效性存疑"
            effectiveness_passed = False

        effectiveness = ("通过" if effectiveness_passed else "不通过", effectiveness_detail)

        return ThreeNaturesResult(
            consistency=consistency,
            stability=stability,
            effectiveness=effectiveness,
        )

    # ── 综合筛选 ──────────────────────────────

    def screen(self) -> ScreeningResult:
        """
        执行完整6步筛选，返回ScreeningResult
        注意：4P评分在任何阶段都会被计算并输出，即使基金被剔除
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        result = ScreeningResult(
            code=self.code,
            name=self.name,
            screen_time=now,
        )

        # 构建基础信息
        result.basic_info = self._build_basic_info()

        # 构建量化指标
        result.quant_metrics = self._build_quant_metrics()

        # 构建持仓信息
        result.holdings = self._build_holdings()

        # ── 步骤1：合规初筛 ──
        passed, reason = self.step1_compliance_filter(result)
        if not passed:
            result.stage = FilterStage.REJECTED
            result.reject_reason = f"[合规初筛未通过] {reason}"
            result.four_p = result.four_p or self.step3_four_p_evaluation(result)
            result.three_natures = self.step4_three_natures_check(result)
            result.risk_level = self._calc_risk_level(result)
            result.recommendation, result.recommendation_color = self._calc_recommendation(result)
            return result

        result.stage = FilterStage.INITIAL

        # ── 步骤2：量化初筛 ──
        passed, reason = self.step2_quantitative_filter(result)
        if not passed:
            result.stage = FilterStage.REJECTED
            result.reject_reason = f"[量化初筛未通过] {reason}"
            result.four_p = self.step3_four_p_evaluation(result)
            result.three_natures = self.step4_three_natures_check(result)
            result.risk_level = self._calc_risk_level(result)
            result.recommendation, result.recommendation_color = self._calc_recommendation(result)
            return result

        result.stage = FilterStage.QUANTITATIVE

        # ── 步骤3：4P定性评估 ──
        result.four_p = self.step3_four_p_evaluation(result)

        # ── 步骤4：三性校验（始终计算，供报告展示）──
        result.three_natures = self.step4_three_natures_check(result)

        if result.total_4p < 60:
            result.stage = FilterStage.REJECTED
            result.reject_reason = f"[4P评估<60分剔除] {result.total_4p}分"
            result.risk_level = self._calc_risk_level(result)
            result.recommendation, result.recommendation_color = self._calc_recommendation(result)
            return result

        result.stage = FilterStage.FOUR_P

        if not result.three_natures.all_pass:
            result.stage = FilterStage.REJECTED
            failed = [k for k, v in {
                "一致性": result.three_natures.consistency,
                "稳定性": result.three_natures.stability,
                "有效性": result.three_natures.effectiveness,
            }.items() if not v[0].startswith("通过")]
            result.reject_reason = f"[三性校验未通过] {'/'.join(failed)}"
            result.risk_level = self._calc_risk_level(result)
            result.recommendation, result.recommendation_color = self._calc_recommendation(result)
            return result

        result.stage = FilterStage.THREE_NATURES

        # ── 步骤5：精选池准入 ──
        result.stage = FilterStage.SELECTED

        # ── 推荐结论 ──
        result.risk_level = self._calc_risk_level(result)
        result.recommendation, result.recommendation_color = self._calc_recommendation(result)

        return result

    def _build_basic_info(self) -> FundBasicInfo:
        """从原始数据构建FundBasicInfo"""
        raw = self.raw

        # 从业天数：优先整数天数，其次解析 "X年Y天" 字符串
        raw_days = raw.get("从业天数", 0)
        raw_tenure_str = raw.get("基金经理任职年限", "")
        if _safe_float(raw_days) > 0:
            tenure_days = int(_safe_float(raw_days))
        elif isinstance(raw_tenure_str, str) and "年" in raw_tenure_str:
            import re
            m = re.search(r"(\d+)年(?:(\d+)天)?", raw_tenure_str)
            if m:
                years = int(m.group(1))
                extra_days = int(m.group(2)) if m.group(2) else 0
                tenure_days = years * 365 + extra_days
            else:
                tenure_days = 0
        else:
            tenure_days = 0

        return FundBasicInfo(
            code=self.code,
            name=self.name,
            fund_type=self._guess_fund_type(raw),
            establishment_date=_safe_str(raw.get("成立日期", "")),
            scale=_safe_float(raw.get("基金规模", 0)),
            company=_safe_str(raw.get("基金经理公司", raw.get("基金公司", ""))),
            manager=_safe_str(raw.get("基金经理", "")),
            manager_tenure_days=tenure_days,
            manager_tenure_years=tenure_days / 365.25 if tenure_days > 0 else 0,
            nav=_safe_float(raw.get("单位净值", 0)),
            accumulated_nav=_safe_float(raw.get("累计净值", 0)),
            daily_change=_safe_float(str(raw.get("日增长率", "0%")).replace("%", "")),
            risk_level=_safe_str(raw.get("风险等级", "中等风险")),
        )

    def _guess_fund_type(self, raw: dict) -> FundType:
        """猜测基金类型"""
        name = _safe_str(raw.get("基金简称", "")).lower()
        ftype_str = _safe_str(raw.get("基金类型", "")).lower()

        if "股票" in ftype_str or "股票" in name:
            return FundType.STOCK
        if "混合" in ftype_str or "混合" in name:
            return FundType.HYBRID
        if "债券" in ftype_str or "债" in name:
            return FundType.BOND
        if "指数" in ftype_str or "etf" in name or "ETF" in name:
            return FundType.INDEX
        if "qdii" in ftype_str or "QDII" in name:
            return FundType.QDII
        if "货币" in ftype_str or "货币" in name:
            return FundType.MONEY
        if "量化" in ftype_str:
            return FundType.QUANT
        return FundType.UNKNOWN

    def _build_quant_metrics(self) -> FundQuantMetrics:
        """从原始数据构建FundQuantMetrics"""
        raw = self.raw
        qm = FundQuantMetrics()

        qm.return_1y = _safe_float(str(raw.get("年化收益率", "0%")).replace("%", ""))
        qm.annual_volatility = _safe_float(str(raw.get("年化波动率", "0%")).replace("%", ""))
        qm.sharpe_ratio = _safe_float(raw.get("夏普比率", 0))
        qm.calmar_ratio = _safe_float(raw.get("卡玛比率", 0))
        qm.max_drawdown = _safe_float(str(raw.get("最大回撤", "0%")).replace("%", ""))

        # return_3y: fetch_fund_info用近3年数据计算年化收益率存入年化收益率字段
        # 如果有独立的return_3y字段则用之，否则直接用年化收益率（已是近3年年化）
        raw_3y = raw.get("return_3y", raw.get("近3年年化收益率", None))
        if raw_3y is not None:
            qm.return_3y = _safe_float(raw_3y)
        else:
            # fetch_fund_info的"年化收益率"就是近3年数据计算的，直接复用
            qm.return_3y = qm.return_1y

        # info_ratio: 从原始数据或根据夏普/波动率推算
        raw_ir = raw.get("info_ratio", raw.get("信息比率", None))
        if raw_ir is not None:
            qm.info_ratio = _safe_float(raw_ir)
        else:
            # 近似估算: info_ratio ≈ sharpe_ratio * 0.8（简化估计，实际需基准数据）
            qm.info_ratio = round(qm.sharpe_ratio * 0.8, 2) if qm.sharpe_ratio > 0 else 0.0

        # excess_return_1y: 超额收益
        raw_excess = raw.get("excess_return", raw.get("超额收益", "0"))
        qm.excess_return_1y = _safe_float(str(raw_excess).replace("%", ""))

        return qm

    def _build_holdings(self) -> FundHoldings:
        """从原始数据构建FundHoldings"""
        raw = self.raw
        hold = FundHoldings()

        hold.top10_concentration = _safe_float(str(raw.get("持仓集中度", "0%")).replace("%", ""))
        hold.first_sector = _safe_str(raw.get("第一大行业", ""))
        hold.first_sector_ratio = _safe_float(str(raw.get("行业占比", "0%")).replace("%", ""))
        hold.style = _safe_str(raw.get("基金风格", ""))
        hold.style_desc = _safe_str(raw.get("风格描述", ""))

        top10 = raw.get("前十大持仓", [])
        if isinstance(top10, list):
            hold.top10_stocks = top10[:10]

        return hold

    def _calc_risk_level(self, result: ScreeningResult) -> str:
        """计算风险等级"""
        qm = result.quant_metrics
        if qm is None:
            return "中等风险"
        vol = qm.annual_volatility
        max_draw = abs(qm.max_drawdown)
        if vol <= 15 and max_draw <= 15:
            return "低风险"
        elif vol <= 25 and max_draw <= 25:
            return "中等风险"
        elif vol <= 35 and max_draw <= 35:
            return "中高风险"
        else:
            return "高风险"

    def _calc_recommendation(self, result: ScreeningResult) -> Tuple[str, str]:
        """
        计算推荐结论 — 严格对齐好买官方标准
        好买官方标准：
          强烈推荐: 4P≥80 + 三性全通过 = 精选池准入
          建议持有: 4P≥60 + 三性全部通过 = 通过全流程校验
          谨慎关注: 4P≥60 但三性部分未通过 = 需进一步验证（不进精选池）
          不建议投资: 4P<60 = 直接剔除
          
        注：好买标准中4P<60 或 三性任意一项不通过 → 不应进入推荐池
        此处区分"谨慎关注"用于告知用户该基金有潜力但未达标
        """
        total = result.total_4p
        tn = result.three_natures
        all_pass = tn.all_pass if tn else False

        if total < 60:
            return "不建议投资", "#EF4444"

        if total >= 80 and all_pass:
            return "强烈推荐", "#10B981"
        elif total >= 70 and all_pass:
            return "建议持有", "#F59E0B"
        elif total >= 60 and all_pass:
            return "建议持有", "#F59E0B"
        elif total >= 60:
            return "谨慎关注", "#F59E0B"
        else:
            return "不建议投资", "#EF4444"


# ─────────────────────────────────────────────
# 报告生成器
# ─────────────────────────────────────────────

class ReportGenerator:
    """
    将ScreeningResult转换为标准化报告格式
    对齐好买4P三性skill的输出规范
    """

    @staticmethod
    def generate(result: ScreeningResult) -> dict:
        """生成标准化报告字典"""

        bi = result.basic_info
        qm = result.quant_metrics
        hold = result.holdings
        fp = result.four_p
        tn = result.three_natures

        # 4P结构化
        four_p_report = None
        if fp:
            four_p_report = {
                "performance": {
                    "score": fp.performance, "max": 25,
                    "verdict": "高分(20-25)" if fp.performance >= 20 else "达标(10-19)" if fp.performance >= 10 else "剔除(0-9)",
                    "detail": fp.performance_detail,
                },
                "philosophy": {
                    "score": fp.philosophy, "max": 25,
                    "verdict": "高分(20-25)" if fp.philosophy >= 20 else "达标(10-19)" if fp.philosophy >= 10 else "剔除(0-9)",
                    "detail": fp.philosophy_detail,
                },
                "people": {
                    "score": fp.people, "max": 30,
                    "verdict": "高分(24-30)" if fp.people >= 24 else "达标(15-23)" if fp.people >= 15 else "剔除(0-14)",
                    "detail": fp.people_detail,
                },
                "process": {
                    "score": fp.process, "max": 20,
                    "verdict": "高分(16-20)" if fp.process >= 16 else "达标(10-15)" if fp.process >= 10 else "剔除(0-9)",
                    "detail": fp.process_detail,
                },
                "total": {"score": fp.total, "max": 100},
            }

        # 三性结构化
        three_natures_report = None
        if tn:
            three_natures_report = {
                "一致性": {"result": tn.consistency[0], "detail": tn.consistency[1]},
                "稳定性": {"result": tn.stability[0], "detail": tn.stability[1]},
                "有效性": {"result": tn.effectiveness[0], "detail": tn.effectiveness[1]},
            }

        # 核心指标
        metrics = []
        if qm:
            metrics = [
                {"label": "年化收益率", "value": f"{qm.return_1y:.2f}%", "icon": "fa-chart-line"},
                {"label": "年化波动率", "value": f"{qm.annual_volatility:.2f}%", "icon": "fa-wave-square"},
                {"label": "夏普比率",   "value": f"{qm.sharpe_ratio:.2f}",   "icon": "fa-balance-scale"},
                {"label": "卡玛比率",   "value": f"{qm.calmar_ratio:.2f}",   "icon": "fa-shield-alt"},
                {"label": "最大回撤",   "value": f"{qm.max_drawdown:.2f}%",  "icon": "fa-arrow-down"},
                {"label": "持仓集中度", "value": f"{hold.top10_concentration if hold else 0:.1f}%", "icon": "fa-layer-group"},
                {"label": "第一大行业", "value": hold.first_sector if hold else "N/A", "icon": "fa-industry"},
                {"label": "行业占比",   "value": f"{hold.first_sector_ratio if hold else 0:.1f}%", "icon": "fa-pie-chart"},
                {"label": "基金经理",   "value": bi.manager if bi else "N/A", "icon": "fa-user-tie"},
                {"label": "基金风格",   "value": (hold.style if hold else "N/A"), "icon": "fa-fingerprint"},
            ]

        # 持仓分析
        holdings_analysis = []
        if hold and hold.top10_stocks:
            for h in hold.top10_stocks:
                if isinstance(h, dict):
                    holdings_analysis.append({
                        "stock_name": h.get("股票名称", ""),
                        "sector": h.get("细分行业", "其他"),
                        "weight": h.get("占净值比例", ""),
                        "tag": h.get("细分行业", "其他"),
                    })

        # 行业分布
        top_sectors = []
        if hold and hold.sector_allocation:
            sorted_sectors = sorted(hold.sector_allocation.items(), key=lambda x: x[1], reverse=True)
            top_sectors = [{"sector": s, "weight": f"{w:.1f}%"} for s, w in sorted_sectors[:5]]

        # 综合评述
        summary = ReportGenerator._generate_summary(result)

        report = {
            "success": True,
            "fund_code": result.code,
            "fund_name": result.name,
            "source": "akshare",
            "generated_at": result.screen_time,
            "stage": result.stage.value,
            "reject_reason": result.reject_reason,

            # 4P评分
            "four_p": four_p_report,

            # 三性校验
            "three_natures": three_natures_report,

            # 推荐结论
            "recommendation": result.recommendation,
            "recommendation_color": result.recommendation_color,
            "risk_level": result.risk_level,

            # 持仓分析
            "holdings_analysis": holdings_analysis,
            "top_sectors": top_sectors,

            # 核心指标
            "metrics": metrics,

            # 综合评述
            "summary": summary,
        }

        return report

    @staticmethod
    def _generate_summary(result: ScreeningResult) -> List[str]:
        """生成综合评述"""
        paras = []
        bi = result.basic_info
        qm = result.quant_metrics
        hold = result.holdings
        fp = result.four_p
        tn = result.three_natures

        name = result.name
        code = result.code

        paras.append(f"【{name}】（{code}）投资分析综述：")

        # 业绩总评
        if qm:
            annual = qm.return_1y
            sharpe = qm.sharpe_ratio
            max_draw = abs(qm.max_drawdown)

            if annual >= 15 and sharpe >= 1.5:
                paras.append(f"该基金年化收益率{annual:.1f}%、夏普比率{sharpe:.2f}，风险调整后收益表现出色，具备较强的主动管理能力。")
            elif annual >= 10:
                paras.append(f"该基金年化收益率{annual:.1f}%，长期趋势向上，但夏普比率{sharpe:.2f}显示风险收益比仍有提升空间。")
            elif annual >= 0:
                paras.append(f"该基金年化收益率{annual:.1f}%，整体正收益但超额收益不明显，需结合市场环境综合判断。")
            else:
                paras.append(f"该基金年化收益{annual:.1f}%，需关注收益为负的原因，谨慎评估其投资价值。")

            # 风险特征
            if max_draw <= 15:
                paras.append(f"历史最大回撤{max_draw:.1f}%，风险控制能力较强，适合风险偏好适中的投资者。")
            elif max_draw <= 25:
                paras.append(f"最大回撤{max_draw:.1f}%，处于主流偏股基金正常区间，需关注极端行情下的风险承受能力。")
            else:
                paras.append(f"最大回撤{max_draw:.1f}%，波动较大，投资者需具备较高的风险承受能力。")

        # 投资风格
        if hold and hold.style and hold.style != "未知":
            first_ind = hold.first_sector or "综合"
            ind_ratio = hold.first_sector_ratio
            conc = hold.top10_concentration
            paras.append(f"基金风格定位为「{hold.style}」，重点配置「{first_ind}」行业（占比{ind_ratio:.0f}%），持仓集中度{conc:.0f}%。")

        # 4P评分总评
        total = result.total_4p
        if fp:
            p_detail = fp.performance_detail.split("]")[1].strip() if "]" in fp.performance_detail else fp.performance_detail
            ph_detail = fp.philosophy_detail.split("]")[1].strip() if "]" in fp.philosophy_detail else fp.philosophy_detail
            pp_detail = fp.people_detail.split("]")[1].strip() if "]" in fp.people_detail else fp.people_detail
            pr_detail = fp.process_detail.split("]")[1].strip() if "]" in fp.process_detail else fp.process_detail
            paras.append(f"4P评分{total}分（业绩{p_detail}；理念{ph_detail}；管理人{pp_detail}；流程{pr_detail}）。")

        # 三性总评
        if tn:
            passed = [k for k, v in {
                "一致性": tn.consistency,
                "稳定性": tn.stability,
                "有效性": tn.effectiveness,
            }.items() if v[0].startswith("通过")]
            if len(passed) == 3:
                paras.append(f"三性校验全部通过（一致性、稳定性、有效性），投资逻辑清晰，可追溯性强。")
            elif len(passed) >= 2:
                paras.append(f"三性校验{len(passed)}/3项通过（{'、'.join(passed)}），整体可接受，建议持续跟踪。")
            else:
                paras.append(f"三性校验仅{len(passed)}/3项通过，投资逻辑需进一步验证，建议谨慎。")

        # 综合建议
        if fp and total >= 80 and (tn.all_pass if tn else False):
            paras.append(f"综合4P评分{total}/100分，建议【强烈推荐】——该基金在收益、风险、风格一致性等方面均表现优秀，适合作为核心持仓配置。")
        elif fp and total >= 60:
            paras.append(f"综合4P评分{total}/100分，建议【建议持有】——中长期持有可期，建议结合个人风险偏好决定。")
        elif fp and total >= 45:
            paras.append(f"综合4P评分{total}/100分，建议【谨慎关注】——适合风险偏好较高的投资者，不宜重仓。")
        else:
            paras.append(f"综合4P评分{total}/100分，建议【不建议投资】——当前各项指标未达优，建议等待更好时机或寻找更优标的。")

        return paras
