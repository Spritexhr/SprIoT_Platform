"""
平台配置管理命令——PlatformConfig 表的唯一写入入口。

用法：
  python manage.py configure                    # 交互式 wizard
  python manage.py configure --init             # 仅补缺失的 key（首次部署 / 升级）
  python manage.py configure --set k=v          # 单键写入
  python manage.py configure --unset key        # 恢复默认值（不删除条目）
  python manage.py configure --list             # 列出所有当前值
  python manage.py configure --no-reload        # 写完不调用 reload API

写入后默认会调用 reload，让 MQTT 等服务无感重连。
"""
import getpass
import json
import logging
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from platform_settings.defaults import (
    DEFAULT_CONFIGS,
    ConfigValidationError,
    coerce_config_value,
    get_meta,
    validate_config_value,
)
from platform_settings.models import PlatformConfig

logger = logging.getLogger("platform_settings")


def _coerce(key: str, raw: str) -> Any:
    """CLI 文本转换也复用 defaults.py 的统一类型与范围规则。"""
    try:
        return coerce_config_value(key, raw)
    except ConfigValidationError as exc:
        raise CommandError(str(exc)) from exc


def _format_value(value: Any, secret: bool = False) -> str:
    """显示用：密码打码，列表/字典 JSON 化"""
    if secret and value:
        return "***"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _upsert(key: str, value: Any, meta: dict) -> str:
    """写入或更新一条 PlatformConfig，返回 'created' / 'updated' / 'unchanged'"""
    try:
        value = validate_config_value(key, value)
    except ConfigValidationError as exc:
        raise CommandError(str(exc)) from exc
    obj, created = PlatformConfig.objects.get_or_create(
        key=key,
        defaults={
            "value": value,
            "category": meta.get("category", "general"),
            "description": meta.get("description", ""),
        },
    )
    if created:
        return "created"
    if obj.value == value and obj.category == meta.get("category", obj.category):
        return "unchanged"
    obj.value = value
    obj.category = meta.get("category", obj.category)
    obj.description = meta.get("description", obj.description)
    obj.save()
    return "updated"


def _trigger_reload(stdout) -> None:
    """通过 Redis control stream 通知唯一 mqtt_runner 应用最新配置。"""
    try:
        from services.mqtt_command_bus import get_mqtt_command_bus

        bus = get_mqtt_command_bus()
        request_id = bus.enqueue_reload()
        result = bus.wait_result(request_id, timeout=2.5)
        stdout.write(
            f"  MQTT: {result.get('status', 'queued')} request_id={request_id}"
        )
    except Exception as e:
        # 配置已经写入数据库；runner 还有 30 秒指纹轮询作为漏通知补偿。
        stdout.write(f"  MQTT: reload enqueue failed ({e})")
        logger.warning(f"configure reload 异常: {e}")


def _print_banner(stdout, style) -> None:
    """首次种子完成后打出醒目提示"""
    stdout.write("")
    stdout.write(style.SUCCESS("=" * 64))
    stdout.write(style.SUCCESS("✅ 平台配置已用默认值初始化"))
    stdout.write("")
    stdout.write("默认 MQTT broker: 127.0.0.1:1883（仅占位，请按实际 EMQX 地址修改）")
    stdout.write("首次部署请运行交互式 wizard 完成配置：")
    stdout.write(style.WARNING(
        "  docker compose exec backend python manage.py configure"
    ))
    stdout.write("")
    stdout.write("或单键设置：")
    stdout.write(
        "  docker compose exec backend python manage.py configure "
        "--set mqtt_broker=192.168.1.10"
    )
    stdout.write(style.SUCCESS("=" * 64))
    stdout.write("")


class Command(BaseCommand):
    help = "PlatformConfig 写入入口（wizard / --set / --unset / --init / --list）"

    def add_arguments(self, parser):
        parser.add_argument(
            "--init",
            action="store_true",
            help="补齐 DEFAULT_CONFIGS 中缺失的 key，已有 key 不动；用于首次部署和升级",
        )
        parser.add_argument(
            "--set",
            dest="set_pairs",
            action="append",
            default=[],
            metavar="KEY=VALUE",
            help="单键设置，可重复。例：--set mqtt_broker=192.168.1.10",
        )
        parser.add_argument(
            "--unset",
            dest="unset_keys",
            action="append",
            default=[],
            metavar="KEY",
            help="把 key 重置为 DEFAULT_CONFIGS 中的默认值",
        )
        parser.add_argument(
            "--list",
            action="store_true",
            help="列出所有当前配置",
        )
        parser.add_argument(
            "--no-reload",
            action="store_true",
            help="写完不调用 reload（不让 MQTT 重连）",
        )

    def handle(self, *args, **options):
        if options["list"]:
            self._handle_list()
            return

        if options["init"]:
            self._handle_init()
            return

        if options["set_pairs"] or options["unset_keys"]:
            self._handle_set_unset(options["set_pairs"], options["unset_keys"])
            if not options["no_reload"]:
                self.stdout.write("\n触发 reload:")
                _trigger_reload(self.stdout)
            return

        # 默认行为：交互式 wizard
        self._handle_wizard()
        if not options["no_reload"]:
            self.stdout.write("\n触发 reload:")
            _trigger_reload(self.stdout)

    # ---------- 各模式实现 ----------

    def _handle_list(self) -> None:
        """列出所有当前配置（密码打码）"""
        self.stdout.write(self.style.HTTP_INFO("当前 PlatformConfig 内容："))
        configs = PlatformConfig.objects.order_by("category", "key")
        if not configs.exists():
            self.stdout.write("  （空，可执行 configure --init 写入默认值）")
            return
        last_cat = None
        for cfg in configs:
            if cfg.category != last_cat:
                self.stdout.write(f"\n[{cfg.category}]")
                last_cat = cfg.category
            meta = get_meta(cfg.key)
            secret = meta.get("secret", False)
            self.stdout.write(
                f"  {cfg.key} = {_format_value(cfg.value, secret)}"
                f"   {self.style.HTTP_INFO('# ' + (cfg.description or ''))}"
            )

    def _handle_init(self) -> None:
        """首次部署 / 升级：仅补缺失的 key"""
        existing_keys = set(PlatformConfig.objects.values_list("key", flat=True))
        is_first_run = len(existing_keys) == 0
        created = 0
        skipped = 0

        for item in DEFAULT_CONFIGS:
            key = item["key"]
            if key in existing_keys:
                skipped += 1
                continue
            _upsert(key, item["default"], item)
            created += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"  + {key} = {_format_value(item['default'], item.get('secret', False))}"
                )
            )

        self.stdout.write(
            self.style.SUCCESS(f"\n完成: 新增 {created} 项, 跳过 {skipped} 项")
        )

        if is_first_run and created > 0:
            _print_banner(self.stdout, self.style)

    def _handle_set_unset(self, set_pairs: list, unset_keys: list) -> None:
        """处理 --set / --unset；全部校验通过后才原子写库。"""
        pending = []
        for pair in set_pairs:
            if "=" not in pair:
                raise CommandError(f"--set 格式错误，需 KEY=VALUE: {pair!r}")
            key, raw = pair.split("=", 1)
            key = key.strip()
            meta = get_meta(key)
            if not meta:
                raise CommandError(
                    f"未知配置项: {key!r}。可用 key: "
                    f"{', '.join(c['key'] for c in DEFAULT_CONFIGS)}"
                )
            pending.append((key, _coerce(key, raw), meta, False))

        for raw_key in unset_keys:
            key = raw_key.strip()
            meta = get_meta(key)
            if not meta:
                raise CommandError(f"未知配置项: {key!r}")
            pending.append((key, validate_config_value(key, meta["default"]), meta, True))

        outputs = []
        with transaction.atomic():
            for key, value, meta, is_unset in pending:
                result = _upsert(key, value, meta)
                outputs.append((key, value, meta, is_unset, result))

        for key, value, meta, is_unset, result in outputs:
            text = (
                f"  [{result}] {key} = "
                f"{_format_value(value, meta.get('secret', False))}"
            )
            if is_unset:
                self.stdout.write(self.style.WARNING(f"{text} (重置为默认)"))
            else:
                self.stdout.write(self.style.SUCCESS(text))

    def _handle_wizard(self) -> None:
        """交互式 wizard：按 DEFAULT_CONFIGS 顺序询问，回车保留当前值"""
        self.stdout.write(self.style.HTTP_INFO(
            "\n=== 平台配置 wizard ==="
        ))
        self.stdout.write("直接回车保留当前值；按 Ctrl+C 中止。\n")

        last_cat = None
        for item in DEFAULT_CONFIGS:
            key = item["key"]
            description = item.get("description", "")
            secret = item.get("secret", False)
            category = item["category"]
            default = item["default"]

            if category != last_cat:
                self.stdout.write(self.style.HTTP_INFO(f"\n[{category}]"))
                last_cat = category

            current = PlatformConfig.get_value(key, default)
            display_current = _format_value(current, secret)
            prompt = f"  {key} ({description}) [{display_current}]: "

            try:
                if secret:
                    raw = getpass.getpass(prompt)
                else:
                    raw = input(prompt)
            except EOFError:
                self.stdout.write("\n（无 stdin，跳过 wizard，使用 --set 或 --init 代替）")
                return

            raw = raw.strip()
            if raw == "":
                continue

            try:
                value = _coerce(key, raw)
            except CommandError as e:
                self.stdout.write(self.style.ERROR(f"    × {e}, 保留原值"))
                continue

            result = _upsert(key, value, item)
            self.stdout.write(self.style.SUCCESS(
                f"    [{result}] {key} = {_format_value(value, secret)}"
            ))

        self.stdout.write(self.style.SUCCESS("\n配置完成。"))
