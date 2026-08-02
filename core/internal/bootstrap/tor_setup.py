#!/usr/bin/env python3
# GREP_SUMMARY: tor-setup, tor, privoxy, obfs4proxy, webtunnel, apt, packages, degradation, fallback, detect-transports, install
# STRUCTURE: ▶ ┌present/repo pkg sets┐ → plan_install (pure: webtunnel degradation) → apt-get update → detect_available_transports → install (retry w/o webtunnel on fail) → ⎋ list[str] installed
# region MODULE_CONTRACT
## @purpose  Деградационная state-machine установки Tor+Privoxy пакетов (DevPlan 119 D2, AUDIT-1 F4).
##           Перенос install_packages() из install-tor-proxy.sh (52-116): webtunnel→obfs4 fallback.
##           Shell-фасад получает список установленных пакетов через CLI `python3 tor_setup.py --install`.
## @scope    Вызывается install-tor-proxy.sh install_packages (тонкий фасад <20 LOC). Чистая логика
##           планирования (plan_install) + apt I/O (dpkg -s / apt-cache show / apt-get) — тестируемо.
## @invariants
##   - plan_install: чистая функция — webtunnel деградируется если НЕ в apt-репозиториях
##     (TRAP[DECISION] 2026-07-17: apt preferred, static binary reserved)
##   - install_tor_packages: apt-get update → install; провал установки webtunnel → retry без webtunnel
##     (TRAP[DECISION] 2026-07-17 · MED: degradation, not abort); провал базовых → TorSetupError
##   - Пакетный план: [tor, privoxy, obfs4proxy] + webtunnel (опционально, с конца) — порядок канона
##   - dry_run=True: план возвращён без каких-либо apt-мутаций (для тестов/--dry-run)
##   - Функции никогда не выходят по sys.exit вне main(); main() -> int канон (core/AGENTS.md)
## @rationale D2 (DevPlan 119): install_packages() — деградационная state-machine с >3 if-веток
##   бизнес-логики (Tier-1 Strangler trigger). Python + unit-тесты ПЕРЕД миграцией (test-first).
## @changes  2026-08-02 | DevPlan 119 D2 — Created (test-first: tests/unit/test_tor_setup.py)
## @see      core/internal/bootstrap/install-tor-proxy.sh (install_packages → тонкий фасад)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import subprocess
import sys

logger = logging.getLogger(__name__)

# Канонический пакетный план (порядок сохранён из shell install_packages)
TOR_BASE_PACKAGES: list[str] = ["tor", "privoxy", "obfs4proxy"]
WEBTUNNEL_PACKAGE: str = "webtunnel"


class TorSetupError(Exception):
    """Fail-fast: установка базовых пакетов провалилась (shell exit 1 канон)."""


# ═══════════════════════════════════════════════════════════════════════════
# I/O-примитивы (тестируются через patch subprocess.run)
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_dpkg_installed
def dpkg_installed(pkg: str) -> bool:
    """dpkg -s <pkg> → True если пакет установлен (rc 0)."""
    result = subprocess.run(["dpkg", "-s", pkg], capture_output=True, text=True)
    return result.returncode == 0


# endregion FUNC_dpkg_installed


# region FUNC_apt_cache_has
def apt_cache_has(pkg: str) -> bool:
    """apt-cache show <pkg> → True если пакет доступен в репозиториях (rc 0)."""
    result = subprocess.run(["apt-cache", "show", pkg], capture_output=True, text=True)
    return result.returncode == 0


# endregion FUNC_apt_cache_has


# region FUNC_apt_update
def apt_update() -> None:
    """apt-get update -qq — обновление индексов перед установкой (канон shell)."""
    result = subprocess.run(["apt-get", "update", "-qq"], capture_output=True, text=True)
    if result.returncode != 0:
        logger.warning(
            "[IMP:8][tor-setup][update] apt-get update rc=%d: %s", result.returncode, result.stderr.strip()[:200]
        )


# endregion FUNC_apt_update


# region FUNC_apt_install
def apt_install(packages: list[str]) -> None:
    """apt-get install -y -qq <packages> — raise TorSetupError при провале.

    ## @purpose  Единственная точка apt-установки — деградационный retry без webtunnel в
    ##            install_tor_packages ловит TorSetupError.
    """
    result = subprocess.run(["apt-get", "install", "-y", "-qq", *packages], capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(
            "[IMP:10][tor-setup][install] apt-get install failed (%s): %s",
            " ".join(packages),
            result.stderr.strip()[:300],
        )
        raise TorSetupError(f"apt-get install failed: {' '.join(packages)}")
    logger.info("[IMP:9][tor-setup][install] Installed: %s", " ".join(packages))


# endregion FUNC_apt_install


# ═══════════════════════════════════════════════════════════════════════════
# Бизнес-логика
# ═══════════════════════════════════════════════════════════════════════════


# region FUNC_plan_install
def plan_install(present: set[str], repo: set[str]) -> list[str]:
    """Чистый расчёт плана установки с webtunnel-деградацией.

    ▶ ┌present + repo┐ → ○ missing = базовые не установленные + webtunnel → ◇ webtunnel ∉ repo? DROP
      → ⎋ list[str] (порядок канона: tor, privoxy, obfs4proxy, [webtunnel])

    ## @purpose — Планирование install_packages (DevPlan 119 D2) — чистая функция, no I/O.
    ## @io — ⇥ present: set[str] (dpkg -s результат), repo: set[str] (apt-cache) → ⎋ list[str]
    ## @complexity — O(P) — P = кандидаты
    ## @invariants
    ##   - webtunnel деградируется если отсутствует в repo (TRAP[DECISION] 2026-07-17)
    ##   - Порядок плана: базовые (tor, privoxy, obfs4proxy) + webtunnel в конце
    """
    missing = [p for p in TOR_BASE_PACKAGES if p not in present]
    if WEBTUNNEL_PACKAGE not in present:
        missing.append(WEBTUNNEL_PACKAGE)

    if WEBTUNNEL_PACKAGE in missing and WEBTUNNEL_PACKAGE not in repo:
        # ⚠️ TRAP[DECISION] · 2026-07-17 · — · webtunnel binary delivery: apt preferred, static binary reserved
        # · Rejected: static binary in core/bootstrap/tor/bin/webtunnel (not yet packaged for noble)
        # · Reason: apt package is cleaner; first release degrades to obfs4-only if absent
        # · Rev: if webtunnel apt package remains unavailable → implement static binary delivery
        logger.warning("[IMP:8][tor-setup][plan] webtunnel not in apt repositories — skipping (degradation to obfs4)")
        missing.remove(WEBTUNNEL_PACKAGE)

    logger.info("[IMP:9][tor-setup][plan] Install plan: %s", " ".join(missing) if missing else "(none — all installed)")
    return missing


# endregion FUNC_plan_install


# region FUNC_detect_available_transports
def detect_available_transports() -> dict[str, bool]:
    """Какие транспорты доступны в apt-репозиториях (apt-cache probe).

    ▶ ┌None┐ → ○ apt-cache show per transport → ⊕ dict[transport: bool] → ⎋ dict[str, bool]

    ## @purpose — transport availability probe (DevPlan 119 D2): webtunnel + obfs4proxy.
    ## @io — ⇥ None → ⎋ dict[str, bool] — {"webtunnel": bool, "obfs4proxy": bool}
    ## @complexity — O(T) — T = транспорты
    """
    transports = {WEBTUNNEL_PACKAGE: apt_cache_has(WEBTUNNEL_PACKAGE)}
    transports["obfs4proxy"] = apt_cache_has("obfs4proxy")
    logger.info("[IMP:8][tor-setup][detect] Available transports: %s", transports)
    return transports


# endregion FUNC_detect_available_transports


# region FUNC_install_tor_packages
def install_tor_packages(dry_run: bool = False) -> list[str]:
    """Полный apt-flow установки пакетов Tor+Privoxy (деградационная state-machine, D2).

    ▶ ┌present┐ → ○ missing? none → ⎋ [] → ○ apt-get update → ○ detect transports → ○ plan_install
      → ○ install → ◇ webtunnel failed? retry without webtunnel → ⎋ list[str] installed

    ## @purpose — install_packages() из install-tor-proxy.sh (DevPlan 119 D2): webtunnel→obfs4 fallback.
    ## @io — ⇥ dry_run: bool → ⎋ list[str] — установленные пакеты ([] = все уже на месте)
    ## @complexity — O(1) subprocess per step
    ## @raises — TorSetupError: провал установки базовых пакетов (без webtunnel-деградации)
    """
    all_packages = [*TOR_BASE_PACKAGES, WEBTUNNEL_PACKAGE]
    present = {p for p in all_packages if dpkg_installed(p)}
    if all(p in present for p in all_packages):
        logger.info("[IMP:9][tor-setup][flow] All packages already installed")
        return []

    if not dry_run:
        apt_update()

    repo = {p for p in (WEBTUNNEL_PACKAGE, "obfs4proxy") if apt_cache_has(p)}
    plan = plan_install(present, repo)
    if not plan:
        logger.info("[IMP:9][tor-setup][flow] Nothing to install after degradation")
        return []

    if dry_run:
        logger.info("[IMP:8][tor-setup][flow] DRY-RUN — plan without installation: %s", " ".join(plan))
        return plan

    try:
        apt_install(plan)
    except TorSetupError:
        if WEBTUNNEL_PACKAGE in plan:
            # ⚠️ TRAP[DECISION] · 2026-07-17 · MED · webtunnel apt failure → degradation, not abort
            # · If webtunnel install fails → drop webtunnel from install list, continue with base packages
            # · Rationale: webtunnel is optional; obfs4 is the primary transport for Telegram Bot API
            # · Rev: if production requires webtunnel → fail instead of degrade
            logger.warning(
                "[IMP:8][tor-setup][flow] apt-get install failed for webtunnel (degradation) — retrying without it"
            )
            no_webtunnel = [p for p in plan if p != WEBTUNNEL_PACKAGE]
            if no_webtunnel:
                apt_install(no_webtunnel)
            else:
                raise
        else:
            raise

    logger.info("[IMP:9][tor-setup][flow] Installed packages: %s", " ".join(plan) if plan else "(none)")
    return plan


# endregion FUNC_install_tor_packages


# region FUNC_main
def main(argv: list[str] | None = None) -> int:
    """CLI: `python3 tor_setup.py --install [--dry-run]` / `--detect`.

    ▶ ┌argv┐ → ◇ --install? → install_tor_packages → print(installed) | ◇ --detect? → print transports → ⎋ exit 0|1

    ## @purpose — Интерфейс для install-tor-proxy.sh (DevPlan 119 D2): shell читает список установленных
    ##            пакетов из stdout (пустой → SKIP «all installed»).
    ## @io — ⇥ argv → ⎋ int (0 = ok, 1 = TorSetupError)
    ## @invariants
    ##   - --install: stdout = установленные пакеты через пробел (пусто = всё установлено)
    ##   - --detect: stdout = "transport=bool" per line
    ##   - TorSetupError → exit 1 (shell FAIL + exit 1 канон)
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    parser = argparse.ArgumentParser(description="Tor+Privoxy package installation (DevPlan 119 D2)")
    parser.add_argument("--install", action="store_true", help="Install missing tor/privoxy/obfs4proxy/[webtunnel]")
    parser.add_argument("--dry-run", action="store_true", help="Compute plan without installing (no apt-get)")
    parser.add_argument("--detect", action="store_true", help="Print available transports in apt repositories")
    args = parser.parse_args(argv)

    if args.detect:
        transports = detect_available_transports()
        for transport, available in transports.items():
            print(f"{transport}={available}")
        return 0

    if args.install:
        try:
            installed = install_tor_packages(dry_run=args.dry_run)
        except TorSetupError as exc:
            logger.error("[IMP:10][tor-setup][main] %s", exc)
            return 1
        if installed:
            print(" ".join(installed))
        return 0

    parser.error("No action specified — use --install or --detect")
    return 2  # unreachable (parser.error exits)


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
