import unittest
from unittest.mock import patch

from iot_mcp import server
from iot_mcp.confirmation import ConfirmationStore


class FakeClient:
    def __init__(self):
        self.posts = []

    async def get(self, endpoint, *, params=None):
        if endpoint == "/devices/device-1/":
            return {
                "device_id": "device-1",
                "name": "测试继电器",
                "is_online": True,
                "device_type_info": {
                    "name": "继电器",
                    "commands": {
                        "set_pin": {
                            "description": "设置引脚",
                            "params": ["pin", "value"],
                            "mqtt_message": {"command": "set_pin"},
                        }
                    },
                },
            }
        raise AssertionError(f"unexpected GET {endpoint}")

    async def post(self, endpoint, *, data):
        self.posts.append((endpoint, data))
        return {"success": True, "command": data["command_name"]}


class ServerToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_requires_preview_and_token_is_single_use(self):
        fake = FakeClient()
        store = ConfirmationStore("test-confirmation-secret")
        with patch.object(server, "client", fake), patch.object(
            server, "confirmations", store
        ):
            preview = await server.preview_iot_command(
                kind="device",
                asset_id="device-1",
                command_name="set_pin",
                params={"pin": "D5", "value": 1},
            )
            self.assertTrue(preview["ok"])
            self.assertNotIn(
                "mqtt_message", preview["data"], "不得向 Agent 暴露底层 MQTT 模板"
            )

            token = preview["data"]["confirmation_token"]
            executed = await server.execute_iot_command(token)
            repeated = await server.execute_iot_command(token)

        self.assertTrue(executed["ok"])
        self.assertEqual(executed["data"]["status"], "mqtt_published")
        self.assertFalse(repeated["ok"])
        self.assertEqual(repeated["error"]["code"], "invalid_confirmation")
        self.assertEqual(fake.posts[0][0], "/devices/device-1/command/")

    async def test_unknown_command_parameter_is_rejected(self):
        with patch.object(server, "client", FakeClient()):
            result = await server.preview_iot_command(
                kind="device",
                asset_id="device-1",
                command_name="set_pin",
                params={"pin": "D5", "value": 1, "raw_topic": "forbidden"},
            )

        self.assertFalse(result["ok"])
        self.assertIn("未定义参数", result["error"]["message"])


if __name__ == "__main__":
    unittest.main()
