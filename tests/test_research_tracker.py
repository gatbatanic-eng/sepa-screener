import copy
import tempfile
import unittest
from pathlib import Path
from research_tracker import outcome, process

DATES = ['2026-09-01','2026-09-02','2026-09-03','2026-09-04','2026-09-07','2026-09-08']
class ResearchTests(unittest.TestCase):
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
            p={'market':'us','strategyId':'a','strategy':{},'recordedAt':'now',
               'rows':[{'code':'X','name':'X','market':'US','status':'OK','inUniverse':True,
                        'trendOk':True,'setupReady':False,'entryState':'WAIT','close':100,'priceAsOf':DATES[0]}],
               'prices':{'X':{DATES[0]:100}},'benchmarks':{'US':{DATES[0]:100}}}
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
            p['strategyId']='b';process(p,state,root)
            self.assertEqual(len(state['signals']),3)
if __name__=='__main__': unittest.main()
