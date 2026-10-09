import unittest
import pandas as pd
from entry_lab.audit import replay_checklist, availability, core_comparison


class ArchiveReplayTests(unittest.TestCase):
    def row(self):
        return dict(code='MRNA',name='Moderna',passAll=True,marketGate='우호적',rsRank=99.8031496063,
                    high52wPosition=-.0247863219,dryupRatio=.7847820625,setupScore=7.65,
                    pivotNear=False,breakoutSignal=False,entryState='TREND_OK',boDaysAgo=None,
                    pivotDist=3.34,entryVerdict='NO-GO',entryChecklistCount=4)

    def test_recorded_full_market_inputs_reproduce_final_verdict(self):
        x=replay_checklist(self.row());self.assertEqual(x['status'],'MATCH')
        self.assertEqual(x['replayedVerdict'],'NO-GO')

    def test_missing_input_is_unavailable_not_false(self):
        row=self.row();del row['rsRank']
        self.assertEqual(replay_checklist(row)['status'],'UNAVAILABLE')

    def test_future_outcomes_do_not_change_archived_replay(self):
        row=self.row();original=replay_checklist(row);row['outcomes']={'5':{'returnPct':99999}}
        self.assertEqual(replay_checklist(row),original)

    def test_late_archiving_cannot_imply_timely_delivery(self):
        f=pd.DataFrame(index=pd.to_datetime(['2026-09-11','2026-09-14','2026-09-15']))
        self.assertEqual(availability('2026-09-15T00:00:00Z','2026-09-11',f)['status'],'LATE')
        self.assertEqual(availability('2026-09-12T00:00:00Z','2026-09-11',f)['status'],'TIMELY')
        with self.assertRaises(ValueError):availability('2026-09-12T00:00:00','2026-09-11',f)

    def test_legacy_and_lagged_v2_dryup_are_separate(self):
        row=dict(entryState='TREND_OK',dryupRatio=.78478,volDryup=.724)
        old=dict(entry_state='TREND_OK',volume_dryup_ratio=.724)
        self.assertEqual(core_comparison(row,old)['storedDryup'],.724)
        self.assertEqual(core_comparison(row,old)['replayedDryup'],.724)


if __name__=='__main__':unittest.main()
