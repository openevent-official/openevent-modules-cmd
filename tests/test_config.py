from __future__ import annotations

import unittest

from openevent.cmd_worker.config import ConfigError, parse_config


class ConfigTest(unittest.TestCase):
    def test_parse_config_defaults(self) -> None:
        config = parse_config(
            {
                "protocol": "cmd.v1",
                "open_event": {"addr": "127.0.0.1:9527"},
                "principal": 30001,
                "token": "token",
                "channel_ids": [2001, 2002],
            }
        )
        self.assertEqual(config.open_event.max_payload_bytes, 16777216)
        self.assertEqual(config.channel_ids, (2001, 2002))
        self.assertEqual(str(config.output_dir), "cmd-worker-output")

    def test_reject_duplicate_channels(self) -> None:
        with self.assertRaises(ConfigError):
            parse_config(
                {
                    "protocol": "cmd.v1",
                    "open_event": {"addr": "127.0.0.1:9527"},
                    "principal": 30001,
                    "token": "token",
                    "channel_ids": [2001, 2001],
                }
            )

    def test_reject_unknown_field(self) -> None:
        with self.assertRaises(ConfigError):
            parse_config(
                {
                    "protocol": "cmd.v1",
                    "open_event": {"addr": "127.0.0.1:9527"},
                    "principal": 30001,
                    "token": "token",
                    "channel_ids": [2001],
                    "unexpected": True,
                }
            )


if __name__ == "__main__":
    unittest.main()
