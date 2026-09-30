import unittest
from datetime import date
from fpd.provider_naver import parse_annual,number
from fpd.derive import derive_snapshot
from fpd.config import research_definition_hash
class KoreanConsensusTest(unittest.TestCase):
    def payload(self):
        return {'itemCode':'005930','financePeriodType':'annual','financeInfo':{'trTitleList':[{'key':'202512','isConsensus':'N'},{'key':'202612','isConsensus':'Y'}], 'rowList':[{'title':'EPS','columns':{'202512':{'value':'111'},'202612':{'value':'2,000'}}},{'title':'매출액','columns':{'202612':{'value':'100'}}}]}}
    def test_consensus_only_and_units(self):
        rows=parse_annual(self.payload(),'005930',date(2026,9,28));self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['periodEnd'],'2026-12-31');self.assertEqual(rows[0]['eps']['avg'],2000)
        self.assertEqual(rows[0]['revenue']['avg'],100*1e8)
    def test_actual_missing_and_wrong_symbol(self):
        p=self.payload();p['financeInfo']['trTitleList'][1]['isConsensus']='N';self.assertEqual(parse_annual(p,'005930',date(2026,9,28)),[])
        with self.assertRaises(ValueError):parse_annual(p,'000660',date(2026,9,28))
        self.assertIsNone(number('-'));self.assertIsNone(number('nan'));self.assertEqual(number('0'),0)
    def test_expired_period_never_spliced(self):
        self.assertEqual(parse_annual(self.payload(),'005930',date(2027,1,1)),[])
    def test_korean_definition_does_not_use_us_hash(self):
        definition={'status':'FROZEN','lookbacks':{'7D':{'targetCalendarDays':7,'toleranceDays':2}}}
        raw={'snapshotDate':'2026-09-28','researchId':'KR','datasetId':'KR','market':'KR','observations':{}}
        result=derive_snapshot(raw,[],definition)
        self.assertEqual(result['researchDefinitionHash'],research_definition_hash(definition));self.assertEqual(result['market'],'KR')
    def test_split_unknown_blocks_eps_and_price_not_revenue(self):
        from copy import deepcopy
        definition={'status':'FROZEN','lookbacks':{f'{d}D':{'targetCalendarDays':d,'toleranceDays':t} for d,t in [(7,2),(30,5),(60,7),(90,10)]}}
        rows=parse_annual(self.payload(),'005930',date(2026,9,28))
        raw={'snapshotDate':'2026-09-28','researchId':'KR','datasetId':'KR','market':'KR','observations':{'005930':rows},'events':{'splitCoverage':{}},'universe':{'005930':{'close':100}}}
        prior=deepcopy(raw);prior['snapshotDate']='2026-09-21';prior['observations']['005930'][0]['revenue']['avg']/=1.1
        result=derive_snapshot(raw,[prior],definition)['symbols']['005930'][0]['lookbacks']['7D']
        self.assertIsNone(result['epsRevisionRaw']);self.assertIsNone(result['priceReturnRaw']);self.assertAlmostEqual(result['revenueRevisionRaw'],.1)
    def test_intraday_quote_is_not_a_close(self):
        from fpd.provider_naver import fetch_close
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from unittest.mock import Mock
        session=Mock();session.get.return_value.json.return_value=[{'localTradedAt':'2026-09-28','closePrice':'100'}]
        self.assertIsNone(fetch_close(session,'005930',datetime(2026,9,28,9,tzinfo=ZoneInfo('Asia/Seoul'))))
        self.assertEqual(fetch_close(session,'005930',datetime(2026,9,28,18,tzinfo=ZoneInfo('Asia/Seoul'))),100)
    def test_delayed_run_records_previous_weekday_close(self):
        from fpd.kr_pipeline import close_session
        from datetime import datetime
        from zoneinfo import ZoneInfo
        kst=ZoneInfo('Asia/Seoul')
        self.assertEqual(close_session(datetime(2026,9,29,18,tzinfo=kst)),date(2026,9,29))
        self.assertEqual(close_session(datetime(2026,9,30,0,40,tzinfo=kst)),date(2026,9,29))
        self.assertEqual(close_session(datetime(2026,10,3,2,tzinfo=kst)),date(2026,10,2))  # Sat early -> Fri
        self.assertEqual(close_session(datetime(2026,10,5,1,tzinfo=kst)),date(2026,10,2))  # Mon early -> Fri
        self.assertIsNone(close_session(datetime(2026,9,30,10,tzinfo=kst)))  # market hours
        self.assertIsNone(close_session(datetime(2026,10,3,18,tzinfo=kst)))  # Saturday evening
        self.assertIsNone(close_session(datetime(2026,10,4,1,tzinfo=kst)))  # Sunday early
