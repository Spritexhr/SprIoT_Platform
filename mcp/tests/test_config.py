import os
import unittest
from unittest.mock import patch

from iot_mcp.config import Settings


class SettingsTests(unittest.TestCase):
    def test_http_transport_requires_confirmation_secret(self):
        env = {
            "IOT_MCP_TRANSPORT": "streamable-http",
            "IOT_MCP_CONFIRMATION_SECRET": "",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ValueError, "必须设置"):
                Settings.from_env()

    def test_stdio_can_use_ephemeral_confirmation_secret(self):
        with patch.dict(os.environ, {"IOT_MCP_TRANSPORT": "stdio"}, clear=True):
            settings = Settings.from_env()
        self.assertTrue(settings.confirmation_secret)


if __name__ == "__main__":
    unittest.main()
