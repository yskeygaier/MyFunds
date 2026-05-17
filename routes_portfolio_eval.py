# -*- coding: utf-8 -*-
"""组合评估和回测路由 — 薄层，委托给 PortfolioClinic"""
from flask import Blueprint, render_template, request, jsonify
from portfolio_clinic import (
    PortfolioClinic, ClinicReport, extract_from_image, ImageProcessor
)

portfolio_eval_bp = Blueprint('portfolio_eval', __name__)


@portfolio_eval_bp.route('/portfolio-eval')
def portfolio_eval_page():
    return render_template('portfolio_eval.html')


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

    # Step 0 验证的两个关键处理：压缩+长图拆分，均在 extract_from_image 中
    holdings = extract_from_image(image_bytes)

    if not holdings:
        return jsonify({
            'success': False,
            'error': '未能从图片中识别到基金信息，请手动输入'
        })

    return jsonify({
        'success': True,
        'holdings': holdings,
        'total_amount': '',
        'extraction_notes': f'识别到 {len(holdings)} 只基金',
    })


@portfolio_eval_bp.route('/api/portfolio-eval/analyze', methods=['POST'])
def analyze_portfolio():
    """分析基金组合"""
    data = request.get_json() or {}
    holdings = data.get('holdings', [])

    report = PortfolioClinic.analyze(holdings)

    if not report.holdings and not report.missing_funds:
        return jsonify({'success': False, 'error': '无法获取任何基金数据'})

    resp = report.to_dict()
    resp['success'] = True

    # 如有缺失基金，加警告
    if report.missing_funds:
        resp['warning'] = f'以下基金数据暂缺：{", ".join(report.missing_funds)}'

    return jsonify(resp)


@portfolio_eval_bp.route('/api/portfolio-eval/backtest', methods=['POST'])
def backtest_portfolio():
    """回测组合历史表现"""
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
    """LLM诊断摘要（独立接口，异步调用）"""
    data = request.get_json() or {}
    holdings = data.get('holdings', [])

    report = PortfolioClinic.analyze(holdings)
    summary = PortfolioClinic.generate_llm_summary(report)

    return jsonify({
        'success': True,
        'summary': summary or 'AI诊断暂不可用'
    })


@portfolio_eval_bp.route('/api/portfolio-eval/verify-formulas', methods=['POST'])
def verify_formulas():
    """公式验证端点 — 开发模式比JS和Python计算结果是否一致"""
    data = request.get_json() or {}
    holdings = data.get('holdings', [])
    adjusted_weights = data.get('adjusted_weights', [])

    if not holdings or not adjusted_weights:
        return jsonify({'success': False, 'error': '参数不足'})

    # 用调整后的权重重新计算
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
