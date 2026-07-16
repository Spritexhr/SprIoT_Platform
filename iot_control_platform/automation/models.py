"""
自动化规则模型 - 简化版
使用 script（脚本）实现自动化逻辑，devices 字典提供设备和传感器数据
与 devices.Device、sensors.Sensor 模型对接
"""
from django.db import models

from .execution_policy import ensure_script_execution_enabled


PROCESS_STATUS_CHOICES = [
    ('idle', '未启动'),
    ('running', '正在运行'),
    ('stopped_by_user', '由用户停止'),
    ('error_stopped', '由错误停止'),
]


class AutomationRule(models.Model):
    """
    自动化规则 - 运行脚本实现自动化逻辑

    脚本可访问关联的设备和传感器，数据来源于：
    - Sensor：sensor.data_records (SensorData)，最新一条的 data 字段
    - Device：device.status_records (DeviceStatusCollection)，最新一条的 data 字段
    设备控制通过 DeviceType.commands 定义的命令发送
    """

    # ========== 基本信息 ==========
    name = models.CharField(max_length=100, verbose_name="规则名称")
    description = models.TextField(blank=True, verbose_name="规则描述")
    project = models.ForeignKey(
        "projects.Project",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="automation_rules",
        verbose_name="所属项目",
        help_text="留空表示全局规则（不归属任何项目）",
    )
    section = models.ForeignKey(
        "projects.ProjectSection",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="automation_rules",
        verbose_name="所属房间",
        help_text="项目脚本必须归属一个房间；可用资源仅限该房间已导入成员",
    )
    script_id = models.CharField(
        max_length=50,
        unique=True,
        verbose_name="脚本唯一ID",
        help_text="唯一标识，通过 execute_by_script_id('xxx') 调用执行，如 humidity_overflow_print、humidity_alert",
    )
    folder = models.ForeignKey(
        "resource_folders.ResourceFolder",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="automation_rules",
        verbose_name="管理文件夹",
    )
    sort_order = models.IntegerField(default=0, db_index=True, verbose_name="显示顺序")

    # ========== 脚本 ==========
    script = models.TextField(
        blank=True,
        verbose_name="脚本",
        help_text="""
        Python 脚本，两种写法均可：
        1. 类风格：定义含 loop() 方法的类，引擎自动实例化并调用
        2. 函数风格：直接定义顶层 loop() 函数

        from engine import sensors, devices（由引擎注入）
        loop() 返回 True 表示执行成功，False 表示条件未满足。

        完整示例参见 docs/backend/backend_design/AutomationRules_design.md
        """
    )

    # ========== 关联设备列表 ==========
    device_list = models.JSONField(
        default=list,
        verbose_name="设备与传感器列表",
        help_text="""
        脚本中可访问的设备和传感器，device_id 需与 devices.Device 或 sensors.Sensor 表中的 device_id 对应。
        格式示例：
        [
            {"device_id": "DHT11-WEMOS-001", "device_type": "Sensor", "name": "温湿度传感器"},
            {"device_id": "SG_80_01", "device_type": "Device", "name": "LED灯"}
        ]
        """
    )

    # ========== 轮询状态 ==========
    is_launched = models.BooleanField(
        default=False,
        verbose_name="是否启动轮询",
        help_text="标记该规则是否应持续轮询执行"
    )
    poll_interval = models.PositiveIntegerField(
        default=30,
        verbose_name="轮询间隔（秒）",
        help_text="轮询执行的时间间隔，单位为秒，最小 1 秒"
    )
    process_status = models.CharField(
        max_length=20,
        choices=PROCESS_STATUS_CHOICES,
        default='idle',
        verbose_name="进程状态"
    )
    error_message = models.TextField(
        blank=True,
        verbose_name="错误信息",
        help_text="当 process_status 为 error_stopped 时的错误详情"
    )
    last_run_time = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="最后执行时间",
        help_text="后台轮询调度器记录的最后一次执行时间"
    )

    # ========== 时间戳 ==========
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "自动化规则"
        verbose_name_plural = "自动化规则"
        ordering = ['sort_order', '-created_at']

    def __str__(self):
        return self.name

    def get_device_count(self):
        """获取关联设备数量"""
        if not isinstance(self.device_list, list):
            return 0
        return len(self.device_list)

    def get_device_summary(self):
        """获取设备列表简要摘要，要求每项为 {"device_id": "xxx", "device_type": "Sensor/Device"}"""
        if not isinstance(self.device_list, list) or not self.device_list:
            return "-"
        parts = []
        for item in self.device_list[:5]:
            if not isinstance(item, dict):
                return "输入不规范"
            did = item.get('device_id', '-')
            dtype = item.get('device_type', '-')
            parts.append(f"{did}({dtype})")
        if len(self.device_list) > 5:
            parts.append("...")
        return ", ".join(parts)

    def execute(self):
        """在独立限时子进程执行脚本；异常向调用方传播。"""
        ensure_script_execution_enabled()
        from automation.executor import execute_rule_with_timeout
        return execute_rule_with_timeout(self.pk, using=self._state.db or 'default')

    def execute_detailed(self):
        """独立限时执行，并返回手动 API 所需的 stdout / 日志。"""
        ensure_script_execution_enabled()
        from automation.executor import execute_rule_with_timeout_details
        return execute_rule_with_timeout_details(
            self.pk,
            using=self._state.db or 'default',
        )

    @classmethod
    def execute_by_script_id(cls, script_id: str):
        """
        按脚本唯一ID执行规则。
        Returns:
            bool: loop() 的布尔结果；未找到规则返回 False，脚本异常向调用方传播
        """
        ensure_script_execution_enabled()
        try:
            rule = cls.objects.get(script_id=script_id)
            return rule.execute()
        except cls.DoesNotExist:
            return False

    @classmethod
    def execute_by_script_id_with_timed_polling(cls, script_id: str, interval_seconds: float = 30):
        """
        按脚本唯一ID定时轮询执行规则，Ctrl+C 停止。
        Args:
            script_id: 脚本唯一ID
            interval_seconds: 轮询间隔（秒），默认 30
        """
        ensure_script_execution_enabled()
        import time
        try:
            rule = cls.objects.get(script_id=script_id)
        except cls.DoesNotExist:
            return
        print(f"定时轮询已启动：script_id={script_id}，间隔 {interval_seconds} 秒，Ctrl+C 停止\n")
        try:
            while True:
                rule.execute()
                time.sleep(interval_seconds)
        except KeyboardInterrupt:
            print("\n定时轮询已停止")


# ============================================================================
# 控制方案（结构化闭环控制）
# ============================================================================
# 与上面的自由脚本 AutomationRule 并存、互不影响。
# AutomationRule = 用户手写 Python 脚本；ControlScheme = 选模板(双位/PI/PID) + 绑定 + 填参数。
# 核心逻辑：周期性「对比传感器实测值(PV) 与 设定值(SP) → 驱动执行器设备」。

CONTROL_TYPE_CHOICES = [
    ('on_off', '双位控制'),
    ('pi', '比例积分(PI)'),
    ('pid', 'PID控制'),
]

# 作用方向：决定误差符号（PV 偏离 SP 时输出该增大还是减小）
CONTROL_ACTION_CHOICES = [
    ('heat', '正作用（PV 低于 SP 时增大输出/开，如加热）'),
    ('cool', '反作用（PV 高于 SP 时增大输出/开，如降温开阀）'),
]

OUTPUT_MODE_CHOICES = [
    ('analog', '模拟量（下发数值，如舵机角度/阀门开度%）'),
    ('switch', '开关量（开/关命令）'),
]

CONTROL_STATUS_CHOICES = [
    ('idle', '未启用'),
    ('running', '运行中'),
    ('error', '错误停止'),
]


def _default_runtime_state():
    """控制器内部运行态默认值（每拍更新，启停时重置）"""
    return {'integral': 0.0, 'prev_error': 0.0, 'on': False, 'pwm_phase': 0.0}


class ControlScheme(models.Model):
    """
    控制方案 - 基于模板的结构化闭环控制。

    绑定项目内的一只传感器成员（被控量 PV 来源）与一个设备成员（执行器），
    选择控制类型（双位 / PI / PID）并填写参数，启用后由后台调度器周期性执行：
      读 PV → 与 setpoint 比较算出控制量 → 映射成设备命令下发。
    """

    # ========== 基本信息与作用域 ==========
    name = models.CharField(max_length=100, verbose_name="方案名称")
    description = models.TextField(blank=True, verbose_name="方案描述")
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="control_schemes",
        verbose_name="所属项目",
    )
    section = models.ForeignKey(
        "projects.ProjectSection",
        on_delete=models.CASCADE,
        related_name="control_schemes",
        verbose_name="所属房间",
    )

    # ========== 绑定（被控量来源 / 执行器） ==========
    sensor_member = models.ForeignKey(
        "projects.ProjectSensorMember",
        on_delete=models.PROTECT,
        related_name="control_schemes",
        verbose_name="被控量传感器（PV）",
        help_text="被删除前需先解绑控制方案，避免静默失控",
    )
    data_key = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="数据字段",
        help_text="从传感器最新数据中取哪个字段作为 PV；留空则用传感器成员的 data_key",
    )
    device_member = models.ForeignKey(
        "projects.ProjectDeviceMember",
        on_delete=models.PROTECT,
        related_name="control_schemes",
        verbose_name="执行器设备",
    )

    # ========== 控制配置 ==========
    control_type = models.CharField(
        max_length=10, choices=CONTROL_TYPE_CHOICES, default='on_off',
        verbose_name="控制类型",
    )
    setpoint = models.FloatField(default=0.0, verbose_name="设定值（SP）")
    action = models.CharField(
        max_length=10, choices=CONTROL_ACTION_CHOICES, default='cool',
        verbose_name="作用方向",
    )
    sample_interval = models.PositiveIntegerField(
        default=5, verbose_name="控制周期（秒）",
        help_text="后台多久执行一次控制计算，最小 1 秒",
    )
    output_mode = models.CharField(
        max_length=10, choices=OUTPUT_MODE_CHOICES, default='switch',
        verbose_name="输出方式",
    )
    params = models.JSONField(
        default=dict, blank=True,
        verbose_name="算法参数与输出映射",
        help_text=(
            "结构示例：{deadband, kp, ki, kd, out_min, out_max, "
            "analog:{command, param, range_min, range_max}, "
            "switch:{on_command, off_command, convert(threshold|pwm), pwm_period}}"
        ),
    )

    # ========== 运行态 ==========
    runtime_state = models.JSONField(
        default=_default_runtime_state, blank=True,
        verbose_name="控制器内部运行态",
        help_text="integral / prev_error / on / pwm_phase，每拍更新",
    )
    is_enabled = models.BooleanField(default=False, verbose_name="是否启用控制环")
    status = models.CharField(
        max_length=10, choices=CONTROL_STATUS_CHOICES, default='idle',
        verbose_name="运行状态",
    )
    error_message = models.TextField(blank=True, verbose_name="错误信息")
    last_run_time = models.DateTimeField(null=True, blank=True, verbose_name="最后执行时间")
    last_pv = models.FloatField(null=True, blank=True, verbose_name="最近实测值")
    last_output = models.FloatField(null=True, blank=True, verbose_name="最近控制输出")
    last_command = models.CharField(max_length=200, blank=True, verbose_name="最近下发命令")

    # ========== 时间戳 ==========
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "控制方案"
        verbose_name_plural = "控制方案"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name}({self.get_control_type_display()})"

    @property
    def pv_key(self) -> str:
        """实际用于取 PV 的字段名"""
        return self.data_key or (self.sensor_member.data_key if self.sensor_member else '')

    def reset_runtime_state(self):
        """重置控制器内部运行态（启停、重新启用时调用）"""
        self.runtime_state = _default_runtime_state()
