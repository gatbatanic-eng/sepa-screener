import tempfile
import unittest
from pathlib import Path
from nhplug.money_flow import build, normalize_news, explain, save


def stock(name='기업', sector='반도체', turn=1e9, chg=2, news=3):
    return dict(name=name, sector=sector, turn=turn, chg=chg, news=news, surge=2)


class MoneyFlowTests(unittest.TestCase):
    def test_money_is_not_change_or_news_rank(self):
        r = build('20261022', {'a': stock(turn=9e9, chg=-8, news=1), 'b': stock(sector='은행', turn=1e9, chg=9, news=9)}, 2)
        self.assertEqual(r['moneyRanking'][0], '반도체')
        self.assertEqual(r['attentionRanking'][0], '은행')
        self.assertEqual(r['sectors'][0]['turnoverSharePct'], 90)
        self.assertEqual(r['sectors'][0]['moneyLeaders'][0]['changePct'], -8)
        self.assertEqual(r['status'], 'degraded')

    def test_missing_news_not_zero_and_bad_turnover_excluded(self):
        r = build('20261022', {'a': stock(news=None), 'b': stock(turn=-1)}, 2)
        self.assertEqual(r['coverage']['valid'], 1)
        self.assertEqual(r['sectors'][0]['newsCoveredStocks'], 0)
        self.assertEqual(r['attentionRanking'], [])

    def test_named_today_dated_linked_articles_only(self):
        article = dict(title='기업 수주 확대', datetime='2026-10-22 13:00:00', officeId='001', articleId='123')
        news = normalize_news([article, article, dict(article, datetime='20261021'), dict(article, title='타사 수주'), dict(article, datetime='')], '기업', '20261022')
        self.assertEqual(len(news), 1)
        self.assertIn('/001/123', news[0]['url'])
        self.assertEqual(explain(news)['catalysts'][0]['category'], '수주·계약')
        self.assertEqual(explain([])['status'], 'unconfirmed')

    def test_company_in_article_summary_is_relevant(self):
        article = dict(title='중동 신사업 점검', body='삼성전자 회장이 AI 투자 현장을 방문했다.', datetime='202610221540', officeId='001', articleId='55')
        news = normalize_news([article], '삼성전자', '20261022')
        self.assertEqual(len(news), 1)
        self.assertEqual(explain(news)['catalysts'][0]['category'], '산업 수요·투자')

    def test_share_change_and_separate_leaders(self):
        data = {'a': stock(turn=3e9, news=1), 'b': stock(turn=1e9, news=9)}
        old = {'date': '20261021', 'sectors': [{'sector': '반도체', 'turnoverSharePct': 80}]}
        r = build('20261022', data, 2, previous=old)
        g = r['sectors'][0]
        self.assertEqual(g['shareChangePp'], 20)
        self.assertEqual(g['moneyLeaders'][0]['code'], 'a')
        self.assertEqual(g['newsLeaders'][0]['code'], 'b')

    def test_archive_and_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            save(build('20261022', {}, 0), folder)
            self.assertTrue((folder / 'latest.json').exists())
            self.assertEqual((folder / 'index.json').read_text(), '["20261022"]')
