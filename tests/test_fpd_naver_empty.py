import unittest
from datetime import date
from fpd.provider_naver import parse_annual

class EmptyConsensusTest(unittest.TestCase):
    def test_null_coverage_preserves_identity_and_schema_checks(self):
        value={'itemCode':'088980','financePeriodType':'annual','financeInfo':None}
        self.assertEqual(parse_annual(value,'088980',date(2026,10,19)),[])
        with self.assertRaises(ValueError):
            parse_annual(value,'005930',date(2026,10,19))
        del value['financeInfo']
        with self.assertRaises(KeyError):
            parse_annual(value,'088980',date(2026,10,19))
