#!/usr/bin/env python3
# GREP_SUMMARY: install-acme, acme.sh, clone, dnsapi, dns-extensions, git, letsencrypt, idempotent, merge-fallback, ecc, DI, runner, APT_TIMEOUT
# STRUCTURE: ▶ ┌ACME_HOME env┐ → ◇ acme.sh binary? SKIP → ○ apt-get git (APT_TIMEOUT, graceful) → ○ git clone (depth 1)
#           → ◇ clone fail + dir exists? merge-fallback (.clone-tmp + no-clobber *_ecc) → ○ dnsapi_ext clone (non-fatal WARN) → ⎋ 0|1
# region MODULE_CONTRACT
## @purpose  Idempotent Python-оркестрация установки acme.sh + DNS API-расширений (DevPlan 164 W3.5-1 S8).
##           Strangler-декомпозиция install-acme.sh (93 LOC shell → тестируемый модуль):
##           git clone/merge-fallback/apt-оркестрация в Python; install-acme.sh остаётся тонким
##           фасадом (<100 LOC, `exec python3 -m core.internal.bootstrap.install_acme "$@"`).
##           Вызывается один раз при bootstrap/init (φ7 certificates), НЕ при каждом update.
## @scope    Потребители: install-acme.sh (фасад), lifecycle/phases/certs.py::_install_acme (через фасад).
##           Клонирует acme.sh в ACME_HOME (default /opt/acme.sh) и regtime-ltd/dnsapi-расширения
##           для российских регистраторов (webnames, reg.ru).
## @invariants
##   - Идемпотентность: acme.sh binary существует в ACME_HOME → SKIP (exit 0)
##   - apt-get install git — под timeout=APT_TIMEOUT (канон shared/timeouts, DevPlan 123 T7);
##     graceful (rc!=0/not-found → продолжаем) — прежний «GNU timeout 300 + || true» канон
##   - git clone --depth 1 в непустой существующий ACME_HOME → merge-fallback:
##     clone в ${ACME_HOME}.clone-tmp + no-clobber merge (cp -rn семантика — *_ecc НЕ перезаписываются) + rm tmp
##   - dnsapi_ext clone — non-fatal (WARN при провале; webnames TLS работать не будет)
##   - clone-tmp создаётся/удаляется детерминированно (rm -rf перед clone, rm после merge)
##   - Proxy-переменные ожидаются чистыми (unset_platform_proxy канон; certs.py вычищает env subprocess)
##   - Логи — в stderr (logging.basicConfig stream=sys.stderr), LDD [IMP:1-10]
## @rationale Q: Почему Python-модуль, а не продолжение shell?
##            A: Языковая политика (root AGENTS.md) — новый код на Python; shell — тонкие фасады.
##            W3-7 аудит классифицировал install-acme.sh как ORCHESTRATION (93 LOC); Tier-2 плановая
##            миграция (DevPlan 164 W3.5-1). DI (runner/facts) делает git/apt-оркестрацию тестируемой.
##            Merge-fallback (DevPlan 136 W1 D4, 017e1c1) — battle-tested фикс: bare clone падал на
##            re-run, cert stores *_ecc терялись. Семантика сохраняется байт-в-байт (cp -rn → no-clobber).
## @changes  2026-08-14 | DevPlan 164 W3.5-1 — создан (Strangler install-acme.sh)
## @see      core/internal/bootstrap/install-acme.sh (фасад), tor_setup.py (apt-канон),
##           shared/timeouts.py (APT_TIMEOUT), shared/subprocess_io.py (CommandRunner),
##           shared/env_facts.py (EnvironmentFacts)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

from core.internal.shared.contracts import EXIT_GENERIC, EXIT_OK
from core.internal.shared.env_facts import EnvironmentFacts, default_env_facts
from core.internal.shared.subprocess_io import CommandRunner, default_command_runner
from core.internal.shared.timeouts import APT_TIMEOUT

logger = logging.getLogger(__name__)

# ── Канонические константы (совпадают с прежними литералами install-acme.sh) ──
DEFAULT_ACME_HOME: str = "/opt/acme.sh"
ACME_REPO_URL: str = "https://github.com/acmesh-official/acme.sh.git"
DNSAPI_REPO_URL: str = "https://github.com/regtime-ltd/dnsapi.git"
GIT_CLONE_TIMEOUT: int = 300  # depth-1 clone acme.sh — малый репо; hang-защита (как apt 300)
DNSAPI_CLONE_TIMEOUT: int = 120


# region FUNC__log_step
def _log_step(step: str, status: str, msg: str) -> None:
    """log_step-эквивалент: [IMP:8][install-acme][<step>] <STATUS>: <msg> (logging.sh канон).

    ## @purpose  Байт-совместимый вывод шагов с прежним shell log_step (logging.sh).
    ## @io — ⇥ step, status, msg → ⎋ stderr via logger
    ## @complexity — O(1)
    """
    logger.info("[IMP:8][install-acme][%s] %s: %s", step, status, msg)


# endregion FUNC__log_step


# region FUNC__merge_no_clobber
def _merge_no_clobber(src: Path, dst: Path) -> None:
    """Рекурсивный no-clobber merge (cp -rn семантика): существующие файлы НЕ перезаписываются.

    ▶ ┌src, dst┐ → ○ for item in src: ◇ dir? recurse │ ◇ dst missing? copy → ⎋ None

    ## @purpose  Merge свежего clone в существующий ACME_HOME БЕЗ перезаписи *_ecc cert stores
    ##            (DevPlan 136 W1 D4, 017e1c1). cp -rn сохраняет leftover-каталоги прошлого install.
    ## @io — ⇥ src: Path (clone-tmp), dst: Path (ACME_HOME) → ⎋ None
    ## @complexity — O(F) — F = файлов в src
    ## @invariants
    ##   - Существующий dst-файл/каталог НЕ трогается (cp -rn no-clobber)
    ##   - Новые файлы (acme.sh, dnsapi/) копируются (shutil.copy2 — атрибуты сохраняются)
    """
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            _merge_no_clobber(item, target)
        elif not target.exists():
            shutil.copy2(item, target)


# endregion FUNC__merge_no_clobber


# region FUNC_install_acme
## @purpose  Установить acme.sh и DNS API-расширения (импорт-совместимая функция модуля).
## @io       ⇥ acme_home: str; runner: CommandRunner | None; facts: EnvironmentFacts | None
##           → ⎋ bool (True = установлен или уже присутствует)
## @complexity — O(G) — G = git clone время (depth 1)
## @invariants
##   - Идемпотентно: acme.sh exists → True (SKIP, без мутаций)
##   - apt-get git — graceful (APT_TIMEOUT канон); не-найдено (rc 127) → continue
##   - git clone ACME_HOME провалился (непустой каталог) → merge-fallback в .clone-tmp
##   - dnsapi_ext — non-fatal (WARN); отсутствие = webnames TLS недоступен (прежний контракт)
##   - Любой фатальный провал (clone-tmp тоже failed) → False (exit 1 канон)
def install_acme(
    acme_home: str = DEFAULT_ACME_HOME,
    *,
    runner: CommandRunner | None = None,
    facts: EnvironmentFacts | None = None,
) -> bool:
    """Install acme.sh and DNS API extensions (idempotent). True = installed or already present.

    ▶ ┌acme_home┐ → ◇ binary? SKIP → ○ apt git → ○ clone | merge-fallback → ○ dnsapi_ext → ⎋ bool
    """
    r = runner if runner is not None else default_command_runner()
    facts_obj = facts if facts is not None else default_env_facts()
    acme_sh = Path(acme_home) / "acme.sh"

    if facts_obj.path_isfile(acme_sh) and os.access(acme_sh, os.X_OK):
        _log_step("acme", "SKIP", f"acme.sh already installed at {acme_home}")
        logger.info("[IMP:9][install-acme][acme] SKIP — already installed at %s", acme_home)
        return True

    _log_step("acme", "START", f"Installing acme.sh to {acme_home}")

    # GNU timeout (300s) вокруг apt-get install git — hang-защита на свежей VPS (DevPlan 123 T7);
    # канон: timeout=APT_TIMEOUT из shared/timeouts (заменяет прежний shell GNU timeout wrapper).
    # rc 127 (apt-get отсутствует на macOS/dev) → graceful continue (|| true канон).
    r.run(
        ["apt-get", "install", "-y", "-qq", "git"],
        timeout=APT_TIMEOUT,
        check=False,
        non_fatal=True,
    )

    # Proxy-переменные здесь не нужны: unset_platform_proxy() в bootstrap.sh уже вычистил
    # HTTP_PROXY/HTTPS_PROXY на уровне хоста (контракт install-acme, TRAP 141 B4).
    home = Path(acme_home)
    if not _clone_acme_github(home, r):
        return False

    # Clone dnsapi extensions for Russian registrars (webnames, reg.ru, etc.) — non-fatal
    _clone_dnsapi_ext(home, r)

    _log_step("acme", "DONE", f"acme.sh installed at {acme_home}")
    logger.info("[IMP:9][install-acme][acme] Installation complete: %s", acme_home)
    return True


# endregion FUNC_install_acme


# region FUNC__clone_acme_github
## @purpose  git clone acme.sh (depth 1) + idempotent merge-fallback при непустом ACME_HOME.
## @io       ⇥ acme_home: Path, runner: CommandRunner → ⎋ bool
## @complexity — O(G) — clone время
## @invariants
##   - clone в непустой каталог → merge-fallback (clone-tmp + no-clobber + rm tmp)
##   - WARN «merged with fresh clone» при fallback (D4 контракт, DevPlan 136 W1)
##   - Оба clone провалились → FAIL False
def _clone_acme_github(acme_home: Path, runner: CommandRunner) -> bool:
    """Clone acme.sh repo; on failure (non-empty existing dir) — merge-fallback preserving *_ecc."""
    result = runner.run(
        ["git", "clone", "--depth", "1", ACME_REPO_URL, str(acme_home)],
        timeout=GIT_CLONE_TIMEOUT,
        check=False,
    )
    if result.returncode == 0:
        logger.info("[IMP:9][install-acme][clone] acme.sh cloned to %s", acme_home)
        return True

    # ⚠️ TRAP[BUG] · 2026-08-04 · P1 · bare git clone падал на re-run (bootstrap φ7 blocker)
    # · Symptom: повторный bootstrap — `git clone` в существующий /opt/acme.sh → fatal:
    # ·   "destination path already exists and is not an empty directory" → φ7 done_with_warnings
    # · Root: clone без fallback; leftover-каталоги *_ecc (прошлый install) делают каталог непустым
    # · Fix (017e1c1): idempotent fallback — clone в .clone-tmp + cp -rn merge БЕЗ перезаписи
    # ·   существующего (сохранение cert data) + rm tmp. Семантика сохранена в _merge_no_clobber.
    # · Prevention: любой clone в существующий каталог платформы — через merge-fallback.
    logger.info(
        "[IMP:8][install-acme][clone] git clone failed (rc=%d) — merge-fallback (idempotent re-run)",
        result.returncode,
    )
    clone_tmp = Path(f"{acme_home}.clone-tmp")
    shutil.rmtree(clone_tmp, ignore_errors=True)
    fallback = runner.run(
        ["git", "clone", "--depth", "1", ACME_REPO_URL, str(clone_tmp)],
        timeout=GIT_CLONE_TIMEOUT,
        check=False,
    )
    if fallback.returncode == 0:
        _merge_no_clobber(clone_tmp, acme_home)
        shutil.rmtree(clone_tmp, ignore_errors=True)
        _log_step("acme", "WARN", f"Existing {acme_home} merged with fresh clone (idempotent re-run)")
        logger.info("[IMP:9][install-acme][merge] *_ecc cert stores preserved (no-clobber merge)")
        return True
    _log_step("acme", "FAIL", "Failed to clone acme.sh repository")
    return False


# endregion FUNC__clone_acme_github


# region FUNC__clone_dnsapi_ext
## @purpose  Clone regtime-ltd/dnsapi extensions (webnames/reg.ru plugins) — non-fatal.
## @io       ⇥ acme_home: Path, runner: CommandRunner → ⎋ None
## @complexity — O(C) — clone время
## @invariants
##   - Уже существует (is_dir) → SKIP
##   - Провал clone → WARN (webnames TLS не будет работать), НЕ FAIL
def _clone_dnsapi_ext(acme_home: Path, runner: CommandRunner) -> None:
    """Clone DNS API extension repo (non-fatal) for Russian registrars."""
    dnsapi_ext = acme_home / "dnsapi_ext"
    if dnsapi_ext.is_dir():
        _log_step("dnsapi-ext", "SKIP", f"DNS API extensions already present at {dnsapi_ext}")
        return
    result = runner.run(
        ["git", "clone", "--depth", "1", DNSAPI_REPO_URL, str(dnsapi_ext)],
        timeout=DNSAPI_CLONE_TIMEOUT,
        check=False,
    )
    if result.returncode != 0:
        _log_step("acme", "WARN", "Failed to clone regtime-ltd/dnsapi — webnames TLS will not work")
    else:
        logger.info("[IMP:9][install-acme][dnsapi-ext] DNS API extensions cloned to %s", dnsapi_ext)


# endregion FUNC__clone_dnsapi_ext


# region FUNC_run
## @purpose  Executor: читает ACME_HOME из env и запускает install_acme (exit-контракт contracts.py).
## @io       ⇥ environ: Mapping[str, str]; runner/facts DI → ⎋ int (0 = ok, 1 = generic error)
## @complexity — O(G) — clone время
## @invariants
##   - sys.exit НЕ вызывается — run() возвращает int (канон core/AGENTS.md)
##   - ACME_HOME env отсутствует → DEFAULT_ACME_HOME (/opt/acme.sh — прежний shell default)
def run(
    environ: dict[str, str] | None = None,
    *,
    runner: CommandRunner | None = None,
    facts: EnvironmentFacts | None = None,
) -> int:
    """Run acme.sh installation from env (ACME_HOME). Exit 0 = ok, 1 = generic error."""
    env: dict[str, str] = dict(os.environ if environ is None else environ)
    acme_home = env.get("ACME_HOME", DEFAULT_ACME_HOME)
    logger.info("[IMP:7][install-acme][main] Starting acme.sh installation")
    if install_acme(acme_home, runner=runner, facts=facts):
        return EXIT_OK
    return EXIT_GENERIC


# endregion FUNC_run


# region FUNC_main
def main(_argv: list[str] | None = None) -> int:
    """CLI: `python3 -m core.internal.bootstrap.install_acme` (фасад exec python3 -m ... "$@").

    ▶ ┌argv┐ → ○ logging stderr → ○ run(environ) → ⎋ exit 0|1

    ## @purpose  Composition root для фасада install-acme.sh. Аргументы shell игнорируются
    ##            (прежняя install_acme "$@" также не имела параметров) — env-контракт (ACME_HOME).
    ## @io — ⇥ argv: list[str] | None → ⎋ int
    ## @complexity — O(G)
    ## @invariants
    ##   - sys.exit НЕ вызывается — main() возвращает int (канон core/AGENTS.md)
    ##   - Логи в stderr (фасад пробрасывает exit-код; stdout пуст — данные CLI не выдаются)
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    return run()


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
