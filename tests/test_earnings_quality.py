import unittest
from earnings_quality import assess,follow
class QualityTests(unittest.TestCase):
 def payload(self):
  qs=[]
  for y in (2025,2026):
   for q in range(1,5):
    revenue,profit=(100,20) if y==2025 else (120,25)
    qs.append(dict(year=y,quarter=q,basis='CFS',revenue=revenue,operatingProfit=profit,operatingMargin=profit/revenue*100,operatingCashFlow=q*10,equity=100))
  return {'market':'kr','industryCode':'26','checkedAt':'2027-02-01T00:00:00+00:00','quarters':qs}
 def test_pass_and_cumulative_cash(self):
  a=assess(self.payload(),'2027-02-01')
  self.assertEqual(a['status'],'pass')
  self.assertEqual(next(c for c in a['checks'] if c['key']=='cashFlow')['values'],[40])
 def test_missing_and_sector(self):
  d=self.payload();d['quarters'][-1]['equity']=None
  self.assertEqual(assess(d,'2027-02-01')['status'],'hold')
  d=self.payload();d['industryCode']='64'
  self.assertEqual(assess(d,'2027-02-01')['status'],'hold')
 def test_turnaround(self):
  d=self.payload();d['quarters'][-5]['operatingProfit']=-1
  self.assertTrue(assess(d,'2027-02-01')['turnaround'])
 def test_future_baseline_and_missing_target(self):
  s={'observationDate':'2026-01-01'};b={'2026-01-01':10,'2026-01-02':20,'2026-01-05':22}
  self.assertEqual(follow(s,b,b,1)['returnPct'],10.000000000000009)
  self.assertEqual(follow(s,{'2026-01-02':20},b,1)['status'],'unavailable')
  self.assertEqual(follow(s,b,b,20)['status'],'pending')
if __name__=='__main__':unittest.main()
