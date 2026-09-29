from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openevent.cmd_sdk.dependencies import require_sdk
from openevent.cmd_sdk.model import CmdOutputReadRequest, CmdRunRequest
from openevent.cmd_worker.config import ConfigError, CmdWorkerConfig, OpenEventConfig, parse_config
from openevent.cmd_worker.publisher import PublishFatal
from openevent.cmd_worker.worker import CmdWorker, OutputReadEvent, RunEvent


class RecordingClient:
    def __init__(self):
        self.payloads = []

    def get_uuid(self):
        return 1

    def publish_auto_seq(self, *, payload, uuid, **kwargs):
        self.payloads.append(payload)
        return SimpleNamespace(seq=2)


class ResultLimitsTest(unittest.TestCase):
    def test_config_rejects_insufficient_result_budget(self):
        data = dict(principal=1, token='token', channel_ids=[1])
        for size in (1, 512, 16383):
            with self.subTest(size=size), self.assertRaises(ConfigError):
                parse_config(dict(data, open_event=dict(addr='localhost:9527', max_payload_bytes=size)))
        config = parse_config(dict(data, open_event=dict(addr='localhost:9527', max_payload_bytes=16384)))
        self.assertEqual(config.open_event.max_payload_bytes, 16384)

    def test_minimum_budget_fits_escaped_short_outputs_and_bounded_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = 2**64 - 1
            channel_dir = root / str(value)
            channel_dir.mkdir()
            for stream in ('stdout', 'stderr'):
                (channel_dir / f'{value}.{stream}').write_bytes(b'\x00' * 1024)
            client = RecordingClient()
            worker = CmdWorker(CmdWorkerConfig(
                open_event=OpenEventConfig('localhost:9527', 16384),
                principal=1, token='token', channel_ids=(value,), output_dir=root,
            ), client)
            event = RunEvent(value, value, 1, 1000, CmdRunRequest(command='true', timeout_ms=value, ts_ms=1000))
            worker.requests[(value, value)] = event
            worker._publish_run_result(event, 'RECOVERY_FAILED', timeout_ms=value, error_message='\x01' * 20000)
            payload = client.payloads[0]
            self.assertLessEqual(len(payload), 16384)
            data = json.loads(payload)
            self.assertEqual(data['stdout']['content'], '\x00' * 1024)
            self.assertEqual(data['stderr']['content'], '\x00' * 1024)
            self.assertEqual(len(data['error_message'].encode('utf-8')), 512)
            self.assertFalse(worker.requests)

    def test_output_envelope_too_large_never_returns_unsendable_empty_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '1').mkdir()
            (root / '1/1.stdout').write_bytes(b'x')
            worker = CmdWorker(CmdWorkerConfig(
                open_event=OpenEventConfig('localhost:9527', 150),
                principal=1, token='token', channel_ids=(1,), output_dir=root,
            ), RecordingClient())
            event = OutputReadEvent(2, 1, 1, CmdOutputReadRequest(target_seq=1, stream='stdout', offset=0, nbytes=1, ts_ms=1))
            with self.assertRaises(PublishFatal):
                worker._build_output_read_result(event)

    def test_installed_sdk_version_uses_packaging_semantics(self):
        for version in ('0.8.1', '0.11.0', '0.11.1rc1'):
            with self.subTest(version=version), patch('openevent.cmd_sdk.dependencies.version', return_value=version):
                with self.assertRaisesRegex(RuntimeError, 'openevent-sdk>=0.11.1'):
                    require_sdk()
        for version in ('0.11.1', '0.11.1+local', '0.12.0'):
            with self.subTest(version=version), patch('openevent.cmd_sdk.dependencies.version', return_value=version):
                require_sdk()


if __name__ == '__main__':
    unittest.main()
