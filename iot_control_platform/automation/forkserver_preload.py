"""自动化执行器 forkserver 的单线程 Django 预加载入口。

该模块只由 ``multiprocessing`` 的 forkserver 服务进程导入。forkserver 自身
不执行用户脚本；每次规则运行仍会 fork 出全新子进程，并继续受父进程硬超时和
进程组回收约束。
"""
from __future__ import annotations

import os
import sys


# 必须在 django.setup() 之前设置，阻止 AutomationConfig.ready() 在预加载服务
# 进程中启动 scheduler。argv 同时避免其他 AppConfig 把它误判为 mqtt_runner。
os.environ["AUTOMATION_EXECUTOR_CHILD"] = "1"
os.environ["AUTOMATION_FORKSERVER_PRELOADED"] = "1"
sys.argv = [sys.argv[0], "automation_executor_forkserver"]

import django  # noqa: E402


django.setup()
