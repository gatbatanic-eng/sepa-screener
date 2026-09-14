import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from range_screen import indicators, evaluate, wilder
class RangeTests(unittest.TestCase):
    def frame(self):
        dates=pd.bdate_range('2025-01-01',periods=240)
        close=np.full(240,100.)
        return pd.DataFrame({'Open':close,'High':close+1,'Low':close-1,'Close':close,'Volume':np.full(240,1e7)},index=dates)
    def test_wilder_seed(self):
        self.assertEqual(wilder(pd.Series([1.,2.,3.,4.]),3).iloc[2],2)
        self.assertAlmostEqual(wilder(pd.Series([1.,2.,3.,4.]),3).iloc[3],8/3)
    def test_flat_prices_and_unknown_quote(self):
        f=self.frame();d=indicators(f)
        self.assertEqual(d.adx.iloc[-1],0)
        row={'code':'X','name':'X','market':'US','status':'OK','inUniverse':True}
        self.assertEqual(evaluate(row,f,f,'us')['status'],'OK')
        f.loc[f.index[-1],'Close']=np.nan
        self.assertEqual(evaluate(row,f,self.frame(),'us')['status'],'UNKNOWN')
    def test_go_uses_prior_box_and_confirms_recovery(self):
        f=self.frame();f['High']=110.;f['Low']=90.
        f.loc[f.index[-2],['Close','High']]=[92,93]
        f.loc[f.index[-1],['Close','High']]=[94,95]
        d=indicators(f);d['sma200']=80.;d['slope50Pct']=0.;d['adx']=10.;d['atr']=3.
        row={'code':'X','name':'X','market':'US','status':'OK','inUniverse':True}
        with patch('range_screen.indicators',return_value=d):
            result=evaluate(row,f,f,'us')
        self.assertTrue(result['rangeGo'])
        self.assertEqual(result['boxLow'],90)
        self.assertEqual(result['boxHigh'],110)
        d.loc[d.index[-1],'Close']=92
        with patch('range_screen.indicators',return_value=d):
            self.assertFalse(evaluate(row,f,f,'us')['rangeGo'])
        d.loc[d.index[-1],'Close']=94;d.loc[d.index[-1],'adx']=30
        with patch('range_screen.indicators',return_value=d):
            self.assertFalse(evaluate(row,f,f,'us')['rangeGo'])
if __name__=='__main__':unittest.main()
