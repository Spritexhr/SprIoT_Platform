from rest_framework import serializers
from .models import AutomationRule, ControlScheme
from .resources import normalize_device_list, validate_scoped_resources
from resource_folders.models import ResourceFolder


class AutomationRuleListSerializer(serializers.ModelSerializer):
    """自动化规则列表序列化器"""
    device_count = serializers.SerializerMethodField()
    project_name = serializers.CharField(source='project.name', read_only=True, default='')
    project_code = serializers.CharField(source='project.code', read_only=True, default='')
    section_name = serializers.CharField(source='section.name', read_only=True, default='')
    folder_info = serializers.SerializerMethodField()

    class Meta:
        model = AutomationRule
        fields = [
            'id', 'name', 'description', 'script_id',
            'project', 'project_name', 'project_code', 'section', 'section_name',
            'device_list', 'device_count',
            'folder', 'folder_info', 'sort_order',
            'is_launched', 'poll_interval', 'process_status', 'error_message',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'device_count',
                            'is_launched', 'process_status', 'error_message']

    def get_device_count(self, obj):
        return obj.get_device_count()

    def get_folder_info(self, obj):
        if not obj.folder_id:
            return None
        return {'id': obj.folder_id, 'name': obj.folder.name, 'parent': obj.folder.parent_id}


class AutomationRuleDetailSerializer(AutomationRuleListSerializer):
    """自动化规则详情序列化器（含脚本内容）"""
    class Meta(AutomationRuleListSerializer.Meta):
        fields = AutomationRuleListSerializer.Meta.fields + ['script']


class AutomationRuleCreateUpdateSerializer(serializers.ModelSerializer):
    """自动化规则创建/更新序列化器"""
    class Meta:
        model = AutomationRule
        fields = [
            'id', 'name', 'description', 'script_id',
            'project', 'section', 'script', 'device_list', 'poll_interval', 'folder',
        ]
        read_only_fields = ['id']

    def validate_folder(self, value):
        if value is not None and value.resource_type != ResourceFolder.AUTOMATION:
            raise serializers.ValidationError('请选择自动化规则文件夹')
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        instance = self.instance
        project = attrs.get('project', getattr(instance, 'project', None))
        section = attrs.get('section', getattr(instance, 'section', None))

        if instance is not None:
            if 'project' in attrs and attrs['project'] != instance.project:
                raise serializers.ValidationError({'project': '规则创建后不能修改所属项目'})
            if 'section' in attrs and attrs['section'] != instance.section:
                raise serializers.ValidationError({'section': '规则创建后不能修改所属房间'})

        if bool(project) != bool(section):
            field = 'section' if project else 'project'
            raise serializers.ValidationError({field: '项目脚本必须同时指定项目和房间'})
        if project and section.project_id != project.id:
            raise serializers.ValidationError({'section': '该房间不属于所选项目'})

        device_list = attrs.get('device_list', getattr(instance, 'device_list', []))
        try:
            device_list = normalize_device_list(device_list)
            if section:
                validate_scoped_resources(device_list, section)
        except serializers.ValidationError as exc:
            raise serializers.ValidationError({'device_list': exc.detail}) from exc
        attrs['device_list'] = device_list
        return attrs


# ============================================================================
# 控制方案（双位 / PI / PID）
# ============================================================================

class ControlSchemeSerializer(serializers.ModelSerializer):
    """控制方案读序列化器：嵌套绑定的传感器/设备关键信息 + 设备可用命令。"""
    control_type_display = serializers.CharField(source='get_control_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    sensor_tag = serializers.CharField(source='sensor_member.tag', read_only=True)
    sensor_id = serializers.CharField(source='sensor_member.sensor.sensor_id', read_only=True)
    device_tag = serializers.CharField(source='device_member.tag', read_only=True)
    device_id = serializers.CharField(source='device_member.device.device_id', read_only=True)
    pv_key = serializers.CharField(read_only=True)
    device_commands = serializers.SerializerMethodField()

    class Meta:
        model = ControlScheme
        fields = [
            'id', 'name', 'description', 'project', 'section',
            'sensor_member', 'sensor_tag', 'sensor_id', 'data_key', 'pv_key',
            'device_member', 'device_tag', 'device_id', 'device_commands',
            'control_type', 'control_type_display', 'setpoint', 'action',
            'sample_interval', 'output_mode', 'params',
            'is_enabled', 'status', 'status_display', 'error_message',
            'last_run_time', 'last_pv', 'last_output', 'last_command',
            'created_at', 'updated_at',
        ]
        read_only_fields = fields  # 该序列化器仅用于读

    def get_device_commands(self, obj):
        from projects.serializers import public_command_schema
        dev = obj.device_member.device if obj.device_member_id else None
        return public_command_schema(dev) if dev else {}


class ControlSchemeCreateUpdateSerializer(serializers.ModelSerializer):
    """控制方案创建/更新序列化器。校验绑定一致性与必填命令映射。"""

    class Meta:
        model = ControlScheme
        fields = [
            'id', 'name', 'description', 'project', 'section',
            'sensor_member', 'data_key', 'device_member',
            'control_type', 'setpoint', 'action', 'sample_interval',
            'output_mode', 'params',
        ]
        read_only_fields = ['id']

    def validate_sample_interval(self, v):
        return max(1, int(v))

    def validate(self, attrs):
        attrs = super().validate(attrs)
        get = lambda k: attrs.get(k) if k in attrs else getattr(self.instance, k, None)
        project = get('project')
        section = get('section')
        sensor_member = get('sensor_member')
        device_member = get('device_member')

        # section 必须属于 project
        if section is not None and project is not None and section.project_id != project.id:
            raise serializers.ValidationError({'section': '该房间不属于此项目'})
        # 绑定的成员必须属于同一项目
        if sensor_member is not None and project is not None and sensor_member.project_id != project.id:
            raise serializers.ValidationError({'sensor_member': '该传感器成员不属于此项目'})
        if device_member is not None and project is not None and device_member.project_id != project.id:
            raise serializers.ValidationError({'device_member': '该设备成员不属于此项目'})

        # 命令映射必填校验
        control_type = get('control_type')
        output_mode = get('output_mode')
        params = get('params') or {}
        if control_type == 'on_off':
            sw = params.get('switch', {}) if isinstance(params.get('switch'), dict) else {}
            if not sw.get('on_command') or not sw.get('off_command'):
                raise serializers.ValidationError(
                    {'params': '双位控制需在 params.switch 配置 on_command 与 off_command'})
        elif control_type in ('pi', 'pid'):
            if output_mode == 'analog':
                ac = params.get('analog', {}) if isinstance(params.get('analog'), dict) else {}
                if not ac.get('command'):
                    raise serializers.ValidationError(
                        {'params': '模拟量输出需在 params.analog 配置 command'})
            else:
                sw = params.get('switch', {}) if isinstance(params.get('switch'), dict) else {}
                if not sw.get('on_command') or not sw.get('off_command'):
                    raise serializers.ValidationError(
                        {'params': '开关量输出需在 params.switch 配置 on_command 与 off_command'})
        return attrs
