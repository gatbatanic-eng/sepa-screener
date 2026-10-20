import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

try:
    import requests
except ModuleNotFoundError:
    sys.modules['requests'] = Mock()

from nhplug import kr_extra, probe


class IntegrityTest(unittest.TestCase):
    def test_failed_sector_collection_preserves_previous_generation(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            out = Path(tmp) / 'kr_extra.json'
            out.write_text('last good metadata')
            with patch.object(kr_extra, 'OUT', out), patch.object(kr_extra, 'universe', return_value=[('007810', '1')]), \
                 patch.object(kr_extra, 'kr_period', return_value={'http': 200, 'data': {}}):
                self.assertEqual(kr_extra.run_full('token'), 1)
            self.assertEqual(out.read_text(), 'last good metadata')

    def test_http_200_business_error_does_not_become_empty_rows(self):
        with self.assertRaisesRegex(RuntimeError, 'missing Output_0'):
            probe.rows({'http': 200, 'data': {'message': {'msg_code': 'FAIL'}}}, 'Output_0')

    def test_failed_collection_preserves_last_good_generation_and_history(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            out = Path(tmp) / 'kr_flow.json'
            original = '{"stocks":{"007810":{"asOf":"20261019","days":20}}}'
            out.write_text(original)
            store = Mock()
            store.merge.return_value = 0
            with patch.object(kr_extra, 'FLOW_OUT', out), patch.object(kr_extra, 'universe', return_value=[('007810', '1')]), \
                 patch.object(kr_extra, 'investor', side_effect=RuntimeError('missing output')), \
                 patch('nhplug.flowstore.FlowStore', return_value=store):
                self.assertEqual(kr_extra.run_flow('token'), 1)
            self.assertEqual(out.read_text(), original)
            store.save.assert_not_called()
            self.assertTrue(json.loads((Path(tmp) / 'flow_status.json').read_text())['previousRetained'])


if __name__ == '__main__':
    unittest.main()

