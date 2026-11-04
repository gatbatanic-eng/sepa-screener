import unittest
import subprocess
from unittest.mock import patch
from datetime import datetime, timezone
from collection_health import route, latest_closed_session, caught_up, apply_floor, weekday_floor

class CollectionHealthTests(unittest.TestCase):
    def test_source_provenance_uses_checkout_not_delayed_event(self):
        from research_tracker import source_commit
        with patch('research_tracker.subprocess.check_output', return_value='a'*40+'\n'), patch.dict('os.environ', {'GITHUB_SHA':'b'*40}):
            self.assertEqual(source_commit(), 'a'*40)
        with patch('research_tracker.subprocess.check_output', side_effect=subprocess.CalledProcessError(1, 'git')), patch.dict('os.environ', {'GITHUB_SHA':'b'*40}):
            self.assertIsNone(source_commit())
    def test_delayed_us_schedule_catches_kr_without_guessing_calendar(self):
        plan = route('schedule', '7 5 * * 2-6', '', '', {'KR':'2026-10-06','US':'2026-10-05'}, {'KR':'2026-10-02','US':'2026-10-05'})
        self.assertEqual(plan['markets'], ['KR'])
    def test_closed_holiday_and_fresh_backup_skip_unchanged_sessions(self):
        plan=route('schedule','43 11 * * 1-5','','',{'KR':'2026-10-02','US':'2026-10-02'},{'KR':'2026-10-02','US':'2026-10-02'})
        self.assertFalse(plan['needed'])
    def test_source_failure_is_not_a_holiday_and_unknown_cron_is_not_us(self):
        self.assertTrue(route('schedule','7 11 * * 1-5','','',{'KR':None,'US':'2026-10-05'},{'US':'2026-10-05'})['needed'])
        with self.assertRaises(ValueError):route('schedule','wrong','','',{}, {})
    def test_saved_session_ahead_of_lagging_index_is_caught_up(self):
        self.assertTrue(caught_up('2026-10-27', '2026-10-26'))
        self.assertTrue(caught_up('2026-10-26', '2026-10-26'))
        self.assertFalse(caught_up('2026-10-23', '2026-10-26'))
        self.assertFalse(caught_up(None, '2026-10-26'))
        plan = route('schedule', '7 11 * * 1-5', '', '', {'KR': '2026-10-26', 'US': '2026-10-26'}, {'KR': '2026-10-27', 'US': '2026-10-26'})
        self.assertFalse(plan['needed'])
    def test_manual_market_is_preserved(self):
        self.assertEqual(route('workflow_dispatch','','KR','',{}, {})['markets'],['KR'])
        self.assertEqual(route('push','','','fix [run-kr]',{}, {})['markets'],['KR'])
    def test_holiday_restart_and_intraday_exclusion(self):
        stamps=[int(datetime(2026,10,d,tzinfo=timezone.utc).timestamp()) for d in (2,6)]
        payload={'chart':{'result':[{'timestamp':stamps,'indicators':{'quote':[{'close':[100,101]}]}}]}}
        self.assertEqual(latest_closed_session(payload,'KR',datetime(2026,10,6,5,tzinfo=timezone.utc)),'2026-10-02')
        self.assertEqual(latest_closed_session(payload,'KR',datetime(2026,10,6,11,tzinfo=timezone.utc)),'2026-10-06')
        self.assertEqual(latest_closed_session(payload,'US',datetime(2026,10,6,11,tzinfo=timezone.utc)),'2026-10-05')
    def test_lagging_index_is_raised_to_last_closed_weekday(self):
        late = datetime(2026,11,2,20,43,tzinfo=timezone.utc)  # 11/3 05:43 KST
        self.assertEqual(weekday_floor('KR', late), '2026-11-02')
        self.assertEqual(apply_floor('2026-10-30', 'KR', late), '2026-11-02')
        self.assertEqual(apply_floor('2026-11-02', 'KR', late), '2026-11-02')
        self.assertIsNone(apply_floor(None, 'KR', late))
        sat = datetime(2026,10,31,11,tzinfo=timezone.utc)
        self.assertEqual(apply_floor('2026-10-30', 'KR', sat), '2026-10-30')
        self.assertEqual(weekday_floor('US', datetime(2026,11,2,20,53,tzinfo=timezone.utc)), '2026-10-30')

if __name__ == '__main__':unittest.main()
