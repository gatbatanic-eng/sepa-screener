import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import query as q


def bars(n=140):
    return [dict(date=(dt.date(2025, 1, 1)+dt.timedelta(days=i)).isoformat(), open=100+i,
                 high=102+i, low=99+i, close=101+i, volume=10) for i in range(n)]

class QueryTests(unittest.TestCase):
    def test_week_cross_year(self):
        source = bars(3)
        for b, date in zip(source, ['2025-12-29', '2025-12-30', '2026-01-02']): b['date'] = date
        result = q.aggregate(source, 'weekly')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['volume'], 30)
        self.assertEqual(result[0]['close'], 103)

    def test_month_ohlcv(self):
        result = q.aggregate(bars(40), 'monthly')
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['open'], 100)
        self.assertEqual(result[0]['close'], 131)
        self.assertEqual(result[0]['volume'], 310)

    def test_indicator_warmup_and_trend(self):
        result = q.indicators(bars())
        self.assertIsNone(result[13]['rsi14'])
        self.assertEqual(result[14]['rsi14'], 100)
        self.assertIsNone(result[24]['macd'])
        self.assertAlmostEqual(result[25]['macd'], 7)
        self.assertIsNone(result[32]['macdSignal'])
        self.assertAlmostEqual(result[33]['macdSignal'], 7)
        self.assertAlmostEqual(result[33]['macdHistogram'], 0)
        self.assertEqual(result[19]['ma20'], 110.5)

    def test_flat(self):
        source = bars(30)
        for row in source: row['close'] = 100
        result = q.indicators(source)
        self.assertEqual(result[-1]['rsi14'], 50)
        self.assertEqual(result[-1]['bollingerUpper'], 100)
        self.assertEqual(result[-1]['bollingerLower'], 100)

    def test_short_series(self):
        result = q.indicators(bars(2))
        self.assertIsNone(result[-1]['macdSignal'])
        self.assertIsNone(result[-1]['ma5'])

    def test_validation(self):
        row = dict(bsop_date='20261030', stck_oprc='100', stck_hgpr='110', stck_lwpr='90', stck_prpr='105', acml_vol='2,000')
        self.assertEqual(q.daily_bars({'Output_1':[row]})[0]['volume'], 2000)
        with self.assertRaises(q.DataError): q.daily_bars({'Output_1':[row, row]})
        row['stck_prpr'] = '120'
        with self.assertRaises(q.DataError): q.daily_bars({'Output_1':[row]})
        row['stck_prpr'] = 'NaN'
        with self.assertRaises(q.DataError): q.daily_bars({'Output_1':[row]})

    def test_read_only(self):
        client = object.__new__(q.Client)
        with self.assertRaises(q.DataError): client.call('order', {})

    def test_missing_key(self):
        with patch.dict(q.os.environ, {}, clear=True):
            with self.assertRaisesRegex(q.DataError, 'Missing repository secrets'): q.Client()

    def test_collect_and_render(self):
        class Fake:
            def call(self, endpoint, body):
                if endpoint == 'currentPrice': return {'Output_0':[{'stck_prpr':'250','iem_nm':'<script>bad</script>', 'hoga_bsop_hour':'100000'}]}
                if endpoint == 'period': return {'Output_1':[dict(bsop_date=b['date'].replace('-',''), stck_oprc=b['open'], stck_hgpr=b['high'], stck_lwpr=b['low'], stck_prpr=b['close'], acml_vol=b['volume']) for b in reversed(bars())]}
                raise q.DataError('API HTTP 503')
        result = q.collect(Fake(), '005930', 'KOSPI')
        self.assertEqual(result['quoteFreshness'], 'unverified')
        self.assertIsNone(result['quoteSourceDate'])
        self.assertTrue(any('수급' in x for x in result['warnings']))
        with tempfile.TemporaryDirectory() as folder:
            q.write_result(result, Path(folder))
            page = (Path(folder)/'chart.html').read_text()
            self.assertNotIn('<script>', page)
            self.assertEqual(page.count('<svg'), 3)
            self.assertEqual(json.loads((Path(folder)/'snapshot.json').read_text())['code'], '005930')

if __name__ == '__main__': unittest.main()
