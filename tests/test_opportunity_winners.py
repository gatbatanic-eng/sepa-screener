import unittest
import numpy as np
import pandas as pd
from leader_backtest.opportunity_winners import episodes, label, holm, fit_rule, apply_rule, contrast
from leader_backtest.rules import features

class WinnerStudyTests(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(list(label(pd.Series([10,-5,9.999,-4.999]))),['winner','failure','neutral','neutral'])

    def test_cooldown_original_sessions_and_no_outcome_selection(self):
        p=pd.DataFrame({'code':['a']*4,'session':[0,5,21,42],'opportunity':[True]*4,'excess':[np.nan,10,20,np.nan]})
        self.assertEqual(episodes(p,p.opportunity).session.tolist(),[0,21,42])
        p['excess']=999
        self.assertEqual(episodes(p,p.opportunity).session.tolist(),[0,21,42])

    def test_holm(self):
        np.testing.assert_allclose(holm([.03,.001,.04]),[.06,.003,.06])

    def test_trailing_features_invariant_to_future(self):
        rng=np.random.default_rng(4); c=100*np.exp(np.cumsum(rng.normal(0,.01,400)))
        bars=pd.DataFrame({'close':c,'open':c,'high':c*1.01,'low':c*.99,'volume':rng.integers(100,1000,400)})
        before=features(bars.iloc[:320]); bars.loc[320:,'close']*=5
        pd.testing.assert_frame_equal(before,features(bars).iloc[:320])

    def test_train_only_fit(self):
        x=np.arange(500,dtype=float)
        p=pd.DataFrame({k:x for k in ['rs','rsi','risk','volRatio','pivDist','trend']}); p['excess']=x
        rule=fit_rule(p)
        test=p.copy(); test['excess']=-x
        self.assertTrue(apply_rule(test,rule).equals(apply_rule(p,rule)))
        self.assertEqual(rule['train_n'],125)

    def test_calendar_cluster_bootstrap(self):
        dates=pd.bdate_range('2024-01-01',periods=80)
        p=pd.DataFrame({'date':np.repeat(dates,2),'code':['a','b']*80,'group':['winner','failure']*80,'rs':[90.,70.]*80})
        out=contrast(p,'rs',dates,n=100)
        self.assertEqual(out['difference'],20)
        self.assertEqual(out['ci95'],[20,20])

if __name__=='__main__': unittest.main()
