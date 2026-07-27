"""
传感器管理数据模型
用于管理物联网输入器（传感器）设备和数据
"""
from django.db import models, transaction
from django.db.models.functions import Now
from django.utils import timezone
from django.contrib.auth.models import User
from datetime import timedelta
#from core.models import BaseIoTDevice
import json


class SensorType(models.Model):
    """
    传感器类型模型
    定义不同类型的传感器（DHT11、DHT22、BMP180等）
    存储传感器类型级别的固定参数和配置
    """
    SensorType_id = models.CharField(
        max_length=50, 
        unique=True,
        db_index=True,
        verbose_name="传感器类型唯一ID"
    )

    name = models.CharField(
        max_length=50,
        unique=True,
        verbose_name="传感器类型名称",
        help_text="例如：DHT11温湿度传感器、BMP180气压传感器"
    )
    
    description = models.TextField(
        blank=True,
        verbose_name="类型描述",
        help_text="传感器类型的详细说明"
    )
    
    # 数据字段列表（传感器上传数据内容中需要提取的字段）
    data_fields = models.JSONField(
        default=list,
        verbose_name="数据字段列表",
        help_text='传感器上报的数据字段名称列表。示例：["temperature",""humidity"]'
    )
    
    # 设备参数列表（传感器上传状态的时候需要提取的字段）
    config_parameters = models.JSONField(
        default=list,
        verbose_name="配置参数列表",
        help_text='可配置的参数名称列表。示例：["samplingInterval", "is_enabled"]'
    )
    
    # 命令列表（可执行的命令）
    commands = models.JSONField(
        default=list,
        verbose_name="可用命令列表",
        help_text=
        """
        该类型传感器支持的命令列表。示例：        
        {
            "turn_on": {
                "mqtt_message": {"command": "enable"},
                "description": "启动传感器",
                "params": [] 
            },
            "turn_off": {
                "mqtt_message": {"command": "disable"},
                "description": "关闭传感器",
                "params": []
            },
            "set_interval": {
                "mqtt_message": {
                    "command": "set_interval",
                    "interval": "{val}"
                },
                "description": "设置传输间隔",
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
        verbose_name = "传感器类型"
        verbose_name_plural = "传感器类型"
        ordering = ['name']
    
    def __str__(self):
        return self.name

class Sensor(models.Model):
    """
    传感器模型（输入器）
    专注于数据采集，使用传感器类型中定义的固定变量和参数。
    业务逻辑已迁移到 sensors.services.sensor_service。
    """

    # ========== 基本信息 ==========
    sensor_id = models.CharField(
        max_length=50, 
        unique=True,
        db_index=True,
        verbose_name="传感器唯一ID"
    )
    
    name = models.CharField(
        max_length=100,
        verbose_name="传感器名称"
    )
    
    description = models.TextField(
        blank=True,
        verbose_name="传感器描述"
    )
    
    location = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="传感器位置"
    )

    # ========== MQTT 通信 ==========
    mqtt_topic_data = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="数据主题"
    )

    mqtt_topic_status = models.CharField(
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
    sensor_type = models.ForeignKey(
        'SensorType',  # 假设 SensorType 在同一文件或已导入
        on_delete=models.PROTECT,
        related_name='sensors',
        verbose_name="传感器类型"
    )
    folder = models.ForeignKey(
        "resource_folders.ResourceFolder",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sensors",
        verbose_name="管理文件夹",
    )

    class Meta:
        verbose_name = "传感器"
        verbose_name_plural = "传感器列表"
        ordering = ['sort_order', '-created_at']

    def __str__(self):
        return f"{self.name} ({self.sensor_id})"

    @property
    def computed_is_online(self):
        """根据 last_seen 实时计算在线状态：3分钟内有上报视为在线"""
        if not self.last_seen:
            return False
        age = timezone.now() - self.last_seen
        return timedelta(0) <= age < timedelta(minutes=3)

    def update_last_seen(self, timestamp=None):
        """
        以服务器接收时间更新最后上报时间。

        ``timestamp`` 仅保留给服务器内部调用；超过当前服务器时间的值会被截断，
        旧值不会让 ``last_seen`` 回退。数据库条件表达式保证并发上报时单调更新。
        """
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
                # 仅修复明显的历史未来脏值。使用数据库语句执行时刻并留出
                # 5 分钟时钟偏差，避免一个较早开始、较晚落库的并发请求把
                # 另一请求刚写入的合法新心跳误判为“未来”并回退。
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

    def save(self, *args, **kwargs):
        """保存时自动设置 MQTT 主题，与 Arduino 固件保持一致"""
        expected_topics = {
            'mqtt_topic_data': f"iot/sensors/{self.sensor_id}/data",
            'mqtt_topic_status': f"iot/sensors/{self.sensor_id}/status",
            'mqtt_topic_control': f"iot/sensors/{self.sensor_id}/control",
        }
        generated_fields = []
        old_values = None
        for field_name, expected_value in expected_topics.items():
            current_value = getattr(self, field_name)
            should_generate = not current_value
            if not should_generate and self.pk and current_value != expected_value:
                if old_values is None:
                    old_values = type(self).objects.filter(pk=self.pk).values(
                        'sensor_id',
                        'mqtt_topic_data',
                        'mqtt_topic_status',
                        'mqtt_topic_control',
                    ).first()
                if old_values and old_values['sensor_id'] != self.sensor_id:
                    suffix = {
                        'mqtt_topic_data': 'data',
                        'mqtt_topic_status': 'status',
                        'mqtt_topic_control': 'control',
                    }[field_name]
                    old_default = f"iot/sensors/{old_values['sensor_id']}/{suffix}"
                    # ID 改名时只迁移系统生成主题，用户自定义主题保持不变。
                    should_generate = current_value == old_default
            if should_generate:
                setattr(self, field_name, expected_value)
                generated_fields.append(field_name)

        # update_fields 场景也要把新生成的主题真正写入数据库。
        if generated_fields and kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = set(kwargs['update_fields']) | set(generated_fields)

        super().save(*args, **kwargs)

class SensorStatusCollection(models.Model):
    """
    传感器状态采集记录模型
    存储传感器上报的所有状态数据
    """
    sensor = models.ForeignKey(
        Sensor,
        on_delete=models.CASCADE,
        related_name='status_records',
        verbose_name="传感器"
    )
    
    # 数据内容（灵活存储各种传感器数据）
    data = models.JSONField(
        verbose_name="数据内容",
        help_text="传感器上报的状态数据，如：{'is_enabled': true, 'samplingInterval': 120}"
    )
    
    # 时间戳
    timestamp = models.DateTimeField(
        db_index=True,
        verbose_name="数据时间",
        help_text="数据采集的时间戳"
    )
    
    #事件名称
    event_name = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="事件名称",
        help_text="传感器状态事件的名称，例如：online、offline、interval_updated"
    )

    message_id = models.CharField(
        max_length=128,
        null=True,
        blank=True,
        verbose_name="MQTT 消息幂等 ID",
        help_text="设备可选上报；同一传感器内用于去重 QoS 重投消息",
    )

    received_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name="接收时间",
        help_text="服务器接收到数据的时间"
    )
    
    class Meta:
        verbose_name = "传感器状态数据"
        verbose_name_plural = "传感器状态数据记录"
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['sensor', '-timestamp']),
            models.Index(
                fields=['sensor', '-received_at'],
                name='sensor_recv_latest_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['sensor', 'message_id'],
                name='uniq_sensor_status_msg',
            ),
        ]
    
    def __str__(self):
        return f"{self.sensor.sensor_id} - {self.timestamp}"

    def save(self, *args, **kwargs):
        """原子写入状态记录，并在 post_save 广播前更新在线状态。"""
        is_new = self._state.adding
        with transaction.atomic():
            if is_new:
                # 在线状态使用服务器接收时间，不能信任设备自带 timestamp。
                self.sensor.update_last_seen()
            super().save(*args, **kwargs)
    

class SensorData(models.Model):
    """
    传感器数据记录模型
    存储传感器上报的所有历史数据
    """
    sensor = models.ForeignKey(
        Sensor,
        on_delete=models.CASCADE,
        related_name='data_records',
        verbose_name="传感器"
    )
    
    # 数据内容（灵活存储各种传感器数据）
    data = models.JSONField(
        verbose_name="数据内容",
        help_text="传感器上报的原始数据，如：{'temperature': 25.5, 'humidity': 60.0}"
    )
    
    # 时间戳
    timestamp = models.DateTimeField(
        db_index=True,
        verbose_name="数据时间",
        help_text="数据采集的时间戳"
    )

    message_id = models.CharField(
        max_length=128,
        null=True,
        blank=True,
        verbose_name="MQTT 消息幂等 ID",
        help_text="设备可选上报；同一传感器内用于去重 QoS 重投消息",
    )

    received_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name="接收时间",
        help_text="服务器接收到数据的时间"
    )
    
    class Meta:
        verbose_name = "传感器数据"
        verbose_name_plural = "传感器数据记录"
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['sensor', '-timestamp']),
            models.Index(
                fields=['sensor', '-received_at'],
                name='sensor_data_recv_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['sensor', 'message_id'],
                name='uniq_sensor_data_msg',
            ),
        ]
    
    def __str__(self):
        return f"{self.sensor.sensor_id} - {self.timestamp}"

    def save(self, *args, **kwargs):
        """原子写入数据记录，并在 post_save 广播前更新在线状态。"""
        is_new = self._state.adding
        with transaction.atomic():
            if is_new:
                # 数据时间仍按设备上报值保存；在线心跳只认服务器接收时间。
                self.sensor.update_last_seen()
            super().save(*args, **kwargs)
    
    
