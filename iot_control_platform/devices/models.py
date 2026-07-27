"""
设备管理数据模型
用于管理物联网输出器（执行器）设备
参考 sensors 应用结构实现
"""
from django.db import models, transaction
from django.db.models.functions import Now
from django.utils import timezone
from datetime import timedelta


class DeviceType(models.Model):
    """
    设备类型模型
    定义不同类型的输出器（LED、舵机、继电器等）
    存储设备类型级别的固定参数和配置
    参考 SensorType 结构
    """
    DeviceType_id = models.CharField(
        max_length=50,
        unique=True,
        db_index=True,
        verbose_name="设备类型唯一ID",
        help_text="例如：LED-01、FAN-01"
    )

    name = models.CharField(
        max_length=50,
        unique=True,
        verbose_name="设备类型名称",
        help_text="例如：LED灯、智能风扇、继电器"
    )

    description = models.TextField(
        blank=True,
        verbose_name="类型描述",
        help_text="设备类型的详细说明"
    )

    # 配置参数列表（该类型设备的所有可读字段名，含状态值与配置项）
    config_parameters = models.JSONField(
        default=list,
        verbose_name="配置参数列表",
        help_text='该类型设备的所有可读字段名（状态值 + 配置项合并）。示例：["power_state", "brightness", "heartbeat_interval"]'
    )

    # 命令列表（可发送给设备的控制命令）
    commands = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="可用命令列表",
        help_text=
        """
        该类型设备支持的命令列表。示例：
        {
            "turn_on": {
                "mqtt_message": {"command": "power_on"},
                "description": "打开设备",
                "params": []
            },
            "turn_off": {
                "mqtt_message": {"command": "power_off"},
                "description": "关闭设备",
                "params": []
            },
            "set_brightness": {
                "mqtt_message": {"command": "set_brightness", "value": "{val}"},
                "description": "设置亮度",
                "params": ["val"]
            }
        }
        """
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="创建时间"
    )

    class Meta:
        verbose_name = "设备类型"
        verbose_name_plural = "设备类型"
        ordering = ['name']

    def __str__(self):
        return self.name

    def get_config_parameters(self):
        """获取配置参数列表"""
        return self.config_parameters if isinstance(self.config_parameters, list) else []

    def get_heartbeat_interval(self):
        """获取心跳间隔（秒）"""
        return 60  # 默认值


class Device(models.Model):
    """
    设备模型（输出器/执行器）
    参考 Sensor 结构，专注于控制执行
    设备实例使用设备类型中定义的固定变量和参数
    """

    # ========== 基本信息 ==========
    device_id = models.CharField(
        max_length=50,
        unique=True,
        db_index=True,
        verbose_name="设备唯一ID"
    )

    name = models.CharField(
        max_length=100,
        verbose_name="设备名称"
    )

    description = models.TextField(
        blank=True,
        verbose_name="设备描述"
    )

    location = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="设备位置"
    )

    # ========== MQTT 通信 ==========
    mqtt_topic_data = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="状态主题"
    )

    mqtt_topic_control = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="控制主题"
    )

    # ========== 状态管理 ==========
    is_online = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name="是否在线"
    )

    last_seen = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="最后上报时间"
    )

    # ========== 显示顺序 ==========
    sort_order = models.IntegerField(
        default=0,
        db_index=True,
        verbose_name="显示顺序",
        help_text="数值越小越靠前；新建项默认 0 排在最前，由前端拖拽更新"
    )

    # ========== 时间戳 ==========
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="创建时间"
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="更新时间"
    )

    # ========== 关联关系 ==========
    device_type = models.ForeignKey(
        DeviceType,
        on_delete=models.PROTECT,
        related_name='devices',
        verbose_name="设备类型"
    )
    folder = models.ForeignKey(
        "resource_folders.ResourceFolder",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="devices",
        verbose_name="管理文件夹",
    )

    class Meta:
        verbose_name = "设备"
        verbose_name_plural = "设备列表"
        ordering = ['sort_order', '-created_at']

    def __str__(self):
        return f"{self.name} ({self.device_id})"

    def save(self, *args, **kwargs):
        """保存时自动设置 MQTT 主题，与 Arduino 固件保持一致"""
        expected_topics = {
            'mqtt_topic_data': f"iot/devices/{self.device_id}/status",
            'mqtt_topic_control': f"iot/devices/{self.device_id}/control",
        }
        generated_fields = []
        old_values = None

        for field_name, expected_value in expected_topics.items():
            current_value = getattr(self, field_name)
            should_generate = not current_value
            if not should_generate and self.pk and current_value != expected_value:
                if old_values is None:
                    old_values = type(self).objects.filter(pk=self.pk).values(
                        'device_id', 'mqtt_topic_data', 'mqtt_topic_control'
                    ).first()
                if old_values and old_values['device_id'] != self.device_id:
                    old_default = (
                        f"iot/devices/{old_values['device_id']}/status"
                        if field_name == 'mqtt_topic_data'
                        else f"iot/devices/{old_values['device_id']}/control"
                    )
                    # ID 改名时只迁移系统生成的主题，保留用户自定义主题。
                    should_generate = current_value == old_default
            if should_generate:
                setattr(self, field_name, expected_value)
                generated_fields.append(field_name)

        if generated_fields and kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = set(kwargs['update_fields']) | set(generated_fields)

        super().save(*args, **kwargs)

    def get_heartbeat_interval(self):
        """获取心跳间隔（从类型获取）"""
        if self.device_type:
            return self.device_type.get_heartbeat_interval()
        return 60

    @property
    def computed_is_online(self):
        """根据平台统一的离线阈值实时计算在线状态。"""
        if not self.last_seen:
            return False
        from .online_status import get_device_offline_timeout
        timeout = get_device_offline_timeout()
        age = (timezone.now() - self.last_seen).total_seconds()
        return 0 <= age < timeout

    def check_online_status(self):
        """
        检查设备是否在线
        如果超过心跳间隔的3倍时间未收到心跳，标记为离线
        """
        if self.last_seen:
            from .online_status import get_device_offline_timeout
            timeout = get_device_offline_timeout()
            time_diff = (timezone.now() - self.last_seen).total_seconds()

            if time_diff < 0 or time_diff > timeout:
                if self.is_online:
                    self.is_online = False
                    self.save(update_fields=['is_online', 'updated_at'])
                return False
            return True
        return False

    def update_heartbeat(self, timestamp=None):
        """以服务器接收时间单调更新心跳；未来时间会被截断。"""
        now = timezone.now()
        ts = timestamp or now
        if timezone.is_naive(ts):
            ts = timezone.make_aware(ts, timezone.get_current_timezone())
        if ts > now:
            ts = now

        type(self).objects.filter(pk=self.pk).update(
            last_seen=models.Case(
                models.When(last_seen__isnull=True, then=models.Value(ts)),
                models.When(last_seen__lt=ts, then=models.Value(ts)),
                models.When(
                    last_seen__gt=Now() + timedelta(minutes=5),
                    then=Now(),
                ),
                default=models.F('last_seen'),
                output_field=models.DateTimeField(),
            ),
            is_online=True,
            updated_at=Now(),
        )
        if (
            self.last_seen is None
            or self.last_seen < ts
            or self.last_seen > now + timedelta(minutes=5)
        ):
            self.last_seen = ts
        self.is_online = True
        self.updated_at = now

    def get_data_count(self, hours=24):
        """
        获取指定时间内的数据记录数

        Args:
            hours: 小时数，默认24小时

        Returns:
            int: 数据记录数
        """
        start_time = timezone.now() - timedelta(hours=hours)
        return self.status_records.filter(received_at__gte=start_time).count()


class DeviceStatusCollection(models.Model):
    """
    设备状态记录模型
    存储设备上报的状态事件（与 SensorStatusCollection 对齐）
    """
    device = models.ForeignKey(
        Device,
        on_delete=models.CASCADE,
        related_name='status_records',
        verbose_name="设备"
    )

    # 状态内容（灵活存储设备上报的状态字段）
    data = models.JSONField(
        verbose_name="状态内容",
        help_text="设备上报的状态字段，如：{'power_state': true, 'brightness': 80}"
    )

    # 事件名（参照 SensorStatusCollection.event_name）
    event_name = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="事件名",
        help_text="状态事件标签，如 online / offline / interval_updated"
    )

    # 时间戳
    timestamp = models.DateTimeField(
        db_index=True,
        verbose_name="状态时间",
        help_text="状态记录的时间戳"
    )

    message_id = models.CharField(
        max_length=128,
        null=True,
        blank=True,
        verbose_name="MQTT 消息幂等 ID",
        help_text="设备可选上报；同一设备内用于去重 QoS 重投消息",
    )

    received_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name="接收时间",
        help_text="服务器接收到数据的时间"
    )

    class Meta:
        verbose_name = "设备状态"
        verbose_name_plural = "设备状态记录"
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['device', '-timestamp']),
            models.Index(
                fields=['device', '-received_at'],
                name='device_recv_latest_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['device', 'message_id'],
                name='uniq_device_status_msg',
            ),
        ]

    def __str__(self):
        return f"{self.device.device_id} - {self.timestamp}"

    def save(self, *args, **kwargs):
        """原子写入状态记录，并在 post_save 广播前更新在线状态。"""
        if not self.timestamp:
            self.timestamp = timezone.now()

        is_new = self._state.adding
        with transaction.atomic():
            if is_new:
                # 设备自带 timestamp 仅用于历史数据排序，不能决定在线心跳。
                self.device.update_heartbeat()
            super().save(*args, **kwargs)
