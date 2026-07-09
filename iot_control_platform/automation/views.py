import logging
import io
import contextlib
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from config.permissions import IsSuperuser
from rest_framework.response import Response
from django.db import transaction
from django.db.models import Q
from .models import AutomationRule, ControlScheme
from .serializers import (
    AutomationRuleListSerializer,
    AutomationRuleDetailSerializer,
    AutomationRuleCreateUpdateSerializer,
    ControlSchemeSerializer,
    ControlSchemeCreateUpdateSerializer,
)
from .resources import RuleResourceUnavailable, effective_device_list
from resource_folders.models import ResourceFolder

logger = logging.getLogger(__name__)


_CAPTURE_LOGGERS = ('automation', 'services.devices_service', 'services.sensors_service')


class _LogCaptureHandler(logging.Handler):
    """临时 handler，在规则执行期间捕获 INFO 及以上级别的应用日志"""

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.records: list[dict] = []

    def emit(self, record):
        self.records.append({
            'level': record.levelname,
            'message': f'[{record.name}] {record.getMessage()}',
        })


class AutomationRuleViewSet(viewsets.ModelViewSet):
    """
    自动化规则 CRUD API
    项目/房间脚本由工作人员管理；全局脚本仍仅超级用户可改。
    执行、启动、停止仅限工作人员；普通登录用户仅可查看。
    """
    queryset = AutomationRule.objects.select_related('project', 'section', 'folder').all()

    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update', 'destroy'):
            # 项目脚本属于项目/场景配置：is_staff 可管理。
            # 全局脚本不受项目资源隔离，保留 is_superuser 级别。
            if self.action == 'create':
                is_project_rule = bool(
                    self.request.data.get('project') and self.request.data.get('section')
                )
            else:
                is_project_rule = AutomationRule.objects.filter(
                    pk=self.kwargs.get('pk'), project__isnull=False, section__isnull=False,
                ).exists()
            permission = IsAdminUser() if is_project_rule else IsSuperuser()
            return [IsAuthenticated(), permission]
        if self.action in ('execute', 'launch', 'stop', 'bulk_move', 'reorder'):
            return [IsAuthenticated(), IsAdminUser()]
        return [IsAuthenticated()]

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return AutomationRuleDetailSerializer
        if self.action in ('create', 'update', 'partial_update'):
            return AutomationRuleCreateUpdateSerializer
        return AutomationRuleListSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        # 搜索
        search = self.request.query_params.get('search')
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(script_id__icontains=search)
                | Q(description__icontains=search)
            )
        project = self.request.query_params.get('project')
        section = self.request.query_params.get('section')
        if project:
            qs = qs.filter(project_id=project)
        if section:
            qs = qs.filter(section_id=section)
        folder = self.request.query_params.get('folder')
        if folder == 'unfiled':
            qs = qs.filter(folder__isnull=True)
        elif folder:
            if not folder.isdigit():
                return qs.none()
            qs = qs.filter(folder_id=int(folder), folder__resource_type=ResourceFolder.AUTOMATION)
        return qs

    def _ensure_resources_available(self, rule):
        try:
            effective_device_list(rule)
        except RuleResourceUnavailable as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return None

    @action(detail=False, methods=['post'], url_path='bulk-move')
    def bulk_move(self, request):
        rule_ids = request.data.get('rule_ids')
        folder_id = request.data.get('folder')
        if not isinstance(rule_ids, list) or not rule_ids or not all(isinstance(x, int) for x in rule_ids):
            return Response({'detail': 'rule_ids 必须是非空整数数组'}, status=400)
        folder = None
        if folder_id is not None:
            try:
                folder = ResourceFolder.objects.get(pk=folder_id, resource_type=ResourceFolder.AUTOMATION)
            except ResourceFolder.DoesNotExist:
                return Response({'detail': '自动化规则文件夹不存在'}, status=400)
        unique_ids = set(rule_ids)
        qs = AutomationRule.objects.filter(id__in=unique_ids)
        if qs.count() != len(unique_ids):
            return Response({'detail': '包含不存在的自动化规则'}, status=400)
        if qs.filter(project__isnull=True, section__isnull=True).exists() and not request.user.is_superuser:
            return Response({'detail': '全局自动化规则仅超级用户可移动'}, status=status.HTTP_403_FORBIDDEN)
        updated = qs.update(folder=folder)
        return Response({'updated': updated})

    @action(detail=False, methods=['post'], url_path='reorder')
    def reorder(self, request):
        order = request.data.get('order')
        if not isinstance(order, list) or not all(isinstance(x, int) for x in order):
            return Response({'detail': 'order 必须是规则 ID 整数数组'}, status=status.HTTP_400_BAD_REQUEST)
        if (
            AutomationRule.objects.filter(id__in=order, project__isnull=True, section__isnull=True).exists()
            and not request.user.is_superuser
        ):
            return Response({'detail': '全局自动化规则仅超级用户可排序'}, status=status.HTTP_403_FORBIDDEN)

        folder = request.data.get('folder')
        page = request.data.get('page')
        page_size = request.data.get('page_size')
        scope = AutomationRule.objects.all()
        if folder == 'unfiled' or folder is None:
            scope = scope.filter(folder__isnull=True)
        else:
            scope = scope.filter(folder_id=folder, folder__resource_type=ResourceFolder.AUTOMATION)

        if page is not None or page_size is not None:
            try:
                page = int(page)
                page_size = int(page_size)
                if page < 1 or page_size < 1 or page_size > 96:
                    raise ValueError
            except (TypeError, ValueError):
                return Response({'detail': 'page/page_size 参数无效'}, status=400)
            all_ids = list(scope.order_by('sort_order', '-created_at').values_list('id', flat=True))
            start = (page - 1) * page_size
            page_ids = all_ids[start:start + page_size]
            if len(order) != len(page_ids) or set(order) != set(page_ids):
                return Response({'detail': '排序内容与当前页资源不一致，请刷新后重试'}, status=409)
            all_ids[start:start + len(page_ids)] = order
            rows = list(AutomationRule.objects.filter(id__in=all_ids))
            row_map = {row.id: row for row in rows}
            for index, rule_id in enumerate(all_ids, start=1):
                row_map[rule_id].sort_order = index
            with transaction.atomic():
                AutomationRule.objects.bulk_update(rows, ['sort_order'])
        else:
            with transaction.atomic():
                for index, rule_id in enumerate(order, start=1):
                    AutomationRule.objects.filter(pk=rule_id).update(sort_order=index)
        return Response({'updated': len(order)})

    @action(detail=True, methods=['post'], url_path='launch')
    def launch(self, request, pk=None):
        """启动轮询：标记规则为持续轮询状态，可附带轮询间隔"""
        rule = self.get_object()
        invalid_response = self._ensure_resources_available(rule)
        if invalid_response:
            return invalid_response
        poll_interval = request.data.get('poll_interval')
        if poll_interval is not None:
            try:
                poll_interval = max(1, int(poll_interval))
                rule.poll_interval = poll_interval
            except (ValueError, TypeError):
                pass
        rule.is_launched = True
        rule.process_status = 'running'
        rule.error_message = ''
        rule.last_run_time = None  # 重新启动时清空上次执行时间
        rule.save(update_fields=['is_launched', 'process_status', 'error_message',
                                 'poll_interval', 'last_run_time', 'updated_at'])
        return Response({
            'id': rule.id,
            'is_launched': rule.is_launched,
            'process_status': rule.process_status,
            'poll_interval': rule.poll_interval,
        })

    @action(detail=True, methods=['post'], url_path='stop')
    def stop(self, request, pk=None):
        """停止轮询：标记规则为停止状态，可附带原因和错误信息"""
        rule = self.get_object()
        reason = request.data.get('reason', 'user')
        error_message = request.data.get('error_message', '')

        rule.is_launched = False
        if reason == 'error':
            rule.process_status = 'error_stopped'
            rule.error_message = error_message
        else:
            rule.process_status = 'stopped_by_user'
            rule.error_message = ''
        rule.save(update_fields=['is_launched', 'process_status', 'error_message', 'updated_at'])
        return Response({
            'id': rule.id,
            'is_launched': rule.is_launched,
            'process_status': rule.process_status,
            'error_message': rule.error_message,
        })

    @action(detail=False, methods=['get'], url_path='available-sources')
    def available_sources(self, request):
        """返回全局资源，或指定项目房间中已导入的资源。"""
        from sensors.models import Sensor
        from devices.models import Device
        from projects.models import ProjectSection

        project_id = request.query_params.get('project')
        section_id = request.query_params.get('section')
        if bool(project_id) != bool(section_id):
            return Response(
                {'detail': 'project 与 section 必须同时提供'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if project_id and section_id:
            try:
                section = ProjectSection.objects.get(pk=section_id, project_id=project_id)
            except (ProjectSection.DoesNotExist, ValueError):
                return Response({'detail': '项目或房间不存在'}, status=status.HTTP_404_NOT_FOUND)
            sensor_members = section.sensor_members.select_related(
                'sensor', 'sensor__sensor_type'
            ).order_by('sort_order', 'id')
            device_members = section.device_members.select_related(
                'device', 'device__device_type'
            ).order_by('sort_order', 'id')

            sensors_data = []
            seen_sensors = set()
            for member in sensor_members:
                sensor = member.sensor
                if sensor.sensor_id in seen_sensors:
                    continue
                seen_sensors.add(sensor.sensor_id)
                fields = sensor.sensor_type.data_fields if sensor.sensor_type_id else []
                sensors_data.append({
                    'id': sensor.sensor_id,
                    'name': member.tag or sensor.name,
                    'data_fields': fields,
                })

            devices_data = []
            for member in device_members:
                device = member.device
                commands = (
                    list(device.device_type.commands.keys())
                    if device.device_type_id and isinstance(device.device_type.commands, dict)
                    else []
                )
                devices_data.append({
                    'id': device.device_id,
                    'name': member.tag or device.name,
                    'commands': commands,
                })
            return Response({'sensors': sensors_data, 'devices': devices_data})

        sensors_data = []
        for s in Sensor.objects.select_related('sensor_type').order_by('name'):
            fields = s.sensor_type.data_fields if s.sensor_type else []
            sensors_data.append({
                'id': s.sensor_id,
                'name': s.name,
                'data_fields': fields,
            })

        devices_data = []
        for d in Device.objects.select_related('device_type').order_by('name'):
            cmds = list(d.device_type.commands.keys()) if d.device_type and d.device_type.commands else []
            devices_data.append({
                'id': d.device_id,
                'name': d.name,
                'commands': cmds,
            })

        return Response({'sensors': sensors_data, 'devices': devices_data})

    @action(detail=True, methods=['post'], url_path='execute')
    def execute(self, request, pk=None):
        """
        手动执行一次规则，捕获 print 输出和 WARNING+ 日志并返回
        """
        rule = self.get_object()
        invalid_response = self._ensure_resources_available(rule)
        if invalid_response:
            return invalid_response

        stdout_capture = io.StringIO()
        log_capture = _LogCaptureHandler()
        captured_loggers = [logging.getLogger(name) for name in _CAPTURE_LOGGERS]
        for lg in captured_loggers:
            lg.addHandler(log_capture)
        try:
            with contextlib.redirect_stdout(stdout_capture):
                success = rule.execute()
            output = stdout_capture.getvalue()
            return Response({
                'success': success,
                'output': output,
                'logs': log_capture.records,
                'rule_name': rule.name,
            })
        except Exception as e:
            logger.exception("执行自动化规则失败 [%s]", rule.name)
            output = stdout_capture.getvalue()
            return Response({
                'success': False,
                'output': output,
                'logs': log_capture.records,
                'error': str(e),
                'rule_name': rule.name,
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        finally:
            for lg in captured_loggers:
                lg.removeHandler(log_capture)


class ControlSchemeViewSet(viewsets.ModelViewSet):
    """
    控制方案（双位 / PI / PID）CRUD + 启停 + 单拍测试 + 模板。
    增删改、启停和试运行仅限工作人员；普通登录用户仅可查看。
    """
    queryset = ControlScheme.objects.select_related(
        'sensor_member__sensor', 'device_member__device', 'project', 'section'
    ).all()

    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update', 'destroy'):
            return [IsAuthenticated(), IsAdminUser()]
        if self.action in ('enable', 'disable', 'step'):
            return [IsAuthenticated(), IsAdminUser()]
        return [IsAuthenticated()]

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return ControlSchemeCreateUpdateSerializer
        return ControlSchemeSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        project = self.request.query_params.get('project')
        section = self.request.query_params.get('section')
        if project:
            qs = qs.filter(project_id=project)
        if section:
            qs = qs.filter(section_id=section)
        return qs

    @action(detail=True, methods=['post'], url_path='enable')
    def enable(self, request, pk=None):
        """启用控制环：重置内部运行态并标记为运行中。"""
        scheme = self.get_object()
        scheme.reset_runtime_state()
        scheme.is_enabled = True
        scheme.status = 'running'
        scheme.error_message = ''
        scheme.last_run_time = None
        scheme.save(update_fields=['runtime_state', 'is_enabled', 'status',
                                   'error_message', 'last_run_time', 'updated_at'])
        return Response(ControlSchemeSerializer(scheme).data)

    @action(detail=True, methods=['post'], url_path='disable')
    def disable(self, request, pk=None):
        """停用控制环。"""
        scheme = self.get_object()
        scheme.is_enabled = False
        scheme.status = 'idle'
        scheme.error_message = ''
        scheme.save(update_fields=['is_enabled', 'status', 'error_message', 'updated_at'])
        return Response(ControlSchemeSerializer(scheme).data)

    @action(detail=True, methods=['post'], url_path='step')
    def step(self, request, pk=None):
        """手动跑一拍控制（真实下发），返回算得的 PV / 输出 / 命令，供前端"试一下"。"""
        from .controllers import run_control_scheme
        scheme = self.get_object()
        send = request.data.get('send', True)
        result = run_control_scheme(scheme, send=bool(send))
        return Response({**result, 'scheme': ControlSchemeSerializer(scheme).data})

    @action(detail=False, methods=['get'], url_path='templates')
    def templates(self, request):
        """返回三套控制方案模板（参数 schema + 默认值），供前端表单使用。"""
        from .controllers import CONTROL_TEMPLATES
        return Response({'templates': CONTROL_TEMPLATES})
