# -*- coding: utf-8 -*-
"""
基金数据获取模块 — 直接调用天天基金/东方财富HTTP接口
替代akshare，实现更快的净值/持仓/经理/行业数据获取
"""

import warnings
warnings.filterwarnings('ignore')

import re
import json
import statistics
import subprocess
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, List, Dict, Any

# ══════════════════════════════════════════════════════════════
# HTTP 工具
# ══════════════════════════════════════════════════════════════

def _curl(url: str, headers: dict = None, timeout: int = 15) -> str:
    """curl GET请求天天基金/东方财富接口"""
    import urllib.request
    h = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer": "https://fundf10.eastmoney.com/",
    }
    if headers:
        h.update(headers)
    try:
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode('utf-8', errors='replace')
    except Exception:
        return ""


# ══════════════════════════════════════════════════════════════
# HTML 解析工具
# ══════════════════════════════════════════════════════════════

def _re_one(pattern: str, text: str, group: int = 1, default: str = "") -> str:
    m = re.search(pattern, text)
    return m.group(group).strip() if m else default


def _re_all(pattern: str, text: str) -> List[str]:
    return re.findall(pattern, text)


# ══════════════════════════════════════════════════════════════
# 行业/板块标签
# ══════════════════════════════════════════════════════════════

def _get_sector_tag(stock_name: str) -> str:
    sector_map = {
        '贵州茅台': '高端白酒 / 酱香型龙头',
        '宁德时代': '锂电池 / 全球动力电池龙头',
        '比亚迪': '新能源汽车 / 全产业链龙头',
        '美的集团': '家电消费 / 白电龙头',
        '格力电器': '家电消费 / 空调龙头',
        '中国平安': '保险 / 金融综合集团',
        '招商银行': '银行 / 零售银行龙头',
        '迈瑞医疗': '医疗器械 / 国产替代',
        '恒瑞医药': '创新药 / 医药龙头',
        '药明康德': 'CXO / 医药外包龙头',
        '海康威视': '安防监控 / AI视觉',
        '中微公司': '半导体 / CMP设备',
        '北方华创': '半导体设备 / 国产替代',
        '中际旭创': '光模块 / 数通龙头',
        '寒武纪': 'AI芯片 / 国产GPU',
        '沪电股份': 'PCB / 通信PCB龙头',
        '深南电路': 'PCB / 通信PCB龙头',
        '新易盛': '光模块 / 5G光通信',
        '天孚通信': '光模块 / 精密器件',
        '中天科技': '光纤+海缆 / 新能源布局',
        '工业富联': '电子代工 / AI服务器',
        '通富微电': '封装测试 / AMD大客户',
        '中国移动': '运营商 / 通信基础设施',
        '立讯精密': '消费电子 / 精密制造',
        '三花智控': '热管理 / 新能源车热管理',
        '德赛西威': '汽车电子 / 智能驾驶',
        '科大讯飞': 'AI / 语音识别龙头',
        '浪潮信息': '服务器 / AI算力',
        '金山办公': '软件 / 办公SaaS',
        '兆易创新': 'MCU / 存储芯片',
        '长电科技': '封装测试 / 国产替代',
        '中芯国际': '晶圆代工 / 国产替代',
        '韦尔股份': 'CIS / 图像传感器',
        '晶晨股份': 'SoC芯片 / 多媒体',
        '澜起科技': '内存接口 / 数据中心',
        '中兴通讯': '通信设备 / 5G',
        '神州泰岳': '游戏+AI / 运营服务商',
        '恺英网络': '游戏 / 网络游戏',
        '三七互娱': '游戏 / 手游研运',
        '巨人网络': '游戏 / 网络游戏',
        '昆仑万维': 'AI+游戏 / 互联网平台',
        '博创科技': '光器件 / 光通信',
        '生益科技': '覆铜板 / PCB材料',
        '华正新材': '覆铜板 / 5G材料',
        '雅克科技': '半导体材料 / 电子特气',
        '芯源微': '半导体设备 / 涂胶显影',
        '拓荆科技': '半导体薄膜 / PECVD',
        '英维克': '温控 / 数据中心温控',
        '拓普集团': '汽车零部件 / 轻量化',
        '东山精密': '精密制造 / FPC软板',
        '鹏鼎控股': 'PCB / 苹果供应链',
        '蓝思科技': '玻璃盖板 / 消费电子',
        '歌尔股份': '声学 / VR/AR',
        '京东方A': '面板 / 显示龙头',
        '舜宇光学': '光学镜头 / 汽车镜头',
        '中国中免': '免税零售 / 消费龙头',
        '东方财富': '券商 / 互联网券商',
        '同花顺': '金融科技 / 券商IT',
        '阳光电源': '光伏逆变器 / 储能',
        '隆基绿能': '光伏 / 单晶硅片龙头',
        '海大集团': '饲料 / 农业龙头',
        '牧原股份': '生猪养殖 / 龙头企业',
        '伊利股份': '乳制品 / 龙头',
        '分众传媒': '广告 / 楼宇媒体',
        '中国神华': '煤炭 / 能源龙头',
        '中国核电': '核电 / 清洁能源',
        '中国船舶': '造船 / 周期龙头',
        '中国海油': '油气 / 海洋油气',
        '华虹半导体': '晶圆代工 / 特色工艺',
        '萤石网络': '智能家居 / 安防',
        '达梦数据': '数据库 / 国产替代',
        '诺瓦星云': 'LED显示 / 控制系统',
        '德明利': '存储模组 / NAND闪存',
        '鼎泰高科': '精密制造 / PCB钻针',
        '源杰科技': '光通信 / 半导体激光器',
        '亨通光电': '光纤+海缆 / 通信',
        '长飞光纤': '光纤 / 通信线缆',
        '光库科技': '光器件 / 光通信',
    }
    for name, tag in sector_map.items():
        if name in stock_name:
            return tag
    return '其他'


# ══════════════════════════════════════════════════════════════
# 持仓解析器
# ══════════════════════════════════════════════════════════════

def _parse_holdings_html(raw: str) -> List[Dict[str, str]]:
    """解析天天基金持仓HTML，返回[{code, name, weight}]"""
    from html.parser import HTMLParser

    class Parser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.in_tbody = False
            self.in_td = False
            self.td_class = ""
            self.td_data = ""
            self.row = []
            self.stocks = []

        def handle_starttag(self, tag, attrs):
            if tag == "tbody":
                self.in_tbody = True
            elif tag == "td" and self.in_tbody:
                self.in_td = True
                self.td_class = dict(attrs).get("class", "")
                self.td_data = ""

        def handle_endtag(self, tag):
            if tag == "td" and self.in_td:
                self.in_td = False
                self.row.append((self.td_class, self.td_data.strip()))
            elif tag == "tr" and self.in_tbody:
                if len(self.row) >= 7:
                    code = self.row[1][1]
                    name = self.row[2][1]
                    weight = self.row[6][1]
                    if code.isdigit() and len(code) == 6:
                        self.stocks.append({"股票代码": code, "股票名称": name, "占净值比例": weight})
                self.row = []
            elif tag == "tbody":
                self.in_tbody = False

        def handle_data(self, data):
            if self.in_td:
                self.td_data += data

    parser = Parser()
    try:
        parser.feed(raw)
    except Exception:
        pass
    return parser.stocks


# ══════════════════════════════════════════════════════════════
# 风险指标计算
# ══════════════════════════════════════════════════════════════

def _compute_metrics_from_nav_list(navs: List[float], changes: List[float], days_count: int) -> dict:
    """根据净值列表和日涨幅列表计算风险指标"""
    if len(navs) < 30 or days_count < 30:
        return {
            'annual_return': 0.0, 'annual_volatility': 0.0,
            'sharpe_ratio': 0.0, 'calmar_ratio': 0.0, 'max_drawdown': 0.0,
        }

    # 年化收益率
    if navs[-1] > 0 and navs[0] > 0:
        total_ret = navs[-1] / navs[0] - 1
        ann_ret = ((1 + total_ret) ** (365.25 / days_count) - 1) * 100
    else:
        ann_ret = 0.0

    # 年化波动率
    if len(changes) > 1:
        std_dev = statistics.stdev(changes)
        ann_vol = std_dev * (252 ** 0.5) * 100
    else:
        ann_vol = 0.0

    # 夏普比率（无风险3%）
    risk_free = 0.03
    sharpe = (ann_ret / 100 - risk_free) / (ann_vol / 100) if ann_vol > 0 else 0.0

    # 最大回撤
    cumulative = [1.0]
    for c in changes:
        cumulative.append(cumulative[-1] * (1 + c))
    peak = cumulative[0]
    max_dd = 0.0
    for val in cumulative:
        if val > peak:
            peak = val
        dd = (peak - val) / peak
        if dd > max_dd:
            max_dd = dd
    max_dd_pct = max_dd * 100

    # 卡玛比率
    calmar = ann_ret / max_dd_pct if max_dd_pct > 0 else 0.0

    return {
        'annual_return': round(ann_ret, 2),
        'annual_volatility': round(ann_vol, 2),
        'sharpe_ratio': round(sharpe, 2),
        'calmar_ratio': round(calmar, 2),
        'max_drawdown': round(max_dd_pct, 2),
    }


# ══════════════════════════════════════════════════════════════
# 子接口（天天基金直调）
# ══════════════════════════════════════════════════════════════

def _fetch_realtime_nav(fund_code: str) -> dict:
    """
    实时估值 + 基本信息
    接口: https://fundgz.1234567.com.cn/js/{code}.js
    耗时: ~100ms
    """
    import time, urllib.request
    ts = int(time.time() * 1000)
    url = f"https://fundgz.1234567.com.cn/js/{fund_code}.js?rt={ts}"
    raw = _curl(url)
    if not raw:
        return {}

    # 解析 jsonpgz({...})
    m = re.search(r"jsonpgz\((.+)\)", raw)
    if not m:
        return {}
    try:
        data = json.loads(m.group(1))
    except Exception:
        return {}

    return {
        'fund_code': data.get('fundcode', ''),
        'fund_name': data.get('name', ''),
        'net_value': float(data.get('dwjz', 0)),
        'nav_date': data.get('jzrq', ''),
        'day_growth': float(data.get('gszzl', 0)),
        'estimated_value': float(data.get('gsz', 0)),
        'estimated_time': data.get('gztime', ''),
    }


def _fetch_fund_basic_info(fund_code: str) -> dict:
    """
    基金基本信息（经理、公司、规模、任期）
    接口:
      - https://fundf10.eastmoney.com/jbgk_{code}.html（基本信息）
      - https://fundf10.eastmoney.com/jjjl_{code}.html（经理任期）
    耗时: ~400ms
    """
    info = {}

    # 1. 基本信息页（jbgk）
    url_gk = f"https://fundf10.eastmoney.com/jbgk_{fund_code}.html"
    raw_gk = _curl(url_gk)
    if raw_gk:
        raw_clean = re.sub(r'[\r\n\t]+', '', raw_gk)

        # 基金经理
        m = re.search(r'基金经理：[^<]*<a[^>]*>([^<]+)</a>', raw_clean)
        if m:
            info['基金经理'] = m.group(1).strip()

        # 管理人
        m = re.search(r'管理人：<a[^>]*>([^<]+)</a>', raw_clean)
        if m:
            info['管理人'] = m.group(1).strip()

        # 规模
        m = re.search(r'净资产规模：.*?(\d+\.\d+)亿元.*?截止至：(.+?)）', raw_clean)
        if m:
            info['规模'] = f"{m.group(1)}亿元（截止至：{m.group(2)}）"
        else:
            m2 = re.search(r'净资产规模[^<]*?(\d+\.\d+)亿', raw_clean)
            if m2:
                info['规模'] = f"{m2.group(1)}亿元"

        # 成立日期
        m = re.search(r'成立日期/规模</th><td>([^<]+)', raw_clean)
        if m:
            info['成立日期'] = m.group(1).strip()

        # 投资风格 — 从 jbgk 页面的"投资风格"行提取
        m = re.search(r'投资风格[：:][^<]*<[^>]+>([^<]+)</', raw_clean)
        if m:
            info['投资风格'] = m.group(1).strip()
        else:
            # 尝试另一个格式
            m2 = re.search(r'投资风格[：:]\s*([^<]+?)(?:<br|<[^a]|$)', raw_clean)
            if m2:
                info['投资风格'] = m2.group(1).strip()

        # 基金类型 — 从 jbgk 页面的"基金类型"行或标题提取
        m = re.search(r'基金类型[：:]\s*([^<]+)', raw_clean)
        if m:
            info['基金类型'] = m.group(1).strip()

    # 2. 基金经理任期页（jjjl）— 单独拉取获取上任日期
    url_jl = f"https://fundf10.eastmoney.com/jjjl_{fund_code}.html"
    raw_jl = _curl(url_jl)
    if raw_jl:
        raw_jl_clean = re.sub(r'[\r\n\t]+', '', raw_jl)

        # 上任日期
        m = re.search(r'上任日期：.*?(\d{4}-\d{2}-\d{2})', raw_jl_clean)
        if m:
            info['基金经理上任日期'] = m.group(1)
            try:
                start = datetime.strptime(m.group(1), '%Y-%m-%d')
                days = (datetime.now() - start).days
                info['基金经理任职天数'] = days
                info['基金经理任职年限'] = f"{days // 365}年{days % 365}天"
            except Exception:
                pass

    return info


def _fetch_holdings(fund_code: str, year: int = None, quarter: int = None) -> dict:
    """
    前十大持仓
    接口: https://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=jjcc&code={code}&topline=10&year=&month=
    耗时: ~300ms
    """
    if year is None:
        year = ""
    if quarter is None:
        quarter = ""
    url = f"https://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=jjcc&code={fund_code}&topline=10&year={year}&month={quarter}"
    raw = _curl(url)
    if not raw:
        return {'前十大持仓': [], '持仓集中度': '0%', '报告期': ''}

    # 提取 content 字段（JSON中带转义HTML）
    m = re.search(r'content:\\?"(.*?)",arryear', raw, re.DOTALL)
    if not m:
        return {'前十大持仓': [], '持仓集中度': '0%', '报告期': ''}

    html = m.group(1)
    html = html.replace('\\"', '"').replace('\\n', '').replace('\\/', '/').replace('\\t', '')

    # 提取报告期
    date_m = re.search(r'截止至：<font[^>]*>([^<]+)</font>', html)
    report_date = date_m.group(1).strip() if date_m else ''

    # 解析持仓
    stocks = _parse_holdings_html(html)

    # 计算持仓集中度
    total = 0.0
    for s in stocks:
        try:
            total += float(s['占净值比例'].replace('%', ''))
        except (ValueError, AttributeError):
            pass

    # 添加细分行业
    for s in stocks:
        s['细分行业'] = _get_sector_tag(s.get('股票名称', ''))
        # 格式化代码
        code = s['股票代码']
        if code.startswith(('6', '5')):
            s['股票代码'] = f"sh{code}"
        else:
            s['股票代码'] = f"sz{code}"

    return {
        '前十大持仓': stocks,
        '前十大持仓_raw': stocks,
        '持仓集中度': f'{total:.2f}%' if total > 0 else '0%',
        '报告期': report_date,
    }


# ══════════════════════════════════════════════════════════════
# 历史净值（东方财富直调）
# ══════════════════════════════════════════════════════════════

def _fetch_nav_history_via_http(fund_code: str, years: int = 3) -> dict:
    """
    历史净值（东方财富 push2his API）
    接口: https://push2his.eastmoney.com/api/qt/stock/lszc/get
    耗时: ~200ms
    返回: {nav_df, metrics}
    """
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=years * 365)).strftime("%Y%m%d")

    # 东方财富 secid: 1=上交所, 0=深交所
    secid = f"1.{fund_code}"
    url = (
        f"https://push2his.eastmoney.com/api/qt/stock/lszc/get"
        f"?fields1=f1,f2,f3,f4,f5,f6"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
        f"&ut=7eea3edcaed734bea9cbfc24409ed989"
        f"&klt=01&fqt=1&secid={secid}"
        f"&beg={start}&end={end}&smplmt=460&lmt=1000000"
    )
    raw = _curl(url)
    if not raw:
        return _fetch_nav_history_fallback(fund_code, years)

    try:
        data = json.loads(raw)
        klines = data.get('data', {}).get('lszc', [])
        if not klines:
            return _fetch_nav_history_fallback(fund_code, years)
    except Exception:
        return _fetch_nav_history_fallback(fund_code, years)

    # 解析净值列表
    dates, navs, changes = [], [], []
    for item in klines:
        date_str = item.get('f51', '')
        if not date_str:
            continue
        try:
            nav = float(item.get('f53', 0))
            change = float(item.get('f54', 0))
        except (ValueError, TypeError):
            continue
        dates.append(date_str)
        navs.append(nav)
        changes.append(change / 100)  # 转为小数

    if len(navs) < 30:
        return _fetch_nav_history_fallback(fund_code, years)

    # 构建 DataFrame（供 crawl_fund_nav_df 等调用方使用）
    import pandas as _pd_nav
    nav_df = _pd_nav.DataFrame({'净值日期': dates, '单位净值': navs, '日增长率': [c * 100 for c in changes]})

    # 计算指标
    metrics = _compute_metrics_from_nav_list(navs, changes, len(navs))
    metrics['nav_date'] = dates[-1] if dates else ''
    metrics['net_value'] = navs[-1] if navs else 0
    metrics['day_growth'] = changes[-1] * 100 if changes else 0
    metrics['fund_name'] = ''
    metrics['nav_df'] = nav_df

    return metrics


def _fetch_nav_history_fallback(fund_code: str, years: int = 3) -> dict:
    """备用方案：通过天天基金API获取普通基金净值历史"""
    import urllib.request

    # 方案A: 天天基金API (适用于普通开放式基金)
    try:
        all_records = []
        page = 1
        max_pages = (years * 250) // 20 + 2
        while page <= max_pages:
            url = f"https://api.fund.eastmoney.com/f10/lsjz?fundCode={fund_code}&pageIndex={page}&pageSize=20"
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0',
                'Referer': 'https://fundf10.eastmoney.com/',
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            records = data.get('Data', {}).get('LSJZList', [])
            if not records:
                break
            for r in records:
                try:
                    nav = float(r.get('DWJZ', 0))
                    change = float(r.get('JZZZL', 0))
                    if nav > 0:
                        all_records.append({
                            '净值日期': r.get('FSRQ', ''),
                            '单位净值': nav,
                            '日增长率': change,
                        })
                except (ValueError, TypeError):
                    continue
            if len(records) < 20:
                break
            page += 1

        if len(all_records) >= 30:
            import pandas as _pd_nav
            all_records.sort(key=lambda x: x['净值日期'])
            df = _pd_nav.DataFrame(all_records)
            df = df.dropna(subset=['单位净值'])
            metrics = _compute_metrics(df)
            metrics['nav_date'] = str(all_records[-1]['净值日期'])[:10]
            metrics['net_value'] = float(all_records[-1]['单位净值'])
            metrics['day_growth'] = float(all_records[-1]['日增长率'])
            metrics['fund_name'] = ''
            metrics['nav_df'] = df
            return metrics
    except Exception as e:
        print(f"[nav_fallback] 天天基金API失败: {e}")

    # 方案B: akshare (最后手段)
    try:
        import akshare as ak
        df_nav = ak.fund_open_fund_info_em(fund_code, '单位净值走势', f'近{years}年')
        if df_nav is None or len(df_nav) == 0:
            return {}
        df_nav.columns = ['净值日期', '单位净值', '日增长率']
        df_nav = df_nav.dropna(subset=['单位净值'])
        latest = df_nav.iloc[-1]
        metrics = _compute_metrics(df_nav)
        metrics['nav_date'] = str(latest['净值日期'])[:10]
        metrics['net_value'] = float(latest['单位净值'])
        metrics['day_growth'] = float(latest['日增长率'])
        metrics['fund_name'] = ''
        metrics['nav_df'] = df_nav
        return metrics
    except Exception as e:
        print(f"[nav_fallback] akshare失败: {e}")
        return {}


def _compute_metrics(nav_df) -> dict:
    """akshare DataFrame版指标计算（兼容备用路径）"""
    import pandas as pd
    if nav_df is None or len(nav_df) < 30:
        return {
            'annual_return': 0.0, 'annual_volatility': 0.0,
            'sharpe_ratio': 0.0, 'calmar_ratio': 0.0, 'max_drawdown': 0.0,
        }
    df = nav_df.dropna(subset=['单位净值']).copy()
    if len(df) < 30:
        return {
            'annual_return': 0.0, 'annual_volatility': 0.0,
            'sharpe_ratio': 0.0, 'calmar_ratio': 0.0, 'max_drawdown': 0.0,
        }
    # 取近3年数据（数据不够则取全部）
    # datetime64[s] → int64 已是秒级时间戳，无需再 // 10**9
    cutoff = (datetime.now() - timedelta(days=3 * 365)).timestamp()
    recent = df[pd.to_datetime(df['净值日期']).astype('int64') >= cutoff]
    if len(recent) < 60:
        recent = df.tail(730) if len(df) >= 60 else df
    if len(recent) < 30:
        return {
            'annual_return': 0.0, 'annual_volatility': 0.0,
            'sharpe_ratio': 0.0, 'calmar_ratio': 0.0, 'max_drawdown': 0.0,
        }
    navs = recent['单位净值'].tolist()
    changes = []
    for pct in recent['日增长率'].tolist():
        try:
            changes.append(float(pct) / 100)
        except (TypeError, ValueError):
            changes.append(0.0)
    return _compute_metrics_from_nav_list(navs, changes, len(recent))


# ══════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════

def crawl_fund_full(fund_code: str) -> dict:
    """
    直接调天天基金/东方财富HTTP接口获取完整基金数据
    并发调用（~0.3-0.5s总耗时）：
    - 实时估值 + 基本信息: fundgz.1234567.com.cn (~100ms)
    - 持仓明细: fundf10.eastmoney.com (~300ms)
    - 历史净值: push2his.eastmoney.com (~200ms)
    """
    result = {'fund_code': fund_code}

    with ThreadPoolExecutor(max_workers=4) as executor:
        f_nav = executor.submit(_fetch_realtime_nav, fund_code)
        f_basic = executor.submit(_fetch_fund_basic_info, fund_code)
        f_hold = executor.submit(_fetch_holdings, fund_code)
        f_hist = executor.submit(_fetch_nav_history_via_http, fund_code, 1)
        f_mgr = executor.submit(crawl_manager_fund_list, fund_code)

        nav_data = f_nav.result(timeout=8)
        basic_data = f_basic.result(timeout=8)
        hold_data = f_hold.result(timeout=8)
        hist_data = f_hist.result(timeout=8)
        try:
            mgr_data = f_mgr.result(timeout=15)
        except Exception:
            mgr_data = None

    # 实时净值
    result.update(nav_data)

    # 基本信息（经理/公司/规模）
    result['基金经理'] = basic_data.get('基金经理', '')
    result['基金经理公司'] = basic_data.get('管理人', '')
    result['管理规模'] = basic_data.get('规模', '')
    result['成立日期'] = basic_data.get('成立日期', '')
    result['基金经理任职年限'] = basic_data.get('基金经理任职年限', '')

    # 持仓
    result['前十大持仓_raw'] = hold_data.get('前十大持仓', [])
    result['前十大持仓'] = hold_data.get('前十大持仓', [])
    result['持仓集中度'] = hold_data.get('持仓集中度', '0%')
    result['报告期'] = hold_data.get('报告期', '')

    # 行业配置（简化：用第一大持仓行业）
    top_stocks = hold_data.get('前十大持仓', [])
    if top_stocks:
        result['第一大行业'] = top_stocks[0].get('细分行业', '其他')
        try:
            result['行业占比'] = top_stocks[0].get('占净值比例', '0%')
        except Exception:
            result['行业占比'] = '0%'
    else:
        result['第一大行业'] = '其他'
        result['行业占比'] = '0%'

    # 基金风格 — 从天天基金基本信息页抓取
    result['基金风格'] = basic_data.get('投资风格', '')
    result['风格描述'] = basic_data.get('基金类型', '')

    # 历史净值 + 风险指标
    nav_df = hist_data.get('nav_df')
    if nav_df is not None and len(nav_df) > 30:
        metrics = _compute_metrics(nav_df)
        result['annual_return'] = metrics.get('annual_return', 0.0)
        result['annual_volatility'] = metrics.get('annual_volatility', 0.0)
        result['sharpe_ratio'] = metrics.get('sharpe_ratio', 0.0)
        result['calmar_ratio'] = metrics.get('calmar_ratio', 0.0)
        result['max_drawdown'] = metrics.get('max_drawdown', 0.0)

    # 合并经理详情（从 crawl_manager_fund_list 获取）
    if mgr_data:
        result['基金经理'] = result.get('基金经理') or mgr_data.get('基金经理', '')
        result['基金经理公司'] = result.get('基金经理公司') or mgr_data.get('基金经理公司', '')
        if not result.get('基金经理任职年限'):
            result['基金经理任职年限'] = mgr_data.get('基金经理任职年限', '')
        result['管理基金数量'] = mgr_data.get('管理基金数量', 0)
        result['管理基金总规模'] = mgr_data.get('管理基金规模', '')
        result['最佳回报率'] = mgr_data.get('最佳回报率', '')
        result['manager_details'] = mgr_data.get('manager_details', [])
        # 从业天数 — 从任职年限字符串解析
        tenure_str = result.get('基金经理任职年限', '')
        if tenure_str:
            import re as _re_tenure
            y = _re_tenure.search(r'(\d+)年', tenure_str)
            d = _re_tenure.search(r'(\d+)天', tenure_str)
            days = (int(y.group(1)) * 365 + int(d.group(1))) if y and d else 0
            result['从业天数'] = days
        else:
            result['从业天数'] = 0

    return result


# NAV 历史缓存（减少重复 HTTP 请求）
_nav_cache = {}  # {(fund_code, years): (timestamp, [records])}


def crawl_fund_nav_df(fund_code: str, years: int = None) -> list:
    """
    返回历史净值列表（用于图表/指标计算）
    返回: [{净值日期, 单位净值, 日增长率, 累计净值}, ...]
    """
    import time
    years = years or 3
    cache_key = (fund_code, years)
    now = time.time()

    # 估值场景缓存 5 分钟，回测场景缓存 30 分钟
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

    # fallback: 返回内存构建的列表
    return []


def crawl_fund_info(fund_code: str) -> dict:
    """基金基础信息（净值、日涨跌幅）"""
    return crawl_fund_full(fund_code)


def crawl_fund_holdings(fund_code: str) -> dict:
    """前十大持仓股票"""
    data = crawl_fund_full(fund_code)
    return {
        '前十大持仓': data.get('前十大持仓', []),
        '持仓集中度': data.get('持仓集中度', '0%'),
    }


def crawl_fund_industry(fund_code: str) -> dict:
    """行业配置"""
    data = crawl_fund_full(fund_code)
    return {
        '第一大行业': data.get('第一大行业', ''),
        '行业占比': data.get('行业占比', ''),
        '基金风格': data.get('基金风格', ''),
    }


def crawl_fund_manager(fund_code: str) -> dict:
    """基金经理信息"""
    data = crawl_fund_full(fund_code)
    return {
        '基金经理': data.get('基金经理', ''),
        '基金经理公司': data.get('基金经理公司', ''),
        '管理规模': data.get('管理规模', ''),
    }


def crawl_manager_fund_list(fund_code: str) -> dict:
    """
    抓取基金经理在管基金列表。
    使用字符串切割避免正则回溯，确保 <0.5s。
    """
    import re
    from datetime import datetime

    result = {
        '基金经理': '',
        '基金经理公司': '',
        '基金经理任职年限': '',
        '管理基金规模': '',
        '管理基金数量': 0,
        '最佳回报率': '',
        'manager_details': [],
    }

    raw = _curl(f"https://fundf10.eastmoney.com/jjjl_{fund_code}.html", timeout=5)
    if not raw:
        return result

    clean = re.sub(r'[\r\n\t]+', '', raw)

    # 用简单正则提取元数据（这些不会回溯）
    m = re.search(r'基金经理[：:]&nbsp;+<a[^>]*>([^<]+)</a>', clean)
    if m: result['基金经理'] = m.group(1).strip()

    m = re.search(r'管理人[：:]\s*<a[^>]*>([^<]+)</a>', clean)
    if m: result['基金经理公司'] = m.group(1).strip()

    m = re.search(r'上任日期[：:].*?(\d{4}-\d{2}-\d{2})', clean)
    if m:
        try:
            start = datetime.strptime(m.group(1), '%Y-%m-%d')
            d = (datetime.now() - start).days
            result['基金经理任职年限'] = f"{d // 365}年{d % 365}天"
        except Exception:
            pass

    m = re.search(r'净资产规模[：:].*?(\d+\.?\d*)\s*亿元', clean)
    if m: result['管理基金规模'] = f"{m.group(1)}亿元"

    # 提取基金列表：用 str.find 避免正则回溯
    manager_details = []
    best_return = -999.0
    search_start = 0
    marker = '历任基金一览</label>'

    while True:
        idx = clean.find(marker, search_start)
        if idx == -1:
            break
        # 找到最近的 </table> 结束标签
        table_end = clean.find('</table>', idx)
        if table_end == -1:
            break
        table_html = clean[idx:table_end]

        # 解析表格行（只匹配在当前 table 内）
        rows = re.findall(
            r'<td><a[^>]*>(\d{6})</a></td>\s*'
            r'<td[^>]*><a[^>]*>([^<]+)</a></td>\s*'
            r'<td[^>]*>[^<]*</td>\s*'   # 基金类型
            r'<td[^>]*>[^<]*</td>\s*'   # 起始时间
            r'<td[^>]*>[^<]*</td>\s*'   # 截止时间
            r'<td[^>]*>(\d+)天</td>\s*'  # 任职天数
            r'<td[^>]*>(-?[\d.]+)%?</td>',  # 任职回报
            table_html
        )

        for fcode, fname, days_str, ret_str in rows:
            try:
                d = int(days_str)
                r = float(ret_str)
                manager_details.append({
                    'fund_code': fcode,
                    'fund_name': fname.strip(),
                    'days': d,
                    'best_return': round(r, 2),
                })
                if r > best_return:
                    best_return = r
            except (ValueError, TypeError):
                continue

        search_start = table_end + 8

    result['manager_details'] = manager_details
    result['管理基金数量'] = len(manager_details)
    if best_return > -999.0:
        result['最佳回报率'] = f"{best_return:.2f}%"

    # fallback: jbgk 补充
    if not result['基金经理'] or not result['基金经理公司'] or not result['管理基金规模']:
        raw_gk = _curl(f"https://fundf10.eastmoney.com/jbgk_{fund_code}.html", timeout=5)
        if raw_gk:
            gk = re.sub(r'[\r\n\t]+', '', raw_gk)
            if not result['基金经理']:
                m = re.search(r'基金经理[：:]\s*<a[^>]*>([^<]+)</a>', gk)
                if m: result['基金经理'] = m.group(1).strip()
            if not result['基金经理公司']:
                m = re.search(r'管理人[：:]\s*<a[^>]*>([^<]+)</a>', gk)
                if m: result['基金经理公司'] = m.group(1).strip()
            if not result['管理基金规模']:
                m = re.search(r'净资产规模[：:].*?(\d+\.?\d*)\s*亿元', gk)
                if m: result['管理基金规模'] = f"{m.group(1)}亿元"

    return result
