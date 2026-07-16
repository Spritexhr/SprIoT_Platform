"""自由 Python 自动化的独立进程执行边界。

父进程只传递规则主键和可序列化运行配置；子进程使用 ``spawn`` 重新初始化
Django、关闭旧连接、从数据库重取规则，再调用 engine.execute_rule()。
执行结果通过有界 JSON 协议返回，任何超时都会 terminate / kill / join 回收。

独立进程提供可终止性，不是 OS 沙箱：脚本仍具有服务容器的文件、网络和数据库权限。
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import logging
import math
import multiprocessing
import os
import pickle
import signal
import sys
import tempfile
import time
import traceback
import uuid
from dataclasses import dataclass
from typing import Any

from .execution_policy import (
    ensure_script_execution_enabled,
    get_script_execution_timeout,
)


_PROTOCOL_VERSION = 1
_MAX_OUTPUT_CHARS = 64 * 1024
_MAX_LOG_RECORDS = 200
_MAX_LOG_MESSAGE_CHARS = 2 * 1024
_MAX_ERROR_MESSAGE_CHARS = 8 * 1024
_MAX_TRACEBACK_CHARS = 32 * 1024
_MAX_RESULT_BYTES = 512 * 1024
_MAX_LOG_SIDECAR_BYTES = 512 * 1024
_TERMINATE_GRACE_SECONDS = 0.5
_KILL_GRACE_SECONDS = 1.0
_LOCK_REDIS_TIMEOUT_SECONDS = 1.0
_LOCK_SAFETY_MARGIN_SECONDS = 15.0
_DEFAULT_LOCK_PREFIX = "spr:iot:automation:execution"
_RELEASE_LOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""
_CAPTURE_LOGGERS = (
    "automation",
    "services.devices_service",
    "services.sensors_service",
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AutomationExecutionResult:
    """一次成功完成的子进程执行结果；success 可为 False。"""

    success: bool
    output: str = ""
    logs: tuple[dict[str, str], ...] = ()


class AutomationExecutorError(RuntimeError):
    """独立执行器自身或远端脚本执行失败。"""

    code = "automation_executor_error"

    def __init__(self, message: str, *, output: str = "", logs=()):
        super().__init__(message)
        self.output = output
        self.logs = tuple(logs or ())


class AutomationScriptExecutionTimeout(AutomationExecutorError, TimeoutError):
    """规则超过硬超时，子进程已被终止并回收。"""

    code = "automation_script_timeout"

    def __init__(self, rule_id: int, timeout: float):
        self.rule_id = rule_id
        self.timeout = timeout
        super().__init__(
            f"自动化规则 {rule_id} 执行超过 {timeout:g} 秒，子进程已终止并回收"
        )


class AutomationScriptRemoteError(AutomationExecutorError):
    """子进程内脚本、ORM 或初始化异常的安全文本表示。"""

    code = "automation_script_remote_error"

    def __init__(
        self,
        *,
        error_type: str,
        message: str,
        remote_traceback: str,
        output: str = "",
        logs=(),
    ):
        self.error_type = error_type
        self.remote_message = message
        self.remote_traceback = remote_traceback
        detail = message or "无错误消息"
        super().__init__(
            f"子进程脚本异常 [{error_type}]: {detail}",
            output=output,
            logs=logs,
        )


class AutomationExecutorCrashed(AutomationExecutorError):
    """子进程未返回合法结果或异常退出。"""

    code = "automation_executor_crashed"


class AutomationRuleBusy(AutomationExecutorError):
    """同一规则已有手动或调度执行，不等待、不重复执行。"""

    code = "automation_rule_busy"

    def __init__(self, rule_id: int):
        self.rule_id = rule_id
        super().__init__(f"自动化规则 {rule_id} 正在执行，请勿重复触发")


class AutomationExecutionLockUnavailable(AutomationExecutorError):
    """无法确认单飞锁状态；为避免重复执行而 fail closed。"""

    code = "automation_execution_lock_unavailable"


@dataclass(frozen=True)
class _ExecutionLease:
    client: Any
    key: str
    owner: str


class _BoundedTextBuffer(io.TextIOBase):
    """保留前 N 个字符的文本缓冲，持续 print 也不会无限占用内存。"""

    encoding = "utf-8"

    def __init__(self, limit: int, *, mirror=None):
        super().__init__()
        self._limit = limit
        self._mirror = mirror
        self._parts: list[str] = []
        self._size = 0
        self._truncated = False

    def writable(self):
        return True

    def write(self, value):
        text = str(value)
        if self._mirror is not None:
            self._mirror.write(text)
        remaining = self._limit - self._size
        if remaining > 0:
            kept = text[:remaining]
            self._parts.append(kept)
            self._size += len(kept)
        if len(text) > max(0, remaining):
            self._truncated = True
        return len(text)

    def flush(self):
        return None

    def getvalue(self) -> str:
        value = "".join(self._parts)
        if self._truncated:
            value += "\n...[输出已截断]"
        return value


class _BoundedLogHandler(logging.Handler):
    def __init__(self, *, mirror=None):
        super().__init__(level=logging.INFO)
        self._mirror = mirror
        self.records: list[dict[str, str]] = []

    def emit(self, record):
        if len(self.records) >= _MAX_LOG_RECORDS:
            return
        try:
            message = record.getMessage()
        except Exception:
            message = "<日志格式化失败>"
        item = {
            "level": str(record.levelname)[:20],
            "message": f"[{record.name}] {message}"[:_MAX_LOG_MESSAGE_CHARS],
        }
        self.records.append(item)
        if self._mirror is not None:
            self._mirror.write(json.dumps(item, ensure_ascii=True) + "\n")


class _BoundedSidecarWriter:
    """子进程旁路捕获文件；写入严格有界，不受结果 pipe 背压影响。"""

    _TRUNCATED_MARKER = b"\n...[capture truncated]"

    def __init__(self, path: str, limit: int):
        flags = os.O_WRONLY | os.O_APPEND
        flags |= getattr(os, "O_CLOEXEC", 0)
        self._fd = os.open(path, flags)
        self._remaining = limit
        self._truncated = False
        self._disabled = False

    @staticmethod
    def _write_all(fd: int, data: bytes) -> None:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("sidecar write returned zero bytes")
            view = view[written:]

    def write(self, value: str) -> None:
        if self._disabled or self._truncated:
            return
        text = str(value)
        # 每个字符编码后至少占 1 byte，只截取 remaining 个字符即可覆盖所有
        # 可能写入的 byte，避免对超大 print 再做一次无界 UTF-8 分配。
        candidate = text[:self._remaining]
        data = candidate.encode("utf-8", errors="backslashreplace")
        kept = data[:self._remaining]
        try:
            if kept:
                self._write_all(self._fd, kept)
                self._remaining -= len(kept)
            if len(text) > len(candidate) or len(data) > len(kept):
                self._write_all(self._fd, self._TRUNCATED_MARKER)
                self._truncated = True
        except OSError:
            # 捕获通道故障不能改变用户脚本自身的执行语义；父进程仍会从结果协议诊断。
            self._disabled = True

    def close(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            os.close(fd)
        except OSError:
            pass


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        # 脚本可能产生孤立 surrogate；ASCII 转义可确保协议始终能编码。
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _safe_exception_message(exc: BaseException) -> str:
    try:
        return _utf8_safe_text(str(exc), _MAX_ERROR_MESSAGE_CHARS)
    except BaseException:
        return f"<{type(exc).__module__}.{type(exc).__qualname__}.__str__ failed>"


def _safe_traceback() -> str:
    try:
        return _utf8_safe_text(
            traceback.format_exc()[-_MAX_TRACEBACK_CHARS:],
            _MAX_TRACEBACK_CHARS,
        )
    except BaseException:
        return "<traceback formatting failed>"


def _utf8_safe_text(value: Any, limit: int) -> str:
    """把孤立 surrogate 转成可安全返回 DRF/JSON 的反斜杠文本。"""
    return (
        str(value)[:limit]
        .encode("utf-8", errors="backslashreplace")
        .decode("utf-8")
    )


def _send_child_payload(connection, payload: dict[str, Any]) -> None:
    raw = _json_bytes(payload)
    if len(raw) > _MAX_RESULT_BYTES:
        fallback = {
            "version": _PROTOCOL_VERSION,
            "status": "error",
            "error_type": "automation.executor.ResultTooLarge",
            "message": "子进程结果超过协议大小上限",
            "traceback": "",
            "output": "",
            "logs": [],
        }
        raw = _json_bytes(fallback)
    connection.send_bytes(raw)


def _child_process_main(
    rule_id: int,
    database_alias: str,
    database_settings: dict,
    settings_module: str,
    allowed_imports: tuple[str, ...],
    output_capture_path: str,
    log_capture_path: str,
    process_group_flag,
    result_connection,
) -> None:
    """spawn 子进程入口；必须保持模块顶层且参数均可 pickle。"""
    os.environ["AUTOMATION_EXECUTOR_CHILD"] = "1"
    os.environ["DJANGO_SETTINGS_MODULE"] = settings_module
    # 避免 AppConfig 根据父进程的 runserver / mqtt_runner argv 启动常驻任务。
    sys.argv = [sys.argv[0], "automation_executor_child"]

    process_group_ready = False
    if os.name == "posix":
        try:
            os.setsid()
        except OSError:
            process_group_ready = False
        else:
            process_group_ready = True
            try:
                process_group_flag.value = 1
            except Exception:
                # ready 协议仍会把状态通知父进程；共享标志只是清理竞态的第二通道。
                pass

    output = _BoundedTextBuffer(_MAX_OUTPUT_CHARS)
    log_handler = _BoundedLogHandler()
    output_sidecar = None
    log_sidecar = None
    captured_loggers: list[logging.Logger] = []
    connections = None

    try:
        _send_child_payload(result_connection, {
            "version": _PROTOCOL_VERSION,
            "status": "ready",
            "process_group": process_group_ready,
        })

        output_sidecar = _BoundedSidecarWriter(
            output_capture_path,
            _MAX_OUTPUT_CHARS,
        )
        log_sidecar = _BoundedSidecarWriter(
            log_capture_path,
            _MAX_LOG_SIDECAR_BYTES,
        )
        output = _BoundedTextBuffer(_MAX_OUTPUT_CHARS, mirror=output_sidecar)
        log_handler = _BoundedLogHandler(mirror=log_sidecar)

        import django

        django.setup()

        from django.conf import settings as django_settings
        from django.db import connections as django_connections

        connections = django_connections
        connections.close_all()

        # spawn 会重新加载默认 settings；显式同步父进程实际使用的数据库（尤其是
        # Django test DB / 多数据库别名），同时杜绝复用 setup 阶段可能建立的连接。
        configured_database = copy.deepcopy(database_settings)
        configured_database["CONN_MAX_AGE"] = 0
        configured_database["CONN_HEALTH_CHECKS"] = False
        django_settings.DATABASES[database_alias] = configured_database
        connections.settings[database_alias] = configured_database
        if hasattr(connections._connections, database_alias):
            del connections[database_alias]

        # 父进程已经通过默认关闭策略授权本次执行；子进程仍由 engine 再次守卫。
        django_settings.AUTOMATION_SCRIPT_EXECUTION_ENABLED = True
        django_settings.AUTOMATION_ALLOWED_IMPORTS = list(allowed_imports)

        for logger_name in _CAPTURE_LOGGERS:
            child_logger = logging.getLogger(logger_name)
            child_logger.addHandler(log_handler)
            captured_loggers.append(child_logger)

        from automation.engine import execute_rule
        from automation.models import AutomationRule

        rule = AutomationRule.objects.using(database_alias).get(pk=rule_id)
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = execute_rule(rule)

        _send_child_payload(result_connection, {
            "version": _PROTOCOL_VERSION,
            "status": "ok",
            "result": bool(result),
        })
    except BaseException as exc:  # 子进程必须把 SystemExit/KeyboardInterrupt 也转成诊断
        error_type = f"{type(exc).__module__}.{type(exc).__qualname__}"
        try:
            _send_child_payload(result_connection, {
                "version": _PROTOCOL_VERSION,
                "status": "error",
                "error_type": error_type[:500],
                "message": _safe_exception_message(exc),
                "traceback": _safe_traceback(),
            })
        except BaseException:
            # 父进程会根据 EOF / exitcode 报告 executor crash。
            pass
    finally:
        for child_logger in captured_loggers:
            try:
                child_logger.removeHandler(log_handler)
            except Exception:
                pass
        if connections is not None:
            try:
                connections.close_all()
            except Exception:
                pass
        if output_sidecar is not None:
            output_sidecar.close()
        if log_sidecar is not None:
            log_sidecar.close()
        try:
            result_connection.close()
        except Exception:
            pass


def _signal_process(process, *, force: bool, process_group_ready: bool) -> None:
    if process.pid is None:
        return
    sig = signal.SIGKILL if force else signal.SIGTERM
    if os.name == "posix" and process_group_ready:
        try:
            os.killpg(process.pid, sig)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        if force and hasattr(process, "kill"):
            process.kill()
        else:
            process.terminate()
    except (ProcessLookupError, ValueError, OSError):
        pass


def _terminate_kill_join(process, *, process_group_ready: bool) -> None:
    """唯一清理路径：TERM → join → 必要时 KILL → 无条件 join。"""
    _signal_process(process, force=False, process_group_ready=process_group_ready)
    process.join(_TERMINATE_GRACE_SECONDS)

    # POSIX 子进程已 setsid 时，即使主进程响应 TERM 退出，也补杀同组后代。
    if process.is_alive() or (os.name == "posix" and process_group_ready):
        _signal_process(process, force=True, process_group_ready=process_group_ready)
    process.join(_KILL_GRACE_SECONDS)
    if process.is_alive():
        raise AutomationExecutorCrashed(
            f"无法回收自动化执行子进程 pid={process.pid}"
        )


def _read_protocol_message(connection, timeout: float) -> dict | None:
    if timeout <= 0 or not connection.poll(timeout):
        return None
    try:
        raw = connection.recv_bytes(_MAX_RESULT_BYTES)
        payload = json.loads(raw.decode("utf-8"))
    except (EOFError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AutomationExecutorCrashed(f"子进程返回了非法结果协议: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") != _PROTOCOL_VERSION:
        raise AutomationExecutorCrashed("子进程返回了不支持的结果协议")
    return payload


def _create_capture_sidecars():
    """父进程预建 0700 目录 / 0600 文件，子进程只能追加有界诊断。"""
    directory = tempfile.TemporaryDirectory(prefix="automation-executor-")
    output_path = os.path.join(directory.name, "stdout.bin")
    log_path = os.path.join(directory.name, "logs.jsonl")
    try:
        for path in (output_path, log_path):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
    except BaseException:
        directory.cleanup()
        raise
    return directory, output_path, log_path


def _read_capture_sidecars(output_path: str, log_path: str):
    """读取有界旁路文件；SIGKILL 截断的最后一条 JSONL 日志会被安全忽略。"""
    try:
        with open(output_path, "rb") as output_file:
            output_raw = output_file.read(
                _MAX_OUTPUT_CHARS + len(_BoundedSidecarWriter._TRUNCATED_MARKER) + 1
            )
        with open(log_path, "rb") as log_file:
            log_raw = log_file.read(
                _MAX_LOG_SIDECAR_BYTES
                + len(_BoundedSidecarWriter._TRUNCATED_MARKER)
                + 1
            )
    except OSError as exc:
        raise AutomationExecutorCrashed(f"无法读取自动化子进程诊断旁路文件: {exc}") from exc

    output = output_raw.decode("utf-8", errors="replace")
    logs: list[dict[str, str]] = []
    for raw_line in log_raw.splitlines()[:_MAX_LOG_RECORDS]:
        try:
            item = json.loads(raw_line.decode("ascii"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(item, dict):
            continue
        logs.append({
            "level": _utf8_safe_text(item.get("level") or "", 20),
            "message": _utf8_safe_text(
                item.get("message") or "",
                _MAX_LOG_MESSAGE_CHARS,
            ),
        })
    return output, tuple(logs)


def _database_settings_for_child(database_alias: str) -> dict:
    from django.db import connections

    try:
        database_settings = copy.deepcopy(connections[database_alias].settings_dict)
        database_settings["CONN_MAX_AGE"] = 0
        database_settings["CONN_HEALTH_CHECKS"] = False
        # 提前验证 spawn 参数，避免 process.start() 才给出晦涩 pickle 错误。
        pickle.dumps(database_settings)
        return database_settings
    except Exception as exc:
        raise AutomationExecutorCrashed(
            f"数据库配置无法传入自动化子进程 alias={database_alias}: {exc}"
        ) from exc


def _get_execution_lock_client():
    """构造短超时 Redis 客户端；连接失败由 acquire 转成 fail-closed 异常。"""
    from django.conf import settings

    try:
        import redis

        return redis.Redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=_LOCK_REDIS_TIMEOUT_SECONDS,
            socket_timeout=_LOCK_REDIS_TIMEOUT_SECONDS,
            retry_on_timeout=False,
        )
    except Exception as exc:
        raise AutomationExecutionLockUnavailable(
            f"无法初始化自动化规则单飞锁: {exc}"
        ) from exc


def _acquire_execution_lease(
    rule_id: int,
    *,
    using: str,
    timeout: float,
) -> _ExecutionLease:
    """Redis SET NX 单飞锁；不排队，Redis 异常时禁止脚本执行。"""
    from django.conf import settings

    prefix = str(
        getattr(settings, "AUTOMATION_SCRIPT_LOCK_PREFIX", _DEFAULT_LOCK_PREFIX)
        or _DEFAULT_LOCK_PREFIX
    ).strip().rstrip(":")
    key = f"{prefix}:{using}:{rule_id}"
    owner = uuid.uuid4().hex
    ttl = max(
        1,
        math.ceil(
            timeout
            + _TERMINATE_GRACE_SECONDS
            + _KILL_GRACE_SECONDS
            + _LOCK_SAFETY_MARGIN_SECONDS
        ),
    )
    client = _get_execution_lock_client()
    try:
        acquired = client.set(key, owner, nx=True, ex=ttl)
    except Exception as exc:
        raise AutomationExecutionLockUnavailable(
            f"无法获取自动化规则 {rule_id} 的单飞锁；为避免重复执行，本次已拒绝: {exc}"
        ) from exc
    if not acquired:
        raise AutomationRuleBusy(rule_id)
    return _ExecutionLease(client=client, key=key, owner=owner)


def _release_execution_lease(lease: _ExecutionLease, *, rule_id: int) -> None:
    """仅锁 owner 可原子删除；锁丢失也不能把后来执行者的锁删掉。"""
    try:
        deleted = lease.client.eval(
            _RELEASE_LOCK_SCRIPT,
            1,
            lease.key,
            lease.owner,
        )
    except Exception as exc:
        raise AutomationExecutionLockUnavailable(
            f"自动化规则 {rule_id} 已执行，但释放单飞锁失败: {exc}"
        ) from exc
    if deleted != 1:
        raise AutomationExecutionLockUnavailable(
            f"自动化规则 {rule_id} 的单飞锁所有权已丢失；执行结果状态不确定"
        )


def _execute_rule_process_under_lease(
    rule_id: int,
    *,
    using: str,
    timeout: float,
    database_settings: dict,
) -> AutomationExecutionResult:

    from django.conf import settings
    from django.db import close_old_connections

    # Web worker / scheduler 可能在等待脚本时跨过 MySQL wait_timeout；等待前先
    # 丢弃已不可用连接，结束后再清一次，避免后续 ORM 复用陈旧连接。
    close_old_connections()

    settings_module = os.environ.get("DJANGO_SETTINGS_MODULE") or getattr(
        settings, "SETTINGS_MODULE", "config.settings"
    )
    allowed_imports = tuple(
        str(item)
        for item in getattr(settings, "AUTOMATION_ALLOWED_IMPORTS", ())
        if str(item)
    )

    capture_directory, output_capture_path, log_capture_path = _create_capture_sidecars()
    try:
        context = multiprocessing.get_context("spawn")
        # 无锁单写单读标志；不能用 Event/Condition，避免子进程被 SIGKILL 时
        # 恰好持有同步锁，导致父进程 finally 永久阻塞。
        process_group_flag = context.RawValue("b", 0)
        receive_connection, send_connection = context.Pipe(duplex=False)
        process = context.Process(
            target=_child_process_main,
            args=(
                int(rule_id),
                str(using),
                database_settings,
                str(settings_module),
                allowed_imports,
                output_capture_path,
                log_capture_path,
                process_group_flag,
                send_connection,
            ),
            name=f"automation-rule-{rule_id}",
            daemon=True,
        )
    except BaseException:
        capture_directory.cleanup()
        close_old_connections()
        raise

    deadline = time.monotonic() + timeout
    process_group_ready = False
    payload = None
    started = False
    force_cleanup = False
    execution_error: BaseException | None = None

    try:
        process.start()
        started = True
        send_connection.close()

        while payload is None:
            remaining = deadline - time.monotonic()
            message = _read_protocol_message(receive_connection, remaining)
            if message is None:
                force_cleanup = True
                raise AutomationScriptExecutionTimeout(int(rule_id), timeout)
            status = message.get("status")
            if status == "ready":
                process_group_ready = message.get("process_group") is True
                continue
            payload = message

        remaining = max(0.0, deadline - time.monotonic())
        process.join(remaining)
        if process.is_alive():
            force_cleanup = True
            raise AutomationScriptExecutionTimeout(int(rule_id), timeout)

        captured_output, captured_logs = _read_capture_sidecars(
            output_capture_path,
            log_capture_path,
        )
        status = payload.get("status")
        if status == "ok":
            return AutomationExecutionResult(
                success=payload.get("result") is True,
                output=captured_output,
                logs=captured_logs,
            )
        if status == "error":
            raise AutomationScriptRemoteError(
                error_type=_utf8_safe_text(
                    payload.get("error_type") or "unknown",
                    500,
                ),
                message=_utf8_safe_text(
                    payload.get("message") or "",
                    _MAX_ERROR_MESSAGE_CHARS,
                ),
                remote_traceback=_utf8_safe_text(
                    payload.get("traceback") or "",
                    _MAX_TRACEBACK_CHARS,
                ),
                output=captured_output,
                logs=captured_logs,
            )
        raise AutomationExecutorCrashed(
            f"子进程返回未知状态 {status!r}，exitcode={process.exitcode}"
        )
    except BaseException as exc:
        execution_error = exc
        if started and process.is_alive():
            force_cleanup = True
        raise
    finally:
        try:
            try:
                send_connection.close()
            except Exception:
                pass

            if started:
                try:
                    try:
                        flag_confirmed_group = (
                            os.name == "posix" and bool(process_group_flag.value)
                        )
                    except Exception:
                        flag_confirmed_group = False
                    process_group_ready = (
                        process_group_ready
                        or flag_confirmed_group
                    )
                    if force_cleanup or process.is_alive():
                        _terminate_kill_join(
                            process,
                            process_group_ready=process_group_ready,
                        )
                    else:
                        process.join()
                        # 正常返回也清掉脚本可能遗留在独立 session 中的后代。
                        if os.name == "posix" and process_group_ready:
                            _signal_process(
                                process,
                                force=True,
                                process_group_ready=True,
                            )
                finally:
                    try:
                        process.close()
                    except ValueError:
                        pass
        finally:
            try:
                # timeout / crash 没有最终 JSON，但旁路文件在 kill+join 后仍可诊断。
                if isinstance(execution_error, AutomationExecutorError):
                    try:
                        captured_output, captured_logs = _read_capture_sidecars(
                            output_capture_path,
                            log_capture_path,
                        )
                    except AutomationExecutorCrashed:
                        logger.exception("读取规则 %s 的超时旁路诊断失败", rule_id)
                    else:
                        if not execution_error.output:
                            execution_error.output = captured_output
                        if not execution_error.logs:
                            execution_error.logs = captured_logs
            finally:
                try:
                    receive_connection.close()
                finally:
                    try:
                        capture_directory.cleanup()
                    finally:
                        close_old_connections()


def _execute_rule_process(rule_id: int, *, using: str) -> AutomationExecutionResult:
    """统一入口：策略校验 → Redis 单飞 → spawn 硬超时 → owner 安全解锁。"""
    ensure_script_execution_enabled()
    timeout = get_script_execution_timeout()
    normalized_rule_id = int(rule_id)
    normalized_using = str(using)

    # 数据库配置错误不应占住 Redis 锁；完成本地预检后再竞争单飞锁。
    database_settings = _database_settings_for_child(normalized_using)
    lease = _acquire_execution_lease(
        normalized_rule_id,
        using=normalized_using,
        timeout=timeout,
    )
    primary_error: BaseException | None = None
    try:
        return _execute_rule_process_under_lease(
            normalized_rule_id,
            using=normalized_using,
            timeout=timeout,
            database_settings=database_settings,
        )
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            _release_execution_lease(lease, rule_id=normalized_rule_id)
        except AutomationExecutionLockUnavailable:
            if primary_error is None:
                raise
            # 不掩盖更有价值的脚本/超时异常；锁会由 TTL 最终释放。
            logger.exception(
                "规则 %s 执行失败后释放单飞锁异常，保留原始执行异常",
                normalized_rule_id,
            )


def execute_rule_with_timeout(rule_id: int, *, using: str = "default") -> bool:
    """执行规则并保留原有 bool 返回语义。"""
    return _execute_rule_process(rule_id, using=using).success


def execute_rule_with_timeout_details(
    rule_id: int,
    *,
    using: str = "default",
) -> AutomationExecutionResult:
    """执行规则并返回 API 所需的 stdout / 日志。"""
    return _execute_rule_process(rule_id, using=using)
