import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from receiver.blink_live_events.diagnose import client_diagnostics
from receiver.blink_live_events.state import atomic_json


class ClientDiagnosticTests(unittest.TestCase):
    def test_only_comparisons_are_exposed_and_registration_is_untouched(self):
        for info_id, header_id, matches in ((1234567, '1234567', True), (1234567, '7654321', False), (None, '7654321', None)):
            with self.subTest(matches=matches), tempfile.TemporaryDirectory() as directory:
                state_path = Path(directory) / 'blink-auth.json'
                atomic_json(state_path, {'tier': 'e001', 'account_id': 'private-account',
                    'access_token': 'private-token', 'client_id': '7654321', 'expires_at': time.time() + 3600})
                original = state_path.read_bytes()
                replies = [(200, {}, json.dumps({'client_id': info_id, 'email': 'private@example.com'}).encode()),
                           (200, {'Client-Id': header_id}, b'{}')]
                with patch('receiver.blink_live_events.diagnose._request', side_effect=replies) as request:
                    result = client_diagnostics(Path(directory))
                self.assertIs(result['client_ids_match'], matches)
                self.assertEqual([call.args[0] for call in request.call_args_list], ['GET', 'GET'])
                serialized = json.dumps(result)
                for private in ('private', '1234567', '7654321', '@'):
                    self.assertNotIn(private, serialized)
                self.assertEqual(state_path.read_bytes(), original)
