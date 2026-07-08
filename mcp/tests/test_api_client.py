import unittest

import httpx

from iot_mcp.api_client import IoTAPIClient, IoTAPIError, unpack_results
from iot_mcp.config import Settings


def make_settings(**overrides):
    values = {
        "api_base_url": "http://iot.test/api",
        "api_username": "agent",
        "api_password": "secret",
        "api_access_token": None,
        "confirmation_secret": "confirmation-secret",
        "confirmation_ttl_seconds": 120,
        "request_timeout_seconds": 5,
        "host": "127.0.0.1",
        "port": 8000,
        "transport": "stdio",
    }
    values.update(overrides)
    return Settings(**values)


class IoTAPIClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_logs_in_and_sends_bearer_token(self):
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/api/auth/login/":
                return httpx.Response(200, json={"access": "access-1", "refresh": "refresh-1"})
            self.assertEqual(request.headers["Authorization"], "Bearer access-1")
            return httpx.Response(200, json={"results": [{"sensor_id": "s1"}]})

        client = IoTAPIClient(make_settings(), transport=httpx.MockTransport(handler))
        try:
            result = await client.get("/sensors/")
        finally:
            await client.close()

        self.assertEqual(unpack_results(result), [{"sensor_id": "s1"}])
        self.assertEqual(len(requests), 2)

    async def test_api_error_is_structured(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"detail": "资源不存在"})

        client = IoTAPIClient(
            make_settings(api_access_token="token"),
            transport=httpx.MockTransport(handler),
        )
        try:
            with self.assertRaises(IoTAPIError) as caught:
                await client.get("/sensors/missing/")
        finally:
            await client.close()

        self.assertEqual(caught.exception.code, "not_found")
        self.assertEqual(caught.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
