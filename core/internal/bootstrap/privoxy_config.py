#!/usr/bin/env python3
# GREP_SUMMARY: privoxy-config, privoxy, listen-address, permit-access, forward-socks5t, idempotent-mutation, config-writer, tor
# STRUCTURE: ▶ mutate_config (pure: listen/permit-access/forward guards) → ○ template? copy | mutate → ◇ content == current? no-op │ atomic write → ⎋ bool changed
# region MODULE_CONTRACT
## @purpose  Идемпотентный Python-мутатор конфигурации Privoxy (DevPlan 119 D3, AUDIT-1 F5).
##           Перенос write_privoxy_config() из install-tor-proxy.sh (172-213): grep-guard + sed
##           мутации → тестируемый mutate_config + write_privoxy_config (no-op при повторном вызове).
## @scope    Вызывается install-tor-proxy.sh write_privoxy_config (тонкий фасад) через CLI
##           `python3 privoxy_config.py --config <path>`. Шаблон tor/privoxy-config.template —
##           авторитетный конфиг, если существует (ветка shell cp); иначе идемпотентная мутация.
## @invariants
##   - Идемпотентность: если конфиг уже содержит нужные строки → no-op (возврат False, без записи)
##   - Мутация (по приоритету): listen-address append/upgrade 127.0.0.1→0.0.0.0 (TRAP[BUGFIX] 2026-06-24),
##     permit-access 127.0.0.1 + 172.16.0.0/12 ПЕРЕД первой forward-socks5t, forward-socks5t append
##   - permit-access: при отсутствии forward-socks5t строки — append в конец (исправление silent-drop sed)
##   - non-clobber: существующие не-целевые строки конфига сохраняются байт-в-байт
##   - Функции никогда не выходят по sys.exit вне main(); main() -> int канон (core/AGENTS.md)
## @rationale D3 (DevPlan 119): write_privoxy_config() — идемпотентная мутация (grep-guard + sed),
##   ~40 LOC бизнес-логики без unit-тестов. Python-мутатор + тесты идемпотентности (test-first).
##   Шаблонная ветка (cp template) сохранена для parity — но идемпотентна: повторный вызов с тем же
##   содержимым шаблона = no-op (shell перезаписывал без проверки).
## @changes  2026-08-02 | DevPlan 119 D3 — Created (test-first: tests/unit/test_privoxy_config.py)
## @see      core/internal/bootstrap/install-tor-proxy.sh (write_privoxy_config → тонкий фасад)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Канонические адреса (совпадают с прежними литералами install-tor-proxy.sh)
DEFAULT_LISTEN_ADDR: str = "0.0.0.0:8118"
DEFAULT_FORWARD_ADDR: str = "127.0.0.1:9050"
# Шаблон Privoxy-конфига (ветка cp из shell write_privoxy_config) — рядом с модулем
TEMPLATE_PATH: Path = Path(__file__).resolve().parent / "tor" / "privoxy-config.template"


# region FUNC__insert_before
def _insert_before(lines: list[str], marker_prefix: str, insert_line: str) -> tuple[list[str], bool]:
    """Вставить строку ПЕРЕД первой строкой с префиксом marker_prefix; при отсутствии — append.

    ## @purpose — Эквивалент sed '/^forward-socks5t/i <line>' с исправлением silent-drop:
    ##            sed молча пропускает вставку при отсутствии матча — Python аппендится в конец
    ##            (permit-access не должен теряться, TRAP[BUGFIX] 2026-06-24).
    ## @io — ⇥ lines, marker_prefix, insert_line → ⎋ (обновлённый список, changed=True)
    """
    for i, line in enumerate(lines):
        if line.startswith(marker_prefix):
            lines.insert(i, insert_line)
            return lines, True
    lines.append(insert_line)
    return lines, True


# endregion FUNC__insert_before


# region FUNC_mutate_config
def mutate_config(content: str, listen_addr: str, forward_addr: str) -> tuple[str, bool]:
    """Чистая идемпотентная мутация конфига Privoxy.

    ▶ ┌content┐ → ○ listen-address (append | upgrade 127.0.0.1) → ○ permit-access ×2 (insert before forward | append)
      → ○ forward-socks5t (append) → ⎋ (new_content, changed)

    ## @purpose — guard-логика write_privoxy_config (DevPlan 119 D3) — чистая функция, no I/O.
    ## @io — ⇥ content: str, listen_addr: str, forward_addr: str → ⎋ tuple[str, bool]
    ## @complexity — O(L) — L = строк конфига
    ## @invariants
    ##   - listen-address: отсутствует → append; 127.0.0.1:8118 → upgrade (TRAP[BUGFIX] 2026-06-24)
    ##   - permit-access вставляется перед первой forward-socks5t (или append — не теряется)
    ##   - forward-socks5t append если строка с forward_addr отсутствует
    ##   - Повторный вызов с корректным конфигом → (content, False)
    """
    had_trailing = content.endswith("\n")
    lines = content.splitlines()
    changed = False

    # 1. listen-address: append если отсутствует; иначе upgrade 127.0.0.1 → listen_addr
    if not any(line.startswith("listen-address") for line in lines):
        lines.append(f"listen-address {listen_addr}")
        changed = True
    else:
        upgraded = [line.replace("listen-address 127.0.0.1:8118", f"listen-address {listen_addr}") for line in lines]
        if upgraded != lines:
            lines = upgraded
            changed = True

    # 2-3. permit-access: 127.0.0.1 + 172.16.0.0/12 (Docker bridge) перед forward-socks5t
    if not any(line.startswith("permit-access 127.0.0.1") for line in lines):
        lines, c = _insert_before(lines, "forward-socks5t", "permit-access 127.0.0.1")
        changed = changed or c
    if not any(line.startswith("permit-access 172.16.0.0/12") for line in lines):
        lines, c = _insert_before(lines, "forward-socks5t", "permit-access 172.16.0.0/12")
        changed = changed or c

    # 4. forward-socks5t (Tor SOCKS5) — append если отсутствует
    if not any(f"forward-socks5t / {forward_addr}" in line for line in lines):
        lines.append(f"forward-socks5t / {forward_addr} .")
        changed = True

    new_content = "\n".join(lines)
    if lines and had_trailing:
        new_content += "\n"
    return new_content, changed


# endregion FUNC_mutate_config


# region FUNC_write_privoxy_config
def write_privoxy_config(
    config_path: str,
    listen_addr: str = DEFAULT_LISTEN_ADDR,
    forward_addr: str = DEFAULT_FORWARD_ADDR,
) -> bool:
    """Идемпотентная запись конфига Privoxy — True если изменения внесены.

    ▶ ┌config_path┐ → ◇ template? base=template │ base=current → mutate_config → ◇ new == current? no-op
      → ○ write → ⎋ bool changed

    ## @purpose — write_privoxy_config() из install-tor-proxy.sh (DevPlan 119 D3).
    ## @io — ⇥ config_path: str, listen_addr, forward_addr → ⎋ bool (True = изменения внесены)
    ## @complexity — O(L) — L = строк конфига
    ## @invariants
    ##   - Template существует → base = содержимое шаблона (shell cp-ветка, но идемпотентно)
    ##   - Template отсутствует → base = текущий конфиг + mutate_config
    ##   - new == current → False (no-op, не трогает файл)
    ##   - OSError → пробрасывается в main() → exit 1 (fail-fast, никогда не молчит)
    """
    path = Path(config_path)
    current = path.read_text(encoding="utf-8") if path.is_file() else ""

    if TEMPLATE_PATH.is_file():
        new_content = TEMPLATE_PATH.read_text(encoding="utf-8")
    else:
        new_content, _ = mutate_config(current, listen_addr, forward_addr)

    if new_content == current:
        logger.info("[IMP:9][privoxy-config][write] No changes needed for %s (idempotent)", config_path)
        return False

    # Атомарная запись (tempfile + os.replace) — конфиг никогда не остаётся полузаписанным
    import tempfile
    from contextlib import suppress

    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".privoxy-config-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_content)
        os.replace(tmp_name, path)
        # ⚠️ TRAP[BUG] · 2026-08-03 · D-6 (DevPlan 125 T9) · privoxy config root-only 0600
        # · Symptom: systemctl status privoxy → «Fatal error: can't open configuration file
        # ·   '/etc/privoxy/config': Permission denied» — сервис (user privoxy) не читает конфиг.
        # · Root: tempfile.mkstemp создаёт файл с mode 0600; os.replace сохраняет 0600 (владелец root) —
        # ·   dpkg-шаблон /etc/privoxy/config (0644, world-readable) заменялся root-only файлом.
        # · Fix: явный chmod 0644 после replace — privoxy-конфиг не содержит секретов
        # ·   (listen-address 127.0.0.1:8118, forward-socks5t на локальный tor), канон dpkg.
        # · Prevention: writer'ы конфигов для systemd-сервисов обязаны задавать режим явно.
        os.chmod(path, 0o644)
    except BaseException:
        with suppress(OSError):
            os.unlink(tmp_name)
        raise

    logger.info("[IMP:9][privoxy-config][write] %s written (changed)", config_path)
    return True


# endregion FUNC_write_privoxy_config


# region FUNC_main
def main(argv: list[str] | None = None) -> int:
    """CLI: `python3 privoxy_config.py --config <path> [--listen-addr A] [--forward-addr F]`.

    ▶ ┌argv┐ → ○ write_privoxy_config → ◇ OSError? exit 1 │ ⎋ exit 0

    ## @purpose — Интерфейс для install-tor-proxy.sh (DevPlan 119 D3): shell-фасад вызывает
    ##            `python3 privoxy_config.py --config "$PRIVOXY_CONFIG"`.
    ## @io — ⇥ argv → ⎋ int (0 = ok, 1 = OSError)
    ## @invariants — exit 0 всегда при успешной записи/no-op; OSError → exit 1 (fail-fast)
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="Privoxy config idempotent writer (DevPlan 119 D3)")
    parser.add_argument("--config", required=True, help="Path to privoxy config file")
    parser.add_argument("--listen-addr", default=DEFAULT_LISTEN_ADDR, help="listen-address value")
    parser.add_argument("--forward-addr", default=DEFAULT_FORWARD_ADDR, help="forward-socks5t value")
    args = parser.parse_args(argv)

    try:
        write_privoxy_config(args.config, listen_addr=args.listen_addr, forward_addr=args.forward_addr)
    except OSError as exc:
        logger.error("[IMP:10][privoxy-config][main] Failed to write %s: %s", args.config, exc)
        return 1
    return 0


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
