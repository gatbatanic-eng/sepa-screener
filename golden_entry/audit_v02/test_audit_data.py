import unittest
import tempfile
from pathlib import Path
import pandas as pd
from audit_data import row_flags, calendar_differences, zero_runs, verify_sources, terminal_gap


class IntegrityTests(unittest.TestCase):
    def frame(self):
        return pd.DataFrame({'ticker':['X']*4,'market':['KOSPI']*4,
            'Date':pd.to_datetime(['2020-01-02','2020-01-03','2020-01-06','2020-01-07']),
            'Open':[100.]*4,'High':[102.]*4,'Low':[99.]*4,'Close':[101.]*4,'Volume':[1000.]*4})

    def test_missing_duplicate_cancellation_detected(self):
        x=self.frame();cal={'KOSPI':pd.DatetimeIndex(x.Date)}
        y=x.iloc[[0,0,2,3]].reset_index(drop=True)
        self.assertEqual(len(y),len(x))
        d=calendar_differences(y,cal)
        self.assertEqual(d.issue.tolist(),['MISSING_IN_OBSERVED_SPAN'])
        self.assertEqual(d.Date.iloc[0],x.Date.iloc[1])

    def test_halt_preserves_calendar_and_is_not_tradeable(self):
        x=self.frame();x.loc[1,['Open','High','Low','Volume']]=0
        f,_=row_flags(x)
        self.assertTrue(f.halt_pattern.iloc[1]);self.assertFalse(f.mechanically_valid_bar.iloc[1])
        self.assertTrue(calendar_differences(x,{'KOSPI':pd.DatetimeIndex(x.Date)}).empty)

    def test_missing_close_not_forward_filled(self):
        x=self.frame();x.loc[1,'Close']=float('nan');x.loc[2,'Close']=200
        f,r=row_flags(x);self.assertTrue(f.nonfinite.iloc[1]);self.assertTrue(pd.isna(r.iloc[2]))

    def test_invalid_ohlc_does_not_pass(self):
        x=self.frame();x.loc[2,'High']=80
        f,_=row_flags(x);self.assertTrue(f.ohlc_inconsistent.iloc[2]);self.assertFalse(f.mechanically_valid_bar.iloc[2])

    def test_runs_do_not_cross_symbols(self):
        x=self.frame();x['ticker']=['X','X','Y','Y'];x.Volume=0
        r=zero_runs(x,x.Volume.eq(0));self.assertEqual(r.rows.tolist(),[2,2])

    def test_noncalendar_date_explicit(self):
        x=self.frame();x.loc[1,'Date']=pd.Timestamp('2020-01-04')
        cal={'KOSPI':pd.DatetimeIndex(self.frame().Date)}
        issues=calendar_differences(x,cal)
        self.assertEqual(set(issues.issue),{'MISSING_IN_OBSERVED_SPAN','OUTSIDE_INDEX_CALENDAR'})

    def test_source_bytes_must_match(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);(p/'listing.parquet').write_bytes(b'changed')
            tree={'truncated':False,'tree':[{'path':'golden_entry/data_store/listing.parquet',
                 'type':'blob','size':7,'sha':'wrong'}]}
            with self.assertRaisesRegex(ValueError,'source mismatch'):verify_sources(p,tree)

    def test_delisting_day_not_false_missing_session(self):
        cal=pd.DatetimeIndex(self.frame().Date)
        self.assertEqual(terminal_gap(cal,cal[-2],cal[-1]),0)
        self.assertEqual(terminal_gap(cal,cal[-3],cal[-1]),1)


if __name__=='__main__':unittest.main()
