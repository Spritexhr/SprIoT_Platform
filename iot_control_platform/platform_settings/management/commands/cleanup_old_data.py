"""
按 platform_settings 配置的数据留存天数清理过期数据
从 platform_config 读取 sensor_data_retention_days、device_data_retention_days
可配合 cron 定时执行，或通过 API 触发
采用分批删除避免大表锁表或内存溢出
"""
import logging
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from config.platform_config import get_config

logger = logging.getLogger("platform_settings")

# 每批删除的记录数
BATCH_SIZE = 1000


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

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        batch_size = options["batch_size"]
        if batch_size < 1 or batch_size > 10000:
            raise CommandError("--batch-size 必须在 1 到 10000 之间")
        if dry_run:
            msg = "【试运行模式】不会实际删除数据"
            logger.info(msg)
            self.stdout.write(self.style.WARNING(msg))

        sensor_days = get_config("sensor_data_retention_days", 30, int)
        device_days = get_config("device_data_retention_days", 30, int)
        if sensor_days < 1 or sensor_days > 3650:
            raise CommandError("sensor_data_retention_days 必须在 1 到 3650 之间")
        if device_days < 1 or device_days > 3650:
            raise CommandError("device_data_retention_days 必须在 1 到 3650 之间")

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

        sensor_data_count = sensor_data_qs.count()
        sensor_status_count = sensor_status_qs.count()
        device_count = device_qs.count()

        logger.info(f"传感器采样数据保留 {sensor_days} 天，将清理 {sensor_data_count} 条")
        logger.info(f"传感器状态记录保留 {sensor_days} 天，将清理 {sensor_status_count} 条")
        logger.info(f"设备状态记录保留 {device_days} 天，将清理 {device_count} 条")
        self.stdout.write(f"传感器采样数据保留 {sensor_days} 天，将清理 {sensor_data_count} 条")
        self.stdout.write(f"传感器状态记录保留 {sensor_days} 天，将清理 {sensor_status_count} 条")
        self.stdout.write(f"设备状态记录保留 {device_days} 天，将清理 {device_count} 条")

        if not dry_run:
            total_deleted = 0
            if sensor_data_count > 0:
                deleted = self._batch_delete(sensor_data_qs, "传感器采样", batch_size)
                total_deleted += deleted
            if sensor_status_count > 0:
                deleted = self._batch_delete(sensor_status_qs, "传感器状态", batch_size)
                total_deleted += deleted
            if device_count > 0:
                deleted = self._batch_delete(device_qs, "设备状态", batch_size)
                total_deleted += deleted
            if sensor_data_count == 0 and sensor_status_count == 0 and device_count == 0:
                msg = "  无过期数据需要清理"
                logger.info(msg)
                self.stdout.write(msg)
            else:
                logger.info(f"清理完成，共删除 {total_deleted} 条记录")
                self.stdout.write(self.style.SUCCESS(f"清理完成，共删除 {total_deleted} 条记录"))

    def _batch_delete(self, queryset, label: str, batch_size: int) -> int:
        """分批删除，避免一次性删除大量记录导致锁表或内存溢出"""
        total_deleted = 0
        while True:
            # 每次取一批 ID 删除，避免 queryset 缓存问题
            ids = list(queryset.values_list('id', flat=True)[:batch_size])
            if not ids:
                break
            deleted_count, _ = queryset.model.objects.filter(id__in=ids).delete()
            # 从关联删除的统计中提取当前模型的删除数
            model_label = f"{queryset.model._meta.app_label}.{queryset.model._meta.model_name}"
            actual_deleted = deleted_count if isinstance(deleted_count, int) else deleted_count.get(model_label, 0)
            total_deleted += actual_deleted
            logger.info(f"  {label}数据：本批删除 {actual_deleted} 条，累计 {total_deleted} 条")
            self.stdout.write(f"  {label}数据：本批删除 {actual_deleted} 条，累计 {total_deleted} 条")
        self.stdout.write(self.style.SUCCESS(f"  {label}数据删除完成，共 {total_deleted} 条"))
        return total_deleted
