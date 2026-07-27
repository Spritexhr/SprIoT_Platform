"""projects 应用自己的实时控制事件。

项目样本仍由 services.realtime.dispatch 统一发布；成员变化属于 projects 的领域事件，
放在本应用内可以避免主实时层感知具体业务模型。
"""

from services.realtime.dispatch import _safe_send, g_project


def publish_project_membership_changed(project_id) -> None:
    """通知该项目的已连接 consumer 重新读取成员快照。"""
    _safe_send(
        g_project(project_id),
        {
            "type": "broadcast.project.membership",
            "payload": {"project_id": project_id},
        },
    )
