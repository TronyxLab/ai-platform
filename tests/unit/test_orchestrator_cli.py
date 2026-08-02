#!/usr/bin/env python3
# GREP_SUMMARY: test-orchestrator-cli, build-channel, forced-command, scp, local, channel-selection, metadata-defaults
# STRUCTURE: ▶ argparse.Namespace → ◇ --forced-command → ForcedCommandChannel → ◇ --scp → SCPChannel → ◇ host без флага → ForcedCommandChannel → ◇ без host → LocalChannel → ⊕ metadata_defaults → ⎋
# region MODULE_CONTRACT
## @purpose  Unit tests for orchestrator_cli.build_channel() (DevPlan 119 F5, HOLE-2).
##           build_channel() не был покрыт тестами — 3 кейса каналов: ForcedCommandChannel,
##           SCPChannel, LocalChannel + metadata_defaults проброс (host/user/key_file).
## @scope    Tests build_channel() channel selection + metadata_defaults (native import, no Docker).
## @invariants
##   - --forced-command → ForcedCommandChannel (даже без host)
##   - --scp → SCPChannel
##   - host указан без явного флага → ForcedCommandChannel (операторский путь, D7)
##   - нет host и флагов → LocalChannel (на-ноде bootstrap deploy-many, D7)
##   - host → channel.metadata_defaults = {"host": ...} (+user/key_file при наличии)
##   - DEPLOY_HOST env используется как fallback host
## @rationale  HOLE-2 (AUDIT-5): build_channel() — 0 тестов. Критичный выбор транспорта
##             доставки (SSH forced-command vs SCP vs local) — дефолт влияет на deploy-many.
## @changes    2026-08-02 | Created (DevPlan 119 F5)
# endregion MODULE_CONTRACT

import argparse
import logging

from core.internal.deploy.channels import ForcedCommandChannel, LocalChannel, SCPChannel
from core.internal.deploy.orchestrator_cli import build_channel

logger = logging.getLogger(__name__)


def _ns(**kwargs) -> argparse.Namespace:
    """Build argparse.Namespace with defaults for build_channel accessors."""
    defaults = {
        "forced_command": False,
        "scp": False,
        "host": "",
        "user": "",
        "key_file": "",
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# region FUNC_test_build_channel_forced_command
## @purpose — --forced-command → ForcedCommandChannel (даже при пустом host).
## @io — ⇥ _ns(forced_command=True) → ⎋ ForcedCommandChannel instance
## @complexity — O(1)
# 🧪 TRAP[TEST] · DevPlan 119 F5 (HOLE-2) · build_channel forced-command
# · Last fail: N/A — build_channel не покрыт тестами (0 tests)
# · Remove if: build_channel channel-selection логика меняется
def test_build_channel_forced_command() -> None:
    """--forced-command → ForcedCommandChannel."""
    channel = build_channel(_ns(forced_command=True))
    print(f"[IMP:9][test] channel={type(channel).__name__}")
    assert isinstance(channel, ForcedCommandChannel)
    assert not isinstance(channel, (SCPChannel, LocalChannel))


# endregion FUNC_test_build_channel_forced_command


# region FUNC_test_build_channel_scp
## @purpose — --scp → SCPChannel с metadata (host/user/key_file проброс).
## @io — ⇥ _ns(scp=True, host="vps", user="root", key_file="/tmp/k") → ⎋ SCPChannel
## @complexity — O(1)
# 🧪 TRAP[TEST] · DevPlan 119 F5 (HOLE-2) · build_channel scp
# · Last fail: N/A — build_channel не покрыт тестами
# · Remove if: build_channel channel-selection логика меняется
def test_build_channel_scp() -> None:
    """--scp → SCPChannel с metadata_defaults (host/user/key_file)."""
    channel = build_channel(_ns(scp=True, host="vps.example.com", user="deploy", key_file="/tmp/deploy-key"))
    print(f"[IMP:9][test] channel={type(channel).__name__} metadata={channel.metadata_defaults}")
    assert isinstance(channel, SCPChannel)
    assert channel.metadata_defaults.get("host") == "vps.example.com"
    assert channel.metadata_defaults.get("user") == "deploy"
    assert channel.metadata_defaults.get("key_file") == "/tmp/deploy-key"


# endregion FUNC_test_build_channel_scp


# region FUNC_test_build_channel_local
## @purpose — Без host и флагов → LocalChannel (на-ноде bootstrap deploy-many, D7).
## @io — ⇥ _ns() → ⎋ LocalChannel
## @complexity — O(1)
# 🧪 TRAP[TEST] · DevPlan 119 F5 (HOLE-2) · build_channel local
# · Last fail: N/A — build_channel не покрыт тестами
# · Remove if: дефолт LocalChannel (D7) меняется
def test_build_channel_local() -> None:
    """Без host/флагов → LocalChannel (на-ноде операция, D7)."""
    channel = build_channel(_ns())
    print(f"[IMP:9][test] channel={type(channel).__name__}")
    assert isinstance(channel, LocalChannel)
    assert not isinstance(channel, (SCPChannel, ForcedCommandChannel))


# endregion FUNC_test_build_channel_local


# region FUNC_test_build_channel_host_without_flag
## @purpose — host указан без явного флага → ForcedCommandChannel (операторский путь через
##            verb-форму receive, DevPlan 116 B1 T6). R5 negative к LocalChannel-дефолту.
## @io — ⇥ _ns(host="vps") → ⎋ ForcedCommandChannel + metadata host
## @complexity — O(1)
# 🧪 TRAP[TEST] · DevPlan 119 F5 (HOLE-2) · host без флага → ForcedCommandChannel
# · Last fail: legacy — host молча игнорировался, LocalChannel сам-себе доставка
# · Remove if: host-без-флага семантика меняется
def test_build_channel_host_without_flag() -> None:
    """host без --scp/--forced-command → ForcedCommandChannel (T6 операторский путь)."""
    channel = build_channel(_ns(host="vps.example.com"))
    print(f"[IMP:9][test] channel={type(channel).__name__} metadata={channel.metadata_defaults}")
    assert isinstance(channel, ForcedCommandChannel)
    assert channel.metadata_defaults.get("host") == "vps.example.com"


# endregion FUNC_test_build_channel_host_without_flag


# region FUNC_test_build_channel_deploy_host_env
## @purpose — DEPLOY_HOST env fallback: host из env используется когда args.host пуст (T5).
## @io — ⇥ _ns() + DEPLOY_HOST=env-host → ⎋ ForcedCommandChannel + metadata host=env-host
## @complexity — O(1)
# 🧪 TRAP[TEST] · DevPlan 119 F5 (HOLE-2) · DEPLOY_HOST env fallback
# · Last fail: N/A — build_channel не покрыт тестами
# · Remove if: DEPLOY_HOST fallback удаляется
def test_build_channel_deploy_host_env(monkeypatch) -> None:
    """DEPLOY_HOST env fallback → host используется при пустом args.host."""
    monkeypatch.setenv("DEPLOY_HOST", "env-host.example.com")
    channel = build_channel(_ns())
    print(f"[IMP:9][test] channel={type(channel).__name__} metadata={channel.metadata_defaults}")
    assert isinstance(channel, ForcedCommandChannel)
    assert channel.metadata_defaults.get("host") == "env-host.example.com"


# endregion FUNC_test_build_channel_deploy_host_env


# region FUNC_test_build_channel_metadata_no_host
## @purpose — R5 negative: без host — metadata_defaults не содержит host-ключа (LocalChannel
##            на-ноде без транспорта). Проверка, что пустой host не создаёт "host": "" мусор.
## @io — ⇥ _ns() → ⎋ LocalChannel с metadata_defaults без host
## @complexity — O(1)
# 🧪 TRAP[TEST] · DevPlan 119 F5 (HOLE-2) · metadata без host
# · Last fail: host="" записывался в metadata_defaults (мусорный ключ)
# · Remove if: build_channel metadata логика меняется
def test_build_channel_metadata_no_host() -> None:
    """Без host — metadata_defaults НЕ создаётся (нет мусорного host-ключа)."""
    channel = build_channel(_ns())
    print(f"[IMP:9][test] channel={type(channel).__name__}")
    assert isinstance(channel, LocalChannel)
    # build_channel устанавливает metadata_defaults ТОЛЬКО при наличии host —
    # LocalChannel без host не должен получать metadata с пустым "host": ""
    assert not hasattr(channel, "metadata_defaults"), (
        f"Без host metadata_defaults не должен создаваться: {getattr(channel, 'metadata_defaults', None)}"
    )


# endregion FUNC_test_build_channel_metadata_no_host
