import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

from receiver.blink_live_events.journal import append_private, load_records, delivery_key
from receiver.blink_live_events.ha_delivery import deliver_pending_once, initialize_delivery_checkpoint


def event(number):
    return {'kind': 'fcm_message', 'classification': 'camera_motion',
            'fcm': {'id': str(number)}, 'payload': {'event_type': 'pir', 'camera_name': 'Door'},
            'received_at': str(number)}


class JournalTests(unittest.TestCase):
    def test_compaction_preserves_pending_without_replaying_sent_events(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'messages.ndjson'
            initialize_delivery_checkpoint(path.parent)
            append_private(path, event(1))
            append_private(path, {'kind': 'ha_webhook_sent', 'delivery_key': delivery_key(event(1))})
            append_private(path, event(2))
            with patch('receiver.blink_live_events.journal.MAX_BYTES', path.stat().st_size):
                append_private(path, {'kind': 'blink_ack_sent'})
            self.assertEqual([r['fcm']['id'] for r in load_records(path) if r['kind'] == 'fcm_message'], ['2'])
            received = []
            class Webhook:
                def send(self, value):
                    received.append(value)
            self.assertEqual(deliver_pending_once(path.parent, Webhook()), (1, 0))
            self.assertEqual(deliver_pending_once(path.parent, Webhook()), (0, 0))
            self.assertEqual(len(received), 1)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_existing_initialization_keys_stay_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'messages.ndjson'
            append_private(path, event(1))
            initialize_delivery_checkpoint(path.parent)
            append_private(path, event(2))
            with patch('receiver.blink_live_events.journal.MAX_BYTES', 1):
                append_private(path, {'kind': 'blink_ack_sent'})
            ids = [r['fcm']['id'] for r in load_records(path) if r['kind'] == 'fcm_message']
            self.assertEqual(ids, ['2'])
            self.assertFalse(initialize_delivery_checkpoint(path.parent))

    def test_size_bound_discards_old_pending_and_oversized_messages(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'messages.ndjson'
            initialize_delivery_checkpoint(path.parent)
            with patch('receiver.blink_live_events.journal.MAX_BYTES', 1024), patch('receiver.blink_live_events.journal.TARGET_BYTES', 512):
                for index in range(50):
                    append_private(path, event(index))
                    self.assertLessEqual(path.stat().st_size, 1024)
                large = event(100)
                large['padding'] = 'x' * 2000
                append_private(path, large)
                self.assertLessEqual(path.stat().st_size, 512)
            records = load_records(path)
            self.assertTrue(any(r.get('fcm', {}).get('id') == '49' for r in records))
            self.assertFalse(any(r.get('fcm', {}).get('id') == '0' for r in records))

    def test_parallel_writes_keep_complete_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'messages.ndjson'
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda i: append_private(path, event(i)), range(100)))
            self.assertEqual(len(load_records(path)), 100)
            for line in path.read_text().splitlines():
                self.assertIsInstance(json.loads(line), dict)

    def test_failed_replace_preserves_previous_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'messages.ndjson'
            append_private(path, event(1))
            before = path.read_bytes()
            with patch('receiver.blink_live_events.journal.MAX_BYTES', 1), patch('receiver.blink_live_events.journal.os.replace', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    append_private(path, event(2))
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(path.parent.glob('.journal-*')), [])
