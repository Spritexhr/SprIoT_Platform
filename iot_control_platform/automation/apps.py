import os
import sys

from django.apps import AppConfig


class AutomationConfig(AppConfig):
    name = "automation"

    def ready(self):
        # 独立脚本执行子进程只加载 ORM/模型，不得再启动一套 scheduler。
        if os.environ.get("AUTOMATION_EXECUTOR_CHILD") == "1":
            return
        # 调度器（自动化规则 + 控制方案）只在单一完整模式常驻进程启动，
        # 避免多 worker 重复执行 / 重复向设备下发命令（判定见 scheduler.scheduler_enabled）。
        # mqtt_runner 会在 Redis command worker 准备好之后显式启动，避免启动竞态。
        if 'mqtt_runner' in sys.argv:
            return
        from .scheduler import scheduler_enabled, start_scheduler
        if scheduler_enabled():
            start_scheduler()
