#!/usr/bin/env python3
# GREP_SUMMARY: converge reconciler flock fcntl-lock orchestration R1-R10 dry-run report-only reconcile exit-mapping python-facade W3.5-1 post-reconcile-nginx-reload F6 reload-reorder
# STRUCTURE: ▶ argparse ┌--node --dry-run --report-only --units --reconcile┐ → ⚡ resolve_node_yaml (3-path, node_resolver) → ⚡ flock(fcntl LOCK_EX|NB, skip dry/report) → ▶ subprocess reconciler.py R1-R10 → ◇ --reconcile? → subprocess reconciler_projects.py → ◇ converge ∧ rc≠2 → ⚡ post-reconcile nginx reload (F6) → ⊕ exit {0,1,2} +3 lock-conflict → ⎋ exit
# region MODULE_CONTRACT
## @purpose  Python-оркестратор converge (DevPlan 164 W3.5-1, SH→Python) — прямое замещение
##           shell core/internal/bootstrap/converge.sh (147 LOC). Вся оркестрация: arg-парсинг,
##           резолв node.yaml (core.internal.shared.node_resolver — Python-канон 127 W2),
##           flock-лок через fcntl.flock (POSIX-семантика shell flock сохранена — Rev-условие
##           TRAP[DECISION] 2026-07-22 сработало), диспатч R1-R10 в converge/reconciler.py
##           и --reconcile в core/internal/reconciler_projects.py (subprocess — оркестрация,
##           контракты stdout/exit-code сохраняются байт-эквивалентно).
## @scope    CLI-слой converge.sh: аргументы --node/--dry-run/--report-only/--units/--reconcile;
##           lock-семантика /var/lock/platform-converge.lock (fallback /tmp);
##           exit-маппинг {0=converged, 1=warnings, 2=errors} + 3=lock-conflict (shell parity).
##           НЕ содержит бизнес-логики R-юнитов — она в converge/ пакете (reconciler.py +
##           домены), сюда делегируется subprocess'ом (как shell-фасад делал python3-вызовом).
## @invariants
##   - R1-R10 делегируются converge/reconciler.py (существующий модуль, НЕ редактируется);
##     --reconcile → core/internal/reconciler_projects.py (B9 T4 D4 прямой вызов)
##   - Exit: 0=converged 1=warnings 2=errors; lock-conflict → 3 (shell-контракт acquire_lock)
##   - flock: fcntl.flock(LOCK_EX | LOCK_NB) на /var/lock/platform-converge.lock; dry-run/
##     report-only → lock SKIP (shell parity); mkdir-fail fallback → /tmp/platform-converge.lock
##   - node.yaml резолвится node_resolver.resolve_node_yaml (3-path канон); отсутствие → exit 2
##   - CONVERGE_PYTHON env (DI тестов test_project_scaffold) → python_exe диспатча; default sys.executable
##   - NODE_HOST_MAP env → --node-host-map в reconcile-диспатч (shell parity)
##   - Диспатч НЕ захватывает stdio (subprocess.run без capture) — JSON-report (--report-only)
##     и LDD-логи reconciler проходят насквозь (shell parity)
##   - main() -> int канон (core/AGENTS.md): sys.exit только в __main__
## @rationale Q: Почему Python, а не shell-оркестрация?
##            A: Языковая политика (root AGENTS.md) — новый код на Python; shell — тонкие фасады
##            (<100 LOC). Вся бизнес-логика R-юнитов уже в Python (converge/ пакет); остаток —
##            чистая оркестрация (args/env/lock/dispatch), тестируемая только в Python
##            (fcntl-lock и dispatch через DI).
##            Q: Почему subprocess-диспатч, а не прямой импорт reconciler.main()?
##            A: reconciler.main() парсит sys.argv (argparse) и печатает JSON-report в stdout —
##            отдельный процесс сохраняет контракт 1:1 (свой argv, свой stdout/stderr), как
##            shell-фасад вызывал python3 reconciler.py. Прямой импорт потребовал бы подмены
##            sys.argv и смешал бы stdout-контракты.
##            Q: Почему фасад вызывает скрипт-путь, а не `python3 -m core.internal.bootstrap.converge`?
##            A: Имя модуля converge.py КОЛЛИЗИРУЕТ с существующим пакетом converge/ (пакет
##            перекрывает модуль при импорте — FileFinder приоритет __init__.py). `-m` исполнил бы
##            пакетный __init__.py (docstring-only no-op). Script-path + PYTHONPATH-экспорт —
##            канонический паттерн (TRAP[BUG] 2026-07-31 в прежнем converge.sh, add-vhost.sh:34).
## @changes 2026-08-14 | DevPlan 164 W3.5-1 — Created (SH→Python converge.sh 147 LOC → фасад <100)
## @changes 2026-09-03 | F6 (DevPlan 031 T5) — post-reconcile nginx reload (после всех R-units,
##            converge mode + recon_rc≠2; +post_reconcile_nginx_reload/_default_nginx_reload, DI reload_fn).
##            Деfer reload закрывает mid-run downtime window FINDING-p3-3 (reload до R6-верификации)
## ⚠️ TRAP[DECISION] · 2026-08-14 · HI · flock переехал в Python (fcntl.flock) — Rev сработал
## · (мигрирован из шапки прежнего converge.sh, TRAP[DECISION] 2026-07-22)
## · Rejected: оставить flock в shell (риск: две подсистемы лока — shell и Python, дрейф семантики)
## · Reason: Rev-условие оригинала («если reconciler.py ever needs its own lock, implement at
## ·   Python level with fcntl.flock») — Python-оркестратор converge.py теперь держит flock сам.
## ·   POSIX-семантика сохранена: flock — per-open-file-description (не per-process!), LOCK_EX|NB,
## ·   дескриптор держится открытым на время converge-цикла (fd → fcntl.flock → close при exit).
## ·   fallback /tmp при недоступности /var/lock (shell mkdir -p parity).
## · Rev: если появится второй Python-потребитель лока — вынести в shared/ (единая точка).
## ⚠️ TRAP[DECISION] · 2026-08-14 · MED · Модуль converge.py vs пакет converge/ — shadowing
## · Rejected: переименовать модуль (converge_cli.py) — DevPlan W3.5-1 фиксирует путь converge.py
## · Reason: имя файла converge.py сохранено (прямое замещение converge.sh); импорт модуля под
## ·   этим именем невозможен (пакет converge/ перекрывает — FileFinder приоритет пакета).
## ·   Фасад использует script-path exec + PYTHONPATH (канон), тесты — importlib
## ·   (spec_from_file_location). Имя «converge» для пакета остаётся за пакетом (reconciler.py +
## ·   домены) — конфликт отсутствует по построению.
## · Rev: если converge/ пакет будет переименован — вернуть `python3 -m core.internal.bootstrap.converge`.
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import fcntl
import logging
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from core.internal.shared.contracts import EXIT_CONFIG_NOT_FOUND, EXIT_GENERIC
from core.internal.shared.docker_ops import docker_exec  # F6 (031 T5): post-reconcile nginx reload
from core.internal.shared.exceptions import ConfigNotFoundError
from core.internal.shared.node_resolver import resolve_node_yaml
from core.internal.shared.timeouts import CONVERGE_DOCKER_TIMEOUT as DOCKER_TIMEOUT

logger = logging.getLogger(__name__)

# ── Канонические константы (shell-parity с прежним converge.sh) ──
LOCK_FILE_DEFAULT: str = "/var/lock/platform-converge.lock"
LOCK_FILE_FALLBACK: str = "/tmp/platform-converge.lock"
# Lock-conflict exit: shell acquire_lock() контракт (## @exit 3 — другой converge уже запущен).
# НЕ путать с EXIT_CONFIG_PARSE=3 из shared/contracts — семантика лока унаследована от shell 1:1.
LOCK_CONFLICT_EXIT: int = 3

# Типы DI-каналов (W4c-паттерн, DevPlan 160 W4c — конструкторная DI без monkeypatch)
DispatchFn = Callable[[Sequence[str]], int]
ResolveFn = Callable[[str], str]
LockFn = Callable[[], int]
ReloadFn = Callable[[], object]


# region FUNC_post_reconcile_nginx_reload
## @purpose  Post-reconcile nginx reload (F6/DevPlan 031 T5): ЕДИНСТВЕННЫЙ nginx-reload в
##           converge-цикле, исполняется ПОСЛЕ всех R-units (reconciler.py) и ТОЛЬКО когда
##           R-юниты не сообщили ошибок (recon_rc != 2 → R6 nginx -t прошёл/не падал).
##           Деfer reload закрывает mid-run downtime window (FINDING-p3-3, ночной прогон
##           2026-09-03): серт, выпущенный/восстановленный R-ssl'ом mid-cycle, НЕ должен
##           перечитывать overlay до того, как R6 проверил целостность vhost'ов — при
##           временно-несогласованном overlay (дрилл: vhost .conf удалён) reload до R6
##           сбросил бы живой vhost из running-конфига. Gate recon_rc != 2 = R6 не падал →
##           конфиг валиден → reload безопасен и подхватывает восстановленные серты
##           (docker-nginx: issue_cert systemctl reload — no-op, см. TRAP ниже).
## @io       ⇥ recon_rc: int (итог reconciler), reload_fn: ReloadFn | None (DI) → ⎋ None
## @complexity O(1) — один docker exec nginx -s reload
## @invariants
##   - Вызывается ТОЛЬКО в converge mode (не dry-run/report-only) — preview не мутирует
##   - recon_rc == 2 (errors: R6 vhost fail и т.п.) → reload НЕ выполняется (running-конфиг
##     nginx остаётся нетронутым — ни downtime, ни применение несогласованного overlay)
##   - reload failure (nginx container отсутствует/rc!=0) → WARN, exit-код НЕ меняется
##     (best-effort: конфиг-валидность уже подтвердил R6; reload — подхват сертов)
## @rationale Q: Почему reload в converge.py (оркестратор), а не в R-ssl?
##            A: reorder-контракт «reload после ВСЕХ R-units» — оркестратор единственный
##            слой, видящий итог всех юнитов (R6 vhost-верификацию включительно); R-ssl
##            (cert-unit) не знает, пройдёт ли R6. Пост-хук в оркестраторе делает окно
##            «reload до верификации» невозможным по построению.
## 🧐 TRAP[DECISION] · 2026-09-03 · — · docker-nginx reload канал (F6)
## · Rejected: systemctl reload nginx (reloadcmd issue_cert) как канон reload'а
## · Reason: nginx — Docker-модуль (module.yaml install_type: docker, НЕ systemd); системного
## ·   юнита nginx.service на ноде нет → systemctl reload в acme.sh reloadcmd — тихий no-op
## ·   (rc!=0 терпится `;`-цепочкой). Канон нодового reload'а — docker exec nginx nginx -s
## ·   reload (shared/docker_ops, R6-паттерн, nginx_reload_hook.sh). Post-reconcile reload
## ·   использует docker-канал → восстановленные R-ssl'ом серты реально подхватываются.
## · Rev: если nginx-модуль вернётся к systemd-управлению — синхронизировать reload-канал
def post_reconcile_nginx_reload(*, recon_rc: int, reload_fn: ReloadFn | None = None) -> None:
    """Run the single converge-cycle nginx reload AFTER all R-units when no R-errors occurred."""
    if recon_rc == EXIT_CONFIG_NOT_FOUND:
        logger.info(
            "[IMP:8][converge][reload] SKIP: R-units reported errors (rc=%d) — running nginx config "
            "left untouched (no mid-run reload, F6/DevPlan 031 T5)",
            recon_rc,
        )
        return
    impl = reload_fn if reload_fn is not None else _default_nginx_reload
    logger.info("[IMP:8][converge][reload] Post-reconcile nginx reload (after all R-units, rc=%d)", recon_rc)
    # docker_exec — non-fatal контракт (никогда не raise; rc!=0 логируется в _default_nginx_reload),
    # поэтому try/except не требуется — reload не может изменить exit-код converge.
    impl()


# endregion FUNC_post_reconcile_nginx_reload


# region FUNC_default_nginx_reload
## @purpose  Default-реализация post-reconcile reload: docker exec nginx nginx -s reload
##           (shared/docker_ops, non-fatal — rc!=0 логируется, не raise).
## @io       ⇥ — → ⎋ None (side-effect: reload)
## @complexity O(1)
## @invariants
##   - nginx container отсутствует/rc!=0 → WARN (R6 уже предупредил «nginx not running»)
##   - Никогда не raise (docker_ops non-fatal контракт)
def _default_nginx_reload() -> None:
    """Reload the nginx container via docker exec (canonical docker-channel reload)."""
    result = docker_exec("nginx", ["nginx", "-s", "reload"], timeout=DOCKER_TIMEOUT)
    if result.returncode == 0:
        logger.info("[IMP:9][converge][reload] nginx reloaded after reconcile (docker exec)")
    else:
        logger.warning(
            "[IMP:7][converge][reload] nginx reload rc=%d (container absent?) — non-fatal", result.returncode
        )


# endregion FUNC_default_nginx_reload


# region EXCEPTION_LockConflictError
class LockConflictError(Exception):
    """Другой converge/node-update уже держит flock — fail-fast exit 3 (shell acquire_lock parity).

    ## @purpose — Раздельный тип для lock-конфликта: main() ловит и маппит в LOCK_CONFLICT_EXIT=3.
    ## @rationale — Отдельный класс вместо флага: явный контракт для тестов (raise → exit 3).
    """


# endregion EXCEPTION_LockConflictError


# region CLASS_CliArgs
class _CliArgs(argparse.Namespace):
    """Типизированный argparse-Namespace (W11-G3): parse_args(namespace=...)."""

    def __init__(self) -> None:
        super().__init__()
        self.node: str | None
        self.dry_run: bool
        self.report_only: bool
        self.units: str
        self.reconcile: bool


# endregion CLASS_CliArgs


# region FUNC_parse_args
def parse_args(argv: Sequence[str]) -> _CliArgs:
    """Парсинг CLI-аргументов converge (shell main() case-эквивалент).

    ▶ ┌argv┐ → ○ argparse ┌--node --dry-run --report-only --units --reconcile --help┐ → ⎋ Namespace

    ## @purpose — Аргументы/семантика совпадают с прежним shell-фасадом 1:1 (прямое замещение):
    ##            --node обязателен, --units — comma-separated фильтр R-юнитов (passthrough),
    ##            --reconcile — прямой вызов reconciler_projects.py после R-диспатча.
    ## @io — ⇥ argv: Sequence[str] (sys.argv[1:] канон) → ⎋ argparse.Namespace
    ## @complexity — O(A) — A = число аргументов
    ## @invariants
    ##   - --node: required=False + ручная проверка в main (контроль exit-кода = 2, IMP-логи)
    ##   - --units пуст → не добавляется в диспатч (shell parity: [[ -n ... ]])
    ##   - Неизвестный аргумент → argparse error exit 2 (девиация от shell usage-exit-0,
    ##     задокументирована в @rationale main — fail-fast канон)
    """
    parser = argparse.ArgumentParser(
        prog="converge",
        description="Idempotent desired-state reconciler for platform VPS (Python-оркестратор, DevPlan 164 W3.5-1).",
        epilog="Exit codes: 0=converged 1=warnings 2=errors 3=lock-conflict (another converge running)",
    )
    parser.add_argument("--node", default=None, type=str, help="Node name to reconcile (required)")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Plan only — no mutations (lock SKIP)")
    parser.add_argument("--report-only", action="store_true", default=False, help="JSON drift report on stdout, exit 0")
    parser.add_argument(
        "--units",
        default="",
        type=str,
        help="Comma-separated R-unit filter (e.g., 'R1,R3'); empty = all units (passthrough to reconciler.py)",
    )
    parser.add_argument(
        "--reconcile", action="store_true", default=False, help="After converge, reconcile stub projects"
    )
    return parser.parse_args(list(argv), namespace=_CliArgs())


# endregion FUNC_parse_args


# region FUNC_should_acquire_lock
def should_acquire_lock(*, dry_run: bool, report_only: bool) -> bool:
    """Lock-решение: dry-run/report-only НЕ берут flock (shell acquire_lock parity).

    ▶ ┌dry_run, report_only┐ → ◇ dry|report → False │ → True → ⎋ bool

    ## @purpose — Чистое предикатное решение: shell `if dry_run || report_only → SKIP`.
    ##            Вынесено для точного unit-теста «--dry-run не мутирует» (нет lock-сайд-эффекта).
    ## @io — ⇥ dry_run/report_only → ⎋ True = flock требуется
    ## @complexity — O(1)
    """
    return not dry_run and not report_only


# endregion FUNC_should_acquire_lock


# region FUNC_acquire_flock
def acquire_flock(lock_path: str | None = None) -> int:
    """Приобрести эксклюзивный non-blocking flock (POSIX, shell flock parity).

    ▶ ┌lock_path?┐ → ○ os.open O_CREAT|O_RDWR → ◇ OSError? → fallback /tmp → ⚡ fcntl.flock(LOCK_EX|NB)
      → ◇ EWOULDBLOCK? LockConflictError │ → ⎋ fd (дескриптор открыт — держать до конца converge-цикла)

    ## @purpose — flock-лок /var/lock/platform-converge.lock через fcntl.flock — Rev-условие
    ##            TRAP[DECISION] 2026-07-22 (Python-уровень). Shell `exec 200>file + flock -n 200`
    ##            эквивалент: LOCK_EX|LOCK_NB, дескриптор живёт до close/exit процесса.
    ## @io — ⇥ lock_path: Optional[str] (None → LOCK_FILE_DEFAULT) → ⎋ fd: int
    ##       ⚡ LockConflictError: другой процесс уже держит flock (shell FATAL exit 3)
    ## @complexity — O(1) syscalls
    ## @invariants
    ##   - fallback: os.open LOCK_FILE_DEFAULT → OSError (нет /var/lock на macOS, нет прав)
    ##     → LOCK_FILE_FALLBACK (/tmp) — shell mkdir -p fallback parity
    ##   - flock — per-open-file-description: ДВА открытых fd на один файл в одном процессе
    ##     КОНФЛИКТУЮТ между собой (в отличие от shell flock-команды) — тест-семантика точная
    ##   - EAGAIN/EWOULDBLOCK → LockConflictError (не блокируемся — shell flock -n канон)
    ##   - fd НЕ закрывается здесь — владелец (main) закрывает при выходе
    """
    target = lock_path if lock_path is not None else LOCK_FILE_DEFAULT
    try:
        fd = os.open(target, os.O_CREAT | os.O_RDWR, 0o644)
    except OSError:
        fd = os.open(LOCK_FILE_FALLBACK, os.O_CREAT | os.O_RDWR, 0o644)
        logger.info("[IMP:7][converge][lock] WARN: %s unavailable — fallback lock: %s", target, LOCK_FILE_FALLBACK)
        target = LOCK_FILE_FALLBACK
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        msg = f"Another converge or node-update is already running (lock: {target})"
        raise LockConflictError(msg) from None
    logger.info("[IMP:7][converge][lock] Acquired exclusive lock: %s", target)
    return fd


# endregion FUNC_acquire_flock


# region FUNC_default_dispatch
def default_dispatch(cmd: Sequence[str]) -> int:
    """Реальный subprocess-диспатч: stdio наследуется (shell `${recon_cmd[@]}` parity).

    ▶ ┌cmd┐ → ○ subprocess.run(cmd, check=False) → ⎋ returncode

    ## @purpose — Диспатч в reconciler.py / reconciler_projects.py БЕЗ capture: JSON-report
    ##            (--report-only) и LDD-логи reconciler проходят насквозь в stdout/stderr
    ##            вызывающего (shell-фасад вызывал python3 так же). run_subprocess из
    ##            shared/subprocess_io захватывает stdout (capture_output=True) — НЕ подходит.
    ## @io — ⇥ cmd: Sequence[str] → ⎋ int returncode (0/1/2 от reconciler)
    ## @complexity — O(M) — время дочернего процесса
    ## @invariants
    ##   - check=False — никогда не raise (graceful, как shell `cmd || rc=$?`)
    ##   - FileNotFoundError → subprocess.run сам raise'ит — это fail-fast баг конфигурации,
    ##     пусть всплывает громко (отсутствие python3 = сломанная доставка core)
    """
    logger.info("[IMP:8][converge][dispatch] Running: %s", " ".join(cmd))
    proc = subprocess.run(list(cmd), check=False)
    return int(proc.returncode)


# endregion FUNC_default_dispatch


# region FUNC_build_reconciler_cmd
def build_reconciler_cmd(
    node_yaml_path: str,
    node_name: str,
    core_dir: Path,
    *,
    dry_run: bool,
    report_only: bool,
    units: str,
    python_exe: str,
) -> list[str]:
    """Собрать argv диспатча в converge/reconciler.py (shell recon_cmd parity 1:1).

    ▶ ┌node_yaml, node_name, core_dir, flags, python_exe┐ → ⊕ argv → ⎋ list[str]

    ## @purpose — Байт-эквивалент прежнего shell:
    ##   recon_cmd=(python3 reconciler.py --node-yaml P --node-name N --core-dir C
    ##              [--dry-run] [--report-only] [--units U])
    ## @io — ⇥ параметры диспатча → ⎋ list[str] argv (порядок сохраняется)
    ## @complexity — O(1)
    ## @invariants
    ##   - --units добавляется ТОЛЬКО при непустом фильтре (shell `[[ -n ]]` parity)
    ##   - reconciler.py — существующий модуль пакета converge/ (НЕ редактируется, W3.5-1 правило)
    """
    cmd = [
        python_exe,
        str(core_dir / "internal" / "bootstrap" / "converge" / "reconciler.py"),
        "--node-yaml",
        node_yaml_path,
        "--node-name",
        node_name,
        "--core-dir",
        str(core_dir),
    ]
    if dry_run:
        cmd.append("--dry-run")
    if report_only:
        cmd.append("--report-only")
    if units:
        cmd.extend(["--units", units])
    return cmd


# endregion FUNC_build_reconciler_cmd


# region FUNC_build_reconcile_cmd
def build_reconcile_cmd(
    node_yaml_path: str,
    node_name: str,
    core_dir: Path,
    *,
    dry_run: bool,
    node_host_map: str,
    python_exe: str,
) -> list[str]:
    """Собрать argv диспатча --reconcile в core/internal/reconciler_projects.py (B9 T4 D4 parity).

    ▶ ┌node_yaml, node_name, core_dir, dry_run, node_host_map, python_exe┐ → ⊕ argv → ⎋ list[str]

    ## @purpose — Байт-эквивалент прежнего shell rec_cmd:
    ##   rec_cmd=(python3 ../reconciler_projects.py --node N --node-yaml P
    ##            [--node-host-map MAP] [--dry-run])
    ## @io — ⇥ параметры reconcile-диспатча → ⎋ list[str] argv
    ## @complexity — O(1)
    ## @invariants
    ##   - --node-host-map добавляется ТОЛЬКО при непустом env NODE_HOST_MAP (shell parity)
    ##   - reconciler_projects.py — существующий модуль core/internal/ (НЕ редактируется)
    """
    cmd = [
        python_exe,
        str(core_dir / "internal" / "reconciler_projects.py"),
        "--node",
        node_name,
        "--node-yaml",
        node_yaml_path,
    ]
    if node_host_map:
        cmd.extend(["--node-host-map", node_host_map])
    if dry_run:
        cmd.append("--dry-run")
    return cmd


# endregion FUNC_build_reconcile_cmd


# region FUNC__default_resolver
def _default_resolver(env: Mapping[str, str]) -> ResolveFn:
    """Фабрика default-резолвера node.yaml (bind env — hermetic DI).

    ▶ ┌env┐ → ⊕ closure → ⎋ ResolveFn(node_name) → str path

    ## @purpose — node_resolver.resolve_node_yaml(env=...) — 3-path канон (NodeYaml.resolve:
    ##            platform_root → ~/projects glob → /opt). projects_dir vestigial (shell-parity).
    ## @io — ⇥ env: Mapping (PLATFORM_ROOT/HOME читаются внутри NodeYaml.resolve) → ⎋ resolver
    ## @complexity — O(P + N)
    """

    def resolve(node_name: str) -> str:
        return resolve_node_yaml(node_name=node_name, env=env)

    return resolve


# endregion FUNC__default_resolver


# region FUNC_main
def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    dispatch_fn: DispatchFn | None = None,
    resolve_fn: ResolveFn | None = None,
    lock_fn: LockFn | None = None,
    reload_fn: ReloadFn | None = None,
    python_exe: str | None = None,
) -> int:
    """Entry point: parse args → resolve node.yaml → flock → reconciler.py → --reconcile → reload → exit.

    ▶ ┌argv, env?, dispatch_fn?, resolve_fn?, lock_fn?, reload_fn?, python_exe?┐ → ◇ --node? exit 2
      → ⚡ resolve node.yaml (ConfigNotFound → exit 2) → ◇ is_file? exit 2
      → ◇ should_acquire_lock? → ⚡ flock (conflict → exit 3) → ▶ dispatch reconciler.py → rc
      → ◇ --reconcile? → dispatch reconciler_projects.py (fail → rc=max(rc,2))
      → ◇ converge mode ∧ rc≠2? → ⚡ post-reconcile nginx reload (F6/031 T5)
      → ⊕ summary {0,1,2} → ⎋ exit rc

    ## @purpose — main()-канон (core/AGENTS.md: business-функции без sys.exit; sys.exit в __main__).
    ##            Все DI-каналы инъектируемы (W4c): dispatch_fn (subprocess), resolve_fn (node.yaml),
    ##            lock_fn (flock), reload_fn (post-reconcile nginx reload), env (os.environ),
    ##            python_exe (CONVERGE_PYTHON|sys.executable).
    ## @io — ⇥ argv: Optional[Sequence[str]]; DI-каналы → ⎋ int exit {0,1,2,3}
    ## @complexity — O(R) — R = суммарное время R-юнитов reconciler
    ## @invariants
    ##   - Exit-маппинг {0,1,2}: passthrough rc reconciler.py (его агрегация R-юнитов — SoT);
    ##     lock-conflict → 3 (shell acquire_lock parity)
    ##   - --node отсутствует → IMP:10 FATAL + usage → exit 2. ДЕВИАЦИЯ от shell (usage exit 0):
    ##     fail-fast канон — missing-required-arg это ошибка (2), а не успех; документировано.
    ##   - Reconcile fail (rc != 0) при rc < 2 → rc = 2 (shell `[[ 2 -gt $recon_rc ]]` parity)
    ##   - node.yaml резолв: ConfigNotFoundError → FATAL exit 2; is_file guard сохраняется
    ##   - lock_fd держится до выхода main (close при return/exit — flock снимается автоматически)
    ##   - stdio диспатча НЕ захватывается (default_dispatch) — JSON-report passthrough
    ##   - F6/031 T5: converge mode (не dry/report) ∧ recon_rc≠2 → post-reconcile nginx reload
    ##     (после ВСЕХ R-units; R6 не падал → конфиг валиден → reload безопасен)
    """
    args = parse_args(argv if argv is not None else sys.argv[1:])
    if args.node is None:
        logger.error("[IMP:10][converge][args] FATAL: --node is required")
        logger.error(
            "[IMP:9][converge][usage] Usage: converge --node <name> [--dry-run] [--report-only] [--reconcile] [--units <R..>]"
        )
        return EXIT_CONFIG_NOT_FOUND

    env_map: Mapping[str, str] = env if env is not None else os.environ
    resolver: ResolveFn = resolve_fn if resolve_fn is not None else _default_resolver(env_map)
    dispatcher: DispatchFn = dispatch_fn if dispatch_fn is not None else default_dispatch
    lock_acquirer: LockFn = lock_fn if lock_fn is not None else acquire_flock
    reloader: ReloadFn = reload_fn if reload_fn is not None else _default_nginx_reload
    py_exe: str = python_exe or env_map.get("CONVERGE_PYTHON") or sys.executable

    # ── Resolve node.yaml (3-path канон; shell setup_environment parity) ──
    try:
        node_yaml_path = resolver(args.node)
    except ConfigNotFoundError as exc:
        logger.error("[IMP:10][converge][setup] FATAL: Cannot resolve node.yaml for node=%s: %s", args.node, exc)
        return EXIT_CONFIG_NOT_FOUND
    if not Path(node_yaml_path).is_file():
        logger.error("[IMP:10][converge][setup] FATAL: node.yaml not found at %s", node_yaml_path)
        return EXIT_CONFIG_NOT_FOUND
    logger.info("[IMP:8][converge][setup] Node: %s node.yaml: %s", args.node, node_yaml_path)

    # ── Acquire flock (dry-run/report-only SKIP — shell parity) ──
    lock_fd: int | None = None
    if should_acquire_lock(dry_run=args.dry_run, report_only=args.report_only):
        try:
            lock_fd = lock_acquirer()
        except LockConflictError as exc:
            logger.error("[IMP:10][converge][lock] FATAL: %s", exc)
            return LOCK_CONFLICT_EXIT
    else:
        logger.info("[IMP:7][converge][lock] SKIP: flock not needed in dry-run/report-only mode")
    if lock_fd is not None:
        # Дескриптор держится до выхода main (close при exit) — flock снимается автоматически.
        logger.info("[IMP:7][converge][lock] Lock held (fd=%d) until converge cycle ends", lock_fd)

    # ── Header ──
    mode = "DRY-RUN" if args.dry_run else ("REPORT-ONLY" if args.report_only else "CONVERGE")
    logger.info("[IMP:9][converge][main] ==============================")
    logger.info(
        "[IMP:9][converge][main] Platform Converge START — Node: %s — Mode: %s — node.yaml: %s",
        args.node,
        mode,
        node_yaml_path,
    )
    logger.info("[IMP:9][converge][main] ==============================")

    core_dir = Path(__file__).resolve().parents[2]

    # ── Dispatch R1-R10 to converge/reconciler.py ──
    logger.info("[IMP:9][converge][main] Dispatching to reconciler.py...")
    recon_rc = dispatcher(
        build_reconciler_cmd(
            node_yaml_path,
            args.node,
            core_dir,
            dry_run=args.dry_run,
            report_only=args.report_only,
            units=args.units,
            python_exe=py_exe,
        )
    )

    # ── Optional: --reconcile stub → deployed (прямой вызов reconciler_projects.py, B9 T4 D4) ──
    if args.reconcile:
        rec_rc = dispatcher(
            build_reconcile_cmd(
                node_yaml_path,
                args.node,
                core_dir,
                dry_run=args.dry_run,
                node_host_map=env_map.get("NODE_HOST_MAP", ""),
                python_exe=py_exe,
            )
        )
        if rec_rc != 0:
            logger.error("[IMP:10][converge][main] Reconcile step failed (rc=%d)", rec_rc)
            # shell `[[ 2 -gt $recon_rc ]] && recon_rc=2` parity — любой reconcile-fail → exit 2
            recon_rc = max(recon_rc, EXIT_CONFIG_NOT_FOUND)

    # ── F6 (DevPlan 031 T5): post-reconcile nginx reload — ПОСЛЕ всех R-units ──
    # Defer-контракт: reload не выполняется mid-run (R-ssl/иной юнит не перечитывает overlay
    # до верификации R6); единственный reload цикла — здесь, и ТОЛЬКО если R-юниты не
    # сообщили ошибок (recon_rc != 2: R6 nginx -t не падал → конфиг валиден). dry-run/
    # report-only → reload SKIP (mode-контракт «no mutations»).
    if not args.dry_run and not args.report_only:
        post_reconcile_nginx_reload(recon_rc=recon_rc, reload_fn=reloader)

    # ── Final summary and exit (shell parity) ──
    logger.info("[IMP:9][converge][main] ==============================")
    if recon_rc == EXIT_CONFIG_NOT_FOUND:
        logger.info("[IMP:9][converge][main] ERRORS DETECTED — some R-units failed (exit 2)")
    elif recon_rc == EXIT_GENERIC:
        logger.info("[IMP:9][converge][main] WARNINGS DETECTED — non-critical drift (exit 1)")
    else:
        logger.info("[IMP:9][converge][main] FULLY CONVERGED — all R-units converged (exit 0)")
    logger.info("[IMP:9][converge][main] ==============================")
    return recon_rc


# endregion FUNC_main


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    sys.exit(main())
