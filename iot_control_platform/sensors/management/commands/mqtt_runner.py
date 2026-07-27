"""
独立 MQTT 客户端进程入口。

部署：backend/ASGI worker 永远不启动 Paho 或自动化调度器；单独运行唯一的
`python manage.py mqtt_runner` 进程，显式持有 MQTT、命令 worker 和调度器。

进程内 MQTT 消息处理仍会调 Django ORM（写 SensorData/DeviceStatusCollection），
触发 services.realtime.signals → dispatch.publish_* → channel layer (redis)
→ backend worker 的 consumer → 浏览器。
"""
import logging
import os
import signal
import socket
import threading
import time
import uuid

from django.core.management.base import BaseCommand
from django.db import close_old_connections

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "运行独立 MQTT 客户端进程（与 web worker 分离）"

    def handle(self, *args, **options):
        # Paho 生命周期只允许在这个独立常驻进程内启动。AppConfig.ready()
        # 对 web worker / shell / migration 均无网络副作用。
        from services.mqtt_service import mqtt_service
        from services.mqtt_command_bus import (
            MqttCommandBusUnavailable,
            MqttCommandWorker,
            get_mqtt_command_bus,
        )

        # SIGTERM / SIGINT 优雅退出
        stop_event = threading.Event()

        def _shutdown(signum, frame):
            stop_event.set()

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)

        bus = get_mqtt_command_bus()
        control_consumer = (
            f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        )
        lease_acquired = False
        while not stop_event.is_set():
            try:
                if bus.acquire_runner_lease(control_consumer):
                    lease_acquired = True
                    break
                logger.warning("已有 MQTT runner 持有租约，5 秒后重试")
            except MqttCommandBusUnavailable as exc:
                logger.warning("无法竞争 MQTT runner 租约，5 秒后重试: %s", exc)
            stop_event.wait(5)
        if not lease_acquired:
            self.stdout.write(self.style.WARNING("MQTT runner stopped before lease acquisition"))
            return

        command_worker = None
        stop_scheduler = None
        try:
            # 只有取得唯一租约后才能连接 broker 或启动调度器。
            # 必须先注册订阅，再启动 connect_async；首次 CONNACK 回调会统一订阅。
            mqtt_service.setup_sensor_data_handler()
            mqtt_service.setup_sensor_status_handler()
            mqtt_service.setup_device_status_handler()
            mqtt_service.connect_async()

            command_worker = MqttCommandWorker(bus, mqtt_service, stop_event)
            command_worker.start()

            # 调度器放到命令 worker 之后启动：自动化第一条命令也必须走 Redis bus，
            # 不能在 runner 尚未准备好时落入旧的进程内直连路径。
            from automation.scheduler import start_scheduler, stop_scheduler
            start_scheduler()
        except Exception:
            # 初始化中途失败也要尽快交出租约；比较删除保证不会误删新所有者。
            stop_event.set()
            if stop_scheduler is not None:
                try:
                    stop_scheduler()
                except Exception:
                    logger.debug("清理半初始化调度器失败（忽略）", exc_info=True)
            if command_worker is not None:
                command_worker.join(timeout=5)
            try:
                mqtt_service.stop()
            except Exception:
                logger.debug("清理半初始化 MQTT client 失败（忽略）", exc_info=True)
            try:
                bus.release_runner_lease(control_consumer)
            except MqttCommandBusUnavailable:
                logger.warning("runner 初始化失败且租约释放失败，等待 TTL 自动回收")
            raise

        self.stdout.write(self.style.SUCCESS("MQTT runner started (async reconnect enabled)"))
        last_heartbeat = 0.0
        last_fingerprint_check = 0.0
        last_connect_retry = 0.0
        last_control_claim = 0.0
        command_worker_restarts = 0
        try:
            while not stop_event.is_set():
                now = time.monotonic()

                if not command_worker.is_alive():
                    command_worker_restarts += 1
                    logger.error(
                        "MQTT command worker 已退出，创建替代 worker restart=%s",
                        command_worker_restarts,
                    )
                    command_worker = MqttCommandWorker(bus, mqtt_service, stop_event)
                    command_worker.start()

                # connect_async 仅在本地配置/客户端创建异常时返回 False；broker
                # 不可达由 Paho 自己无限退避。前者由主循环再次初始化。
                if (
                    (
                        mqtt_service.client is None
                        or mqtt_service.publisher_client is None
                    )
                    and now - last_connect_retry >= 5
                ):
                    last_connect_retry = now
                    mqtt_service.connect_async()

                if now - last_heartbeat >= 5:
                    last_heartbeat = now
                    try:
                        if not bus.renew_runner_lease(control_consumer):
                            logger.critical("MQTT runner 租约已丢失，立即停止所有权")
                            stop_event.set()
                            break
                        bus.touch_runner_status(
                            state=mqtt_service.connection_state,
                            is_connected=int(mqtt_service.is_connected),
                            instance_id=control_consumer,
                            applied_config_fingerprint=(
                                mqtt_service.applied_config_fingerprint
                            ),
                            last_error=mqtt_service.last_error,
                            command_worker_alive=int(command_worker.is_alive()),
                            command_worker_restarts=command_worker_restarts,
                        )
                    except MqttCommandBusUnavailable as exc:
                        # 无法确认租约时继续连接 broker/执行调度会产生 split-brain。
                        logger.critical("MQTT runner 无法续租，立即停止: %s", exc)
                        stop_event.set()
                        break

                # 控制流通知是快速路径；数据库指纹轮询是 Redis 通知丢失的补偿。
                if now - last_fingerprint_check >= 30:
                    last_fingerprint_check = now
                    try:
                        current = mqtt_service.read_config_fingerprint()
                        if (
                            mqtt_service.applied_config_fingerprint
                            and current != mqtt_service.applied_config_fingerprint
                        ):
                            logger.info("检测到 MQTT 配置指纹变化，执行自愈 reload")
                            mqtt_service.reconnect_async()
                    except Exception as exc:
                        logger.warning("检查 MQTT 配置指纹失败: %s", exc)

                try:
                    bus.ensure_groups()
                    controls = bus.read_controls(control_consumer, block_ms=1000)
                    if now - last_control_claim >= 5:
                        last_control_claim = now
                        controls = bus.claim_stale_controls(control_consumer) + controls
                except MqttCommandBusUnavailable as exc:
                    logger.warning("读取 MQTT control stream 失败: %s", exc)
                    stop_event.wait(1)
                    continue

                for message_id, fields in controls:
                    request_id = fields.get("request_id", "")
                    try:
                        existing = bus.get_request(request_id)
                        if existing.get("status") in {"reload_applied", "reload_failed"}:
                            bus.ack_control(message_id)
                            continue
                        bus.set_state(request_id, "reloading", terminal=False)
                        if mqtt_service.reconnect_async():
                            bus.complete_if_pending(request_id, "reload_applied")
                        else:
                            bus.complete_if_pending(
                                request_id,
                                "reload_failed",
                                error=mqtt_service.last_error or "无法启动 MQTT client",
                            )
                        bus.ack_control(message_id)
                    except MqttCommandBusUnavailable:
                        # 留在 pending；Redis 恢复后可以重新应用幂等 reload。
                        break
                    except Exception as exc:
                        logger.exception("应用 MQTT reload 失败")
                        try:
                            bus.complete_if_pending(
                                request_id, "reload_failed", error=str(exc)
                            )
                            bus.ack_control(message_id)
                        except MqttCommandBusUnavailable:
                            break
        finally:
            self.stdout.write("Stopping MQTT runner...")
            stop_event.set()
            try:
                stop_scheduler()
            except Exception:
                logger.debug("停止自动化调度器失败（忽略）", exc_info=True)
            command_worker.join(timeout=5)
            try:
                mqtt_service.stop()
            except Exception:
                logger.debug("停止 MQTT client 失败（忽略）", exc_info=True)
            if lease_acquired:
                try:
                    bus.release_runner_lease(control_consumer)
                except MqttCommandBusUnavailable:
                    # Redis 故障时依靠短 TTL 回收，不能阻塞进程退出。
                    logger.warning("释放 MQTT runner 租约失败，等待 TTL 自动回收")
            close_old_connections()
            self.stdout.write(self.style.SUCCESS("MQTT runner stopped"))
