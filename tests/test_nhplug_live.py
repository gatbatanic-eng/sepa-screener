import datetime as dt
import unittest
from nhplug import live


class LiveTest(unittest.TestCase):
    def test_order_path_refused_before_auth(self):
        with self.assertRaises(ValueError):
            live.Client().call('/krstock/order/v1/cashBuy', {})

    def test_error_envelope_is_not_empty_success(self):
        with self.assertRaisesRegex(live.DataError, 'Missing Output_0'):
            live.normalize('007810', {'message': {'msg_code': 'E123', 'usr_msg': 'denied'}})

    def test_fresh_quote_and_base_mismatch(self):
        now = dt.datetime(2026, 10, 20, 10, 0, tzinfo=live.KST)
        raw = {'Output_0': {'stck_prpr': 110, 'stck_prdy_clpr': 100, 'prdy_ctrt': 10,
            'hoga_bsop_hour': '09595900', 'stck_hgpr': 111, 'stck_lwpr': 99,
            'acml_vol': 100, 'acml_tr_pbmn': 10800}}
        q = live.normalize('007810', raw, now)
        self.assertEqual(q['warnings'], [])
        self.assertEqual(q['vwap'], 108)
        self.assertFalse(q['quoteDateVerified'])
        raw['Output_0']['prdy_ctrt'] = 20
        self.assertIn('return_base_mismatch', live.normalize('007810', raw, now)['warnings'])

    def test_stale_and_unknown_time_excluded_from_candidates(self):
        now = dt.datetime(2026, 10, 20, 10, 0, tzinfo=live.KST)
        raw = {'Output_0': {'stck_prpr': 110, 'prdy_ctrt': 10, 'hoga_bsop_hour': '090000',
            'stck_hgpr': 110, 'stck_lwpr': 100, 'acml_tr_pbmn': 1e10}}
        q = live.normalize('007810', raw, now)
        self.assertIn('quote_time_stale_or_future', q['warnings'])
        self.assertEqual(live.summarize([q])['observationCandidates'], [])


if __name__ == '__main__':
    unittest.main()

