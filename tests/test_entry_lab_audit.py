"""Adversarial evidence checks; these do not tune strategy thresholds."""
import tempfile
from pathlib import Path
import unittest
import pandas as pd
import tests.test_entry_lab as fixtures
from entry_lab.strategies import evaluate
from entry_lab.execution import simulate, portfolio
from entry_lab.study import dump


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.EntryLabTests()

    def candidate(self):
        f=self.fixture.frame();f.iloc[20]=[100,130,99,101,1000]
        f.iloc[-6:-1]=[102,103,101,102,700];f.iloc[-1]=[104,106,103,105,2000]
        return f

    def test_nonfinite_benchmark_cannot_create_candidate(self):
        f=self.candidate()
        for value in (float('inf'),float('-inf'),float('nan')):
            with self.subTest(value=value):
                b=pd.Series(value,index=f.index)
                self.assertEqual(evaluate(f,b,as_of=str(f.index[-1])[:10])['verdict'],'DEFER')

    def test_nonfinite_target_is_not_a_fill(self):
        f=self.fixture.frame(5)
        for value in (float('nan'),float('inf'),-1,True):
            with self.subTest(value=value):
                self.assertEqual(simulate(f,dict(self.fixture.order(f),target1=value),hold=3)['status'],'SKIP')

    def test_bad_risk_policy_raises(self):
        f=self.fixture.frame(5)
        for kwargs in (dict(max_risk=float('nan')),dict(max_extension=float('inf')),dict(max_risk=True),dict(max_risk=-1)):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):
                simulate(f,self.fixture.order(f),**kwargs)

    def test_invalid_horizon_is_unavailable_not_nan(self):
        f=self.fixture.frame(8);f.iloc[5,f.columns.get_loc('Close')]=float('nan')
        # A first-session stop means later path invalidity affects only horizon evidence.
        f.iloc[1]=[101,102,95,100,1000]
        e=simulate(f,self.fixture.order(f))
        self.assertEqual(e['status'],'CLOSED')
        self.assertIsNone(e['horizons']['5'])

    def test_atomic_writer_preserves_no_partial_final_on_nan(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'result.json'
            with self.assertRaises(ValueError):dump(p,{'x':float('nan')})
            self.assertFalse(p.exists())
            dump(p,{'x':1})
            with self.assertRaises(FileExistsError):dump(p,{'x':2})
            self.assertEqual(p.read_text(encoding='utf-8'),'{"x":1}')

    def test_portfolio_rejects_invalid_policy_and_unsorted_sessions(self):
        f=self.fixture.frame(5)
        with self.assertRaises(ValueError):portfolio([],{'X':f},f.index,fraction=float('nan'))
        with self.assertRaises(ValueError):portfolio([],{'X':f},f.index[::-1])

    def test_portfolio_missing_mark_and_malformed_execution(self):
        f=self.fixture.frame(8);e=simulate(f,self.fixture.order(f));trades=[dict(symbol='X',execution=e)]
        bad=f.copy();bad.iloc[2,bad.columns.get_loc('Close')]=float('nan')
        self.assertEqual(portfolio(trades,{'X':bad},f.index)['status'],'UNAVAILABLE')
        e=dict(e,entry=float('inf'))
        with self.assertRaises(ValueError):portfolio([dict(symbol='X',execution=e)],{'X':f},f.index)


if __name__=='__main__':unittest.main()
