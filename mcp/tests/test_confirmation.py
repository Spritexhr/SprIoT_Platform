import time
import unittest
from unittest.mock import patch

from iot_mcp.confirmation import ConfirmationError, ConfirmationStore


class ConfirmationStoreTests(unittest.TestCase):
    def test_token_can_only_be_consumed_once(self):
        store = ConfirmationStore("test-secret", ttl_seconds=60)
        token = store.issue("delete_asset", {"asset_id": "sensor-1"})

        self.assertEqual(
            store.consume(token, "delete_asset"), {"asset_id": "sensor-1"}
        )
        with self.assertRaisesRegex(ConfirmationError, "已经使用"):
            store.consume(token, "delete_asset")

    def test_tampered_token_is_rejected(self):
        store = ConfirmationStore("test-secret")
        token = store.issue("execute_command", {"command": "on"})
        body, signature = token.split(".")
        tampered = f"{body[:-1]}A.{signature}"

        with self.assertRaises(ConfirmationError):
            store.consume(tampered, "execute_command")

    def test_expired_token_is_rejected(self):
        store = ConfirmationStore("test-secret", ttl_seconds=1)
        with patch("iot_mcp.confirmation.time.time", return_value=100):
            token = store.issue("delete_asset", {"asset_id": "sensor-1"})
        with patch("iot_mcp.confirmation.time.time", return_value=102):
            with self.assertRaisesRegex(ConfirmationError, "已过期"):
                store.consume(token, "delete_asset")


if __name__ == "__main__":
    unittest.main()
