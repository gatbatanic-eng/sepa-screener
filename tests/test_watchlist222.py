import copy
import unittest
from nhplug.watchlist222 import select, domestic_themes

DAY = '20261022'

def stock(code, sector='강세', change=5, turn=2e10, surge=2, title=None):
    article = {'title': title or f'{code} 공급계약 체결', 'excerpt': '', 'url': f'https://example.com/{code}', 'publishedAt': DAY+'1530'}
    return dict(code=code, name=code, sector=sector, changePct=change, turnoverKRW=turn, turnoverVs20d=surge,
                providerNewsCount=10, sourceUrl='https://example.com', newsEvidence=[article])

def report(stocks, sectors=None):
    return dict(date=DAY, coverage={'valid': 700}, stockUniverse=stocks,
                sectors=sectors or [dict(sector='강세', stockCount=4, meanChangePct=3, advancingPct=75, turnoverSharePct=10)])

class WatchlistTests(unittest.TestCase):
    def test_six_distinct_slots_and_priority(self):
        stocks = [stock('A'), stock('B'), stock('C', '정책', title='한국 정부 로봇 지원 정책 C 발표'),
                  stock('D', '정책', title='한국 정부 로봇 지원 정책 D 발표'), stock('E', '개별'), stock('F', '개별')]
        result = select(report(stocks))
        self.assertEqual(result['status'], 'complete')
        self.assertEqual([len(g['items']) for g in result['groups']], [2, 2, 2])
        codes = [s['code'] for g in result['groups'] for s in g['items']]
        self.assertEqual(len(set(codes)), 6)
        self.assertIn(result['zeroPriority']['code'], codes)

    def test_high_turnover_falling_sector_is_not_market_leader(self):
        g = [dict(sector='강세', stockCount=10, meanChangePct=-3, advancingPct=20, turnoverSharePct=70)]
        result = select(report([stock('A'), stock('B')], g))
        self.assertEqual(result['groups'][0]['items'], [])
        self.assertEqual(result['status'], 'partial')

    def test_missing_history_does_not_pass_surge_filter(self):
        result = select(report([stock('A', surge=None), stock('B', change=-2), stock('C', turn=1e8)]))
        self.assertEqual(result['selectedCount'], 0)
        self.assertIsNone(result['zeroPriority'])

    def test_old_news_cannot_fill_material_slots(self):
        s = stock('A'); s['newsEvidence'][0]['publishedAt'] = '202610211500'
        self.assertEqual(select(report([s]))['selectedCount'], 0)

    def test_domestic_requires_korean_institution_and_related_second_name(self):
        foreign = [{'title': '미국 정부 로봇 지원 정책', 'excerpt': ''}]
        self.assertEqual(domestic_themes(foreign), [])
        s = stock('C', '정책', title='한국 정부 로봇 지원 정책 C 발표')
        self.assertEqual(select(report([s]))['groups'][1]['items'], [])

    def test_legacy_scope_is_explicit_and_input_unchanged(self):
        a, b = stock('A'), stock('B')
        r = report([])
        del r['stockUniverse']
        r['sectors'][0].update(moneyLeaders=[a, b], newsLeaders=[a])
        original = copy.deepcopy(r)
        result = select(r)
        self.assertEqual(result['coverage']['scope'], 'sector_leaders_only')
        self.assertEqual(result['coverage']['evaluated'], 2)
        self.assertEqual(r, original)
