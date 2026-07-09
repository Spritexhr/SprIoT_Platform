"""SprIoT_Platform MCP Server：标准化发现、查询、控制和资源管理能力。"""

from __future__ import annotations

import json
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .api_client import IoTAPIClient, IoTAPIError, unpack_results
from .config import Settings
from .confirmation import ConfirmationError, ConfirmationStore

AssetKind = Literal["sensor", "device"]

settings = Settings.from_env()
client = IoTAPIClient(settings)
confirmations = ConfirmationStore(
    settings.confirmation_secret, settings.confirmation_ttl_seconds
)

mcp = FastMCP(
    "SprIoT_Platform",
    instructions=(
        "读取和管理 SprIoT_Platform。写操作只能使用精确资源 ID；"
        "设备命令和删除操作必须先调用对应 preview 工具，再使用确认令牌执行。"
    ),
    host=settings.host,
    port=settings.port,
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
)

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)
DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=False,
)


def _endpoint(kind: AssetKind, asset_id: str | None = None) -> str:
    collection = "sensors" if kind == "sensor" else "devices"
    return f"/{collection}/{asset_id}/" if asset_id else f"/{collection}/"


def _type_info(asset: dict[str, Any], kind: AssetKind) -> dict[str, Any]:
    value = asset.get(f"{kind}_type_info")
    return value if isinstance(value, dict) else {}


def _safe_commands(asset: dict[str, Any], kind: AssetKind) -> dict[str, Any]:
    commands = _type_info(asset, kind).get("commands") or {}
    if not isinstance(commands, dict):
        return {}
    result: dict[str, Any] = {}
    for name, info in commands.items():
        if not isinstance(info, dict):
            continue
        result[name] = {
            key: value
            for key, value in info.items()
            if key in {"description", "params"}
        }
    return result


def _failure(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, IoTAPIError):
        return {
            "ok": False,
            "data": None,
            "error": {
                "code": exc.code,
                "message": str(exc),
                "status_code": exc.status_code,
                "retryable": exc.retryable,
                "details": exc.details,
            },
        }
    if isinstance(exc, ConfirmationError):
        return {
            "ok": False,
            "data": None,
            "error": {
                "code": "invalid_confirmation",
                "message": str(exc),
                "retryable": False,
            },
        }
    return {
        "ok": False,
        "data": None,
        "error": {
            "code": "invalid_request",
            "message": str(exc),
            "retryable": False,
        },
    }


def _success(data: Any, **meta: Any) -> dict[str, Any]:
    result = {"ok": True, "data": data, "error": None}
    if meta:
        result["meta"] = meta
    return result


async def _get_asset(kind: AssetKind, asset_id: str) -> dict[str, Any]:
    if not asset_id.strip():
        raise ValueError("asset_id 不能为空")
    payload = await client.get(_endpoint(kind, asset_id))
    if not isinstance(payload, dict):
        raise IoTAPIError("资源详情响应格式无效", code="invalid_api_response")
    return payload


@mcp.tool(
    title="搜索 IoT 资源",
    annotations=READ_ONLY,
    structured_output=True,
)
async def search_iot_assets(
    query: str = "",
    kind: Literal["sensor", "device", "all"] = "all",
    online: bool | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """按名称或 ID 搜索传感器/设备。此工具只返回候选项，绝不隐式执行操作。"""
    try:
        if not 1 <= limit <= 96:
            raise ValueError("limit 必须在 1 到 96 之间")
        kinds: list[AssetKind] = ["sensor", "device"] if kind == "all" else [kind]
        results: list[dict[str, Any]] = []
        for current_kind in kinds:
            params: dict[str, Any] = {"page_size": limit}
            if query.strip():
                params["search"] = query.strip()
            if online is not None:
                params["online"] = str(online).lower()
            payload = await client.get(_endpoint(current_kind), params=params)
            for item in unpack_results(payload)[:limit]:
                results.append(
                    {
                        "kind": current_kind,
                        "asset_id": item.get(f"{current_kind}_id"),
                        "name": item.get("name"),
                        "location": item.get("location"),
                        "is_online": item.get("is_online"),
                        "last_seen": item.get("last_seen"),
                        "type_name": _type_info(item, current_kind).get("name"),
                    }
                )
        return _success(results, count=len(results), limit_per_kind=limit)
    except Exception as exc:
        return _failure(exc)


@mcp.tool(title="读取 IoT 资源详情", annotations=READ_ONLY, structured_output=True)
async def get_iot_asset(kind: AssetKind, asset_id: str) -> dict[str, Any]:
    """使用精确 sensor_id/device_id 获取资源详情与最新数据。"""
    try:
        asset = await _get_asset(kind, asset_id)
        return _success(asset)
    except Exception as exc:
        return _failure(exc)


@mcp.tool(title="查询 IoT 历史数据", annotations=READ_ONLY, structured_output=True)
async def query_iot_telemetry(
    kind: AssetKind,
    asset_id: str,
    hours: int = 1,
    limit: int = 50,
) -> dict[str, Any]:
    """查询传感器数据或设备状态。时间范围最多 720 小时，单次最多 500 条。"""
    try:
        if not 1 <= hours <= 720:
            raise ValueError("hours 必须在 1 到 720 之间")
        if not 1 <= limit <= 500:
            raise ValueError("limit 必须在 1 到 500 之间")
        suffix = "data" if kind == "sensor" else "status"
        records = await client.get(
            f"{_endpoint(kind, asset_id)}{suffix}/",
            params={"hours": hours, "limit": limit},
        )
        if not isinstance(records, list):
            raise IoTAPIError("历史数据响应格式无效", code="invalid_api_response")
        return _success(records, count=len(records), order="newest_first")
    except Exception as exc:
        return _failure(exc)


@mcp.tool(title="列出资源支持的命令", annotations=READ_ONLY, structured_output=True)
async def list_iot_commands(kind: AssetKind, asset_id: str) -> dict[str, Any]:
    """返回某个资源真实支持的高层命令及参数，不暴露任意 MQTT 发布能力。"""
    try:
        asset = await _get_asset(kind, asset_id)
        return _success(_safe_commands(asset, kind))
    except Exception as exc:
        return _failure(exc)


def _required_params(command_info: dict[str, Any]) -> set[str]:
    specs = command_info.get("params") or []
    required: set[str] = set()
    if not isinstance(specs, list):
        return required
    for spec in specs:
        if isinstance(spec, str):
            required.add(spec)
        elif isinstance(spec, dict) and isinstance(spec.get("name"), str):
            if spec.get("required", True):
                required.add(spec["name"])
    return required


def _known_params(command_info: dict[str, Any]) -> set[str]:
    specs = command_info.get("params") or []
    if not isinstance(specs, list):
        return set()
    result: set[str] = set()
    for spec in specs:
        if isinstance(spec, str):
            result.add(spec)
        elif isinstance(spec, dict) and isinstance(spec.get("name"), str):
            result.add(spec["name"])
    return result


@mcp.tool(title="预览 IoT 控制命令", annotations=READ_ONLY, structured_output=True)
async def preview_iot_command(
    kind: AssetKind,
    asset_id: str,
    command_name: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """校验精确目标、命令和必填参数，返回两分钟内有效的单次确认令牌。"""
    try:
        asset = await _get_asset(kind, asset_id)
        commands = _safe_commands(asset, kind)
        if command_name not in commands:
            raise ValueError(
                f"资源不支持命令 {command_name!r}，请先调用 list_iot_commands"
            )
        normalized_params = params or {}
        missing = sorted(_required_params(commands[command_name]) - normalized_params.keys())
        if missing:
            raise ValueError(f"命令缺少必填参数: {', '.join(missing)}")
        unknown = sorted(normalized_params.keys() - _known_params(commands[command_name]))
        if unknown:
            raise ValueError(f"命令包含未定义参数: {', '.join(unknown)}")
        payload = {
            "kind": kind,
            "asset_id": asset_id,
            "asset_name": asset.get("name"),
            "command_name": command_name,
            "params": normalized_params,
        }
        token = confirmations.issue("execute_command", payload)
        return _success(
            {
                **payload,
                "is_online": asset.get("is_online"),
                "warning": None if asset.get("is_online") else "目标当前显示为离线",
                "confirmation_token": token,
                "expires_in_seconds": settings.confirmation_ttl_seconds,
            }
        )
    except Exception as exc:
        return _failure(exc)


@mcp.tool(title="执行 IoT 控制命令", annotations=WRITE, structured_output=True)
async def execute_iot_command(confirmation_token: str) -> dict[str, Any]:
    """执行已预览的命令。成功仅表示 MQTT 已接受发布，不等同于设备已确认执行。"""
    try:
        payload = confirmations.consume(confirmation_token, "execute_command")
        kind: AssetKind = payload["kind"]
        response = await client.post(
            f"{_endpoint(kind, payload['asset_id'])}command/",
            data={
                "command_name": payload["command_name"],
                "params": payload["params"],
                "make_sure": False,
            },
        )
        sent = bool(isinstance(response, dict) and response.get("success"))
        if not sent:
            raise IoTAPIError(
                "平台未能发布 MQTT 命令",
                code="command_publish_failed",
                details=response,
            )
        return _success(
            {
                "status": "mqtt_published",
                "device_confirmed": False,
                "target": payload,
                "platform_response": response,
            }
        )
    except Exception as exc:
        return _failure(exc)


@mcp.tool(title="创建 IoT 资源", annotations=WRITE, structured_output=True)
async def create_iot_asset(
    kind: AssetKind,
    asset_id: str,
    name: str,
    type_id: int,
    description: str = "",
    location: str = "",
    folder_id: int | None = None,
) -> dict[str, Any]:
    """创建传感器或设备。需要平台工作人员权限。"""
    try:
        payload: dict[str, Any] = {
            f"{kind}_id": asset_id,
            "name": name,
            f"{kind}_type": type_id,
            "description": description,
            "location": location,
        }
        if folder_id is not None:
            payload["folder"] = folder_id
        created = await client.post(_endpoint(kind), data=payload)
        return _success(created)
    except Exception as exc:
        return _failure(exc)


@mcp.tool(title="更新 IoT 资源", annotations=WRITE, structured_output=True)
async def update_iot_asset(
    kind: AssetKind,
    asset_id: str,
    name: str | None = None,
    description: str | None = None,
    location: str | None = None,
    folder_id: int | None = None,
) -> dict[str, Any]:
    """按精确 ID 修改资源元数据；不会修改资源唯一 ID 或类型。"""
    try:
        payload = {
            key: value
            for key, value in {
                "name": name,
                "description": description,
                "location": location,
                "folder": folder_id,
            }.items()
            if value is not None
        }
        if not payload:
            raise ValueError("至少提供一个需要更新的字段")
        updated = await client.patch(_endpoint(kind, asset_id), data=payload)
        return _success(updated)
    except Exception as exc:
        return _failure(exc)


@mcp.tool(title="预览删除 IoT 资源", annotations=READ_ONLY, structured_output=True)
async def preview_delete_iot_asset(kind: AssetKind, asset_id: str) -> dict[str, Any]:
    """读取将被删除的资源并生成短时确认令牌；此步骤不会删除数据。"""
    try:
        asset = await _get_asset(kind, asset_id)
        payload = {
            "kind": kind,
            "asset_id": asset_id,
            "asset_name": asset.get("name"),
            "last_seen": asset.get("last_seen"),
        }
        return _success(
            {
                **payload,
                "warning": "删除会级联移除该资源的历史数据，且不可撤销",
                "confirmation_token": confirmations.issue("delete_asset", payload),
                "expires_in_seconds": settings.confirmation_ttl_seconds,
            }
        )
    except Exception as exc:
        return _failure(exc)


@mcp.tool(title="删除 IoT 资源", annotations=DESTRUCTIVE, structured_output=True)
async def delete_iot_asset(confirmation_token: str) -> dict[str, Any]:
    """使用预览阶段生成的单次令牌永久删除资源。需要平台工作人员权限。"""
    try:
        payload = confirmations.consume(confirmation_token, "delete_asset")
        await client.delete(_endpoint(payload["kind"], payload["asset_id"]))
        return _success({**payload, "deleted": True})
    except Exception as exc:
        return _failure(exc)


@mcp.resource(
    "iot://catalog",
    title="IoT 资源目录",
    description="当前传感器与设备的精简目录",
    mime_type="application/json",
)
async def iot_catalog() -> str:
    result = await search_iot_assets(kind="all", limit=96)
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.resource(
    "iot://assets/{kind}/{asset_id}",
    title="IoT 资源详情",
    description="按精确类型和 ID 读取资源详情",
    mime_type="application/json",
)
async def iot_asset_resource(kind: str, asset_id: str) -> str:
    if kind not in {"sensor", "device"}:
        return json.dumps(_failure(ValueError("kind 必须是 sensor 或 device")), ensure_ascii=False)
    result = await get_iot_asset(kind=kind, asset_id=asset_id)  # type: ignore[arg-type]
    return json.dumps(result, ensure_ascii=False, default=str)


def main() -> None:
    mcp.run(transport=settings.transport)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
