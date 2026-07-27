"""
项目（场景）WebSocket consumer。

URL: /ws/projects/<project_id>/

订阅 group：
- projects.{id}   本项目传感器采样流（projects.signals → dispatch.publish_project_sample）
- devices.all     主层全量设备状态流（按本项目已绑定设备过滤后转发）

事件：
- 建连时 _send_initial 发一个 snapshot（含本项目可见成员的最新值 + 设备状态，现查 DB）
- 传感器新样本：broadcast.project.sample → {event: "sample", data: PointSample}
- 设备新状态：broadcast.device.status → 过滤已绑定后转发为 {event: "device.status", data: ...}
- 项目成员变化：broadcast.project.membership → 重读 snapshot，并原样复用 snapshot 事件
"""
from __future__ import annotations

from channels.db import database_sync_to_async

from services.realtime.consumers import _BaseAuthedConsumer
from services.realtime.dispatch import g_project


class ProjectStreamConsumer(_BaseAuthedConsumer):
    # 已绑定且可见的设备 id 集合，建连时查一次用于过滤 devices.all 广播。
    _bound_device_ids: frozenset = frozenset()

    def _project_id(self):
        return self.scope["url_route"]["kwargs"]["project_id"]

    def _compute_groups(self):
        return [g_project(self._project_id()), "devices.all"]

    async def _send_initial(self):
        snap = await database_sync_to_async(self._build_snapshot)(self._project_id())
        self._bound_device_ids = frozenset(d["device_id"] for d in snap.get("devices", []))
        await self.send_json({"event": "snapshot", "data": snap})

    @staticmethod
    def _build_snapshot(project_id) -> dict:
        # 复用 views.build_project_snapshot（现查 DB，不依赖进程缓存）
        from .models import Project
        from .views import build_project_snapshot

        try:
            project = Project.objects.get(id=project_id)
        except Project.DoesNotExist:
            return {"project_id": project_id, "samples": [], "devices": []}
        return build_project_snapshot(project)

    async def broadcast_project_sample(self, event):
        await self.send_json({"event": "sample", "data": event["payload"]})

    async def broadcast_project_membership(self, event):
        # 成员配置变化远少于设备状态事件。只在变化通知到来时重读一次完整快照，
        # 既能立即刷新当前值，也避免为 devices.all 的每条广播执行一次 DB 查询。
        await self._send_initial()

    async def broadcast_device_status(self, event):
        payload = event["payload"]
        # 只转发已绑定到本项目的设备；其余设备的状态广播在此丢弃
        if payload.get("device_id") in self._bound_device_ids:
            await self.send_json({"event": "device.status", "data": payload})
