#!/usr/bin/env python3
"""单元测试：PortfolioClinic 核心功能"""
import os, sys, json, unittest
sys.path.insert(0, os.path.dirname(__file__))

from portfolio_clinic import (
    ImageProcessor, PortfolioClinic, ClinicReport, BacktestResult,
    _calculate_portfolio_metrics, _analyze_portfolio_style,
    _analyze_portfolio_sectors, _assess_portfolio_risk,
    _generate_portfolio_recommendations,
)


SAMPLE_HOLDINGS = [
    {'fund_code': '161725', 'weight': 30},
    {'fund_code': '005918', 'weight': 25},
    {'fund_code': '163406', 'weight': 25},
    {'fund_code': '519674', 'weight': 20},
]


# mock fund info for deterministic testing
MOCK_FUND_INFO = {
    '161725': {
        '基金简称': '招商中证白酒指数', '年化收益率': '-12.3%', '最大回撤': '-35.1%',
        '夏普比率': '-0.45', '基金风格': '指数型', '第一大行业': '白酒', '行业占比': '85%'
    },
    '005918': {
        '基金简称': '易方达蓝筹精选混合', '年化收益率': '6.8%', '最大回撤': '-22.4%',
        '夏普比率': '0.35', '基金风格': '大盘成长', '第一大行业': '食品饮料', '行业占比': '35%'
    },
    '163406': {
        '基金简称': '兴全合润混合', '年化收益率': '8.2%', '最大回撤': '-25.6%',
        '夏普比率': '0.52', '基金风格': '均衡型', '第一大行业': '制造业', '行业占比': '28%'
    },
    '519674': {
        '基金简称': '银河创新成长混合', '年化收益率': '15.1%', '最大回撤': '-30.2%',
        '夏普比率': '0.68', '基金风格': '中盘成长', '第一大行业': '信息技术', '行业占比': '40%'
    },
}


def _mock_fetch_fund_info(code):
    """mock 基金数据获取"""
    return MOCK_FUND_INFO.get(code, None)


class TestImageProcessor(unittest.TestCase):

    def test_validate_fund_code(self):
        self.assertTrue(ImageProcessor.validate_fund_code('161725'))
        self.assertTrue(ImageProcessor.validate_fund_code('000001'))
        self.assertFalse(ImageProcessor.validate_fund_code('16172'))
        self.assertFalse(ImageProcessor.validate_fund_code('abcd12'))
        self.assertFalse(ImageProcessor.validate_fund_code(''))

    def test_compress_image(self):
        """压缩后应该变小但保持可读性"""
        from PIL import Image
        from io import BytesIO
        img = Image.new('RGB', (2000, 1500), 'white')
        buf = BytesIO()
        img.save(buf, format='JPEG')
        compressed = ImageProcessor.compress(buf.getvalue())
        self.assertLess(len(compressed), len(buf.getvalue()),
                        "压缩后应该小于原始大小")

    def test_split_long_image_normal(self):
        """普通图片不应拆分"""
        from PIL import Image
        from io import BytesIO
        img = Image.new('RGB', (500, 500), 'white')
        buf = BytesIO()
        img.save(buf, format='JPEG')
        chunks = ImageProcessor.split_long_image(buf.getvalue())
        self.assertEqual(len(chunks), 1)

    def test_split_long_image(self):
        """长图应拆分多段"""
        from PIL import Image
        from io import BytesIO
        img = Image.new('RGB', (500, 3000), 'white')
        buf = BytesIO()
        img.save(buf, format='JPEG')
        chunks = ImageProcessor.split_long_image(buf.getvalue())
        self.assertGreater(len(chunks), 1)

    def test_merge_holdings_dedup(self):
        """合并应去重"""
        r1 = {'success': True, 'holdings': [
            {'fund_code': '161725', 'weight': 30},
            {'fund_code': '005918', 'weight': 25},
        ]}
        r2 = {'success': True, 'holdings': [
            {'fund_code': '161725', 'weight': 30},
            {'fund_code': '163406', 'weight': 25},
        ]}
        merged, notes, conf = ImageProcessor.merge_holdings([r1, r2])
        self.assertEqual(len(merged), 3)  # 去重后应只有3只
        codes = [h['fund_code'] for h in merged]
        self.assertIn('161725', codes)
        self.assertIn('005918', codes)
        self.assertIn('163406', codes)

    def test_merge_invalid_code(self):
        """无效代码应过滤"""
        r = {'success': True, 'holdings': [
            {'fund_code': '161725', 'weight': 30},
            {'fund_code': 'abcde', 'weight': 10},
        ]}
        merged, _, _ = ImageProcessor.merge_holdings([r])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['fund_code'], '161725')


class TestPortfolioMetrics(unittest.TestCase):

    def setUp(self):
        self.fund_infos = []
        for code, weight in [('161725', 30), ('005918', 25),
                              ('163406', 25), ('519674', 20)]:
            info = MOCK_FUND_INFO.get(code, {})
            self.fund_infos.append({
                'fund_code': code, 'weight': weight, 'info': dict(info)
            })

    def test_calculate_metrics(self):
        """指标计算不应抛出异常"""
        metrics = _calculate_portfolio_metrics(self.fund_infos)
        self.assertIn('annual_return', metrics)
        self.assertIn('sharpe_ratio', metrics)
        self.assertIn('conservative_max_drawdown', metrics)
        self.assertGreater(metrics['fund_count'], 0)

    def test_style_analysis(self):
        """风格分析应返回有效结果"""
        style = _analyze_portfolio_style(self.fund_infos)
        self.assertIn('dominant', style)
        self.assertIn('breakdown', style)
        self.assertIn('is_diversified', style)

    def test_sector_analysis(self):
        """行业分析应返回有效结果"""
        sectors = _analyze_portfolio_sectors(self.fund_infos)
        self.assertIn('breakdown', sectors)
        self.assertIn('concentration', sectors)

    def test_risk_assessment(self):
        """风险评估应有明确等级"""
        metrics = _calculate_portfolio_metrics(self.fund_infos)
        risk = _assess_portfolio_risk(self.fund_infos, metrics)
        self.assertIn('level', risk)
        self.assertIn('description', risk)
        self.assertIn(risk['level'], ['低风险', '中低风险', '中等风险', '中高风险', '高风险'])

    def test_recommendations(self):
        """应生成至少一条建议"""
        metrics = _calculate_portfolio_metrics(self.fund_infos)
        style = _analyze_portfolio_style(self.fund_infos)
        sectors = _analyze_portfolio_sectors(self.fund_infos)
        risk = _assess_portfolio_risk(self.fund_infos, metrics)
        recs = _generate_portfolio_recommendations(
            self.fund_infos, metrics, style, sectors, risk)
        self.assertIn('list', recs)
        self.assertGreater(len(recs['list']), 0)
        self.assertIn('expected_effect', recs)

    def test_health_score_range(self):
        """健康分应在0-100范围内"""
        score = PortfolioClinic._calc_health_score(
            _calculate_portfolio_metrics(self.fund_infos),
            _assess_portfolio_risk(self.fund_infos,
                _calculate_portfolio_metrics(self.fund_infos)),
            _analyze_portfolio_style(self.fund_infos),
            self.fund_infos
        )
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)


class TestPortfolioClinic(unittest.TestCase):

    def test_analyze_empty(self):
        """空持仓应返回空报告"""
        report = PortfolioClinic.analyze([])
        self.assertIsInstance(report, ClinicReport)
        self.assertEqual(len(report.holdings), 0)

    def test_analyze_invalid_code(self):
        """无效代码应被过滤"""
        report = PortfolioClinic.analyze([
            {'fund_code': 'abc', 'weight': 100}
        ])
        self.assertIsInstance(report, ClinicReport)

    def test_backtest_insufficient(self):
        """不足2只基金应返回空结果"""
        result = PortfolioClinic.backtest([{'fund_code': '161725', 'weight': 100}])
        self.assertIsInstance(result, BacktestResult)
        self.assertEqual(result.data_points, 0)

    def test_clinic_report_to_dict(self):
        """ClinicReport 应可序列化为 dict"""
        report = ClinicReport(
            health_score=68,
            share_id='abc123',
        )
        d = report.to_dict()
        self.assertEqual(d['health_score'], 68)
        self.assertEqual(d['share_id'], 'abc123')
        self.assertIn('holdings', d)

    def test_backtest_result_to_dict(self):
        """BacktestResult 应可序列化为 dict"""
        bt = BacktestResult(
            total_return=25.5,
            max_drawdown=15.3,
        )
        d = bt.to_dict()
        self.assertEqual(d['total_return'], 25.5)
        self.assertEqual(d['max_drawdown'], 15.3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
