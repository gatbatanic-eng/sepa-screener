import unittest
from publish_us_fundamentals import normalize
class FiscalTests(unittest.TestCase):
 def test_non_calendar_quarters_and_eps(self):
  def f(start,end,val,filed): return {'start':start,'end':end,'val':val,'filed':filed,'form':'10-K','accn':'0000000001-26-000001'}
  data={'cik':1,'facts':{'us-gaap':{
   'Revenues':{'units':{'USD':[f('2024-10-01','2025-06-30',300,'2025-08-01'),f('2024-10-01','2025-09-30',430,'2025-11-01')]}},
   'EarningsPerShareDiluted':{'units':{'USD/shares':[f('2024-10-01','2025-06-30',3,'2025-08-01'),f('2024-10-01','2025-09-30',4.3,'2025-11-01')]}},
   'NetCashProvidedByUsedInOperatingActivities':{'units':{'USD':[f('2024-10-01','2025-06-30',90,'2025-08-01'),f('2024-10-01','2025-09-30',140,'2025-11-01')]}}
  }}}
  q=normalize(data,'2026-09-15')[-1]
  self.assertEqual(q['periodStart'],'2025-07-01')
  self.assertEqual(q['revenue'],130)
  self.assertEqual(q['operatingCashFlow'],50)
  self.assertIsNone(q['eps'])
  self.assertEqual(len(q['provenance']['revenue']['accessions']),2)
  self.assertEqual(normalize(data,'2025-10-01'),[])
if __name__=='__main__': unittest.main()
