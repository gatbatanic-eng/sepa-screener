import unittest
import pandas as pd
from entry_lab.strategies import evaluate
from entry_lab.execution import simulate, portfolio


class EntryLabTests(unittest.TestCase):
    def frame(self, n=80):
        ix=pd.bdate_range("2026-01-01",periods=n)
        return pd.DataFrame(dict(Open=[100.]*n,High=[102.]*n,Low=[99.]*n,
                                 Close=[101.]*n,Volume=[1000.]*n),index=ix)

    def test_future_append_invariant(self):
        f=self.frame(); b=f.Close.copy(); day=str(f.index[69])[:10]
        self.assertEqual(evaluate(f,b,as_of=day),evaluate(f.iloc[:70],b.iloc[:70],as_of=day))

    def test_stale_missing_duplicate_nonfinite(self):
        f=self.frame(); day=str(f.index[-1])[:10]
        for x,b,d in [(f,f.Close,"2026-12-31"),(f,f.Close.iloc[:-1],day),
                      (pd.concat([f,f.tail(1)]),f.Close,day),
                      (f.assign(Volume=float("nan")),f.Close,day)]:
            self.assertEqual(evaluate(x,b,as_of=d)["verdict"],"DEFER")

    def test_catalyst_is_not_inferred_from_price(self):
        f=self.frame(); x=evaluate(f,f.Close,as_of=str(f.index[-1])[:10],strategy="catalyst")
        self.assertIn("dated official catalyst evidence missing",x["reasons"])
        self.assertFalse(x["productionEligible"])

    def test_no_invented_target(self):
        f=self.frame(); x=evaluate(f,f.Close,as_of=str(f.index[-1])[:10])
        self.assertEqual(x["target1"],102)
        self.assertIn("observed resistance lacks 2R room",x["reasons"])

    def test_structural_positive_and_too_tight_stop(self):
        f=self.frame();f.iloc[20]=[100,130,99,101,1000]
        f.iloc[-6:-1]=[102,103,101,102,700]
        f.iloc[-1]=[104,106,103,105,2000]
        b=pd.Series(100.,index=f.index)
        x=evaluate(f,b,as_of=str(f.index[-1])[:10])
        self.assertEqual(x["verdict"],"CANDIDATE")
        self.assertGreaterEqual(x["rr"],2)
        self.assertLess(x["stop"],101)
        self.assertFalse(x["productionEligible"])

    def test_cash_account_mdd_and_size(self):
        f=self.frame(5)
        signal=self.order(f);x=simulate(f,signal,cost_bp=10,hold=3)
        p=portfolio([dict(symbol="X",execution=x)],{"X":f},f.index)
        self.assertAlmostEqual(p["endingEquity"],95+5*x["exit"]/x["entry"])
        self.assertLess(p["mddPct"],0)

    def order(self,f):
        return dict(signalDate=str(f.index[0])[:10],entry=101,pivot=101,stop=96,target1=112)

    def test_next_open_same_bar_stop(self):
        f=self.frame(5); f.iloc[1]=[101,115,95,110,1000]
        x=simulate(f,self.order(f));self.assertEqual(x["reason"],"STOP")
        self.assertEqual(x["rawEntry"],101)
        self.assertLess(x["returnPct"],-4.9)
        self.assertLess(x["mfeClosePct"],1)

    def test_gap_stop_and_open_target_order(self):
        f=self.frame(5); f.iloc[2]=[94,101,93,99,1000]
        x=simulate(f,self.order(f));self.assertEqual(x["exit"],94*.999)
        f.iloc[2]=[113,115,94,99,1000]
        self.assertEqual(simulate(f,self.order(f))["reason"],"GAP_TARGET")

    def test_gap_chase_and_rr_recheck(self):
        f=self.frame(5); f.iloc[1]=[107,108,100,107,1000]
        self.assertEqual(simulate(f,self.order(f))["status"],"SKIP")
        f.iloc[1]=[103,104,100,103,1000]
        self.assertEqual(simulate(f,self.order(f))["reason"],"next-open resistance below 2R")

    def test_no_same_close_fill_pending_and_cost(self):
        f=self.frame(1); self.assertEqual(simulate(f,self.order(f))["status"],"PENDING")
        f=self.frame(5)
        a=simulate(f,self.order(f),cost_bp=0,hold=3)
        b=simulate(f,self.order(f),cost_bp=25,hold=3)
        self.assertLess(b["returnPct"],a["returnPct"])

    def test_invalid_execution_evidence_and_policy(self):
        f=self.frame(5);s=self.order(f)
        self.assertEqual(simulate(f,dict(s,entry=200))["status"],"SKIP")
        self.assertEqual(simulate(f.iloc[::-1],s)["status"],"SKIP")
        with self.assertRaises(ValueError):simulate(f,s,cost_bp=-10)
        with self.assertRaises(ValueError):simulate(f,s,hold=0)
        f.iloc[2]=[100,98,99,101,1000]
        self.assertEqual(simulate(f,s)["status"],"UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
