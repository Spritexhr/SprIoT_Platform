"""
sensors 应用配置
负责传感器模型初始化；MQTT 生命周期由独立 mqtt_runner 管理
"""
from django.apps import AppConfig


class SensorsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = "sensors"
    verbose_name = '传感器管理'

    # 兼容旧代码的只读标记。Paho 生命周期已收口到 mqtt_runner 命令，
    # ASGI/web/management command 的 AppConfig.ready() 不再产生网络副作用。
    mqtt_service_started = False

    def ready(self):
        """模型应用初始化不启动线程、不连接 broker、不写数据库。"""
        return None
