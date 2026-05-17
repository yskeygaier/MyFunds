# -*- coding: utf-8 -*-
"""组合评估和回测路由 — 薄层，委托给 PortfolioClinic"""
from flask import Blueprint, render_template, request, jsonify, abort
from portfolio_clinic import (
    PortfolioClinic, ClinicReport, extract_from_image, ImageProcessor
)

portfolio_eval_bp = Blueprint('portfolio_eval', __name__)

# 共享报告缓存：share_id → ClinicReport（内存存储，重启丢失）
_shared_reports = {}
_DELETED_SHARES = set()


@portfolio_eval_bp.route('/portfolio-eval')
def portfolio_eval_page():
    return render_template('portfolio_eval.html')


@portfolio_eval_bp.route('/report/<share_id>')
def shared_report(share_id):
    """查看共享的诊断报告"""
    if share_id in _DELETED_SHARES:
        abort(410, '该报告已被删除')
    report = _shared_reports.get(share_id)
    if not report:
        abort(404, '报告不存在或已过期')
    return render_template('portfolio_eval.html', shared_report=report.to_dict())


@portfolio_eval_bp.route('/api/portfolio-eval/upload-image', methods=['POST'])
def upload_and_extract_portfolio():
    """上传图片并提取基金组合信息"""
    data = request.get_json() or {}
    image_data = data.get('image', '')
    if not image_data:
        return jsonify({'success': False, 'error': '请上传图片'})
    try:
        if ',' in image_data:
            image_data = image_data.split(',')[1]
        import base64
        image_bytes = base64.b64decode(image_data)
    except Exception as e:
        return jsonify({'success': False, 'error': f'图片解析失败: {str(e)}'})
    holdings = extract_from_image(image_bytes)
    if not holdings:
        return jsonify({'success': False, 'error': '未能从图片中识别到基金信息，请手动输入'})
    return jsonify({
        'success': True, 'holdings': holdings,
        'total_amount': '', 'extraction_notes': f'识别到 {len(holdings)} 只基金',
    })


@portfolio_eval_bp.route('/api/portfolio-eval/analyze', methods=['POST'])
def analyze_portfolio():
    """分析基金组合"""
    data = request.get_json() or {}
    holdings = data.get('holdings', [])

    report = PortfolioClinic.analyze(holdings)
    if not report.holdings and not report.missing_funds:
        return jsonify({'success': False, 'error': '无法获取任何基金数据'})

    # 存储到共享缓存
    if report.share_id:
        _shared_reports[report.share_id] = report

    resp = report.to_dict()
    resp['success'] = True
    if report.missing_funds:
        resp['warning'] = f'以下基金数据暂缺：{", ".join(report.missing_funds)}'
    return jsonify(resp)


@portfolio_eval_bp.route('/api/portfolio-eval/share/delete', methods=['POST'])
def delete_share():
    """删除共享报告"""
    data = request.get_json() or {}
    share_id = data.get('share_id', '')
    if share_id in _shared_reports:
        _DELETED_SHARES.add(share_id)
        del _shared_reports[share_id]
    return jsonify({'success': True})


@portfolio_eval_bp.route('/api/portfolio-eval/backtest', methods=['POST'])
def backtest_portfolio():
    data = request.get_json() or {}
    holdings = data.get('holdings', [])
    try:
        years = float(data.get('years', 3))
    except (ValueError, TypeError):
        years = 3
    result = PortfolioClinic.backtest(holdings, years=years)
    if result.data_points < 10:
        return jsonify({'success': False, 'error': '净值数据不足，无法回测'})
    resp = result.to_dict()
    resp['success'] = True
    return jsonify(resp)


@portfolio_eval_bp.route('/api/portfolio-eval/llm-summary', methods=['POST'])
def llm_summary():
    data = request.get_json() or {}
    holdings = data.get('holdings', [])
    report = PortfolioClinic.analyze(holdings)
    summary = PortfolioClinic.generate_llm_summary(report)
    return jsonify({'success': True, 'summary': summary or 'AI诊断暂不可用'})


@portfolio_eval_bp.route('/api/portfolio-eval/verify-formulas', methods=['POST'])
def verify_formulas():
    data = request.get_json() or {}
    holdings = data.get('holdings', [])
    adjusted_weights = data.get('adjusted_weights', [])
    if not holdings or not adjusted_weights:
        return jsonify({'success': False, 'error': '参数不足'})
    adjusted = []
    for h in holdings:
        code = h.get('fund_code', '')
        aw = next((a for a in adjusted_weights if a.get('fund_code') == code), None)
        weight = float(aw.get('weight', h.get('weight', 0))) if aw else float(h.get('weight', 0))
        adjusted.append({**h, 'weight': weight})
    report = PortfolioClinic.analyze(adjusted)
    return jsonify({
        'success': True,
        'health_score': report.health_score,
        'metrics': report.metrics.to_dict() if report.metrics else {},
    })
