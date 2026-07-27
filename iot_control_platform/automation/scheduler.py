import logging
import sys
import threading
import time
import traceback
from django.db import close_old_connections, transaction
from django.utils import timezone
from automation.models import AutomationRule, ControlScheme
from automation.executor import AutomationRuleBusy, execute_rule_with_timeout
from automation.execution_policy import (
    AutomationScriptExecutionDisabled,
    SCRIPT_EXECUTION_DISABLED_MESSAGE,
    is_script_execution_enabled,
)
from automation.controllers import run_control_scheme_locked

logger = logging.getLogger('automation.scheduler')

_scheduler_thread = None
# 保留 _scheduler_thread 作为自由脚本线程的兼容名称；结构化控制使用独立线程，
# 避免自由脚本的子进程等待阻塞 PI/PID 控制拍。
_control_scheduler_thread = None
_stop_event = threading.Event()
_lifecycle_lock = threading.Lock()
_SCAN_INTERVAL_SECONDS = 1.0


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


def _iteration_time(now):
    """显式时间用于确定性测试；常驻线程传 None，让每个待执行项重新取时。"""
    return now if now is not None else timezone.now()


def _stop_rule_with_error(rule_id, error_message):
    """只停止仍在运行的规则，避免用调度扫描的旧对象覆盖用户刚完成的停止。"""
    with transaction.atomic():
        rule = AutomationRule.objects.select_for_update().filter(
            pk=rule_id,
            is_launched=True,
            process_status='running',
        ).first()
        if rule is None:
            return
        rule.is_launched = False
        rule.process_status = 'error_stopped'
        rule.error_message = error_message
        # 状态变化必须经过 save()，保留 post_save 实时广播。
        rule.save(update_fields=[
            'is_launched', 'process_status', 'error_message', 'updated_at',
        ])


def _process_automation_rules(now):
    """处理自由脚本规则（原有逻辑）"""
    running_rules = AutomationRule.objects.filter(
        is_launched=True,
        process_status='running',
    )
    if not is_script_execution_enabled():
        # save() 而不是 bulk update：保留 updated_at 与现有 post_save 实时广播。
        for rule_id in running_rules.values_list('pk', flat=True):
            _stop_rule_with_error(rule_id, SCRIPT_EXECUTION_DISABLED_MESSAGE)
        return

    # 执行器会按主键在隔离子进程中重新加载完整规则。调度扫描只取判定与日志字段，
    # 避免每秒把脚本正文、设备清单等大字段从 MySQL 搬到 mqtt_runner。
    rules = running_rules.only(
        'id', 'name', 'poll_interval', 'last_run_time',
    )
    for rule in rules:
        interval = rule.poll_interval
        if not interval or interval < 1:
            interval = 1

        run_at = _iteration_time(now)
        should_run = (not rule.last_run_time
                      or (run_at - rule.last_run_time).total_seconds() >= interval)
        if not should_run:
            continue

        # 同时校验运行态与扫描时看到的 last_run_time。若用户已停止规则，或别的
        # 执行入口抢先推进了时间戳，本次旧快照不再派发。last_run_time 是纯调度
        # 心跳，使用 update() 可避免每秒触发一次规则全量状态广播。
        claimed = AutomationRule.objects.filter(
            pk=rule.pk,
            is_launched=True,
            process_status='running',
            last_run_time=rule.last_run_time,
        ).update(last_run_time=run_at)
        if not claimed:
            continue

        try:
            logger.debug(f"调度器执行规则: {rule.name}")
            result = execute_rule_with_timeout(
                rule.pk,
                using=rule._state.db or 'default',
            )
            if result is not True:
                logger.error("规则 [%s] loop() 返回 False，停止后台轮询", rule.name)
                _stop_rule_with_error(
                    rule.pk,
                    '后台调度器执行失败: 规则 loop() 返回 False',
                )
        except AutomationRuleBusy:
            # 手动执行或上一拍仍持有单飞锁是正常竞争；只放弃当前拍，不能把持续
            # 轮询规则永久改成 error_stopped。
            logger.info("规则 [%s] 当前已有执行，本拍跳过", rule.name)
        except AutomationScriptExecutionDisabled as e:
            logger.error("规则 [%s] 因脚本执行策略关闭而停止", rule.name)
            _stop_rule_with_error(rule.pk, str(e))
        except Exception as e:
            error_msg = f"后台执行异常: {str(e)}\n{traceback.format_exc()}"
            logger.error(f"规则 [{rule.name}] {error_msg}")
            _stop_rule_with_error(
                rule.pk,
                f"后台调度器执行异常: {str(e)}",
            )


def _stop_control_scheme_with_error(scheme_id, error_message):
    """只停止仍在运行的方案，并通过 save() 保留实时状态广播。"""
    with transaction.atomic():
        scheme = ControlScheme.objects.select_for_update().filter(
            pk=scheme_id,
            is_enabled=True,
            status='running',
        ).first()
        if scheme is None:
            return
        scheme.is_enabled = False
        scheme.status = 'error'
        scheme.error_message = error_message
        scheme.save(update_fields=[
            'is_enabled', 'status', 'error_message', 'updated_at',
        ])


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
        run_at = _iteration_time(now)
        should_run = (not scheme.last_run_time
                      or (run_at - scheme.last_run_time).total_seconds() >= interval)
        if not should_run:
            continue
        try:
            logger.debug(f"调度器执行控制方案: {scheme.name}")
            # locked step 会在行锁内重新检查 is_enabled/status/last_run_time，
            # 因此扫描后的停止、手动单拍等变化不会被旧快照覆盖。
            result = run_control_scheme_locked(scheme.pk, send=True, due_at=run_at)
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
                _stop_control_scheme_with_error(
                    scheme.pk,
                    f'后台调度器执行失败: {error_message}',
                )
        except Exception as e:
            logger.error("控制方案 [%s] 后台执行异常: %s\n%s", scheme.name, e, traceback.format_exc())
            _stop_control_scheme_with_error(
                scheme.pk,
                f"后台调度器执行异常: {e}",
            )


def _run_periodic_loop(processor, stop_event, label):
    """按 monotonic 固定节拍扫描，并扣除本轮 ORM/执行耗时。"""
    logger.info("%s已启动", label)
    next_scan = time.monotonic()
    while not stop_event.is_set():
        try:
            # 常驻线程不经过 Django request 生命周期，必须自行清理过期连接。
            close_old_connections()
            # None 表示 processor 在每个真正待执行的项目之前重新取 timezone.now()，
            # 避免前一项耗时后仍沿用循环开始时的陈旧墙钟时间。
            processor(None)
        except Exception as e:
            logger.error("%s发生未捕获异常: %s\n%s", label, e, traceback.format_exc())
        finally:
            # 脚本子进程等待或设备确认期间，MySQL 连接可能已被服务端回收。
            close_old_connections()

        next_scan += _SCAN_INTERVAL_SECONDS
        monotonic_now = time.monotonic()
        if monotonic_now >= next_scan:
            # 本轮已经跨过下一个扫描点时，直接跳到首个未来扫描点。即使只超时
            # 很短也不 wait(0) 追赶，避免慢脚本导致 mqtt_runner 进入 DB 热循环。
            missed = (
                int((monotonic_now - next_scan) // _SCAN_INTERVAL_SECONDS) + 1
            )
            next_scan += missed * _SCAN_INTERVAL_SECONDS
        stop_event.wait(max(0.0, next_scan - time.monotonic()))


def _run_scheduler(stop_event=None):
    """自由脚本调度循环（保留原函数名供既有调用兼容）。"""
    _run_periodic_loop(
        _process_automation_rules,
        stop_event or _stop_event,
        "自动化规则调度器",
    )


def _run_control_scheduler(stop_event=None):
    """结构化控制方案独立循环，不受自由脚本执行耗时影响。"""
    _run_periodic_loop(
        _process_control_schemes,
        stop_event or _stop_event,
        "结构化控制调度器",
    )


def start_scheduler():
    """启动自由脚本与结构化控制两个后台调度线程。"""
    global _scheduler_thread, _control_scheduler_thread, _stop_event
    with _lifecycle_lock:
        automation_alive = bool(_scheduler_thread and _scheduler_thread.is_alive())
        control_alive = bool(
            _control_scheduler_thread and _control_scheduler_thread.is_alive()
        )

        # stop() join 超时说明旧线程仍可能卡在执行器中。绝不能 clear 它正在
        # 观察的 Event，否则会把旧一代线程复活并造成重复调度。
        if _stop_event.is_set() and (automation_alive or control_alive):
            logger.warning("上一代自动化调度线程仍在退出，本次暂不重复启动")
            return
        if not automation_alive and not control_alive:
            _stop_event = threading.Event()

        generation_stop_event = _stop_event
        if not automation_alive:
            _scheduler_thread = threading.Thread(
                target=_run_scheduler,
                args=(generation_stop_event,),
                name="AutomationRuleScheduler",
                daemon=True,
            )
            _scheduler_thread.start()
        if not control_alive:
            _control_scheduler_thread = threading.Thread(
                target=_run_control_scheduler,
                args=(generation_stop_event,),
                name="ControlSchemeScheduler",
                daemon=True,
            )
            _control_scheduler_thread.start()


def stop_scheduler():
    """停止两个后台调度线程。"""
    with _lifecycle_lock:
        _stop_event.set()
        threads = (_scheduler_thread, _control_scheduler_thread)
    for thread in threads:
        if thread and thread.is_alive():
            thread.join(timeout=2)
