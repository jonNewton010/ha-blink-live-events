from __future__ import annotations

import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

from receiver.network import urlopen
from receiver.blink_live_events.blink_account import BlinkAccountError, _request


class NetworkPrivacyTests(unittest.TestCase):
    def test_blink_tokens_are_rejected_before_contacting_other_hosts(self):
        destinations = (
            'https://attacker.example/api',
            'https://rest-prod.immedia-semi.com@attacker.example/api',
            'https://rest-prod.immedia-semi.com.attacker.example/api',
            'http://rest-prod.immedia-semi.com/api',
        )
        with patch('receiver.blink_live_events.blink_account.urlopen') as send:
            for destination in destinations:
                with self.subTest(destination=destination):
                    with self.assertRaises(BlinkAccountError):
                        _request('POST', destination, headers={'Authorization': 'Bearer fake-test-token'})
            send.assert_not_called()

    def test_redirect_does_not_forward_credentials_or_webhook_event(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append(self.path)
                self.send_response(302)
                self.send_header('Location', '/unintended-destination')
                self.end_headers()

            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', '0')))
                self.do_GET()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f'http://127.0.0.1:{server.server_port}/intended-destination'
            for body in (None, b'{"camera_name":"test-camera"}'):
                request = Request(url, data=body, headers={'Authorization': 'Bearer fake-test-token'})
                with self.assertRaises(HTTPError) as raised:
                    urlopen(request)
                self.assertEqual(raised.exception.code, 302)
                raised.exception.close()
            self.assertEqual(received, ['/intended-destination', '/intended-destination'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
