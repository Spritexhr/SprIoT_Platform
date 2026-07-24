"""
simulation webui —— 独立轻量 Web 服务（与主平台 Django 完全无关）

启动（在 simulation/ 目录）：
    uvicorn webui.server:app --port 8800
或：
    python -m webui.server          # 等价于上面，端口取 SIM_WEBUI_PORT（默认 8800）

提供：
  - 节点/分组/broker 的 CRUD（SQLite 为 source of truth）
  - manifest YAML 导入/导出（与 run.py CLI 完全兼容）
  - run.py 子进程整组启停 + 日志
  - MQTT 实时监控（节点在线状态/最新数据）+ 节点级控制命令
  - /ws 单通道推送：log_line / run_state / node_status / node_data / monitor_state
"""
import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webui import db, manifest_io, process_manager
from webui.mqtt_monitor import MqttMonitor
from common.mqtt_compat import create_client

try:
    from common.waveforms import WAVEFORM_SCHEMAS
except ImportError:  # pragma: no cover
    WAVEFORM_SCHEMAS = {}

log = logging.getLogger("webui")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# ============ WS 广播 ============

_ws_clients: set = set()
_loop: Optional[asyncio.AbstractEventLoop] = None


async def _broadcast(event: dict):
    dead = []
    for ws in _ws_clients:
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.discard(ws)


def _emit_threadsafe(event: dict):
    """paho / 日志 tail 线程 → asyncio 广播"""
    if _loop and not _loop.is_closed():
        asyncio.run_coroutine_threadsafe(_broadcast(event), _loop)


monitor = MqttMonitor(on_event=_emit_threadsafe)


def _reconfigure_monitor():
    broker = db.get_default_broker()
    if broker:
        monitor.configure(broker)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _loop
    _loop = asyncio.get_running_loop()
    db.init()
    process_manager.set_event_handler(_emit_threadsafe)
    process_manager.recover()
    _reconfigure_monitor()
    yield
    # 优雅关闭：停止所有仍在运行的 run.py 子进程，防止产生孤儿进程
    running = db.running_runs()
    for run in running:
        log.info(f"shutdown: 正在停止 run {run['id']} (pid={run['pid']})…")
        try:
            process_manager.stop_run(run["id"])
        except Exception:
            log.exception(f"shutdown: 停止 run {run['id']} 失败")
    # 等待进程实际退出，以便 _finish / _cleanup_runtime 有机会完成
    import time
    for run in running:
        pid = run["pid"]
        for _ in range(int(process_manager.STOP_GRACE_S / 0.2)):
            if not process_manager._pid_alive(pid):
                break
            time.sleep(0.2)
    monitor.stop()


app = FastAPI(title="IoT Simulation WebUI", lifespan=lifespan)


@app.middleware("http")
async def prevent_webui_asset_version_skew(request, call_next):
    """本地管理工具优先保证 HTML/CSS/JS 同版本，避免浏览器复用旧脚本导致白屏。"""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


# ============ pydantic 模型 ============

class BrokerIn(BaseModel):
    name: str
    host: str
    port: int = 1883
    username: str = ""
    password: str = ""
    is_default: bool = False


class GroupIn(BaseModel):
    name: str
    description: str = ""
    broker_profile_id: Optional[int] = None


class NodeIn(BaseModel):
    module: str
    node_id: str
    enabled: bool = True
    params: dict = Field(default_factory=dict)
    username: Optional[str] = None
    password: Optional[str] = None
    sort_order: int = 0


class NodeValidateIn(BaseModel):
    module: str
    node_id: str = ""
    params: dict = Field(default_factory=dict)


class RunIn(BaseModel):
    group_ids: List[int]


class ImportIn(BaseModel):
    yaml_text: str
    group_name: Optional[str] = None  # 不传则用 manifest 的 name


class CommandIn(BaseModel):
    command: str
    args: dict = Field(default_factory=dict)


# ============ meta ============

@app.get("/api/meta/modules")
def meta_modules():
    return manifest_io.modules_meta()


@app.get("/api/meta/waveforms")
def meta_waveforms():
    return WAVEFORM_SCHEMAS


# ============ brokers ============

@app.get("/api/brokers")
def brokers_list():
    return db.list_brokers()


@app.post("/api/brokers")
def brokers_create(data: BrokerIn):
    try:
        broker = db.create_broker(data.model_dump())
    except Exception as e:
        raise HTTPException(400, f"创建失败: {e}")
    _reconfigure_monitor()
    return broker


@app.put("/api/brokers/{broker_id}")
def brokers_update(broker_id: int, data: BrokerIn):
    if not db.get_broker(broker_id):
        raise HTTPException(404, "broker 不存在")
    broker = db.update_broker(broker_id, data.model_dump())
    _reconfigure_monitor()
    return broker


@app.delete("/api/brokers/{broker_id}")
def brokers_delete(broker_id: int):
    db.delete_broker(broker_id)
    return {"ok": True}


@app.post("/api/brokers/{broker_id}/test")
def brokers_test(broker_id: int):
    broker = db.get_broker(broker_id)
    if not broker:
        raise HTTPException(404, "broker 不存在")
    client = create_client(client_id=f"sim-webui-test-{os.getpid()}")
    if broker.get("username"):
        client.username_pw_set(broker["username"], broker.get("password", ""))
    try:
        rc = client.connect(broker["host"], broker.get("port", 1883), keepalive=10)
        client.disconnect()
        return {"ok": rc == 0}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/brokers/import-config")
def brokers_import_config():
    """读 simulation/config.yaml 的 broker 段建 profile（兼容旧 CLI 配置）"""
    broker = manifest_io.read_legacy_config()
    if not broker:
        raise HTTPException(404, "simulation/config.yaml 不存在或缺少 broker.host")
    data = {
        "name": f"config.yaml ({broker['host']})",
        "host": broker["host"],
        "port": broker.get("port", 1883),
        "username": broker.get("username", ""),
        "password": broker.get("password", ""),
        "is_default": not db.list_brokers(),
    }
    try:
        created = db.create_broker(data)
    except Exception as e:
        raise HTTPException(400, f"导入失败（可能已存在同名 profile）: {e}")
    _reconfigure_monitor()
    return created


# ============ groups ============

@app.get("/api/groups")
def groups_list():
    return db.list_groups()


@app.post("/api/groups")
def groups_create(data: GroupIn):
    if db.get_group_by_name(data.name):
        raise HTTPException(400, f"分组 '{data.name}' 已存在")
    return db.create_group(data.model_dump())


@app.get("/api/groups/{group_id}")
def groups_get(group_id: int):
    g = db.get_group(group_id)
    if not g:
        raise HTTPException(404, "分组不存在")
    g["nodes"] = db.list_nodes(group_id)
    return g


@app.put("/api/groups/{group_id}")
def groups_update(group_id: int, data: GroupIn):
    if not db.get_group(group_id):
        raise HTTPException(404, "分组不存在")
    return db.update_group(group_id, data.model_dump())


@app.delete("/api/groups/{group_id}")
def groups_delete(group_id: int):
    db.delete_group(group_id)
    return {"ok": True}


@app.post("/api/groups/import")
def groups_import(data: ImportIn):
    """上传 manifest YAML → 建组 + 节点，返回校验报告"""
    try:
        manifest = manifest_io.parse_manifest_yaml(data.yaml_text)
    except ValueError as e:
        raise HTTPException(400, str(e))

    name = data.group_name or manifest.get("name") or "imported"
    if db.get_group_by_name(name):
        raise HTTPException(400, f"分组 '{name}' 已存在，请改名后导入")

    group = db.create_group({
        "name": name,
        "description": manifest.get("description", ""),
    })
    report = {"group": group, "imported": 0, "errors": [], "warnings": []}
    for i, entry in enumerate(manifest["nodes"], start=1):
        node_data = manifest_io.entry_to_node(entry)
        where = f"第 {i} 个节点 ({node_data['module']} / {node_data['node_id'] or '?'})"
        if not node_data["module"] or not node_data["node_id"]:
            report["errors"].append(f"{where}: 缺少 module 或 id，已跳过")
            continue
        errs, warns = manifest_io.validate_node(node_data["module"], entry)
        report["warnings"].extend(f"{where}: {w}" for w in warns)
        if errs:
            # 校验失败的节点仍导入但置为禁用，便于在 GUI 里修复
            node_data["enabled"] = False
            report["errors"].extend(f"{where}: {e}（已导入为禁用状态）" for e in errs)
        try:
            db.create_node(group["id"], node_data)
            report["imported"] += 1
        except Exception as e:
            report["errors"].append(f"{where}: 入库失败 {e}")
    return report


@app.get("/api/groups/{group_id}/export", response_class=PlainTextResponse)
def groups_export(group_id: int):
    g = db.get_group(group_id)
    if not g:
        raise HTTPException(404, "分组不存在")
    manifest = manifest_io.groups_to_manifest([g], {group_id: db.list_nodes(group_id)})
    manifest["name"] = g["name"]
    return manifest_io.dump_manifest_yaml(manifest)


# ============ nodes ============

@app.get("/api/groups/{group_id}/nodes")
def nodes_list(group_id: int):
    if not db.get_group(group_id):
        raise HTTPException(404, "分组不存在")
    return db.list_nodes(group_id)


@app.post("/api/groups/{group_id}/nodes")
def nodes_create(group_id: int, data: NodeIn):
    if not db.get_group(group_id):
        raise HTTPException(404, "分组不存在")
    payload = data.model_dump()
    errs, warns = manifest_io.validate_node(
        payload["module"], {"module": payload["module"], "id": payload["node_id"],
                            **payload["params"]})
    if errs:
        raise HTTPException(400, "；".join(errs))
    try:
        node = db.create_node(group_id, payload)
    except Exception as e:
        raise HTTPException(400, f"创建失败（同组内 module+id 不能重复）: {e}")
    node["warnings"] = warns
    return node


@app.get("/api/nodes/{node_pk}")
def nodes_get(node_pk: int):
    node = db.get_node(node_pk)
    if not node:
        raise HTTPException(404, "节点不存在")
    return node


@app.put("/api/nodes/{node_pk}")
def nodes_update(node_pk: int, data: NodeIn):
    if not db.get_node(node_pk):
        raise HTTPException(404, "节点不存在")
    payload = data.model_dump()
    errs, warns = manifest_io.validate_node(
        payload["module"], {"module": payload["module"], "id": payload["node_id"],
                            **payload["params"]})
    if errs:
        raise HTTPException(400, "；".join(errs))
    node = db.update_node(node_pk, payload)
    node["warnings"] = warns
    return node


@app.delete("/api/nodes/{node_pk}")
def nodes_delete(node_pk: int):
    db.delete_node(node_pk)
    return {"ok": True}


@app.post("/api/nodes/{node_pk}/duplicate")
def nodes_duplicate(node_pk: int):
    node = db.get_node(node_pk)
    if not node:
        raise HTTPException(404, "节点不存在")
    copy = dict(node)
    copy["node_id"] = f"{node['node_id']}-copy"
    try:
        return db.create_node(node["group_id"], copy)
    except Exception as e:
        raise HTTPException(400, f"复制失败: {e}")


@app.post("/api/nodes/validate")
def nodes_validate(data: NodeValidateIn):
    """表单实时校验（不落库）"""
    errs, warns = manifest_io.validate_node(
        data.module, {"module": data.module, "id": data.node_id, **data.params})
    return {"ok": not errs, "errors": errs, "warnings": warns}


# ============ runs ============

@app.get("/api/runs")
def runs_list():
    return db.list_runs()


@app.post("/api/runs")
def runs_create(data: RunIn):
    if not data.group_ids:
        raise HTTPException(400, "group_ids 不能为空")
    try:
        return process_manager.start_run(data.group_ids)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/runs/{run_id}")
def runs_get(run_id: int):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "run 不存在")
    return run


@app.post("/api/runs/{run_id}/stop")
def runs_stop(run_id: int):
    try:
        return process_manager.stop_run(run_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/api/runs/{run_id}/logs")
def runs_logs(run_id: int, offset: int = 0, limit: int = 500):
    return process_manager.read_log(run_id, offset, limit)


@app.get("/api/runs/{run_id}/manifest", response_class=PlainTextResponse)
def runs_manifest(run_id: int):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "run 不存在")
    return run.get("manifest_snapshot") or ""


# ============ live（mqtt monitor） ============

@app.get("/api/live/nodes")
def live_nodes():
    return {"connected": monitor.connected, "nodes": monitor.snapshot()}


@app.post("/api/live/{node_type}/{node_id}/command")
def live_command(node_type: str, node_id: str, data: CommandIn):
    try:
        ok = monitor.publish_command(node_type, node_id, data.command, data.args)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))
    return {"ok": ok}


# ============ WebSocket ============

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.add(ws)
    try:
        while True:
            # 客户端只发 ping，其他消息忽略
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(ws)


# ============ 静态前端 ============

@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("SIM_WEBUI_PORT", 8800)))
