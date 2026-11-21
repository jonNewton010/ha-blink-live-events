import json
import unittest
from io import BytesIO
from urllib.error import HTTPError
from unittest.mock import patch

from receiver.blink_live_events.blink_account import _safe_error_response, _client_update_error, _request


class ErrorPrivacyTests(unittest.TestCase):
    def test_sensitive_response_fields_and_vendor_text_are_removed(self):
        response = json.dumps({'code': 404, 'message': 'Client not found: private-client-id',
                               'access_token': 'private-token', 'url': 'https://private.example/secret'}).encode()
        safe = _safe_error_response(response)
        self.assertEqual(json.loads(safe), {'response_format': 'json', 'provider_code': 404, 'error_kind': 'client_not_found'})
        self.assertNotIn(b'private', safe)
        self.assertNotIn('private', _client_update_error(404, safe))

    def test_non_numeric_codes_cannot_leak_strings_or_structures(self):
        for code in ('private-token', {'token': 'private-token'}, True, -1, 2147483648):
            safe = _safe_error_response(json.dumps({'code': code}).encode())
            self.assertEqual(json.loads(safe), {'response_format': 'json'})

    def test_non_json_error_bodies_are_not_exposed(self):
        for body in (b'<html>private-token</html>', b'\xff', b''):
            self.assertEqual(json.loads(_safe_error_response(body)), {'response_format': 'non_json'})

    def test_request_returns_only_sanitized_error_and_closes_response(self):
        source = BytesIO(json.dumps({'code': 123, 'message': 'private-secret'}).encode())
        error = HTTPError('https://api.oauth.blink.com/oauth/token', 404, 'not found', {}, source)
        with patch('receiver.blink_live_events.blink_account.urlopen', side_effect=error):
            status, _, response = _request('POST', error.url, headers={})
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(response), {'response_format': 'json', 'provider_code': 123})
        self.assertTrue(source.closed)
