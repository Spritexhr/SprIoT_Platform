import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

from django.test import SimpleTestCase


class AsgiUrlconfStartupTests(SimpleTestCase):
    """真实子进程验证首个 ASGI HTTP 请求不会丢失插件路由。"""

    def test_data_viz_route_is_loaded_before_first_async_request(self):
        script = textwrap.dedent(
            """
            import asyncio
            from channels.testing import HttpCommunicator
            from config.asgi import application

            async def main():
                communicator = HttpCommunicator(
                    application,
                    "GET",
                    "/api/plugins/data_viz/ping/",
                )
                response = await communicator.get_response(timeout=3)
                # 未携带 JWT 应由 DRF 拒绝；404 表示插件 URLConf 未挂载。
                assert response["status"] == 401, response

            asyncio.run(main())
            """
        )
        env = os.environ.copy()
        env.pop("DB_USE_MYSQL", None)
        env["DJANGO_SETTINGS_MODULE"] = "config.settings"
        env["DEBUG"] = "True"

        with tempfile.TemporaryDirectory() as temp_dir:
            env["SQLITE_DB_PATH"] = str(Path(temp_dir) / "asgi-startup.sqlite3")
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=Path(__file__).resolve().parent.parent,
                env=env,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )

        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
