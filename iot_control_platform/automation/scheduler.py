import logging
import sys
import threading
import traceback
from django.db import close_old_connections
from django.utils import timezone
from automation.models import AutomationRule, ControlScheme
from automation.executor import execute_rule_with_timeout
from automation.execution_policy import (
    AutomationScriptExecutionDisabled,
    SCRIPT_EXECUTION_DISABLED_MESSAGE,
    is_script_execution_enabled,
)
from automation.controllers import run_control_scheme_locked

logger = logging.getLogger('automation.scheduler')

_scheduler_thread = None
_stop_event = threading.Event()


def scheduler_enabled() -> bool:
    """
    调度器（自动化规则 + 控制方案）必须只在「单一完整模式常驻进程」运行，
    否则多 worker 会重复执行规则 / 重复向设备下发命令。

    判定：
    - 迁移 / 测试：不运行
    - mqtt_runner 独立进程：允许运行（实际由 management command 显式启动）
    - runserver / gunicorn / daphne / uvicorn / 其它进程：一律不运行

    不能依赖 IOT_MQTT_RUNNER=false 来保护 web worker；一旦部署漏配，多个
    ASGI worker 就会重复执行控制方案。生产所有权必须由进程类型决定。
    """
    if 'test' in sys.argv or 'migrate' in sys.argv or 'makemigrations' in sys.argv:
        return False
    return 'mqtt_runner' in sys.argv


def _process_automation_rules(now):
    """处理自由脚本规则（原有逻辑）"""
    rules = AutomationRule.objects.filter(is_launched=True, process_status='running')
    if not is_script_execution_enabled():
        # save() 而不是 bulk update：保留 updated_at 与现有 post_save 实时广播。
        for rule in rules:
            rule.is_launched = False
            rule.process_status = 'error_stopped'
            rule.error_message = SCRIPT_EXECUTION_DISABLED_MESSAGE
            rule.save(update_fields=[
                'is_launched', 'process_status', 'error_message', 'updated_at',
            ])
        return

    for rule in rules:
        interval = rule.poll_interval
        if not interval or interval < 1:
            interval = 1

        should_run = (not rule.last_run_time
                      or (now - rule.last_run_time).total_seconds() >= interval)
        if not should_run:
            continue

        # 记录执行时间
        rule.last_run_time = now
        rule.save(update_fields=['last_run_time'])
        try:
            logger.debug(f"调度器执行规则: {rule.name}")
            result = execute_rule_with_timeout(
                rule.pk,
                using=rule._state.db or 'default',
            )
            if result is not True:
                logger.error("规则 [%s] loop() 返回 False，停止后台轮询", rule.name)
                rule.is_launched = False
                rule.process_status = 'error_stopped'
                rule.error_message = '后台调度器执行失败: 规则 loop() 返回 False'
                rule.save(update_fields=[
                    'is_launched', 'process_status', 'error_message', 'updated_at',
                ])
        except AutomationScriptExecutionDisabled as e:
            logger.error("规则 [%s] 因脚本执行策略关闭而停止", rule.name)
            rule.is_launched = False
            rule.process_status = 'error_stopped'
            rule.error_message = str(e)
            rule.save(update_fields=['is_launched', 'process_status', 'error_message', 'updated_at'])
        except Exception as e:
            error_msg = f"后台执行异常: {str(e)}\n{traceback.format_exc()}"
            logger.error(f"规则 [{rule.name}] {error_msg}")
            rule.is_launched = False
            rule.process_status = 'error_stopped'
            rule.error_message = f"后台调度器执行异常: {str(e)}"
            rule.save(update_fields=['is_launched', 'process_status', 'error_message', 'updated_at'])


def _process_control_schemes(now):
    """处理结构化控制方案（双位 / PI / PID）。

    注意：dt 由 run_control_scheme 内部用上一次 last_run_time 推算，
    这里不预先改写 last_run_time，只判断是否到点。
    """
    # 实际执行会在取得行锁后重新加载完整方案；这里只取调度判定所需字段，
    # 避免每轮为未到期方案预加载整条成员/设备关系。
    schemes = ControlScheme.objects.filter(is_enabled=True, status='running').only(
        'id', 'name', 'sample_interval', 'last_run_time'
    )
    for scheme in schemes:
        interval = scheme.sample_interval or 1
        should_run = (not scheme.last_run_time
                      or (now - scheme.last_run_time).total_seconds() >= interval)
        if not should_run:
            continue
        try:
            logger.debug(f"调度器执行控制方案: {scheme.name}")
            result = run_control_scheme_locked(scheme.pk, send=True, due_at=now)
            if result.get('skipped'):
                continue
            if result.get('error'):
                # 控制器已在同一行锁事务内停环并通过 save() 发出实时状态。
                logger.error("控制方案 [%s] 本拍失败: %s", scheme.name, result['error'])
                continue
            if result.get('sent') is not True:
                # 防御第三方/未来执行器违反结果契约：send=True 时 False/None 都不能当成功。
                error_message = '控制命令未成功下发（sent=False）'
                logger.error("控制方案 [%s] 本拍失败: %s", scheme.name, error_message)
                ControlScheme.objects.filter(pk=scheme.pk).update(
                    is_enabled=False,
                    status='error',
                    error_message=f'后台调度器执行失败: {error_message}',
                    updated_at=timezone.now(),
                )
        except Exception as e:
            logger.error("控制方案 [%s] 后台执行异常: %s\n%s", scheme.name, e, traceback.format_exc())
            ControlScheme.objects.filter(pk=scheme.pk).update(
                is_enabled=False,
                status='error',
                error_message=f"后台调度器执行异常: {e}",
                updated_at=timezone.now(),
            )


def _run_scheduler():
    """后台调度器主循环，每秒检查一次自动化规则与控制方案。"""
    logger.info("自动化调度器已启动（自动化规则 + 控制方案）")
    while not _stop_event.is_set():
        try:
            # 常驻线程不经过 Django request 生命周期，必须自行清理过期连接。
            close_old_connections()
            now = timezone.now()
            _process_automation_rules(now)
            _process_control_schemes(now)
        except Exception as e:
            logger.error(f"自动化调度器发生未捕获异常: {e}")
        finally:
            # 脚本子进程等待或设备确认期间，MySQL 连接可能已被服务端回收。
            close_old_connections()
        _stop_event.wait(1)


def start_scheduler():
    """启动后台调度器线程"""
    global _scheduler_thread
    if _scheduler_thread is None or not _scheduler_thread.is_alive():
        _stop_event.clear()
        _scheduler_thread = threading.Thread(target=_run_scheduler, name="AutomationScheduler", daemon=True)
        _scheduler_thread.start()


def stop_scheduler():
    """停止后台调度器线程"""
    if _scheduler_thread and _scheduler_thread.is_alive():
        _stop_event.set()
        _scheduler_thread.join(timeout=2)
