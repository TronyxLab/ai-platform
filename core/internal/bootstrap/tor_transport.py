#!/usr/bin/env python3
# GREP_SUMMARY: tor-transport bridge-parsing webtunnel-degradation transport-dedup unknown-transport fail-fast ClientTransportPlugin torrc
# STRUCTURE: ▶ parse_bridges(content, available) → ◇ Bridge line regex → ◇ unknown transport? fail-fast | ◇ webtunnel binary absent? drop | ◇ dedup transports → ⊕ filtered_bridges + transports → render_torrc_section → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Transport-парсинг Bridge-строк и деградация транспортов для write_torrc
#           (install-tor-proxy.sh:147-196). Вынесен в Python по DevPlan 118 E1 (D19):
#           тестируемая бизнес-логика (parsing, degradation, dedup, fail-fast) —
#           shell остаётся apt/systemd-оркестратором.
## @scope    Вызывается install_tor_proxy.py write_torrc (DevPlan 127 W1: native import;
##           ранее — install-tor-proxy.sh через CLI emit, тонкий канал).
##           Секция аппендится в torrc напрямую (без shell-прокладки).
## @invariants
##   - Bridge line: ^Bridge\s+([a-zA-Z0-9_-]+) — первый токен = transport
##   - Unknown transport (нет в TRANSPORT_BIN) → TorTransportError (fail-fast, shell exit 1 канон)
##   - Degradation: webtunnel binary отсутствует (available_binaries) → webtunnel Bridge lines DROP,
##     obfs4-only продолжается (TRAP[DECISION] 2026-07-17)
##   - Dedup: ClientTransportPlugin эмитится один раз на уникальный транспорт
##   - Non-Bridge линии (комментарии/пустые) pass-through без изменений
##   - TRANSPORT_BIN registry: obfs4 → /usr/bin/obfs4proxy, webtunnel → /usr/bin/webtunnel
## @rationale D19 (мега-DevPlan): >3 if-веток бизнес-логики (Tier-1) + >150 LOC (Tier-2) в write_torrc.
##   Strangler: парсинг/деградация → Python с unit-тестами ПЕРЕД миграцией (test-first условие выполнено).
## @changes  2026-08-02 | DevPlan 118 E1 — Created (test-first: tests/unit/test_tor_transport.py написан ПЕРЕД)
## @changes  2026-08-13 | DevPlan 160 W4b — resolve_available_binaries +facts: EnvironmentFacts | None (DI)
## @see      core/internal/bootstrap/install_tor_proxy.py (write_torrc использует parse_bridges, 127 W1)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import pathlib
import re
import sys
from dataclasses import dataclass, field

from core.internal.shared.env_facts import EnvironmentFacts, default_env_facts

logger = logging.getLogger(__name__)

# Transport binary path registry (канон install-tor-proxy.sh TRANSPORT_BIN)
TRANSPORT_BIN: dict[str, str] = {
    "obfs4": "/usr/bin/obfs4proxy",
    "webtunnel": "/usr/bin/webtunnel",
}
_BRIDGE_LINE_RE = re.compile(r"^Bridge[ \t]+([a-zA-Z0-9_-]+)")


class TorTransportError(Exception):
    """Fail-fast: неизвестный транспорт без зарегистрированного бинарника (shell exit 1 канон)."""


@dataclass
class BridgeParseResult:
    """Результат парсинга Bridge-файла.

    ## @purpose  Чистый контракт parse_bridges → filtered_bridges + transports_to_emit.
    ## @io       filtered_bridges: str — прошедшие Bridge/non-Bridge линии (webtunnel dropped при деградации)
    ##           transports_to_emit: list[str] — уникальные транспорты для ClientTransportPlugin
    """

    filtered_bridges: str = ""
    transports_to_emit: list[str] = field(default_factory=list)

    def __iter__(self):
        """Tuple-unpacking compat (test-first contract: `filtered, transports = parse_bridges(...)`)."""
        yield self.filtered_bridges
        yield self.transports_to_emit


# region FUNC_parse_bridges
## @purpose  Разобрать Bridge-файл: fail-fast unknown transport, webtunnel degradation (binary absent),
##           dedup транспортов, non-Bridge passthrough.
## @io       ⇥ content: str, available_binaries: set[str] (присутствующие бинарники, shutil.which) →
##           ⎋ BridgeParseResult
## @complexity O(L) — L = строк файла
## @raises   TorTransportError — unknown transport (нет в TRANSPORT_BIN)
def parse_bridges(content: str, available_binaries: set[str]) -> BridgeParseResult:
    """Parse Bridge lines → filtered bridges + unique transports (E1, D19)."""
    result = BridgeParseResult()
    seen_transports: set[str] = set()

    for line in content.splitlines():
        m = _BRIDGE_LINE_RE.match(line)
        if not m:
            # Non-Bridge line — pass through (comments, blanks)
            result.filtered_bridges += line + "\n"
            continue
        transport = m.group(1)

        # Fail-fast: unknown transport → no registered binary
        if transport not in TRANSPORT_BIN:
            logger.error("[IMP:10][tor-transport][parse] Unknown transport '%s' — no registered binary path", transport)
            msg = f"Unknown transport '{transport}' — no registered binary path"
            raise TorTransportError(msg)

        # Degradation: webtunnel binary not found → drop line
        if transport == "webtunnel" and "webtunnel" not in available_binaries:
            logger.warning("[IMP:8][tor-transport][parse] webtunnel binary not found — dropping webtunnel bridge line")
            continue

        result.filtered_bridges += line + "\n"
        if transport not in seen_transports:
            seen_transports.add(transport)
            result.transports_to_emit.append(transport)

    logger.info(
        "[IMP:9][tor-transport][parse] Parsed %d transport(s): %s",
        len(result.transports_to_emit),
        result.transports_to_emit,
    )
    return result


# endregion FUNC_parse_bridges


# region FUNC_render_torrc_section
## @purpose  Собрать torrc-секцию: UseBridges 1 + ClientTransportPlugin per transport + filtered bridges.
## @io       ⇥ filtered_bridges: str, transports_to_emit: list[str] → ⎋ str (секция для аппенда в torrc)
## @complexity O(T) — T = транспорты
def render_torrc_section(filtered_bridges: str, transports_to_emit: list[str]) -> str:
    """Render the torrc bridge section (UseBridges 1 + CTP lines + filtered bridges)."""
    if not transports_to_emit:
        return ""
    lines = ["", "UseBridges 1"]
    lines.extend(
        f"ClientTransportPlugin {transport} exec {TRANSPORT_BIN[transport]}" for transport in transports_to_emit
    )
    lines.append("")
    lines.append(filtered_bridges)
    return "\n".join(lines)


# endregion FUNC_render_torrc_section


# region FUNC_resolve_available_binaries
## @purpose  Определить присутствующие транспорты (shutil.which по TRANSPORT_BIN-путям).
## @io       ⇥ facts: EnvironmentFacts | None (None = реальные системные) → ⎋ set[str] —
##           транспорты с существующим бинарником
## @complexity O(T) — T = транспорты
## @changes 2026-08-13 | DevPlan 160 W4b — +facts (which/path_isfile через DI)
def resolve_available_binaries(facts: EnvironmentFacts | None = None) -> set[str]:
    """Return set of transports whose binary exists on the system (shutil.which)."""
    facts = facts or default_env_facts()

    available: set[str] = set()
    for transport, binary in TRANSPORT_BIN.items():
        if facts.which(binary) or facts.path_isfile(binary):
            available.add(transport)
    return available


# endregion FUNC_resolve_available_binaries


# region FUNC_main
def main(argv: list[str] | None = None) -> int:
    """CLI entry: `python3 tor_transport.py emit --bridges-file <path>`.

    ▶ ┌argv┐ → ○ read bridges file → ○ resolve available binaries → ○ parse_bridges → ○ render section
      → ⎋ stdout=section (0) | stderr=ERROR + exit 1 (unknown transport)

    Args:
        argv: Optional CLI args override (DI — DevPlan 167 D1, AF-4). None = sys.argv.
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="Tor bridge transport parsing (DevPlan 118 E1)")
    parser.add_argument("emit", help="Emit torrc bridge section (WriteBridges 1 + CTP lines)")
    parser.add_argument("--bridges-file", required=True, help="Path to bridges file")

    class _CliArgs(argparse.Namespace):
        """Типизированный argparse-Namespace (W11-G3)."""

        def __init__(self) -> None:
            super().__init__()
            self.emit: str
            self.bridges_file: str

    args = parser.parse_args(argv, namespace=_CliArgs())

    if args.emit != "emit":
        parser.error("Unknown action (expected: emit)")
    try:
        with pathlib.Path(args.bridges_file).open(encoding="utf-8") as f:
            content = f.read()
    except OSError as exc:
        logger.error("[IMP:10][tor-transport][main] Cannot read bridges file %s: %s", args.bridges_file, exc)
        return 1

    try:
        result = parse_bridges(content, available_binaries=resolve_available_binaries())
    except TorTransportError as exc:
        logger.error("[IMP:10][tor-transport][main] %s", exc)
        return 1

    section = render_torrc_section(result.filtered_bridges, result.transports_to_emit)
    if not section:
        logger.warning(
            "[IMP:8][tor-transport][main] No usable bridges found in %s (all dropped or empty)", args.bridges_file
        )
        return 0
    print(section)
    logger.info("[IMP:9][tor-transport][main] Bridges parsed — transports: %s", result.transports_to_emit)
    return 0


# endregion FUNC_main

if __name__ == "__main__":
    sys.exit(main())
