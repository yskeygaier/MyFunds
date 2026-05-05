#!/usr/bin/env python3
"""全量预计算 + 缓存同步（3年数据）：fund_scores 和 analysis_report 使用同一次 FundScreener 调用"""
import sys, time, concurrent.futures, sqlite3
sys.path.insert(0, '/media/yskey/文档/work/mytest')

from db import init as _db_init, db_execute
_db_init(mysql_config={'user':'yskey','password':'yskey','host':'127.0.0.1','port':3306,'database':'fund_data','charset':'utf8mb4','ssl_disabled':True},sqlite_db_path='/media/yskey/文档/work/mytest/fund_data.db',pool_size=5)

from routes_fund import fetch_fund_info
from fund_analyzer import FundScreener, ReportGenerator
from app import set_cache, generate_cache_key, CACHE_CONFIG
from routes_analysis import _get_latest_week_number, _save_report_to_mysql, SCORING_VERSION
from datetime import datetime

# Get fund list from SQLite
conn = sqlite3.connect('/media/yskey/文档/work/mytest/fund_data.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute("SELECT code, name FROM fund_list_cache")
rows = [dict(r) for r in cur.fetchall()]
conn.close()

# Dedup
seen = set()
unique = []
for r in rows:
    code = r['code']
    if code and code not in seen:
        seen.add(code)
        unique.append(r)

total = len(unique)
print(f"{time.strftime('%H:%M:%S')} Starting sync: {total} funds", flush=True)

BATCH = 100
count = 0
errors = 0
lock = __import__('threading').Lock()
week = _get_latest_week_number()
t0 = time.time()

def process_one(row):
    code = row['code']
    name = row.get('name', '')
    try:
        info = fetch_fund_info(code)
        if not info: return ('skip', code)
        screener = FundScreener(fund_info=info, holdings={"前十大持仓": info.get('前十大持仓', [])})
        result = screener.screen()
        fp = result.four_p
        if fp is None: return ('skip', code)

        an = float(str(info.get('年化收益率','0%')).replace('%','').replace('nan','0') or 0)
        dd = abs(float(str(info.get('最大回撤','0%')).replace('%','').replace('nan','0') or 0))
        sr = float(str(info.get('夏普比率','0')).replace('nan','0') or 0)
        ft = str(info.get('基金类型',''))
        if not ft:
            nm = info.get('基金简称', name)
            if any(w in nm for w in ['债券','债','纯债','信用债','利率债']): ft='债券型'
            elif '货币' in nm: ft='货币型'
            elif '指数' in nm or 'ETF' in nm: ft='指数型'
            elif '混合' in nm: ft='混合型'
            elif '股票' in nm: ft='股票型'
            else: ft='混合型'

        db_execute(
            "INSERT INTO fund_scores (fund_code, fund_name, fund_type, p1_performance, p2_philosophy, "
            "p3_people, p4_process, total_score, annual_return, max_drawdown, sharpe_ratio, scoring_version, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW()) "
            "ON DUPLICATE KEY UPDATE fund_type=VALUES(fund_type), p1_performance=VALUES(p1_performance), "
            "p2_philosophy=VALUES(p2_philosophy), p3_people=VALUES(p3_people), p4_process=VALUES(p4_process), "
            "total_score=VALUES(total_score), annual_return=VALUES(annual_return), max_drawdown=VALUES(max_drawdown), "
            "sharpe_ratio=VALUES(sharpe_ratio), scoring_version=VALUES(scoring_version), updated_at=NOW()",
            (code, info.get('基金简称', name), ft, fp.performance, fp.philosophy, fp.people, fp.process, fp.total, an, dd, sr, SCORING_VERSION),
            fetch=False)

        report = ReportGenerator.generate(result)
        report["source"] = "precompute_sync"
        report["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        set_cache(generate_cache_key(CACHE_CONFIG['fund_analysis_report']['prefix'], code), report, CACHE_CONFIG['fund_analysis_report']['expiry'])
        _save_report_to_mysql(code, report, week)

        return ('ok', code)
    except Exception as e:
        return ('err', code, str(e)[:60])

for i in range(0, total, BATCH):
    batch = unique[i:i+BATCH]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(process_one, r) for r in batch]
        for f in concurrent.futures.as_completed(futures):
            r = f.result()
            with lock:
                if r[0] == 'ok': count += 1
                elif r[0] == 'err': errors += 1

    elapsed = time.time() - t0
    done = i + len(batch)
    pct = done/total*100
    eta = (elapsed/done*total - elapsed) if done > 0 else 0
    print(f"{time.strftime('%H:%M:%S')} {done}/{total} ({pct:.0f}%) ok={count} err={errors} eta={eta:.0f}s", flush=True)

print(f"{time.strftime('%H:%M:%S')} DONE: {count} OK, {errors} errors in {time.time()-t0:.0f}s", flush=True)
