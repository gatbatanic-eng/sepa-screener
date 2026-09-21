import copy
import tempfile
import unittest
from pathlib import Path
from research_tracker import outcome, process, session_closed, strategy_series_id

DATES = ['2026-09-01','2026-09-02','2026-09-03','2026-09-04','2026-09-07','2026-09-08']
class ResearchTests(unittest.TestCase):
    def test_no_intraday_observation(self):
        self.assertFalse(session_closed("2026-09-14", "2026-09-14T03:00:00+00:00", "kr"))
        self.assertTrue(session_closed("2026-09-14", "2026-09-14T11:00:00+00:00", "kr"))
        self.assertTrue(session_closed("2026-09-11", "2026-09-11T22:00:00+00:00", "us"))
    def test_exchange_horizon_and_no_signal_day_excursion(self):
        s={'date':DATES[0],'originalClose':100}
        b=dict.fromkeys(DATES,100)
        p=dict(zip(DATES,[100,102,103,104,105,110]))
        r=outcome(s,p,b,5)
        self.assertEqual(r['targetDate'],DATES[-1])
        self.assertAlmostEqual(r['returnPct'],10)
        self.assertEqual(r['maxDownPct'],0)
        self.assertEqual(outcome(s,p,b,20)['status'],'pending')
        del p[DATES[-1]]
        self.assertEqual(outcome(s,p,b,5)['status'],'unavailable')
    def test_missing_path_and_split_consistency(self):
        s={'date':DATES[0],'originalClose':100}
        p=dict(zip(DATES,[50,51,52,53,54,55]));b=dict.fromkeys(DATES,100)
        del p[DATES[1]]
        r=outcome(s,p,b,5)
        self.assertAlmostEqual(r['returnPct'],10)
        self.assertTrue(r['baselineRevised'])
        self.assertIsNone(r['maxUpPct'])
    def test_daily_idempotence_revision_unknown_and_new_episode(self):
        with tempfile.TemporaryDirectory() as tmp:
            p={'market':'us','strategyId':'a','strategy':{'config':{'rs':80},'source':{'screening.py':'a'}},'recordedAt':'2026-09-09T00:00:00+00:00',
               'rows':[{'code':'X','name':'X','market':'US','status':'OK','inUniverse':True,
                        'trendOk':True,'setupReady':False,'entryState':'WAIT','close':100,'priceAsOf':DATES[0]}],
               'prices':{'X':{DATES[0]:100}},'benchmarks':{'US':{DATES[0]:100}}}
            p['strategySeriesId']=strategy_series_id(p['strategy'])
            state={};root=Path(tmp)
            process(p,state,root);process(p,state,root)
            self.assertEqual(len(state['signals']),1)
            self.assertEqual(len(list(root.rglob('*.gz'))),1)
            p['rows'][0]['close']=101
            process(p,state,root)
            self.assertEqual(len(list(root.rglob('*.gz'))),2)
            self.assertEqual(len(state['days']),1)
            for i,flag,status in [(1,True,'OK'),(2,False,'UNKNOWN'),(3,True,'OK'),(4,False,'OK'),(5,True,'OK')]:
                day=DATES[i];p['benchmarks']['US'][day]=100;p['prices']['X'][day]=100
                p['rows'][0].update(priceAsOf=day,trendOk=flag,status=status)
                process(p,state,root)
            self.assertEqual(len(state['signals']),2)
            # Source-only implementation change stays in the same research series.
            p['strategyId']='b';p['strategy']={'config':{'rs':80},'source':{'screening.py':'b'}}
            p['strategySeriesId']=strategy_series_id(p['strategy'])
            process(p,state,root)
            self.assertEqual(len(state['signals']),2)
            # A numerical strategy change intentionally starts a new series.
            p['strategyId']='c';p['strategy']={'config':{'rs':90},'source':{'screening.py':'b'}}
            p['strategySeriesId']=strategy_series_id(p['strategy'])
            process(p,state,root)
            self.assertEqual(len(state['signals']),3)
if __name__=='__main__': unittest.main()
