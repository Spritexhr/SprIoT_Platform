from django.db import transaction
from django.db.models import Count, F, Q
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from .models import ResourceFolder
from .serializers import ResourceFolderSerializer


class ResourceFolderViewSet(viewsets.ModelViewSet):
    serializer_class = ResourceFolderSerializer
    pagination_class = None

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy", "reorder"):
            return [IsAuthenticated(), IsAdminUser()]
        return [IsAuthenticated()]

    def get_queryset(self):
        qs = ResourceFolder.objects.annotate(
            child_count=Count("children", distinct=True),
            sensor_count=Count("sensors", distinct=True),
            device_count=Count("devices", distinct=True),
            automation_count=Count("automation_rules", distinct=True),
        )
        resource_type = self.request.query_params.get("resource_type")
        if resource_type:
            qs = qs.filter(resource_type=resource_type)
        return qs.annotate(
            resource_count=F("sensor_count") + F("device_count") + F("automation_count")
        ).order_by("sort_order", "id")

    def perform_create(self, serializer):
        resource_type = serializer.validated_data["resource_type"]
        parent = serializer.validated_data.get("parent")
        max_order = ResourceFolder.objects.filter(
            resource_type=resource_type, parent=parent
        ).aggregate(value=Count("id"))["value"]
        serializer.save(sort_order=max_order)

    def destroy(self, request, *args, **kwargs):
        folder = self.get_object()
        if (
            folder.children.exists()
            or folder.sensors.exists()
            or folder.devices.exists()
            or folder.automation_rules.exists()
        ):
            return Response(
                {"detail": "非空文件夹不能删除，请先移动其中的资源和子文件夹"},
                status=status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)

    @action(detail=False, methods=["post"], url_path="reorder")
    def reorder(self, request):
        order = request.data.get("order")
        if not isinstance(order, list) or not all(isinstance(x, int) for x in order):
            return Response({"detail": "order 必须是文件夹 ID 数组"}, status=400)
        folders = list(ResourceFolder.objects.filter(id__in=order))
        if len(folders) != len(set(order)):
            return Response({"detail": "包含不存在或重复的文件夹"}, status=400)
        if folders:
            scopes = {(item.resource_type, item.parent_id) for item in folders}
            if len(scopes) != 1:
                return Response({"detail": "只能对同级同类型文件夹排序"}, status=400)
        with transaction.atomic():
            for index, folder_id in enumerate(order, start=1):
                ResourceFolder.objects.filter(pk=folder_id).update(sort_order=index)
        return Response({"updated": len(order)})
