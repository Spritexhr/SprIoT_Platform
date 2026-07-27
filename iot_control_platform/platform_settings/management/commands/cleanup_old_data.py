"""
按 platform_settings 配置的数据留存天数清理过期数据
从 platform_config 读取 sensor_data_retention_days、device_data_retention_days
可配合 cron 定时执行，或通过 API 触发
采用分批删除避免大表锁表或内存溢出
"""
from contextlib import contextmanager
import hashlib
import logging
import threading
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from platform_settings.defaults import ConfigValidationError, validate_config_value

logger = logging.getLogger("platform_settings")

# 每批删除的记录数
BATCH_SIZE = 1000
MAX_RECORDS_LIMIT = 1_000_000
_local_cleanup_lock = threading.Lock()


class CleanupAlreadyRunning(CommandError):
    """已有清理任务持有单飞锁。"""


@contextmanager
def _exclusive_cleanup_lock():
    """真实删除使用跨 worker 单飞锁；SQLite 本地开发退化为进程内锁。"""
    if connection.vendor != "mysql":
        acquired = _local_cleanup_lock.acquire(blocking=False)
        if not acquired:
            raise CleanupAlreadyRunning("已有历史数据清理任务正在运行")
        try:
            yield
        finally:
            _local_cleanup_lock.release()
        return

    database_name = str(connection.settings_dict.get("NAME") or "default")
    digest = hashlib.sha256(database_name.encode("utf-8")).hexdigest()[:16]
    lock_name = f"spr_iot:cleanup:{digest}"
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT GET_LOCK(%s, 0)", [lock_name])
            row = cursor.fetchone()
    except Exception as exc:
        raise CommandError("无法获取历史数据清理锁，已停止且未删除数据") from exc
    if not row or row[0] != 1:
        raise CleanupAlreadyRunning("已有历史数据清理任务正在运行")

    try:
        yield
    finally:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT RELEASE_LOCK(%s)", [lock_name])
        except Exception:
            logger.exception("释放历史数据清理锁失败；数据库连接关闭后会自动释放")


class Command(BaseCommand):
    help = "按 platform_settings 配置清理过期的传感器/设备数据"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="仅统计将删除的数量，不实际删除",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=BATCH_SIZE,
            help=f"每批删除的记录数（默认 {BATCH_SIZE}）",
        )
        parser.add_argument(
            "--max-records",
            type=int,
            default=None,
            help="本次最多删除的主记录数；省略时处理全部过期数据",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        batch_size = options["batch_size"]
        max_records = options.get("max_records")
        if batch_size < 1 or batch_size > 10000:
            raise CommandError("--batch-size 必须在 1 到 10000 之间")
        if (
            max_records is not None
            and (max_records < 1 or max_records > MAX_RECORDS_LIMIT)
        ):
            raise CommandError(
                f"--max-records 必须在 1 到 {MAX_RECORDS_LIMIT} 之间"
            )
        if dry_run:
            msg = "【试运行模式】不会实际删除数据"
            logger.info(msg)
            self.stdout.write(self.style.WARNING(msg))

        if dry_run:
            return self._execute_cleanup(
                dry_run=True,
                batch_size=batch_size,
                max_records=max_records,
            )
        with _exclusive_cleanup_lock():
            return self._execute_cleanup(
                dry_run=False,
                batch_size=batch_size,
                max_records=max_records,
            )

    @staticmethod
    def _load_retention_days() -> tuple[int, int]:
        """一次性、失败即停止地读取删除边界，绝不在数据库异常时套用默认值。"""
        from platform_settings.models import PlatformConfig

        keys = (
            "sensor_data_retention_days",
            "device_data_retention_days",
        )
        try:
            values = dict(
                PlatformConfig.objects.filter(key__in=keys).values_list(
                    "key",
                    "value",
                )
            )
        except Exception as exc:
            raise CommandError(
                "读取数据留存配置失败，已停止且未删除任何数据"
            ) from exc

        validated = {}
        for key in keys:
            raw_value = values.get(key, 30)
            try:
                validated[key] = validate_config_value(key, raw_value)
            except ConfigValidationError as exc:
                raise CommandError(str(exc)) from exc
        return (
            validated["sensor_data_retention_days"],
            validated["device_data_retention_days"],
        )

    def _execute_cleanup(
        self,
        *,
        dry_run: bool,
        batch_size: int,
        max_records: int | None,
    ):
        sensor_days, device_days = self._load_retention_days()

        sensor_cutoff = timezone.now() - timedelta(days=sensor_days)
        device_cutoff = timezone.now() - timedelta(days=device_days)

        from sensors.models import SensorData, SensorStatusCollection
        from devices.models import DeviceStatusCollection

        # 留存周期必须以服务器接收时间为准。设备自带 timestamp 可能漂移、
        # 被重放或来自未来，不能让它决定数据何时被删除。
        sensor_data_qs = SensorData.objects.filter(
            received_at__lt=sensor_cutoff
        ).order_by("received_at", "pk")
        sensor_status_qs = SensorStatusCollection.objects.filter(
            received_at__lt=sensor_cutoff
        ).order_by("received_at", "pk")
        device_qs = DeviceStatusCollection.objects.filter(
            received_at__lt=device_cutoff
        ).order_by("received_at", "pk")

        deleted_counts = {
            "sensor_data": 0,
            "sensor_status": 0,
            "device_status": 0,
        }
        querysets = (
            ("sensor_data", sensor_data_qs, "传感器采样"),
            ("sensor_status", sensor_status_qs, "传感器状态"),
            ("device_status", device_qs, "设备状态"),
        )

        # 试运行和 CLI 全量清理只计数一次；API 有界续批不做全表 COUNT，
        # 避免总数据量大时每 1000 条都重新扫描剩余历史记录。
        bounded_delete = not dry_run and max_records is not None
        if bounded_delete:
            counts_before = {key: None for key, _queryset, _label in querysets}
            msg = (
                f"按留存配置执行有界清理，本次最多选择 {max_records} 条；"
                "为避免大表扫描，不预先统计全部过期记录"
            )
            logger.info(msg)
            self.stdout.write(msg)
        else:
            counts_before = {
                key: queryset.count()
                for key, queryset, _label in querysets
            }
            messages = (
                (
                    "sensor_data",
                    f"传感器采样数据保留 {sensor_days} 天",
                ),
                (
                    "sensor_status",
                    f"传感器状态记录保留 {sensor_days} 天",
                ),
                (
                    "device_status",
                    f"设备状态记录保留 {device_days} 天",
                ),
            )
            for key, label in messages:
                msg = f"{label}，将清理 {counts_before[key]} 条"
                logger.info(msg)
                self.stdout.write(msg)

        total_selected = 0
        if not dry_run:
            # API 可以传 max_records，把一次长清理拆成多个可恢复的小请求；
            # CLI/cron 不传时仍保持原有“一次清完”的行为。
            budget = max_records
            for key, queryset, label in querysets:
                if budget is not None and budget <= 0:
                    break
                deleted, selected = self._batch_delete(
                    queryset,
                    label,
                    batch_size,
                    max_records=budget,
                )
                deleted_counts[key] = deleted
                total_selected += selected
                if budget is not None:
                    # 按本批选中的主记录数消耗预算，即使并发任务先删了其中一部分，
                    # 单次调用也绝不会继续选择超过 max_records 的记录。
                    budget -= selected

            total_deleted = sum(deleted_counts.values())
            if total_selected == 0:
                msg = "  无过期数据需要清理"
                logger.info(msg)
                self.stdout.write(msg)
            else:
                logger.info(f"本次清理共删除 {total_deleted} 条记录")
                self.stdout.write(
                    self.style.SUCCESS(
                        f"本次清理共删除 {total_deleted} 条记录"
                    )
                )

        if dry_run:
            remaining_counts = dict(counts_before)
            remaining_count = sum(remaining_counts.values())
            has_more = False
            remaining_exact = True
        elif max_records is None:
            # 无上限路径的 _batch_delete 会一直选到空，无需再做 COUNT。
            remaining_counts = {
                key: 0 for key, _queryset, _label in querysets
            }
            remaining_count = 0
            has_more = False
            remaining_exact = True
        else:
            remaining_presence = {
                key: queryset.exists()
                for key, queryset, _label in querysets
            }
            has_more = any(remaining_presence.values())
            remaining_counts = {
                key: (None if remaining_presence[key] else 0)
                for key, _queryset, _label in querysets
            }
            # 有界路径只保证“是否还有”，不付出全表 COUNT 的代价。
            remaining_count = None if has_more else 0
            remaining_exact = not has_more

        if has_more:
            self.stdout.write(
                self.style.WARNING(
                    "仍有过期记录，可继续执行下一批"
                )
            )

        # call_command 支持传入 Command 实例。API 读取该结构化结果，不需要解析
        # 面向管理员的文本输出。
        self.cleanup_result = {
            "counts": counts_before,
            "deleted": deleted_counts,
            "remaining": remaining_counts,
            "deleted_count": sum(deleted_counts.values()),
            "remaining_count": remaining_count,
            "remaining_count_is_exact": remaining_exact,
            "has_more": has_more,
        }

    def _batch_delete(
        self,
        queryset,
        label: str,
        batch_size: int,
        *,
        max_records: int | None = None,
    ) -> tuple[int, int]:
        """分批删除，避免一次性删除大量记录导致锁表或内存溢出"""
        total_deleted = 0
        total_selected = 0
        while True:
            if max_records is not None:
                remaining_budget = max_records - total_selected
                if remaining_budget <= 0:
                    break
                current_batch_size = min(batch_size, remaining_budget)
            else:
                current_batch_size = batch_size

            # 每次取一批 ID 删除，避免 queryset 缓存问题
            ids = list(
                queryset.values_list("id", flat=True)[:current_batch_size]
            )
            if not ids:
                break
            total_selected += len(ids)
            _deleted_total, deleted_by_model = (
                queryset.model.objects.filter(id__in=ids).delete()
            )
            # delete() 的第一个返回值含级联对象，清理进度只统计当前历史模型。
            model_label = queryset.model._meta.label
            actual_deleted = deleted_by_model.get(model_label, 0)
            total_deleted += actual_deleted
            logger.info(f"  {label}数据：本批删除 {actual_deleted} 条，累计 {total_deleted} 条")
            self.stdout.write(f"  {label}数据：本批删除 {actual_deleted} 条，累计 {total_deleted} 条")
        if total_selected:
            self.stdout.write(
                self.style.SUCCESS(
                    f"  {label}数据本次删除完成，共 {total_deleted} 条"
                )
            )
        return total_deleted, total_selected
