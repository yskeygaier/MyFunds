#!/usr/bin/env python3
"""Step 0: akshare 并发能力测试
测试同时拉取多只基金数据的耗时，确认最大并行数。
"""
import time
import concurrent.futures
import sys
import os

TEST_FUNDS = [
    '000001', '161725', '110011', '163406', '005918',
    '519674', '161039', '110022', '001875', '270002',
]


def _fetch_nav(code, years=1):
    """单只基金净值获取"""
    from fund_crawler import crawl_fund_nav_df
    t0 = time.time()
    try:
        data = crawl_fund_nav_df(code, years=years)
        elapsed = time.time() - t0
        if data and len(data) > 5:
            return {'code': code, 'ok': True, 'time': round(elapsed, 2), 'rows': len(data)}
        return {'code': code, 'ok': False, 'time': round(elapsed, 2), 'error': f'rows={len(data) if data else 0}'}
    except Exception as e:
        elapsed = time.time() - t0
        return {'code': code, 'ok': False, 'time': round(elapsed, 2), 'error': str(e)[:80]}


def _fetch_info(code):
    """单只基金信息获取（需要Flask上下文）"""
    # 直接使用自己的实现，不依赖routes_fund
    import akshare as ak
    t0 = time.time()
    try:
        df = ak.fund_info_em(code)
        elapsed = time.time() - t0
        if df is not None and len(df) > 0:
            return {'code': code, 'ok': True, 'time': round(elapsed, 2)}
        return {'code': code, 'ok': False, 'time': round(elapsed, 2), 'error': 'empty'}
    except Exception as e:
        elapsed = time.time() - t0
        return {'code': code, 'ok': False, 'time': round(elapsed, 2), 'error': str(e)[:80]}


def run_test(name, test_fn, funds, max_workers, timeout=45):
    """并发测试"""
    t0 = time.time()
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        fut_map = {ex.submit(test_fn, c): c for c in funds}
        for f in concurrent.futures.as_completed(fut_map):
            try:
                results.append(f.result(timeout=timeout))
            except concurrent.futures.TimeoutError:
                code = fut_map[f]
                results.append({'code': code, 'ok': False, 'time': timeout, 'error': 'TIMEOUT'})
    total = time.time() - t0
    ok = [r for r in results if r.get('ok')]
    fail = [r for r in results if not r.get('ok')]
    avg = sum(r['time'] for r in ok) / max(len(ok), 1)
    return {'name': name, 'concurrency': max_workers, 'funds': len(funds),
            'total': round(total, 2), 'ok': len(ok), 'fail': len(fail),
            'avg': round(avg, 2), 'fail_details': [{'code': r['code'], 'e': r.get('error','')} for r in fail]}


def print_result(r, label=''):
    icon = '✅' if r['fail'] == 0 else '⚠️'
    print(f"  {icon} {label:12s} 并发={r['concurrency']}, "
          f"{r['funds']}只, 总耗时={r['total']}s, "
          f"成功={r['ok']}, 失败={r['fail']}, 均每只={r['avg']}s", end='')
    if r['fail_details']:
        print(f"  失败: {[d['code'] for d in r['fail_details']]}")
    else:
        print()


if __name__ == '__main__':
    print("=" * 65)
    print("Step 0: akshare 并发能力测试")
    print("=" * 65)

    # ── 串行基准测试（3只）──
    print("\n▶ 测试1: 净值获取 — 串行基准 (3只)")
    serial = [_fetch_nav(c, years=1) for c in TEST_FUNDS[:3]]
    for r in serial:
        icon = '✅' if r['ok'] else '❌'
        print(f"  {icon} {r['code']}: {r.get('time','?')}s ({r.get('rows','?')}行){'   '+r.get('error','') if not r['ok'] else ''}")
    ok_serial = [r for r in serial if r['ok']]
    print(f"  串行平均: {round(sum(r['time'] for r in ok_serial)/max(len(ok_serial),1), 2)}s/只")
    print()

    # ── 串行测试（基金信息）──
    print("▶ 测试2: 基金信息 — 串行基准 (3只)")
    info_serial = [_fetch_info(c) for c in TEST_FUNDS[:3]]
    for r in info_serial:
        icon = '✅' if r['ok'] else '❌'
        print(f"  {icon} {r['code']}: {r.get('time','?')}s{'   '+r.get('error','') if not r['ok'] else ''}")
    ok_info = [r for r in info_serial if r['ok']]
    print(f"  串行平均: {round(sum(r['time'] for r in ok_info)/max(len(ok_info),1), 2)}s/只")
    print()

    # ── 并发测试 ──
    print("▶ 测试3: 净值获取 — 并发压力")
    for n in [3, 5, 10]:
        r = run_test('nav', _fetch_nav, TEST_FUNDS[:n], n)
        print_result(r, 'NAV并发')
    print()

    print("▶ 测试4: 基金信息 — 并发压力")
    for n in [3, 5, 10]:
        r = run_test('info', _fetch_info, TEST_FUNDS[:n], n)
        print_result(r, 'INFO并发')
    print()

    # ── 结论 ──
    print("=" * 65)
    print("退出条件对照:")
    print("  ✅ 总耗时 < 120s (10只并发) → 通过")
    print("  ⚠️  并发=5时失败增多 → 降低 max_workers=3")
    print("  ❌ 串行单只 > 17s → akshare 数据源可能有问题")
    print("=" * 65)
