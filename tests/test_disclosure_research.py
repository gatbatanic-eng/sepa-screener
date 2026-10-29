import datetime as dt
import io
import unittest
import zipfile

import disclosure_research as dr


def rows(sales, profit, cfo, capex, receipt, ytd=None):
    accounts = [('IS', 'ifrs-full_Revenue', '매출액', sales),
                ('IS', 'dart_OperatingIncomeLoss', '영업이익', profit),
                ('IS', 'ifrs-full_ProfitLoss', '당기순이익', profit),
                ('CF', 'ifrs-full_CashFlowsFromUsedInOperatingActivities', '영업활동현금흐름', cfo),
                ('CF', 'ifrs-full_PurchaseOfPropertyPlantAndEquipment', '유형자산의취득', capex),
                ('BS', 'ifrs-full_Equity', '자본총계', 100),
                ('BS', 'ifrs-full_Liabilities', '부채총계', 50)]
    return [{'sj_div': sj, 'account_id': aid, 'account_nm': name,
             'thstrm_amount': str(value), 'thstrm_add_amount': str(ytd if aid == 'ifrs-full_Revenue' and ytd is not None else value),
             'currency': 'KRW', 'rcept_no': receipt} for sj, aid, name, value in accounts]


class QuarterTests(unittest.TestCase):
    def test_cash_flow_ytd_is_differenced_not_summed(self):
        reports = {(2026, 1): {'rows': rows(100, 10, 20, 5, '20260515000001'), 'basis': 'OFS'},
                   (2026, 2): {'rows': rows(120, 12, 55, 13, '20260814000001', 220), 'basis': 'OFS'}}
        q = dr.normalize(reports)[-1]
        self.assertEqual(q['revenue'], 120)  # Q2 IS is already standalone, not YTD.
        self.assertEqual(q['operatingCashFlow'], 35)
        self.assertEqual(q['capex'], 8)
        self.assertEqual(q['fcf'], 27)

    def test_q4_income_uses_nine_month_ytd(self):
        reports = {(2025, 3): {'rows': rows(70, 7, 40, 10, '20251114000001', 200), 'basis': 'CFS'},
                   (2025, 4): {'rows': rows(300, 30, 70, 17, '20260318000001'), 'basis': 'CFS'}}
        q = dr.normalize(reports)[-1]
        self.assertEqual(q['revenue'], 100)
        self.assertEqual(q['operatingCashFlow'], 30)
        self.assertEqual(q['capex'], 7)
        self.assertEqual(q['provenance']['revenue']['subtractReceipt'], '20251114000001')

    def test_missing_prior_or_changed_basis_does_not_become_zero(self):
        first = {'rows': rows(100, 10, 50, 5, '20260814000001'), 'basis': 'CFS'}
        self.assertIsNone(dr.normalize({(2026, 2): first})[0]['operatingCashFlow'])
        prior = {'rows': rows(90, 9, 20, 2, '20260515000001'), 'basis': 'OFS'}
        self.assertIsNone(dr.normalize({(2026, 1): prior, (2026, 2): first})[-1]['operatingCashFlow'])

    def test_prefers_income_statement_to_comprehensive_duplicate(self):
        data = rows(100, 10, 20, 5, '20260515000001')
        duplicate = {**data[2], 'sj_div': 'CIS'}
        self.assertEqual(dr.pick(data + [duplicate], 'netIncome')['sj_div'], 'IS')
        self.assertEqual(dr.pick(data + [data[2]], 'netIncome'), {})


class EvidenceTests(unittest.TestCase):
    def test_extracts_primary_archive_and_marks_excerpt_unreviewed(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as archive:
            archive.writestr('report.xml', '<DOCUMENT><TITLE>수주현황</TITLE><P>양산 일정 지연, 공급계약은 취소 가능.</P></DOCUMENT>')
        text = dr.text_from_zip(buf.getvalue())
        self.assertIn('취소 가능', text)
        evidence = dr.evidence(text)
        self.assertTrue(all(x['status'] == 'requires_review' for x in evidence))
        self.assertIn('취소 가능', evidence[1]['matches'][0]['excerpt'])

    def test_corrected_filing_selected_once_and_same_quarter_compared(self):
        titles = ['[기재정정]분기보고서 (2026.09)', '분기보고서 (2026.09)',
                  '분기보고서 (2026.03)', '분기보고서 (2025.09)']
        notices = [{'rcept_no': str(10-i), 'report_nm': title} for i, title in enumerate(titles)]
        self.assertEqual([x['report_nm'] for x in dr.select_documents(notices)], [titles[0], titles[3]])

    def test_pagination_fetches_every_page(self):
        class API:
            def request(self, endpoint, **params):
                return [{'rcept_no': str(i), 'report_nm': '공시'} for i in range(100)] if params['page_no'] == 1 else [{'rcept_no': '101', 'report_nm': '공시'}]
        self.assertEqual(len(dr.filings(API(), 'corp', dt.date(2026, 10, 29))), 101)

    def test_missing_financials_cannot_pass(self):
        result = dr.assess([], dt.date(2026, 10, 29))
        self.assertEqual(result['status'], 'hold')


class AssessmentTests(unittest.TestCase):
    def quarters(self):
        reports = {}
        for year, limit in ((2024, 4), (2025, 4), (2026, 2)):
            for quarter in range(1, limit + 1):
                sales = 100 * (1.2 ** (year - 2024))
                data = rows(sales * (4 if quarter == 4 else 1), 10 * (4 if quarter == 4 else 1),
                            quarter * 20, quarter * 5, f'{year+1}0101000001', sales * quarter)
                if quarter == 3:
                    data[1]['thstrm_add_amount'] = '30'
                    data[2]['thstrm_add_amount'] = '30'
                reports[(year, quarter)] = {'rows': data, 'basis': 'CFS'}
        return dr.normalize(reports)

    def test_complete_cash_generating_growth_becomes_reading_candidate(self):
        result = dr.assess(self.quarters(), dt.date(2026, 10, 29))
        self.assertEqual(result['status'], 'review_candidate')

    def test_missing_cashflow_blocks_positive_growth(self):
        data = self.quarters()
        data[-1]['operatingCashFlow'] = None
        self.assertEqual(dr.assess(data, dt.date(2026, 10, 29))['status'], 'hold')

    def test_gap_or_mixed_basis_blocks_candidate(self):
        data = self.quarters()
        self.assertEqual(dr.assess(data[:-2] + data[-1:], dt.date(2026, 10, 29))['status'], 'hold')
        data[-1]['basis'] = 'OFS'
        self.assertEqual(dr.assess(data, dt.date(2026, 10, 29))['status'], 'hold')

    def test_stale_financial_period_blocks_candidate(self):
        self.assertEqual(dr.assess(self.quarters(), dt.date(2027, 1, 1))['status'], 'hold')


if __name__ == '__main__':
    unittest.main()
