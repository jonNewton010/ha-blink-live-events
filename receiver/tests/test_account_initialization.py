import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from receiver.blink_live_events.blink_account import associate_fcm_token, BlinkAccountError
from receiver.blink_live_events.state import atomic_json, read_json


class AccountInitializationTests(unittest.TestCase):
    def prepare(self, directory):
        data_dir = Path(directory)
        atomic_json(data_dir / 'fcm-state.json', {'status': 'ready', 'fcm_token': 'test-fcm'})
        atomic_json(data_dir / 'blink-auth.json', {'access_token': 'test-access',
            'refresh_token': 'test-refresh', 'expires_at': time.time() + 3600,
            'oauth_client_id': 'android', 'tier': 'e001', 'account_id': '123'})
        return data_dir

    def test_verification_requirement_stops_before_client_update(self):
        for flag, reason in (('client_verification_required', 'client_verification_required'),
                             ('phone_verification_required', 'account_verification_required'),
                             ('account_verification_required', 'account_verification_required')):
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as directory:
                data_dir = self.prepare(directory)
                with patch('receiver.blink_live_events.blink_account._request',
                           return_value=(200, {}, json.dumps({flag: True}).encode())) as request:
                    with self.assertRaisesRegex(BlinkAccountError, reason):
                        associate_fcm_token(data_dir)
                self.assertEqual(request.call_count, 1)
                self.assertEqual(read_json(data_dir / 'blink-auth.json')['access_token'], 'test-access')

    def test_client_details_survive_failed_association(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = self.prepare(directory)
            replies = [(200, {}, b'{"client_id": 456}'), (200, {'Client-Id': 'opaque-header'}, b'{}'),
                       (404, {}, b'{"response_format":"json","provider_code":1624}')]
            with patch('receiver.blink_live_events.blink_account._request', side_effect=replies):
                with self.assertRaisesRegex(BlinkAccountError, 'provider_code=1624'):
                    associate_fcm_token(data_dir)
            state = read_json(data_dir / 'blink-auth.json')
            self.assertEqual(state['client_id'], '456')
            self.assertEqual(state['account_id'], '123')
            self.assertEqual((data_dir / 'blink-auth.json').stat().st_mode & 0o777, 0o600)

    def test_failed_account_lookup_stops_before_client_update(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = self.prepare(directory)
            with patch('receiver.blink_live_events.blink_account._request', return_value=(401, {}, b'')) as request:
                with self.assertRaisesRegex(BlinkAccountError, 'account_info: http_401'):
                    associate_fcm_token(data_dir)
            self.assertEqual(request.call_count, 1)

    def test_update_uses_account_client_id_and_repairs_saved_header_value(self):
        for home_headers in ({'Client-Id': 'opaque-header'}, {'Client-Id': '999'}, {}):
            with self.subTest(headers=home_headers), tempfile.TemporaryDirectory() as directory:
                data_dir = self.prepare(directory)
                state = read_json(data_dir / 'blink-auth.json')
                state['client_id'] = 'old-opaque-header'
                atomic_json(data_dir / 'blink-auth.json', state)
                replies = [(200, {}, b'{"client_id": 456}'), (200, home_headers, b'{}'), (200, {}, b'{}')]
                with patch('receiver.blink_live_events.blink_account._request', side_effect=replies) as request:
                    associate_fcm_token(data_dir)
                update = request.call_args_list[2]
                self.assertEqual(update.args[1], 'https://rest-e001.immedia-semi.com/clients/456/update')
                self.assertEqual(update.kwargs['headers']['Client-Id'], '456')
                self.assertEqual(read_json(data_dir / 'blink-auth.json')['client_id'], '456')
                self.assertEqual(read_json(data_dir / 'blink-auth.json')['access_token'], 'test-access')

    def test_invalid_account_client_id_stops_before_update(self):
        for value in (None, '', 'opaque-header', 0, -1, True, 456.0, '1/other', '123?private=value'):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                data_dir = self.prepare(directory)
                response = json.dumps({'client_id': value}).encode()
                with patch('receiver.blink_live_events.blink_account._request', return_value=(200, {}, response)) as request:
                    with self.assertRaisesRegex(BlinkAccountError, 'account_client_id_invalid'):
                        associate_fcm_token(data_dir)
                self.assertEqual(request.call_count, 1)
