# -*- coding: utf-8 -*-
"""4P三性分析报告 Blueprint"""
import json
import threading
import sched
import time as time_module
import sqlite3
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify

from fund_analyzer import FundScreener, ReportGenerator

# ── 从 app.py 导入共享工具 ──
from app import (
    get_cache, set_cache, delete_cache, generate_cache_key,
    CACHE_CONFIG, memory_cache, REDIS_AVAILABLE, r,
    SQLITE_DB_PATH, REPORT_GENERATING, TOP_FUNDS_WARMUP_COUNT,
    ANALYSIS_HISTORY_WEEKS, get_mysql_pool
)
from routes_fund import fetch_fund_info, get_fund_info_from_db

analysis_bp = Blueprint('analysis', __name__)


# ══════════════════════════════════════════════════════════════
# 4P 评分函数
# ══════════════════════════════════════════════════════════════


def _score_performance(info: dict) -> tuple:
    """
    评估业绩表现（满分25分）
    好买标准：同类排名前1/3 + 夏普>1.0 + 信息比率>0.8 + 超额连续为正
    """
    score = 0
    detail = []
    annual = info.get('年化收益率', '0%')
    num = float(str(annual).replace('%', '').replace('nan', '0') or 0)
    sharpe = float(str(info.get('夏普比率', '0')).replace('nan', '0') or 0)
    info_ratio = float(str(info.get('信息比率', '0')).replace('nan', '0') or 0)
    max_draw = abs(float(str(info.get('最大回撤', '0%')).replace('%', '').replace('nan', '0') or 0))
    excess_return = float(str(info.get('超额收益', '0')).replace('nan', '0') or 0)

    # 硬门槛标记
    hard_fail = []
    if sharpe <= 1.0:
        hard_fail.append(f"夏普{sharpe:.2f}<1.0")
    if info_ratio < 0.8 and info_ratio > 0:
        hard_fail.append(f"信息比率{info_ratio:.2f}<0.8")

    # 近1年收益率（8分）
    if num >= 20:   score += 8; detail.append(f'近1年{num:.1f}%，同类前部')
    elif num >= 10: score += 6; detail.append(f'近1年{num:.1f}%，同类中部')
    elif num >= 0:  score += 4; detail.append(f'近1年{num:.1f}%，同类偏后')
    else:           score += 1; detail.append(f'近1年{num:.1f}%，亏损')

    # 夏普比率（6分）
    if sharpe >= 2.0:     score += 6; detail.append(f'夏普{sharpe:.2f}，极佳')
    elif sharpe >= 1.2:   score += 5; detail.append(f'夏普{sharpe:.2f}，优秀')
    elif sharpe >= 1.0:   score += 3; detail.append(f'夏普{sharpe:.2f}，达标')
    else:                 score += 1; detail.append(f'夏普{sharpe:.2f}，未达1.0门槛')

    # 信息比率（5分）
    if info_ratio >= 1.5:     score += 5; detail.append(f'信息比率{info_ratio:.2f}，超额稳定')
    elif info_ratio >= 1.0:   score += 4; detail.append(f'信息比率{info_ratio:.2f}，良好')
    elif info_ratio >= 0.8:   score += 2; detail.append(f'信息比率{info_ratio:.2f}，达标')
    else:                     score += 0; detail.append(f'信息比率{info_ratio:.2f}<0.8未达标')

    # 最大回撤（3分）
    if max_draw <= 15:    score += 3; detail.append(f'最大回撤{max_draw:.1f}%，优秀')
    elif max_draw <= 25:  score += 2; detail.append(f'最大回撤{max_draw:.1f}%，良好')
    elif max_draw <= 40:  score += 1; detail.append(f'最大回撤{max_draw:.1f}%，一般')
    else:                 score += 0; detail.append(f'最大回撤{max_draw:.1f}%，较大')

    # 超额收益持续性（3分）
    if excess_return > 10:
        score += 3; detail.append(f'超额{excess_return:.1f}%，显著')
    elif excess_return > 5:
        score += 2; detail.append(f'超额{excess_return:.1f}%，良好')
    elif excess_return > 0:
        score += 1; detail.append(f'超额{excess_return:.1f}%，正超额')
    else:
        score += 0; detail.append(f'超额{excess_return:.1f}%，无超额')

    verdict = '高分(20-25)' if score >= 20 else '达标(10-19)' if score >= 10 else '剔除(0-9)'
    prefix = f'[{verdict}] '
    if hard_fail:
        prefix += '⚠️硬门槛: ' + '; '.join(hard_fail) + ' | '
    return score, verdict, prefix + '; '.join(detail)


def _score_philosophy(info: dict, holdings: list) -> tuple:
    """
    评估投资理念（满分25分）
    好买标准：理念清晰自洽 + 风格定位明确 + 策略可复制 + 非押注式投资
    """
    score = 0
    detail = []
    style = info.get('基金风格', '')
    first_ind = info.get('第一大行业', '')
    ind_ratio = float(str(info.get('行业占比', '0%')).replace('%', '') or 0)
    conc = float(str(info.get('持仓集中度', '0%')).replace('%', '') or 0)

    # 风格定位清晰（10分）
    clear_styles = {'价值', '成长', '均衡', '大盘价值', '大盘成长', '小盘成长', '小盘价值',
                    '消费', '医药', '科技', '制造', '周期', '金融'}
    if style in clear_styles:
        score += 10; detail.append(f'风格定位清晰：{style}')
    elif style:
        score += 6; detail.append(f'风格：{style}（定位不够明确）')
    else:
        score += 2; detail.append('风格定位模糊')

    # 策略可复制、非押注（8分）
    if ind_ratio <= 40:
        score += 8; detail.append('行业分散，策略可复制性强')
    elif ind_ratio <= 60:
        score += 6; detail.append('行业适度集中，策略有方向')
    elif ind_ratio <= 80:
        score += 4; detail.append(f'行业集中{ind_ratio:.0f}%，赛道型策略')
    else:
        score += 1; detail.append(f'行业高度集中{ind_ratio:.0f}%，押注式风险高')

    # 投资逻辑验证（7分）：持仓与风格匹配
    if style in ('价值', '大盘价值') and conc >= 50:
        score += 7; detail.append('价值风格与适度集中持仓逻辑一致')
    elif style in ('成长', '大盘成长', '科技') and ind_ratio >= 40:
        score += 7; detail.append('成长风格与行业聚焦逻辑一致')
    elif style in ('均衡',) and 30 <= ind_ratio <= 60:
        score += 6; detail.append('均衡风格与分散配置逻辑一致')
    elif first_ind and style:
        score += 4; detail.append(f'风格「{style}」与持仓「{first_ind}」基本一致')
    else:
        score += 2; detail.append('投资逻辑验证数据不足')

    verdict = '高分(20-25)' if score >= 20 else '达标(10-19)' if score >= 10 else '剔除(0-9)'
    return score, verdict, '; '.join(detail)


def _score_people(info: dict) -> tuple:
    """
    评估管理人（满分30分）
    好买标准：从业>=5年经历牛熊（10分）+ 任职该基金>=3年（10分）+ 基金公司实力（10分）
    """
    score = 0
    detail = []
    manager = info.get('基金经理', '')
    company = info.get('基金公司', '')
    tenure = float(str(info.get('从业年限', '0')).replace('年', '').replace('又', '.').replace('天', '0').replace('nan', '0') or 0)

    # 基金经理从业年限（10分）
    if manager and manager not in ('', 'None', 'nan'):
        score += 2; detail.append(f'基金经理：{manager}')
        if tenure >= 8:
            score += 8; detail.append(f'从业{tenure:.1f}年，经历多轮牛熊')
        elif tenure >= 5:
            score += 6; detail.append(f'从业{tenure:.1f}年，经历完整周期')
        elif tenure >= 3:
            score += 4; detail.append(f'从业{tenure:.1f}年，经历部分周期')
        elif tenure >= 1:
            score += 2; detail.append(f'从业{tenure:.1f}年，尚需验证')
        else:
            score += 0; detail.append(f'从业{tenure:.1f}年，经验不足')
    else:
        score += 0; detail.append('基金经理信息缺失')

    # 任职该基金稳定性（10分）
    if manager and manager not in ('', 'None', 'nan'):
        if tenure >= 5:
            score += 10; detail.append(f'任职该基金{tenure:.1f}年，深度绑定')
        elif tenure >= 3:
            score += 8; detail.append(f'任职该基金{tenure:.1f}年，稳定')
        elif tenure >= 1:
            score += 5; detail.append(f'任职该基金{tenure:.1f}年，尚可')
        else:
            score += 1; detail.append(f'任职该基金{tenure:.1f}年，需跟踪')
    else:
        score += 0; detail.append('任职信息缺失')

    # 基金公司实力（10分）
    if company and company not in ('', 'None', 'nan'):
        score += 4; detail.append(f'基金公司：{company}')
        top_companies = ['易方达','华夏','广发','富国','嘉实','南方','汇添富','工银','招商','中欧','兴证全球','景顺长城','鹏华','华安','博时','银华','交银施罗德']
        if any(c in company for c in top_companies):
            score += 6; detail.append('头部基金公司，投研团队实力强')
        else:
            score += 3; detail.append('中小型基金公司，投研实力待验证')
    else:
        score += 3; detail.append('基金公司信息缺失（使用行业默认）')

    verdict = '高分(24-30)' if score >= 24 else '达标(15-23)' if score >= 15 else '剔除(0-14)'
    return score, verdict, '; '.join(detail)


def _score_process(info: dict, holdings: list) -> tuple:
    """
    评估决策流程（满分20分）
    好买标准：投研决策体系完善（8分）+ 选股/择时流程标准化（6分）+ 风控机制（6分）
    """
    score = 0
    detail = []
    conc = float(str(info.get('持仓集中度', '0%')).replace('%', '') or 0)
    ind_ratio = float(str(info.get('行业占比', '0%')).replace('%', '') or 0)

    # 持仓结构合理性（8分）
    if 30 <= conc <= 65:
        score += 8; detail.append(f'持仓集中度{conc:.0f}%，合理分散')
    elif 65 < conc <= 80:
        score += 6; detail.append(f'持仓集中度{conc:.0f}%，偏重仓')
    elif conc > 80:
        score += 3; detail.append(f'持仓集中度{conc:.0f}%，高度集中风险大')
    else:
        score += 5; detail.append(f'持仓集中度{conc:.0f}%，极度分散')

    # 行业配置纪律（7分）
    if 25 <= ind_ratio <= 55:
        score += 7; detail.append(f'行业配置均衡{ind_ratio:.0f}%，纪律良好')
    elif 55 < ind_ratio <= 70:
        score += 5; detail.append(f'行业配置偏集中{ind_ratio:.0f}%，关注赛道风险')
    elif ind_ratio > 70:
        score += 2; detail.append(f'行业高度集中{ind_ratio:.0f}%，无分散化纪律')
    else:
        score += 4; detail.append(f'行业分散{ind_ratio:.0f}%，配置无明显方向')

    # 风险控制（5分）
    if 30 <= conc <= 65 and ind_ratio <= 55:
        score += 5; detail.append('持仓与行业双分散，风控意识强')
    elif conc <= 70 and ind_ratio <= 60:
        score += 3; detail.append('分散控制合理，风控意识一般')
    else:
        score += 1; detail.append('持仓集中度高，风控需关注')

    verdict = '高分(16-20)' if score >= 16 else '达标(10-15)' if score >= 10 else '剔除(0-9)'
    return score, verdict, '; '.join(detail)


def _three_natures_check(info: dict, holdings: list) -> dict:
    """
    三性校验（一票否决制）
    好买标准：一致性（理念风格持仓匹配）+ 稳定性（牛熊周期回撤可控）+ 有效性（持续超额收益）
    任意一项不通过 -> 直接剔除
    """
    results = {}
    style = info.get('基金风格', '')
    first_ind = info.get('第一大行业', '')
    ind_ratio = float(str(info.get('行业占比', '0%')).replace('%', '') or 0)
    conc = float(str(info.get('持仓集中度', '0%')).replace('%', '') or 0)

    # -- 一致性 --
    if style and first_ind:
        if style in ('均衡',) and ind_ratio > 50:
            results['一致性'] = {'result': '不通过', 'detail': f'风格为均衡但第一大行业{ind_ratio:.0f}%，配置偏集中'}
        elif style in ('价值', '大盘价值') and conc < 30:
            results['一致性'] = {'result': '不通过', 'detail': f'价值风格但持仓极度分散{conc:.0f}%，与价值投资理念不一致'}
        else:
            results['一致性'] = {'result': '通过', 'detail': f'风格「{style}」与持仓「{first_ind}」{ind_ratio:.0f}%匹配，逻辑自洽'}
    else:
        results['一致性'] = {'result': '不通过', 'detail': '风格或持仓数据不完整，无法验证一致性'}

    # -- 稳定性 --
    vol = float(str(info.get('年化波动率', '0%')).replace('%', '') or 0)
    max_draw = abs(float(str(info.get('最大回撤', '0%')).replace('%', '') or 0))
    if max_draw <= 20 and vol <= 25:
        results['稳定性'] = {'result': '通过', 'detail': f'最大回撤{max_draw:.1f}%、波动率{vol:.1f}%，风险可控'}
    elif max_draw <= 30 and vol <= 30:
        results['稳定性'] = {'result': '通过', 'detail': f'回撤{max_draw:.1f}%、波动率{vol:.1f}%，处于合理范围'}
    elif max_draw <= 40:
        results['稳定性'] = {'result': '不通过', 'detail': f'回撤{max_draw:.1f}%略高，需关注'}
    else:
        results['稳定性'] = {'result': '不通过', 'detail': f'回撤{max_draw:.1f}%过大，稳定性不达标'}

    # -- 有效性 --
    annual = float(str(info.get('年化收益率', '0%')).replace('%', '') or 0)
    sharpe = float(str(info.get('夏普比率', '0')) or 0)
    excess_return = float(str(info.get('超额收益', '0')).replace('nan', '0') or 0)
    if annual > 10 and sharpe > 1.0 and excess_return > 0:
        results['有效性'] = {'result': '通过', 'detail': f'年化{annual:.1f}%、夏普{sharpe:.2f}、超额{excess_return:.1f}%，超额收益持续为正'}
    elif annual > 5 and sharpe > 0.5:
        results['有效性'] = {'result': '通过', 'detail': f'年化{annual:.1f}%、夏普{sharpe:.2f}，正收益'}
    elif annual > 0:
        results['有效性'] = {'result': '不通过', 'detail': f'年化{annual:.1f}%，勉强正收益，超额不显著'}
    else:
        results['有效性'] = {'result': '不通过', 'detail': f'年化{annual:.1f}%，亏损，有效性存疑'}

    return results


# ══════════════════════════════════════════════════════════════
# 方案B+C：分析报告历史库（MySQL持久化 + 热点基金预热）
# ══════════════════════════════════════════════════════════════

def _init_analysis_history_table():
    """创建分析报告历史表（仅MySQL可用时执行）"""
    pool = get_mysql_pool()
    if pool is None:
        return False
    try:
        conn = pool.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS fund_analysis_history (
                id INT AUTO_INCREMENT PRIMARY KEY,
                fund_code VARCHAR(10) NOT NULL,
                week_number INT NOT NULL,          -- 周数编号（如202601）
                report_data LONGTEXT NOT NULL,     -- JSON序列化报告
                generated_at DATETIME NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uk_fund_week (fund_code, week_number)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        conn.commit()
        cursor.close()
        conn.close()
        print("[history] fund_analysis_history 表就绪")
        return True
    except Exception as e:
        print(f"[history] 建表失败: {e}")
        return False


def _get_latest_week_number():
    """获取当前周数编号（如202617 = 2026年第17周）"""
    now = datetime.now()
    year, week, _ = now.isocalendar()
    return year * 100 + week


def _save_report_to_mysql(fund_code: str, report: dict, week_number: int):
    """保存分析报告到MySQL历史库（Upsert）"""
    pool = get_mysql_pool()
    if pool is None:
        return
    try:
        conn = pool.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO fund_analysis_history (fund_code, week_number, report_data, generated_at)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE report_data = VALUES(report_data), generated_at = VALUES(generated_at)
        """, (fund_code, week_number, json.dumps(report, ensure_ascii=False), datetime.now()))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"[history] 保存报告失败 {fund_code}: {e}")


def _get_report_from_mysql(fund_code: str, week_number: int = None) -> dict:
    """从MySQL历史库读取指定周的报告，week_number为None时读最新周"""
    pool = get_mysql_pool()
    if pool is None:
        return None
    try:
        conn = pool.get_connection()
        cursor = conn.cursor()
        if week_number is None:
            cursor.execute("""
                SELECT report_data, week_number, generated_at FROM fund_analysis_history
                WHERE fund_code = %s ORDER BY week_number DESC LIMIT 1
            """, (fund_code,))
        else:
            cursor.execute("""
                SELECT report_data, week_number, generated_at FROM fund_analysis_history
                WHERE fund_code = %s AND week_number = %s LIMIT 1
            """, (fund_code, week_number))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if row:
            report = json.loads(row['report_data'])
            report['history_week'] = row['week_number']
            report['history_generated_at'] = str(row['generated_at'])
            report['source'] = 'mysql_history'
            return report
        return None
    except Exception as e:
        print(f"[history] 读取报告失败 {fund_code}: {e}")
        return None


def _warmup_top_funds_report():
    """方案B：启动时批量预热Top30热点基金分析报告（后台异步）"""
    def _do_warmup():
        try:
            conn = sqlite3.connect(SQLITE_DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT code FROM fund_list_cache LIMIT ?", (TOP_FUNDS_WARMUP_COUNT,))
            codes = [r[0] for r in cursor.fetchall()]
            conn.close()
            if not codes:
                print("[warmup] 无热点基金数据，跳过")
                return
            print(f"[warmup] 开始预热 {len(codes)} 只基金分析报告...")
            for code in codes:
                try:
                    info_cache_key = generate_cache_key(CACHE_CONFIG['fund_info']['prefix'], code)
                    info = get_cache(info_cache_key)
                    source = 'redis_cache'
                    if not info:
                        info = fetch_fund_info(code)
                        source = 'crawler'
                    if not info:
                        info = get_fund_info_from_db(code)
                        source = 'mysql_database'
                    if not info:
                        continue
                    screener = FundScreener(fund_info=info, holdings={"前十大持仓": info.get("前十大持仓", [])})
                    result = screener.screen()
                    report = ReportGenerator.generate(result)
                    report["source"] = source + "_warmup"
                    report_cache_key = generate_cache_key(CACHE_CONFIG['fund_analysis_report']['prefix'], code)
                    set_cache(report_cache_key, report, CACHE_CONFIG['fund_analysis_report']['expiry'])
                    _save_report_to_mysql(code, report, _get_latest_week_number())
                    print(f"[warmup] {code} 预热完成")
                except Exception as e:
                    print(f"[warmup] {code} 预热失败: {e}")
            print("[warmup] 热点基金预热全部完成")
        except Exception as e:
            print(f"[warmup] 预热流程异常: {e}")

    t = threading.Thread(target=_do_warmup, daemon=True)
    t.start()


def _schedule_weekly_refresh():
    """每周日凌晨2点自动刷新历史报告库"""
    scheduler = sched.scheduler(time_module.time, time_module.sleep)

    def _next_sunday_2am():
        now = datetime.now()
        days_until_sunday = (6 - now.weekday()) % 7
        if days_until_sunday == 0 and now.hour >= 2:
            days_until_sunday = 7
        next_sunday = now + timedelta(days=days_until_sunday)
        next_sunday_2am = next_sunday.replace(hour=2, minute=0, second=0, microsecond=0)
        return next_sunday_2am.timestamp()

    def _run_and_reschedule():
        print("[scheduler] 触发每周历史报告刷新...")
        _weekly_refresh_history_reports()
        delay = _next_sunday_2am() - time_module.time()
        if delay > 0:
            scheduler.enter(delay, 0, _run_and_reschedule)

    delay = _next_sunday_2am() - time_module.time()
    if delay > 0:
        scheduler.enter(delay, 0, _run_and_reschedule)
        t = threading.Thread(target=scheduler.run, daemon=True)
        t.start()
        print(f"[scheduler] 每周刷新已调度，距下次执行 {delay/3600:.1f} 小时")


def _weekly_refresh_history_reports():
    """方案C：每周定时刷新MySQL历史报告库（增量更新）"""
    def _do_refresh():
        try:
            conn = sqlite3.connect(SQLITE_DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT code FROM fund_list_cache LIMIT ?", (TOP_FUNDS_WARMUP_COUNT,))
            codes = [r[0] for r in cursor.fetchall()]
            conn.close()
            week = _get_latest_week_number()
            count = 0
            for code in codes:
                try:
                    info_cache_key = generate_cache_key(CACHE_CONFIG['fund_info']['prefix'], code)
                    info = get_cache(info_cache_key)
                    if not info:
                        info = get_fund_info_from_db(code)
                    if not info:
                        continue
                    screener = FundScreener(fund_info=info, holdings={"前十大持仓": info.get("前十大持仓", [])})
                    result = screener.screen()
                    report = ReportGenerator.generate(result)
                    report["source"] = 'weekly_refresh'
                    _save_report_to_mysql(code, report, week)
                    report_cache_key = generate_cache_key(CACHE_CONFIG['fund_analysis_report']['prefix'], code)
                    set_cache(report_cache_key, report, CACHE_CONFIG['fund_analysis_report']['expiry'])
                    count += 1
                except Exception as e:
                    print(f"[weekly_refresh] {code} 刷新失败: {e}")
            print(f"[weekly_refresh] 周{week}历史报告刷新完成，共{count}只")
        except Exception as e:
            print(f"[weekly_refresh] 刷新流程异常: {e}")

    t = threading.Thread(target=_do_refresh, daemon=True)
    t.start()


def _generate_report_async(fund_code: str, info: dict, source: str):
    """后台异步生成并缓存分析报告（冷启动降级路径）"""
    global REPORT_GENERATING
    try:
        screener = FundScreener(fund_info=info, holdings={"前十大持仓": info.get("前十大持仓", [])})
        result = screener.screen()
        report = ReportGenerator.generate(result)
        report["source"] = source
        report["cached"] = False
        report["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        report_cache_key = generate_cache_key(CACHE_CONFIG['fund_analysis_report']['prefix'], fund_code)
        set_cache(report_cache_key, report, CACHE_CONFIG['fund_analysis_report']['expiry'])
        threading.Thread(
            target=_save_report_to_mysql,
            args=(fund_code, report, _get_latest_week_number()),
            daemon=True
        ).start()
        print(f"[report_async] {fund_code} 报告生成完成（{source}）")
    except Exception as e:
        print(f"[report_async] {fund_code} 生成失败: {e}")
    finally:
        REPORT_GENERATING.pop(fund_code, None)


# ══════════════════════════════════════════════════════════════
# API 路由
# ══════════════════════════════════════════════════════════════


@analysis_bp.route('/api/fund/analysis_report', methods=['GET'])
def get_analysis_report():
    """生成基金投资分析报告（v4 -- 冷启动立即返回，后台异步生成）

    数据获取优先级：
      1. 内存/Redis缓存（最快，TTL 1小时）
      2. MySQL历史库（持久化，最新周数据）
      3. 后台异步生成（info缓存/DB -> FundScreener，客户端轮询）
    """
    t0 = time_module.time()
    fund_code = request.args.get('fund_code', '').strip()
    if not fund_code:
        return jsonify({'success': False, 'message': '请输入基金代码'})

    # -- Step 1：内存/Redis缓存（最快路径）--
    report_cache_key = generate_cache_key(CACHE_CONFIG['fund_analysis_report']['prefix'], fund_code)
    t1 = time_module.time()
    cached_report = get_cache(report_cache_key)
    t2 = time_module.time()
    if cached_report:
        cached_report['source'] = 'redis_cache'
        cached_report['cached'] = True
        print(f"[report timing] {fund_code} 缓存命中 total={t2-t0:.3f}s cache_get={t2-t1:.3f}s")
        return jsonify(cached_report)

    # -- Step 2：MySQL历史库（持久化，次快）--
    t3 = time_module.time()
    mysql_report = _get_report_from_mysql(fund_code)
    t4 = time_module.time()
    if mysql_report:
        mysql_report['cached'] = False
        set_cache(report_cache_key, mysql_report, CACHE_CONFIG['fund_analysis_report']['expiry'])
        print(f"[report timing] {fund_code} MySQL命中 total={t4-t0:.3f}s mysql={t4-t3:.3f}s")
        return jsonify(mysql_report)

    # -- Step 3：同步实时计算（info已缓存，纯内存计算 <1s）--
    info_cache_key = generate_cache_key(CACHE_CONFIG['fund_info']['prefix'], fund_code)
    t5 = time_module.time()
    info = get_cache(info_cache_key)
    t6 = time_module.time()
    source = 'redis_cache'

    # 优先使用新鲜数据（含完整基金经理详情），DB 数据作为降级
    if not info:
        info = fetch_fund_info(fund_code)
        source = 'crawler'

    if not info:
        info = get_fund_info_from_db(fund_code)
        source = 'mysql_database'

    if not info:
        return jsonify({'success': False, 'message': f'无法获取基金 {fund_code} 的信息'})

    try:
        t7 = time_module.time()
        screener = FundScreener(
            fund_info=info,
            holdings={"前十大持仓": info.get("前十大持仓", [])}
        )
        t8 = time_module.time()
        result = screener.screen()
        t9 = time_module.time()
        report = ReportGenerator.generate(result)
        t10 = time_module.time()
        report["source"] = source
        report["cached"] = False
        report["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        set_cache(report_cache_key, report, CACHE_CONFIG['fund_analysis_report']['expiry'])
        t11 = time_module.time()

        threading.Thread(
            target=_save_report_to_mysql,
            args=(fund_code, report, _get_latest_week_number()),
            daemon=True
        ).start()

        print(f"[report timing] {fund_code} 实时计算 total={t11-t0:.3f}s | cache_get={t2-t1:.3f}s mysql={t4-t3:.3f}s info_get={t6-t5:.3f}s screener={t9-t7:.3f}s reportgen={t10-t9:.3f}s")
        return jsonify(report)
    except Exception as e:
        print(f"[report] {fund_code} 同步计算失败: {e}")
        return jsonify({'success': False, 'message': f'报告生成失败: {e}'})


@analysis_bp.route('/api/fund/analysis_report_status', methods=['GET'])
def get_analysis_report_status():
    """轮询分析报告生成状态"""
    fund_code = request.args.get('fund_code', '').strip()
    if not fund_code:
        return jsonify({'success': False, 'message': '请输入基金代码'})

    report_cache_key = generate_cache_key(CACHE_CONFIG['fund_analysis_report']['prefix'], fund_code)
    cached_report = get_cache(report_cache_key)
    if cached_report:
        cached_report['source'] = 'redis_cache'
        cached_report['cached'] = True
        return jsonify(cached_report)

    mysql_report = _get_report_from_mysql(fund_code)
    if mysql_report:
        mysql_report['cached'] = False
        set_cache(report_cache_key, mysql_report, CACHE_CONFIG['fund_analysis_report']['expiry'])
        return jsonify(mysql_report)

    generating = fund_code in REPORT_GENERATING
    return jsonify({
        'success': True,
        'pending': generating,
        'fund_code': fund_code,
        'message': '报告正在生成中，请稍后刷新' if generating else '报告不存在'
    })


@analysis_bp.route('/api/fund/screen', methods=['GET'])
def screen_funds():
    """
    批量筛选基金 -- 执行6步筛选流程
    参数:
      codes: 逗号分隔的基金代码列表，如 "110022,000001,161725"
      min_score: 最小4P总分（默认0）
      min_sharpe: 最小夏普比率（默认0）
      max_drawdown: 最大回撤上限%（默认100）
      risk_level: 风险等级过滤（低/中/中高/高，不传则不过滤）
    返回: 筛选结果列表 + 各阶段统计
    """
    codes_str = request.args.get('codes', '').strip()
    if not codes_str:
        return jsonify({'success': False, 'message': '请提供基金代码列表（codes参数）'})

    codes = [c.strip() for c in codes_str.split(',') if c.strip()]
    if not codes:
        return jsonify({'success': False, 'message': '基金代码列表为空'})

    min_score = int(request.args.get('min_score', 0))
    min_sharpe = float(request.args.get('min_sharpe', 0))
    max_drawdown = float(request.args.get('max_drawdown', 100))
    risk_filter = request.args.get('risk_level', '').strip()

    stage_counts = {
        "合规初筛池": 0,
        "量化初筛优质池": 0,
        "4P评估通过池": 0,
        "三性校验通过池": 0,
        "精选池": 0,
        "已剔除": 0,
    }

    results = []
    errors = []

    for code in codes:
        try:
            cache_key = generate_cache_key(CACHE_CONFIG['fund_info']['prefix'], code)
            info = get_cache(cache_key)
            source = 'redis_cache'

            if not info:
                info = get_fund_info_from_db(code)
                if info:
                    source = 'mysql_database' if info.get('_db_source') == 'mysql' else 'sqlite_database'
                    info.pop('_db_source', None)

            if not info:
                info = fetch_fund_info(code)
                source = 'crawler'

            if not info:
                errors.append({'code': code, 'error': '无法获取基金数据'})
                continue

            screener = FundScreener(fund_info=info)
            result = screener.screen()
            report = ReportGenerator.generate(result)
            report['source'] = source

            total_4p = report['four_p']['total']['score'] if report.get('four_p') else 0
            sharpe = 0.0
            drawdown = 0.0
            for m in (report.get('metrics') or []):
                if m['label'] == '夏普比率':
                    try: sharpe = float(m['value'])
                    except: pass
                if m['label'] == '最大回撤':
                    try: drawdown = abs(float(m['value'].replace('%', '')))
                    except: pass

            if total_4p < min_score:
                continue
            if sharpe < min_sharpe:
                continue
            if drawdown > max_drawdown:
                continue
            if risk_filter and report.get('risk_level') != risk_filter:
                continue

            stage_counts[report.get('stage', '已剔除')] += 1
            results.append(report)

        except Exception as e:
            errors.append({'code': code, 'error': str(e)})

    results.sort(key=lambda x: x.get('four_p', {}).get('total', {}).get('score', 0), reverse=True)

    return jsonify({
        'success': True,
        'total_input': len(codes),
        'total_passed': len(results),
        'total_errors': len(errors),
        'stage_counts': stage_counts,
        'filters_applied': {
            'min_score': min_score,
            'min_sharpe': min_sharpe,
            'max_drawdown': max_drawdown,
            'risk_level': risk_filter or '不过滤',
        },
        'results': results,
        'errors': errors,
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    })


def _generate_summary(name, total_4p, all_pass, three_natures, info, risk):
    """生成综合评述文字"""
    annual = float(str(info.get('年化收益率', '0%')).replace('%', '') or 0)
    sharpe = float(str(info.get('夏普比率', '0')).replace('nan', '0') or 0)
    max_draw = abs(float(str(info.get('最大回撤', '0%')).replace('%', '') or 0))
    style = info.get('基金风格', '未知')
    first_ind = info.get('第一大行业', '综合')
    conc = info.get('持仓集中度', 'N/A')

    paras = []

    paras.append(f"【{name}】（{info.get('基金代码', '')}）投资分析综述：")

    if annual >= 15 and sharpe >= 1.5:
        paras.append(f"该基金年化收益率{annual:.1f}%、夏普比率{sharpe:.2f}，风险调整后收益表现出色，具备较强的主动管理能力。")
    elif annual >= 10:
        paras.append(f"该基金年化收益率{annual:.1f}%，长期趋势向上，但夏普比率{sharpe:.2f}显示风险收益比仍有提升空间。")
    elif annual >= 0:
        paras.append(f"该基金年化收益率{annual:.1f}%，整体正收益但超额收益不明显，需结合市场环境综合判断。")
    else:
        paras.append(f"该基金年化收益{annual:.1f}%，需关注收益为负的原因，谨慎评估其投资价值。")

    if max_draw <= 15:
        paras.append(f"历史最大回撤{max_draw:.1f}%，风险控制能力较强，适合风险偏好适中的投资者。")
    elif max_draw <= 25:
        paras.append(f"最大回撤{max_draw:.1f}%，处于主流偏股基金正常区间，需关注极端行情下的风险承受能力。")
    else:
        paras.append(f"最大回撤{max_draw:.1f}%，波动较大，投资者需具备较高的风险承受能力。")

    if style != '未知':
        paras.append(f"基金风格定位为「{style}」，重点配置「{first_ind}」行业（占比{info.get('行业占比','N/A')}），持仓集中度{conc}。")

    passed = [k for k, v in three_natures.items() if v['result'].startswith('通过')]
    if len(passed) == 3:
        paras.append(f"三性校验全部通过（一致性、稳定性、有效性），投资逻辑清晰，可追溯性强。")
    elif len(passed) >= 2:
        paras.append(f"三性校验{len(passed)}/3项通过（{'、'.join(passed)}），整体可接受，建议持续跟踪。")
    else:
        paras.append(f"三性校验仅{len(passed)}/3项通过，投资逻辑需进一步验证，建议谨慎。")

    if total_4p >= 80 and len(passed) == 3:
        paras.append(f"综合4P评分{total_4p}/100分，建议【强烈推荐】--该基金在收益、风险、风格一致性等方面均表现优秀，适合作为核心持仓配置。")
    elif total_4p >= 60:
        paras.append(f"综合4P评分{total_4p}/100分，建议【建议持有】--中长期持有可期，建议结合个人风险偏好决定。")
    elif total_4p >= 45:
        paras.append(f"综合4P评分{total_4p}/100分，建议【谨慎关注】--适合风险偏好较高的投资者，不宜重仓。")
    else:
        paras.append(f"综合4P评分{total_4p}/100分，建议【不建议投资】--当前各项指标未达优，建议等待更好时机或寻找更优标的。")

    return paras


def init_analysis_module():
    """初始化分析报告模块：MySQL建表 + 热点预热 + 定时调度"""
    _init_analysis_history_table()
    _init_fund_scores_table()
    _warmup_top_funds_report()
    _precompute_top_funds_async()
    _schedule_weekly_refresh()


# ══════════════════════════════════════════════════════════════
# 基金预评分表（教练向导快速筛选用）
# ══════════════════════════════════════════════════════════════

# 评分引擎版本号 — FundScreener 逻辑变更时递增，启动时自动清除旧数据
SCORING_VERSION = 2

def _init_fund_scores_table():
    """创建基金预评分表"""
    from db import db_execute
    db_execute('''
        CREATE TABLE IF NOT EXISTS fund_scores (
            fund_code VARCHAR(10) PRIMARY KEY,
            fund_name VARCHAR(100),
            fund_type VARCHAR(20) DEFAULT '',
            p1_performance INT DEFAULT 0,
            p2_philosophy INT DEFAULT 0,
            p3_people INT DEFAULT 0,
            p4_process INT DEFAULT 0,
            total_score INT DEFAULT 0,
            annual_return DECIMAL(8,2) DEFAULT 0,
            max_drawdown DECIMAL(8,2) DEFAULT 0,
            sharpe_ratio DECIMAL(8,2) DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    ''', fetch=False)
    try:
        db_execute("ALTER TABLE fund_scores ADD COLUMN fund_type VARCHAR(20) DEFAULT ''", fetch=False)
    except Exception:
        pass
    try:
        db_execute("ALTER TABLE fund_scores ADD COLUMN scoring_version INT DEFAULT 0", fetch=False)
    except Exception:
        pass
    # 清除旧版本评分数据，确保所有路径使用同一评分引擎
    deleted = db_execute(
        "DELETE FROM fund_scores WHERE scoring_version < %s OR scoring_version IS NULL",
        (SCORING_VERSION,), fetch=False)
    if deleted and deleted > 0:
        print(f"[scores] Purged {deleted} stale rows (version < {SCORING_VERSION})")
    print("[scores] fund_scores table ready")


def _precompute_top_funds_async():
    """后台线程：全量预计算基金评分（分批，限流）"""
    def _run():
        from db import db_execute
        import time as _time
        BATCH_SIZE = 50
        BATCH_DELAY = 2  # 批次间隔秒，避免触发反爬

        try:
            rows = db_execute("SELECT code, name FROM fund_list_cache", fetch=True)
            if not rows:
                rows = db_execute("SELECT fund_code as code, fund_name as name FROM fund_basic", fetch=True)
            if not rows:
                print("[scores] No fund list for precompute, skip")
                return

            total = len(rows)
            count = 0
            print(f"[scores] Full precompute: {total} funds in batches of {BATCH_SIZE}")
            for i, row in enumerate(rows):
                code = row.get('code', row.get('fund_code', ''))
                name = row.get('name', row.get('fund_name', ''))
                if not code:
                    continue
                try:
                    info = fetch_fund_info(code)
                    if not info:
                        continue
                    # 提取基金类型
                    fund_type = str(info.get('基金类型', info.get('fund_type', '')))
                    if not fund_type:
                        raw_name = info.get('基金简称', '')
                        if '债券' in raw_name or '债' in raw_name: fund_type = '债券型'
                        elif '货币' in raw_name: fund_type = '货币型'
                        elif '指数' in raw_name or 'ETF' in raw_name: fund_type = '指数型'
                        elif '混合' in raw_name: fund_type = '混合型'
                        elif '股票' in raw_name: fund_type = '股票型'
                        else: fund_type = '混合型'
                    # 使用 FundScreener（与 analysis_report 相同的评分引擎）
                    screener = FundScreener(
                        fund_info=info,
                        holdings={"前十大持仓": info.get('前十大持仓', [])}
                    )
                    result = screener.screen()
                    fp = result.four_p
                    if fp is None:
                        continue
                    p1, p2, p3, p4 = fp.performance, fp.philosophy, fp.people, fp.process
                    total_score = fp.total
                    an = float(str(info.get('年化收益率', '0%')).replace('%', '').replace('nan', '0') or 0)
                    dd = abs(float(str(info.get('最大回撤', '0%')).replace('%', '').replace('nan', '0') or 0))
                    sr = float(str(info.get('夏普比率', '0')).replace('nan', '0') or 0)

                    db_execute(
                        "INSERT INTO fund_scores (fund_code, fund_name, fund_type, p1_performance, p2_philosophy, "
                        "p3_people, p4_process, total_score, annual_return, max_drawdown, sharpe_ratio, "
                        "scoring_version, updated_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW()) "
                        "ON DUPLICATE KEY UPDATE fund_type=VALUES(fund_type), "
                        "p1_performance=VALUES(p1_performance), "
                        "p2_philosophy=VALUES(p2_philosophy), p3_people=VALUES(p3_people), "
                        "p4_process=VALUES(p4_process), total_score=VALUES(total_score), "
                        "annual_return=VALUES(annual_return), max_drawdown=VALUES(max_drawdown), "
                        "sharpe_ratio=VALUES(sharpe_ratio), updated_at=NOW()",
                        (code, name, fund_type, p1, p2, p3, p4, total_score, an, dd if dd > 0 else 0, sr, SCORING_VERSION),
                        fetch=False)
                    # 同步更新 analysis_report 缓存（保证同一数据源的评分一致性）
                    try:
                        report = ReportGenerator.generate(result)
                        report["source"] = "precompute_sync"
                        report["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        report_cache_key = generate_cache_key(CACHE_CONFIG['fund_analysis_report']['prefix'], code)
                        set_cache(report_cache_key, report, CACHE_CONFIG['fund_analysis_report']['expiry'])
                        _save_report_to_mysql(code, report, _get_latest_week_number())
                    except Exception:
                        pass
                    count += 1
                except Exception as e:
                    print(f"[scores] Skip {code}: {e}")

                # 分批限流
                if (i + 1) % BATCH_SIZE == 0 and i + 1 < total:
                    print(f"[scores] Progress: {count}/{i+1} done, next batch in {BATCH_DELAY}s...")
                    _time.sleep(BATCH_DELAY)

            print(f"[scores] Full precompute done: {count}/{total} fund scores")
        except Exception as e:
            print(f"[scores] Precompute failed: {e}")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
