import unittest
from entry_lab.event_risk import review


class EventRiskTests(unittest.TestCase):
    sessions=['2026-08-03','2026-08-04','2026-08-05','2026-08-06','2026-08-07','2026-08-10']
    def event(self,**kwargs):
        return dict(symbol='DDOG',verified=True,source='https://investors.datadoghq.com/node/17386/pdf',
                    publishedAt='2026-07-16T16:05:00-04:00',eventDate='2026-08-06',type='earnings',**kwargs)

    def test_prior_announcement_blocks_but_not_signal_detection(self):
        x=review('DDOG','2026-08-04T20:10:00Z',self.sessions,[self.event()])
        self.assertEqual(x['verdict'],'BLOCK');self.assertFalse(x['productionEligible'])
        self.assertEqual(x['knownEvents'][0]['sessionsFromEntry'],1)

    def test_future_announcement_does_not_block_retroactively(self):
        event=self.event();event['publishedAt']='2026-08-06T06:00:00-04:00'
        x=review('DDOG','2026-08-04T20:10:00Z',self.sessions,[event])
        self.assertEqual(x['verdict'],'DEFER');self.assertEqual(x['excludedEventCount'],1)

    def test_missing_coverage_does_not_clear(self):
        self.assertEqual(review('DDOG','2026-08-04T20:10:00Z',self.sessions,[])['verdict'],'DEFER')

    def test_foreign_symbol_bad_source_and_unverified_do_not_clear(self):
        for patch in (dict(symbol='AAPL'),dict(source='http://example.com'),dict(verified=False)):
            event=self.event();event.update(patch)
            with self.subTest(patch=patch):
                self.assertEqual(review('DDOG','2026-08-04T20:10:00Z',self.sessions,[event])['verdict'],'DEFER')

    def test_covered_clear_remains_research_only(self):
        cov=dict(symbol='DDOG',verified=True,complete=True,source='https://example.com/calendar',
                 publishedAt='2026-08-01T12:00:00Z',fromDate='2026-08-04',throughDate='2026-08-10')
        x=review('DDOG','2026-08-04T20:10:00Z',self.sessions,[],cov)
        self.assertEqual(x['verdict'],'CLEAR_REVIEW');self.assertFalse(x['productionEligible'])
        cov['publishedAt']='2026-08-05T12:00:00Z'
        self.assertEqual(review('DDOG','2026-08-04T20:10:00Z',self.sessions,[],cov)['verdict'],'DEFER')

    def test_bad_timezone_calendar_and_missing_next_session(self):
        with self.assertRaises(ValueError):review('DDOG','2026-08-04',self.sessions,[])
        with self.assertRaises(ValueError):review('DDOG','2026-08-04T20:10:00Z',self.sessions[::-1],[])
        self.assertEqual(review('DDOG','2026-08-10T20:10:00Z',self.sessions,[])['verdict'],'DEFER')

    def test_before_open_can_use_same_session_open(self):
        x=review('DDOG','2026-08-05T12:00:00Z',self.sessions,[self.event()])
        self.assertEqual(x['nextSession'],'2026-08-05')
        self.assertEqual(x['verdict'],'BLOCK')


if __name__=='__main__':unittest.main()
