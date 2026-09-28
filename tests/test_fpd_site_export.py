import copy
import gzip
import json
import unittest
from pathlib import Path
from fpd.export_site import build_view

ROOT=Path(__file__).resolve().parents[1]
class SiteExportTest(unittest.TestCase):
    def setUp(self):
        p=ROOT/'docs/data/fpd'
        self.latest=json.loads((p/'latest_us.json').read_text())
        self.derived=json.loads((p/'derived_latest_us.json').read_text())
        self.signal=json.loads((p/'signal_latest_us.json').read_text())
        self.monthly=json.loads((p/'monthly_research_us.json').read_text())
        self.history=[json.load(gzip.open(f)) for f in (ROOT/'research/fpd/raw/us').rglob('*.json.gz')]
        self.raw=next(s for s in self.history if s['datasetId']==self.latest['datasetId'] and s['snapshotDate']==self.latest['snapshotDate'])
    def build(self):
        return build_view(self.latest,self.derived,self.signal,self.raw,self.history,self.monthly)
    def test_exact_fiscal_period_and_observed_values(self):
        view=self.build()
        for row in view['rows']:
            if row['consensusStatus']=='collected':
                obs=next(r for r in self.raw['observations'][row['ticker']] if r['periodEnd']==row['periodEnd'])
                self.assertEqual(row['epsCurrent'],obs['eps']['avg'])
                self.assertEqual(row['revenueCurrent'],obs['revenue']['avg'])
        self.assertEqual(view['rawSnapshots'],sum(s['datasetId']==self.latest['datasetId'] for s in self.history))
    def test_mixed_date_rejected(self):
        self.signal['snapshotDate']='1900-01-01'
        with self.assertRaises(ValueError):self.build()
    def test_cross_dataset_rejected(self):
        self.derived['datasetId']='other'
        with self.assertRaises(ValueError):self.build()
    def test_revision_ratio_and_null_are_preserved(self):
        score=self.signal['rows'][0]
        item=next(r for r in self.derived['symbols'][score['ticker']] if r['periodEnd']==score['periodEnd'])
        item['lookbacks']['7D']['epsRevisionRaw']=0.05
        item['lookbacks']['30D']['epsRevisionRaw']=None
        row=next(r for r in self.build()['rows'] if r['ticker']==score['ticker'])
        self.assertEqual(row['consensusRevisions']['EPS_R7'],0.05)
        self.assertIsNone(row['consensusRevisions']['EPS_R30'])
