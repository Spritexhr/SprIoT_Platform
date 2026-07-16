"""
控制方案执行器（双位 / PI / PID）

与自由脚本引擎 engine.py 分离：这里是结构化闭环控制，
核心逻辑统一为「读传感器实测值 PV → 与设定值 SP 比较 → 算出控制量 → 驱动设备」。

- on_off_step / pi_step / pid_step：纯函数，便于单测（不触库、不下发）。
- run_control_scheme：编排一拍——读 PV、算输出、映射成设备命令、复用既有命令服务下发、回写运行态。
- CONTROL_TEMPLATES：三套模板的参数 schema + 默认值，供前端表单与 templates 接口使用。
"""
import logging
import threading

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger('automation.controllers')


class ControlSchemeExecutionError(RuntimeError):
    """控制方案无法完成一拍计算或命令映射。"""


class ControlCommandDeliveryError(ControlSchemeExecutionError):
    """控制命令未能成功发布到 MQTT。"""


# 进程内用固定数量的分片锁兜住 SQLite / 同进程线程；数据库行锁负责 MySQL
# 多进程互斥。固定分片避免按方案数量永久增长锁表。
_SCHEME_EXECUTION_LOCKS = tuple(threading.Lock() for _ in range(128))


# ---------------------------------------------------------------------------
# 纯算法（无副作用）
# ---------------------------------------------------------------------------

def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _error(pv: float, sp: float, action: str) -> float:
    """误差符号随作用方向：heat（正作用）误差=SP-PV；cool（反作用）误差=PV-SP。
    这样无论加热还是降温，误差为正都意味着"应增大输出"。"""
    return (sp - pv) if action == 'heat' else (pv - sp)


def on_off_step(pv: float, sp: float, deadband: float, action: str, prev_on: bool) -> bool:
    """带回差(死区)的双位控制，返回执行器是否应为 ON。

    回差避免在设定值附近频繁开关：进入死区内时保持上一拍状态。
    """
    db = abs(float(deadband or 0))
    if action == 'cool':            # PV 偏高时开（如温度高开冷却阀）
        if pv >= sp + db:
            return True
        if pv <= sp - db:
            return False
    else:                           # heat：PV 偏低时开（如加热）
        if pv <= sp - db:
            return True
        if pv >= sp + db:
            return False
    return bool(prev_on)            # 死区内保持


def pi_step(pv, sp, kp, ki, action, state, dt, out_min, out_max):
    """比例积分。返回 (output, new_state)。含抗积分饱和（输出限幅时不再累积积分）。"""
    e = _error(pv, sp, action)
    integral = float(state.get('integral', 0.0))
    new_integral = integral + e * dt
    out = kp * e + ki * new_integral
    if out > out_max:
        out, new_integral = out_max, integral
    elif out < out_min:
        out, new_integral = out_min, integral
    ns = dict(state)
    ns['integral'] = new_integral
    ns['prev_error'] = e
    return out, ns


def pid_step(pv, sp, kp, ki, kd, action, state, dt, out_min, out_max):
    """PID。返回 (output, new_state)。在 PI 基础上加微分项，含抗积分饱和。"""
    e = _error(pv, sp, action)
    integral = float(state.get('integral', 0.0))
    prev_error = float(state.get('prev_error', e))
    new_integral = integral + e * dt
    derivative = (e - prev_error) / dt if dt > 0 else 0.0
    out = kp * e + ki * new_integral + kd * derivative
    if out > out_max:
        out, new_integral = out_max, integral
    elif out < out_min:
        out, new_integral = out_min, integral
    ns = dict(state)
    ns['integral'] = new_integral
    ns['prev_error'] = e
    return out, ns


# ---------------------------------------------------------------------------
# 编排（读库、下发、回写）
# ---------------------------------------------------------------------------

def _read_pv(scheme):
    """读取被控量 PV：传感器最新一条 SensorData.data[pv_key]，转 float；失败返回 None。"""
    member = scheme.sensor_member
    if not member or not member.sensor:
        return None
    rec = member.sensor.data_records.order_by('-received_at', '-pk').first()
    if not rec or not isinstance(rec.data, dict):
        return None
    val = rec.data.get(scheme.pv_key)
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _compute(scheme, pv, dt, state):
    """根据控制类型算出 (output, new_state)。output 统一在 [out_min, out_max] 量纲。"""
    params = scheme.params if isinstance(scheme.params, dict) else {}
    out_min = float(params.get('out_min', 0.0))
    out_max = float(params.get('out_max', 100.0))
    ct = scheme.control_type

    if ct == 'on_off':
        is_on = on_off_step(pv, scheme.setpoint, params.get('deadband', 0),
                            scheme.action, state.get('on', False))
        ns = dict(state)
        ns['on'] = is_on
        return (out_max if is_on else out_min), ns

    if ct == 'pi':
        return pi_step(pv, scheme.setpoint,
                       float(params.get('kp', 0)), float(params.get('ki', 0)),
                       scheme.action, state, dt, out_min, out_max)

    if ct == 'pid':
        return pid_step(pv, scheme.setpoint,
                        float(params.get('kp', 0)), float(params.get('ki', 0)),
                        float(params.get('kd', 0)),
                        scheme.action, state, dt, out_min, out_max)

    raise ValueError(f"未知控制类型 {ct}")


def _resolve_command(scheme, output, dt, state):
    """把控制输出映射成「设备命令名 + 命令参数」。返回 (command, params_dict, new_state)。"""
    params = scheme.params if isinstance(scheme.params, dict) else {}
    out_min = float(params.get('out_min', 0.0))
    out_max = float(params.get('out_max', 100.0))
    span = (out_max - out_min) or 1.0

    # 双位天然是开关量
    if scheme.control_type == 'on_off':
        sw = params.get('switch', {}) if isinstance(params.get('switch'), dict) else {}
        is_on = bool(state.get('on', False))
        cmd = sw.get('on_command') if is_on else sw.get('off_command')
        return cmd, {}, state

    # PI / PID
    if scheme.output_mode == 'analog':
        ac = params.get('analog', {}) if isinstance(params.get('analog'), dict) else {}
        cmd = ac.get('command')
        param_name = ac.get('param', 'value')
        range_min = float(ac.get('range_min', out_min))
        range_max = float(ac.get('range_max', out_max))
        val = range_min + (output - out_min) / span * (range_max - range_min)
        return cmd, {param_name: round(val, 2)}, state

    # output_mode == 'switch'：用阈值或时间比例(PWM)把连续输出转开/关
    sw = params.get('switch', {}) if isinstance(params.get('switch'), dict) else {}
    convert = sw.get('convert', 'threshold')
    ns = dict(state)
    if convert == 'pwm':
        period = max(1.0, float(sw.get('pwm_period', scheme.sample_interval)))
        duty = _clamp((output - out_min) / span, 0.0, 1.0)
        phase = (float(state.get('pwm_phase', 0.0)) + dt) % period
        is_on = phase < duty * period
        ns['pwm_phase'] = phase
    else:  # threshold：输出过半即开
        is_on = output >= (out_min + out_max) / 2.0
    cmd = sw.get('on_command') if is_on else sw.get('off_command')
    return cmd, {}, ns


def _mark_scheme_error(
    scheme,
    *,
    now,
    message,
    error_code,
    pv=None,
    output=None,
    command=None,
    cmd_params=None,
    sent=False,
):
    """原子步骤失败后的统一停环与诊断结果。调用方应持有方案行锁。"""
    rounded_output = round(float(output), 2) if output is not None else None
    cmd_params = cmd_params or {}
    scheme.status = 'error'
    scheme.is_enabled = False
    scheme.error_message = message
    scheme.last_run_time = now
    scheme.last_pv = pv
    scheme.last_output = rounded_output
    scheme.last_command = f"{command} {cmd_params}".strip() if command else ''
    scheme.save(update_fields=[
        'status', 'is_enabled', 'error_message', 'last_run_time',
        'last_pv', 'last_output', 'last_command', 'updated_at',
    ])
    return {
        'pv': pv,
        'output': rounded_output,
        'command': command,
        'params': cmd_params,
        'sent': sent,
        'error': message,
        'error_code': error_code,
        'skipped': False,
    }


def run_control_scheme(scheme, send: bool = True) -> dict:
    """
    执行一拍控制并回写运行态。

    Args:
        scheme: ControlScheme 实例
        send: 是否真正下发命令（手动"试一下"也默认 True，跑真实一拍）
    Returns:
        dict: {pv, output, command, params, sent, error, error_code, skipped}

        sent=True  表示 MQTT 发布成功；sent=False 表示该拍失败；
        sent=None 仅表示 send=False 的显式试算，避免把“未请求下发”和“下发失败”混为一谈。
    """
    now = timezone.now()
    state = scheme.runtime_state if isinstance(scheme.runtime_state, dict) else {}
    pv = None
    output = None
    command = None
    cmd_params = {}

    try:
        # 1. 读 PV
        pv = _read_pv(scheme)
        if pv is None:
            # 读不到被控量就停环：闭环控制不应在"看不见"过程值时继续盲目驱动设备
            msg = f"无法读取被控量 PV（传感器无数据或字段 {scheme.pv_key} 缺失/非数值）"
            return _mark_scheme_error(
                scheme,
                now=now,
                message=msg,
                error_code='pv_unavailable',
                sent=False,
            )

        # 2. 时间步长 dt（用上一次执行时间推算，首拍/异常回落到控制周期）
        if scheme.last_run_time:
            dt = (now - scheme.last_run_time).total_seconds()
            if dt <= 0 or dt > scheme.sample_interval * 10:
                dt = scheme.sample_interval
        else:
            dt = scheme.sample_interval

        # 3. 计算控制输出
        output, state = _compute(scheme, pv, dt, state)

        # 4. 映射成设备命令
        command, cmd_params, state = _resolve_command(scheme, output, dt, state)
        if not command:
            raise ControlSchemeExecutionError('控制方案未配置可下发的设备命令')

        # 5. 下发
        sent = None
        if send:
            from services.devices_service.device_command_send_service import (
                device_command_send_service,
            )
            device_id = scheme.device_member.device.device_id
            try:
                # 控制环按固定周期连续运行，不应为每个方案串行等待最多 3 秒 ACK；
                # QoS 1 发布结果是本拍的同步边界，设备状态继续由既有实时链路回传。
                sent = device_command_send_service.send_command(
                    object_id=device_id,
                    command_name=command,
                    params=cmd_params or {},
                )
            except Exception as exc:  # 服务异常统一转换为可诊断的下发失败
                raise ControlCommandDeliveryError(f'设备命令下发异常: {exc}') from exc
            if sent is not True:
                raise ControlCommandDeliveryError(
                    f'设备命令下发失败: {device_id}/{command}'
                )

        # 6. 回写运行态
        scheme.runtime_state = state
        scheme.last_pv = pv
        scheme.last_output = round(float(output), 2)
        scheme.last_command = f"{command} {cmd_params}".strip() if command else ''
        scheme.last_run_time = now
        scheme.error_message = ''
        scheme.status = 'running' if scheme.is_enabled else 'idle'
        scheme.save(update_fields=[
            'runtime_state', 'last_pv', 'last_output', 'last_command',
            'last_run_time', 'error_message', 'status', 'updated_at',
        ])
        return {'pv': pv, 'output': scheme.last_output, 'command': command,
                'params': cmd_params, 'sent': sent, 'error': None,
                'error_code': None, 'skipped': False}

    except ControlCommandDeliveryError as e:
        logger.error("控制方案命令下发失败 [%s]: %s", scheme.name, e)
        return _mark_scheme_error(
            scheme,
            now=now,
            message=str(e),
            error_code='command_delivery_failed',
            pv=pv,
            output=output,
            command=command,
            cmd_params=cmd_params,
            sent=False,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("控制方案执行异常 [%s]: %s", scheme.name, e)
        return _mark_scheme_error(
            scheme,
            now=now,
            message=f"执行异常: {e}",
            error_code='execution_failed',
            pv=pv,
            output=output,
            command=command,
            cmd_params=cmd_params,
            sent=False,
        )


def run_control_scheme_locked(scheme_id, send: bool = True, *, due_at=None) -> dict:
    """在进程锁 + 数据库行锁下执行同一方案的一拍。

    scheduler 传入 due_at 后会在取得锁之后重新检查周期，避免它在等待手动拍
    期间使用旧 last_run_time，随后紧接着重复下发。
    """
    from .models import ControlScheme

    local_lock = _SCHEME_EXECUTION_LOCKS[hash(scheme_id) % len(_SCHEME_EXECUTION_LOCKS)]
    with local_lock:
        with transaction.atomic():
            scheme = ControlScheme.objects.select_for_update().get(pk=scheme_id)
            if due_at is not None:
                is_due = scheme.is_enabled and scheme.status == 'running'
                if is_due and scheme.last_run_time is not None:
                    interval = max(1, scheme.sample_interval or 1)
                    elapsed = (due_at - scheme.last_run_time).total_seconds()
                    is_due = elapsed >= interval
                if not is_due:
                    return {
                        'pv': scheme.last_pv,
                        'output': scheme.last_output,
                        'command': None,
                        'params': {},
                        'sent': None,
                        'error': None,
                        'error_code': None,
                        'skipped': True,
                    }
            return run_control_scheme(scheme, send=send)


# ---------------------------------------------------------------------------
# 模板定义（前端表单 / templates 接口的单一数据源）
# ---------------------------------------------------------------------------

CONTROL_TEMPLATES = [
    {
        'control_type': 'on_off',
        'name': '双位控制',
        'description': '带回差(死区)的开关控制。适合电磁阀、继电器等开关量执行器：'
                       '实测值越过设定值±死区就开/关。',
        'output_modes': ['switch'],
        'param_fields': [
            {'key': 'deadband', 'label': '回差(死区)', 'type': 'number', 'default': 1.0,
             'help': '防止在设定值附近频繁开关，单位与被控量一致'},
        ],
        'defaults': {
            'action': 'cool',
            'sample_interval': 5,
            'output_mode': 'switch',
            'params': {
                'deadband': 1.0,
                'switch': {'on_command': '', 'off_command': '', 'convert': 'threshold'},
            },
        },
    },
    {
        'control_type': 'pi',
        'name': '比例积分(PI)',
        'description': '比例+积分，消除稳态误差。适合舵机/调节阀等模拟量执行器；'
                       '也可用阈值或时间比例(PWM)驱动开关量。',
        'output_modes': ['analog', 'switch'],
        'param_fields': [
            {'key': 'kp', 'label': '比例系数 Kp', 'type': 'number', 'default': 2.0},
            {'key': 'ki', 'label': '积分系数 Ki', 'type': 'number', 'default': 0.1},
            {'key': 'out_min', 'label': '输出下限', 'type': 'number', 'default': 0},
            {'key': 'out_max', 'label': '输出上限', 'type': 'number', 'default': 100},
        ],
        'defaults': {
            'action': 'cool',
            'sample_interval': 5,
            'output_mode': 'analog',
            'params': {
                'kp': 2.0, 'ki': 0.1, 'out_min': 0, 'out_max': 100,
                'analog': {'command': '', 'param': 'value', 'range_min': 0, 'range_max': 100},
                'switch': {'on_command': '', 'off_command': '', 'convert': 'threshold', 'pwm_period': 30},
            },
        },
    },
    {
        'control_type': 'pid',
        'name': 'PID控制',
        'description': '比例+积分+微分，响应更平稳、抗扰更好。适合对稳定性要求高的模拟量执行器。',
        'output_modes': ['analog', 'switch'],
        'param_fields': [
            {'key': 'kp', 'label': '比例系数 Kp', 'type': 'number', 'default': 2.0},
            {'key': 'ki', 'label': '积分系数 Ki', 'type': 'number', 'default': 0.1},
            {'key': 'kd', 'label': '微分系数 Kd', 'type': 'number', 'default': 0.5},
            {'key': 'out_min', 'label': '输出下限', 'type': 'number', 'default': 0},
            {'key': 'out_max', 'label': '输出上限', 'type': 'number', 'default': 100},
        ],
        'defaults': {
            'action': 'cool',
            'sample_interval': 5,
            'output_mode': 'analog',
            'params': {
                'kp': 2.0, 'ki': 0.1, 'kd': 0.5, 'out_min': 0, 'out_max': 100,
                'analog': {'command': '', 'param': 'value', 'range_min': 0, 'range_max': 100},
                'switch': {'on_command': '', 'off_command': '', 'convert': 'threshold', 'pwm_period': 30},
            },
        },
    },
]
