#!/usr/bin/env python3
# GREP_SUMMARY: core-deliverer deliver_core deliver_ci deliver_platform_env deliver_makefile deliver_node_configs deliver_secrets ensure_remote_dirs rsync ssh mkdir-p core-channel strangler scp-to-server DI runner-param W4d
# STRUCTURE: ▶ ┌resolve_remote_base/env chain┐ → ⚡ ensure_remote_dirs(ssh mkdir -p) → ⚡ Phase 1/4 deliver_core(rsync core/) → ⚡ 1b deliver_platform_env → ⚡ 1c deliver_makefile → ⚡ Phase 2/4 deliver_node_configs → ◇ secrets dir? → ⚡ Phase 3/4 deliver_secrets | ▶ deliver_ci(CI-канал, REF-0112: mkdir+guarded phases, один owner excludes) → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Core delivery channel (push-based SCP/rsync, NO git) — Python-порт scp-deliver.sh
##           scp_to_server(): ensure_remote_dirs (ssh mkdir -p) + 5 rsync фаз (core/,
##           platform-env.yaml, Makefile, node-configs/{node}/, secrets/). Standalone — НЕ
##           импортирует overlay_deliverer (разрыв цикла импорта overlay→core, DevPlan 108 D2).
## @scope    Вызывается: (1) scp-deliver.sh фасад — CLI `deliver` (bootstrap.sh путь, AC3);
##           (2) overlay_deliverer.sync_core_to_vps() — делегирование deliver_core() (AC4);
##           (3) CI .github/workflows/core-deploy.yml — CLI `ci-deliver` (REF-0112: модульный
##           вызов вместо inline-rsync; единый owner exclude-set'ов для обоих каналов).
##           Канал Core per root AGENTS.md «Три канала доставки кода на VPS».
## @invariants
##   - SSH_OPTS — единый SoT из shared/ssh_opts.py (DevPlan 116 B5 T2, D1) — НЕ mirror lib/ssh.sh
##   - RSYNC_EXCLUDES_CORE 6 паттернов / _NODE 3 / _SECRETS 1 — точное соответствие таблице AC7
##     (+ docker-compose.test.yml, DevPlan 162 W10-2); REF-0112: ЕДИНСТВЕННЫЙ владелец exclude-set'ов
##     обоих каналов (CI не дублирует rsync/exclude-логику shell'ом)
##   - Fail-fast: первая упавшая фаза → CoreDeliveryError → CLI exit 1 (эквивалент shell || return 1)
##   - Timeouts: mkdir=30 (parity ssh_exec scp-deliver.sh:142), rsync=600 (deploy-дефолт lib/ssh.sh:119)
##   - DRY_RUN: печать команд в stderr (IMP:8), 0 subprocess-вызовов, успех
##   - subprocess.run с list-args (НЕ string concat) — unit-тестируемость (P3 DevPlan 108)
##   - W4d DI: runner: CommandRunner | None = None во всех delivery-функциях — дефолт (None) →
##     прямой subprocess.run (поведение НЕ изменено: TimeoutExpired/FileNotFoundError пробрасываются);
##     runner задан (тесты) → runner.run(cmd, timeout=...) — fake scripted (CompletedProcess).
## @rationale SRP (DevPlan 108 D1): ДВА раздельных канала доставки — Core (push SCP/rsync) vs
##            Context-overlay (git pull-based). core_deliverer.py = канал Core; overlay_deliverer.py
##            = overlay-канал. Standalone-конвенция (D2): SSH_OPTS из shared/ssh_opts.py — единый
##            SoT (5 дублирующих копий SSH_OPTS заменены импортом, DevPlan 116 B5 T2 D1).
##            W4d (160 T4.4): runner-параметр убирает monkeypatch subprocess.run из тестов
##            (fake-раннер с ассертами вместо патчей пол-ОС).
## @changes 2026-07-31 | DevPlan 108 — Strangler-Fig Tier 2: scp-deliver.sh 251→≤60 LOC,
##           вся rsync/ssh оркестрация scp_to_server() → настоящий Python-модуль
##           2026-08-01 | DevPlan 116 B5 T2 — SSH_OPTS → импорт из shared/ssh_opts.py (D1);
##                      _ssh_e → build_rsync_ssh_opts() (единственная реализация)
##           2026-08-13 | DevPlan 160 W4d — +runner: CommandRunner | None (DI), cli(argv, runner)
##           2026-08-24 | REF-0007 — deliver_fallback: AGE-ключ через ssh-stdin (`bash -s`),
##                      redact_secrets() для error-логов; +_run_cmd_stdin
##           2026-08-25 | REF-0112 (meta-refactoring S-пакет) — +deliver_ci()/CLI `ci-deliver`:
##                      CI core-deploy доставляет файлы модульным вызовом (один owner exclude-set)
##           2026-08-25 | QA C5 (DevPlan 14 T1.4) — CLI-флаг --age-secret-key УДАЛЁН:
##                      deliver_fallback детектирует ключ внутри Python (node_detect цепочка),
##                      /proc cmdline локального python не содержит секрета; redact
##                      ПЕРЕД truncate в error-логах (суффикс ключа на границе окна больше
##                      не уходит в лог)
# endregion MODULE_CONTRACT

import argparse
import logging
import os
import pathlib
import shlex
import subprocess
import sys
from collections.abc import Callable
from typing import cast

# DevPlan 118 C7: remote-пути (/opt/platform, /opt/node-configs) — единые резолверы
# shared/deploy_paths (литералы удалены из канона путей доставки).
from core.internal.shared.deploy_paths import node_configs_remote, placement_remote_path, platform_remote_base

# DevPlan 116 B5 T2 (D1): SSH_OPTS — единый SoT shared/ssh_opts.py; дублирующие копии устранены.
from core.internal.shared.node_detect import detect_age_key
from core.internal.shared.ssh_opts import SSH_OPTS, build_rsync_ssh_opts
from core.internal.shared.subprocess_io import CommandRunner

logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stderr)
logger = logging.getLogger(__name__)

# Rsync exclude-паттерны — дословное соответствие scp-deliver.sh (таблица AC7 DevPlan 108).
RSYNC_EXCLUDES_CORE: list[str] = [  # Phase 1 core/ — 6 паттернов
    "--exclude=.git",
    "--exclude=__pycache__",
    "--exclude=.pytest_cache",
    "--exclude=default-user.xml",
    "--exclude=.env",
    # DevPlan 162 W10-2: 13 test-compose файлов (docker-compose.test.yml) не доставляются на прод —
    # тест-оверрайды бесполезны в production и раздувают дерево core (simplification W10-2)
    "--exclude=docker-compose.test.yml",
]
RSYNC_EXCLUDES_NODE: list[str] = [  # Phase 2 node-configs/{node}/ — 3 паттерна
    "--exclude=.git",
    "--exclude=__pycache__",
    "--exclude=.pytest_cache",
]
RSYNC_EXCLUDES_SECRETS: list[str] = [  # Phase 3 secrets/ — 1 паттерн
    "--exclude=.git",
]

# Timeout-hardening (D5 DevPlan 108): mkdir parity scp-deliver.sh:142 (ssh_exec timeout=30),
# rsync = канонический deploy-дефолт lib/ssh.sh:119 (`timeout "${4:-600}"`). Трансфер > 600s
# аномален (обрыв сети, зависший ssh) — при нормальных трансферах поведение идентично shell.
# W1-A1 (план 170): MKDIR_TIMEOUT=30 (дубль SoT) → FILE_OP_TIMEOUT (15) — файловая операция
# mkdir -p на удалённой ноде (канон converge-файловых мутаций, DevPlan 119 B7); RSYNC_TIMEOUT=600
# — точный дубль shared/timeouts.RSYNC_TIMEOUT → импорт из SoT (литерал 600 удалён).
from core.internal.shared.timeouts import FILE_OP_TIMEOUT, RSYNC_TIMEOUT

# 142 W5 (A6): fallback-деплой — ssh provision/node-update таймаут (канон deploy-дефолт 600s,
# node-update ~5-30 мин; 1800s = 30 мин запас на полный update-цикл, TRAP lib/ssh.sh).
# W1-A1 (план 170): значение 1800 уникально — НЕ в SoT-наборе {10,15,30,60,120,180,300,600};
# SSH_CMD_TIMEOUT остаётся модульной константой (delivery-пайплайн, TRAP ниже).
# 🧐 TRAP[DECISION] · 2026-08-14 · — · SSH_CMD_TIMEOUT=1800 — уникальное значение delivery-пайплайна
# · Rejected: импорт существующей SoT-константы (DEPLOY_TIMEOUT=600 недостаточно; SoT теперь 900) · Reason: 600s недостаточно
# ·   для полного node-update-цикла (5-30 мин) — замена изменила бы поведение; значение 1800
# ·   вне канонического SoT-набора, канонизация не требуется (один потребитель — core_deliverer)
# · Rev: если появится второй потребитель 1800 — канонизировать в shared/timeouts
SSH_CMD_TIMEOUT = 1800


# region FUNC__run_cmd
## @purpose  Единая точка исполнения команд доставки: дефолт (runner=None) → прямой
##           subprocess.run (сохранение поведения: TimeoutExpired/FileNotFoundError
##           пробрасываются — НЕ graceful); runner задан (тесты) → runner.run(cmd, timeout=)
##           (fake scripted CompletedProcess; канон CommandRunner W4b).
## @io  input: cmd: list[str], timeout: int, runner: CommandRunner | None
##      output: subprocess.CompletedProcess
## @complexity  O(1) — delegation
## @rationale  W4d (160 T4.4): DI-шов БЕЗ изменения дефолтной семантики. Канон run_subprocess
##             (shared/subprocess_io) имеет graceful-семантику (rc=127/124 вместо raise) —
##             прямая замена subprocess.run на него изменила бы дефолтное поведение
##             (raise TimeoutExpired → graceful rc=124). Условный вызов сохраняет поведение.
##             🧐 TRAP[DECISION] · 2026-08-13 · — · Условный runner вместо полной канонизации на
##             run_subprocess · Rejected: runner.run(check=False) как безусловный default (канон
##             subprocess_io graceful-семантика меняет raise-поведение TimeoutExpired/FileNotFoundError)
##             · Reason: W4d — инъекция runner/facts без изменения поведения (дефолты/exit-коды/
##             идемпотентность); канонизация — wiring-решение W5+ (test_core_deliverer KEEP-контракт)
##             · Rev: W5 wiring-rewrite решает перевод core_deliverer на run_subprocess-канон —
##             тогда условная ветка заменяется единым default_command_runner()
## @invariants  - runner=None → subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
##              - runner задан → runner.run(cmd, timeout=timeout) (env/прочие kwargs не передаются)
def _run_cmd(
    cmd: list[str],
    timeout: int,
    runner: CommandRunner | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a delivery command: subprocess.run (default) or injected runner (DI, W4d)."""
    if runner is None:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    return runner.run(cmd, timeout=timeout)


# endregion FUNC__run_cmd


# region FUNC__run_cmd_stdin
## @purpose  REF-0007: исполнение команды с stdin-скриптом (`bash -s` транспорт секретов).
##           Дефолт (runner=None) → subprocess.run(input=...) — capture_output как _run_cmd;
##           runner задан (тесты) → runner.run(cmd, timeout, input=...) (фейки принимают
##           superset kwargs — tests/helpers/fakes.py).
## @io  input: cmd: list[str], stdin_text: str (НЕ логируется), timeout: int,
##      runner: CommandRunner | None → subprocess.CompletedProcess[str]
## @complexity  O(1) — delegation
## @invariants  - stdin_text НИКОГДА не попадает в логи (значения ключей)
def _run_cmd_stdin(
    cmd: list[str],
    stdin_text: str,
    timeout: int,
    runner: CommandRunner | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command feeding stdin_text (secret prelude transport, REF-0007)."""
    if runner is None:
        return subprocess.run(cmd, input=stdin_text, capture_output=True, text=True, timeout=timeout, check=False)
    runner_run = cast("Callable[..., subprocess.CompletedProcess[str]]", runner.run)
    return runner_run(cmd, timeout=timeout, input=stdin_text)


# endregion FUNC__run_cmd_stdin


# region FUNC_redact_secrets
## @purpose  REF-0007 (TEST-07-стиль): redact значений секретов в текстах, попадающих в
##           логи/stderr. Заменяет каждое непустое значение на ***REDACTED***.
## @io       ⇥ text: str, secrets: str (varargs) → ⎋ str (safe для логов)
## @complexity  O(n × m) — str.replace по каждому секрету
## @invariants  Пустые значения игнорируются; порядок varargs не влияет (непересекающиеся ключи)
def redact_secrets(text: str, *secrets: str) -> str:
    """Redact secret values from log-bound text (REF-0007)."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***REDACTED***")
    return text


# endregion FUNC_redact_secrets


# region EXC_CoreDeliveryError
class CoreDeliveryError(Exception):
    """Raised when any Core delivery phase (ssh mkdir / rsync) fails."""


# endregion EXC_CoreDeliveryError


# region FUNC_resolve_remote_base
## @purpose  Единая точка резолюции remote platform base: PLATFORM_REMOTE_BASE → /opt/platform
##           (PLATFORM_ROOT УБРАН из remote-цепочки — TRAP[BUG] 2026-08-03 в deploy_paths.platform_remote_base).
## @io  input: env PLATFORM_REMOTE_BASE, output: str remote platform base
## @complexity  O(1) — env chain lookup
## ⚠️ TRAP[BUG] · 2026-07-31 · P1 · Единый источник remote base (мигрирован из overlay_deliverer.py:197)
## · Symptom: `make node-update NODE=<host>` доставлял core в /opt/platform/core, а bootstrap —
## ·   в ${PLATFORM_REMOTE_BASE:-${PLATFORM_ROOT:-/opt/platform}}/core → на VPS ДВЕ копии core;
## ·   update-фазы выполнялись из чужого дерева (state.json от init не находил скриптов).
## · Fix: ЕДИНАЯ функция резолюции — resolve_remote_base() (та же цепочка, что scp-deliver.sh:129).
## ·   sync_core_to_vps (overlay) и scp_to_server (фасад) делегируют сюда через deliver_core().
## · Prevention: любой код, доставляющий core на VPS, использует resolve_remote_base() из core_deliverer.
## · Note (2026-08-03, DevPlan 123 T8): актуальная цепочка — PLATFORM_REMOTE_BASE → /opt/platform;
## ·   PLATFORM_ROOT исключён из remote-резолюции (см. TRAP[BUG] 2026-08-03 в deploy_paths.py:190-194).
## @invariants  Делегирует в deploy_paths.platform_remote_base() — единый канон remote-базы
def resolve_remote_base() -> str:
    """Resolve remote platform base: PLATFORM_REMOTE_BASE → /opt/platform (C7; PLATFORM_ROOT excluded — TRAP 2026-08-03)."""
    return str(platform_remote_base())


# endregion FUNC_resolve_remote_base


# region FUNC_resolve_node_configs_base
## @purpose  Resolve remote node-configs base: NODE_CONFIGS_REMOTE_BASE → /opt/node-configs.
## @io  input: env NODE_CONFIGS_REMOTE_BASE, output: str remote node-configs base
## @complexity  O(1) — env chain lookup
def resolve_node_configs_base() -> str:
    """Resolve remote node-configs base: NODE_CONFIGS_REMOTE_BASE → /opt/node-configs (C7)."""
    return str(node_configs_remote())


# endregion FUNC_resolve_node_configs_base


# region FUNC_ensure_remote_dirs
## @purpose  Create remote directory hierarchy before rsync (bare VPS safe): {base}/core,
##           {ncb}/{node}, {ncb}/secrets — один ssh mkdir -p (timeout 30).
## @io  input: host, node, user, base, ncb, dry_run, runner; output: bool True on success
## @complexity  O(1) — single ssh call
def ensure_remote_dirs(
    host: str,
    node: str,
    user: str = "root",
    base: str | None = None,
    ncb: str | None = None,
    *,
    dry_run: bool = False,
    runner: CommandRunner | None = None,
) -> bool:
    """Create remote dir hierarchy {base}/core {ncb}/{node} {ncb}/secrets via ssh mkdir -p.

    @raises CoreDeliveryError  On ssh mkdir failure.
    """
    base = base or resolve_remote_base()
    ncb = ncb or resolve_node_configs_base()
    cmd = ["ssh", *SSH_OPTS, f"{user}@{host}", f"mkdir -p {base}/core {base}/scripts {ncb}/{node} {ncb}/secrets"]
    logger.info("[IMP:8][ensure_remote_dirs][exec] Ensuring remote directories exist on %s", host)
    if dry_run:
        logger.info("[IMP:8][ensure_remote_dirs][dry-run] DRY-RUN: %s", " ".join(cmd))
        return True
    r = _run_cmd(cmd, FILE_OP_TIMEOUT, runner)
    if r.returncode != 0:
        logger.info("[IMP:10][ensure_remote_dirs][error] FATAL: ssh mkdir -p failed for %s", host)
        msg = f"ssh mkdir -p failed for {host} (exit={r.returncode}): {r.stderr.strip()}"
        raise CoreDeliveryError(msg)
    logger.info("[IMP:9][ensure_remote_dirs][done] Remote directories confirmed")
    return True


# endregion FUNC_ensure_remote_dirs


# region FUNC_invalidate_pycache
## @purpose  F-07 (DevPlan 015): post-rsync инвалидация __pycache__ на ноде. rsync исключает
##           __pycache__ (RSYNC_EXCLUDES_CORE) и `--delete` НЕ удаляет excluded-файлы на
##           назначении → после инкрементальной доставки (rsync -t сохраняет mtime источников)
##           Python мог НЕ перекомпилировать .py при более новом .pyc (mtime-сравнение) →
##           стейл байткод против нового кода. Remote `find <base>/core -type d -name
##           __pycache__ -exec rm -rf {} +` гарантирует перекомпиляцию при следующем импорте.
## @io  input: host, base (remote), remote_user, dry_run, runner → bool True (best-effort)
## @complexity  O(F) — find-обход дерева core на ноде
## @invariants
##   - Только remote-пути (base = resolve_remote_base = /opt/platform) — НИКАКИХ локальных путей
##   - Best-effort: find-сбой → WARN, доставка НЕ проваливается (rsync уже успешен)
##   - dry_run: печатает команду (IMP:8), 0 subprocess-вызовов
##   - Guard: `|| true` — find на несуществующем core не должен ронять доставку
def invalidate_pycache(
    host: str,
    base: str,
    remote_user: str = "root",
    dry_run: bool = False,
    runner: CommandRunner | None = None,
) -> bool:
    """Remove __pycache__ dirs on the node after rsync (F-07: force bytecode recompilation)."""
    remote_cmd = f"find {base}/core -type d -name __pycache__ -exec rm -rf {{}} + 2>/dev/null || true"
    cmd = ["ssh", *SSH_OPTS, f"{remote_user}@{host}", remote_cmd]
    logger.info("[IMP:9][invalidate_pycache][exec] F-07: invalidating __pycache__ on %s", host)
    if dry_run:
        logger.info("[IMP:8][invalidate_pycache][dry-run] DRY-RUN: %s", " ".join(cmd))
        return True
    r = _run_cmd(cmd, FILE_OP_TIMEOUT, runner)
    if r.returncode != 0:
        logger.warning(
            "[IMP:7][invalidate_pycache][warn] F-07: __pycache__ invalidation failed on %s "
            "(exit=%d): %s — stale bytecode may persist",
            host,
            r.returncode,
            r.stderr.strip()[:200],
        )
        return False
    logger.info("[IMP:9][invalidate_pycache][done] __pycache__ invalidated on %s", host)
    return True


# endregion FUNC_invalidate_pycache


# region FUNC_deliver_core
## @purpose  Phase 1/4: rsync core/ → {base}/core/ с 6 exclude-паттернами (AC7 + 162 W10-2
##           docker-compose.test.yml). Чистый rsync-фаз —
##           БЕЗ ensure_remote_dirs (mkdir живёт в deliver_all; sync-путь overlay не получает mkdir — D3).
##           F-07 (DevPlan 015): ПОСЛЕ успешного rsync — инвалидация __pycache__ на ноде
##           (invalidate_pycache): rsync исключает __pycache__ и --delete не чистит excluded →
##           стейл .pyc против нового кода при инкрементальной доставке.
## @io  input: host, core_dir, remote_user, base, dry_run, runner; output: bool True on success
## @complexity  O(F) where F = number of files transferred
## @rationale  Делегируется из overlay_deliverer.sync_core_to_vps() — DRY-унификация двойного
##             core/ rsync (P2/D3 DevPlan 108). RSYNC_TIMEOUT=600 = deploy-дефолт ssh.sh (D5, R3).
def deliver_core(
    host: str,
    core_dir: str,
    remote_user: str = "root",
    base: str | None = None,
    dry_run: bool = False,
    runner: CommandRunner | None = None,
) -> bool:
    """Rsync core/ → {base}/core/ with 6 exclude patterns. Pure rsync phase.

    @raises CoreDeliveryError  On rsync failure.
    """
    base = base or resolve_remote_base()
    cmd = [
        "rsync",
        "-avz",
        "--delete",
        "-e",
        build_rsync_ssh_opts(),
        *RSYNC_EXCLUDES_CORE,
        f"{core_dir}/",
        f"{remote_user}@{host}:{base}/core/",
    ]
    logger.info("[IMP:9][deliver_core][exec] Phase 1/4: Rsyncing core/ → %s:%s/core/", host, base)
    if dry_run:
        logger.info("[IMP:8][deliver_core][dry-run] DRY-RUN: %s", " ".join(cmd))
        return True
    r = _run_cmd(cmd, RSYNC_TIMEOUT, runner)
    if r.returncode != 0:
        logger.info("[IMP:10][deliver_core][error] FATAL: rsync core/ failed for %s", host)
        msg = f"rsync core/ failed for {host} (exit={r.returncode}): {r.stderr.strip()}"
        raise CoreDeliveryError(msg)
    # F-07 (DevPlan 015): post-rsync __pycache__ invalidation — best-effort (не роняет доставку)
    invalidate_pycache(host, base, remote_user, dry_run=dry_run, runner=runner)
    logger.info("[IMP:9][deliver_core][done] Phase 1/4: core/ rsync complete")
    return True


# endregion FUNC_deliver_core


# region FUNC_deliver_platform_env
## @purpose  Phase 1b/4: rsync platform-env.yaml (root-level, core_dir/..) → {base}/. Skip если файл отсутствует.
## @io  input: host, core_dir, remote_user, base, dry_run, runner; output: bool True (done или skip)
## @complexity  O(1) — single-file rsync
## 🧐 TRAP[DECISION] · 2026-07-16 · — · SCP platform-env.yaml отдельным rsync (мигрирован из scp-deliver.sh:168)
## · Rejected: duplicating platform-env.yaml into core/ (cross-layer violation)
## · Reason: bootstrap only SCPs core/ and node-configs/; root-level platform-env.yaml is separate
## · Rev: if more root-level files need SCP, implement a manifest-based sync approach
def deliver_platform_env(
    host: str,
    core_dir: str,
    remote_user: str = "root",
    base: str | None = None,
    dry_run: bool = False,
    runner: CommandRunner | None = None,
) -> bool:
    """Rsync platform-env.yaml (core_dir/../) → {base}/. Skip if file absent.

    @raises CoreDeliveryError  On rsync failure.
    """
    base = base or resolve_remote_base()
    src = os.path.normpath(pathlib.Path(core_dir) / ".." / "platform-env.yaml")
    if not pathlib.Path(src).is_file():
        logger.info("[IMP:8][deliver_platform_env][skip] Phase 1b/4: SKIP — platform-env.yaml not found at %s", src)
        return True
    cmd = ["rsync", "-avz", "-e", build_rsync_ssh_opts(), src, f"{remote_user}@{host}:{base}/platform-env.yaml"]
    logger.info("[IMP:9][deliver_platform_env][exec] Phase 1b/4: Rsyncing platform-env.yaml → %s:%s/", host, base)
    if dry_run:
        logger.info("[IMP:8][deliver_platform_env][dry-run] DRY-RUN: %s", " ".join(cmd))
        return True
    r = _run_cmd(cmd, RSYNC_TIMEOUT, runner)
    if r.returncode != 0:
        logger.info("[IMP:10][deliver_platform_env][error] FATAL: rsync platform-env.yaml failed for %s", host)
        msg = f"rsync platform-env.yaml failed for {host} (exit={r.returncode}): {r.stderr.strip()}"
        raise CoreDeliveryError(msg)
    logger.info("[IMP:9][deliver_platform_env][done] Phase 1b/4: platform-env.yaml rsync complete")
    return True


# endregion FUNC_deliver_platform_env


# region FUNC_deliver_makefile
## @purpose  Phase 1c/4: rsync Makefile (root-level, core_dir/..) → {base}/. Skip если файл отсутствует.
## @io  input: host, core_dir, remote_user, base, dry_run, runner; output: bool True (done или skip)
## @complexity  O(1) — single-file rsync
## 🧐 TRAP[DECISION] · 2026-07-17 · — · SCP Makefile отдельным rsync (мигрирован из scp-deliver.sh:187)
## · Rejected: manifest-based sync approach (over-engineering for one extra file)
## · Reason: follows same pattern as platform-env.yaml (Phase 1b) — explicit rsync call
## · Rev: if more root-level files need SCP, implement a manifest-based sync approach
def deliver_makefile(
    host: str,
    core_dir: str,
    remote_user: str = "root",
    base: str | None = None,
    dry_run: bool = False,
    runner: CommandRunner | None = None,
) -> bool:
    """Rsync Makefile (core_dir/../) → {base}/. Skip if file absent.

    @raises CoreDeliveryError  On rsync failure.
    """
    base = base or resolve_remote_base()
    src = os.path.normpath(pathlib.Path(core_dir) / ".." / "Makefile")
    if not pathlib.Path(src).is_file():
        logger.info("[IMP:8][deliver_makefile][skip] Phase 1c/4: SKIP — Makefile not found at %s", src)
        return True
    cmd = ["rsync", "-avz", "-e", build_rsync_ssh_opts(), src, f"{remote_user}@{host}:{base}/Makefile"]
    logger.info("[IMP:9][deliver_makefile][exec] Phase 1c/4: Rsyncing Makefile → %s:%s/", host, base)
    if dry_run:
        logger.info("[IMP:8][deliver_makefile][dry-run] DRY-RUN: %s", " ".join(cmd))
        return True
    r = _run_cmd(cmd, RSYNC_TIMEOUT, runner)
    if r.returncode != 0:
        logger.info("[IMP:10][deliver_makefile][error] FATAL: rsync Makefile failed for %s", host)
        msg = f"rsync Makefile failed for {host} (exit={r.returncode}): {r.stderr.strip()}"
        raise CoreDeliveryError(msg)
    logger.info("[IMP:9][deliver_makefile][done] Phase 1c/4: Makefile rsync complete")
    return True


# endregion FUNC_deliver_makefile


# region FUNC_deliver_scripts
## @purpose  Phase 1d/4: rsync scripts/ (root-level, core_dir/../scripts/) → {base}/scripts/.
##           Skip если директория отсутствует. Без --delete (старые скрипты безвредны).
## @io  input: host, core_dir, remote_user, base, dry_run; output: bool True (done или skip)
## @complexity  O(F) where F = number of scripts
## ⚠️ TRAP[BUG] · 2026-08-06 · HI · REQ_FIX (141 r2, ci-ops): scripts/ НЕ доставлялась ни одним каналом
## · Symptom: /opt/platform/scripts/make-log-shell.sh отсутствовал на чистом сервере →
## ·   Makefile:80 `SHELL := $(_platform_root)/scripts/make-log-shell.sh` → make на ноде падал
## ·   Error 127 (provision). CI core-deploy rsync core/ + bootstrap core_deliverer не включали scripts/.
## · Fix: отдельная rsync-фаза scripts/ → {base}/scripts/ (канал Core, push-based, NO git).
## · Rev: если scripts/ перестанет содержать Makefile-хелперы — фазу можно убрать.
def deliver_scripts(
    host: str,
    core_dir: str,
    remote_user: str = "root",
    base: str | None = None,
    dry_run: bool = False,
    runner: CommandRunner | None = None,
) -> bool:
    """Rsync scripts/ (core_dir/../) → {base}/scripts/. Skip if dir absent.

    @raises CoreDeliveryError  On rsync failure.
    """
    base = base or resolve_remote_base()
    src_dir = os.path.normpath(pathlib.Path(core_dir) / ".." / "scripts")
    if not pathlib.Path(src_dir).is_dir():
        logger.info("[IMP:8][deliver_scripts][skip] Phase 1d/4: SKIP — scripts/ not found at %s", src_dir)
        return True
    cmd = [
        "rsync",
        "-avz",
        "-e",
        build_rsync_ssh_opts(),
        f"{src_dir}/",
        f"{remote_user}@{host}:{base}/scripts/",
    ]
    logger.info("[IMP:9][deliver_scripts][exec] Phase 1d/4: Rsyncing scripts/ → %s:%s/scripts/", host, base)
    if dry_run:
        logger.info("[IMP:8][deliver_scripts][dry-run] DRY-RUN: %s", " ".join(cmd))
        return True
    r = _run_cmd(cmd, RSYNC_TIMEOUT, runner)
    if r.returncode != 0:
        logger.info("[IMP:10][deliver_scripts][error] FATAL: rsync scripts/ failed for %s", host)
        msg = f"rsync scripts/ failed for {host} (exit={r.returncode}): {r.stderr.strip()}"
        raise CoreDeliveryError(msg)
    logger.info("[IMP:9][deliver_scripts][done] Phase 1d/4: scripts/ rsync complete")
    return True


# endregion FUNC_deliver_scripts


# region FUNC_deliver_makefiles
## @purpose  Phase 1e/4: rsync makefiles/ (root-level, core_dir/../makefiles/) → {base}/makefiles/.
##           Parity с CI core-deploy.yml шагом «platform-env.yaml + Makefile + makefiles»
##           (TRAP[BUG] 2026-07-23 P0: rsync ./makefiles/ с trailing slash копирует СОДЕРЖИМОЕ
##           в /opt/platform/, а Makefile делает `include makefiles/*.mk` — нужна директория).
##           Без фазы fallback-канал доставлял Makefile с `include makefiles/loadtest.mk`,
##           но не саму директорию → `make provision` на ноде падал (No rule to make target).
## @io  input: host, core_dir, remote_user, base, dry_run; output: bool True (done или skip)
## @complexity  O(F) where F = number of makefiles
## ⚠️ TRAP[BUG] · 2026-08-12 · HI · makefiles/ НЕ доставлялась fallback-каналом core-deliver
## · Symptom: /opt/platform/makefiles отсутствовал на tronyx-vps → `make provision` в
## ·   deliver_fallback падал: "makefiles/loadtest.mk: No such file or directory"
## · Root: core_deliverer.py доставлял root Makefile (deliver_makefile), но не makefiles/ —
## ·   CI-канал (core-deploy.yml) синкает makefiles отдельным шагом, fallback — нет.
## · Fix: отдельная rsync-фаза makefiles/ (БЕЗ trailing slash — копируется сама директория).
## · Rev: если Makefile перестанет include'ить makefiles/*.mk — фазу можно убрать.
def deliver_makefiles(
    host: str,
    core_dir: str,
    remote_user: str = "root",
    base: str | None = None,
    dry_run: bool = False,
    runner: CommandRunner | None = None,
) -> bool:
    """Rsync makefiles/ (core_dir/../) → {base}/makefiles/. Skip if dir absent.

    @raises CoreDeliveryError  On rsync failure.
    """
    base = base or resolve_remote_base()
    src_dir = os.path.normpath(pathlib.Path(core_dir) / ".." / "makefiles")
    if not pathlib.Path(src_dir).is_dir():
        logger.info("[IMP:8][deliver_makefiles][skip] Phase 1e/4: SKIP — makefiles/ not found at %s", src_dir)
        return True
    cmd = [
        "rsync",
        "-avz",
        "-e",
        build_rsync_ssh_opts(),
        # БЕЗ trailing slash — копируется директория makefiles как есть → {base}/makefiles/
        src_dir,
        f"{remote_user}@{host}:{base}/",
    ]
    logger.info("[IMP:9][deliver_makefiles][exec] Phase 1e/4: Rsyncing makefiles/ → %s:%s/makefiles/", host, base)
    if dry_run:
        logger.info("[IMP:8][deliver_makefiles][dry-run] DRY-RUN: %s", " ".join(cmd))
        return True
    r = _run_cmd(cmd, RSYNC_TIMEOUT, runner)
    if r.returncode != 0:
        logger.info("[IMP:10][deliver_makefiles][error] FATAL: rsync makefiles/ failed for %s", host)
        msg = f"rsync makefiles/ failed for {host} (exit={r.returncode}): {r.stderr.strip()}"
        raise CoreDeliveryError(msg)
    logger.info("[IMP:9][deliver_makefiles][done] Phase 1e/4: makefiles/ rsync complete")
    return True


# endregion FUNC_deliver_makefiles


# region FUNC_deliver_node_configs
## @purpose  Phase 2/4: rsync node-configs/{node}/ → {ncb}/{node}/ с 3 exclude-паттернами (AC7).
## @io  input: host, node, node_configs_dir, remote_user, ncb, dry_run, runner; output: bool True on success
## @complexity  O(F) where F = number of files transferred
def deliver_node_configs(
    host: str,
    node: str,
    node_configs_dir: str,
    remote_user: str = "root",
    ncb: str | None = None,
    dry_run: bool = False,
    runner: CommandRunner | None = None,
) -> bool:
    """Rsync node-configs/{node}/ → {ncb}/{node}/ with 3 exclude patterns.

    @raises CoreDeliveryError  On rsync failure.
    """
    ncb = ncb or resolve_node_configs_base()
    src_dir = f"{node_configs_dir}/{node}/"
    cmd = [
        "rsync",
        "-avz",
        "--delete",
        "-e",
        build_rsync_ssh_opts(),
        *RSYNC_EXCLUDES_NODE,
        src_dir,
        f"{remote_user}@{host}:{ncb}/{node}/",
    ]
    logger.info(
        "[IMP:9][deliver_node_configs][exec] Phase 2/4: Rsyncing node-configs/%s/ → %s:%s/%s/", node, host, ncb, node
    )
    if dry_run:
        logger.info("[IMP:8][deliver_node_configs][dry-run] DRY-RUN: %s", " ".join(cmd))
        return True
    r = _run_cmd(cmd, RSYNC_TIMEOUT, runner)
    if r.returncode != 0:
        logger.info("[IMP:10][deliver_node_configs][error] FATAL: rsync node-configs/%s/ failed for %s", node, host)
        msg = f"rsync node-configs/{node}/ failed for {host} (exit={r.returncode}): {r.stderr.strip()}"
        raise CoreDeliveryError(msg)
    logger.info("[IMP:9][deliver_node_configs][done] Phase 2/4: node-configs/%s/ rsync complete", node)
    return True


# endregion FUNC_deliver_node_configs


# region FUNC_deliver_placement
## @purpose  Phase 2b (DevPlan 16 T1.B / P0-2): доставка node-configs/{context}/placement.yaml →
##           {ncb}/{context}/placement.yaml каналом core-push (SCP/rsync). Phase 2 rsync'ит
##           только node-configs/{node}/ — файл placement по согласованному резолверу
##           (deploy_paths.placement_remote_path) никто не создавал: load_placement на ноде
##           всегда давал None → peer-firewall [], healthcheck проверял чужие singleton'ы.
##           Канал core-push, НЕ context-overlay-git: placement живёт в node-configs рядом с
##           node.yaml (единый SoT; git-канал создал бы второй источник истины).
## @io  input: host, node_configs_dir, context, remote_user, ncb, dry_run, runner;
##      output: bool True (delivered или skip)
## @complexity  O(1) — single-file rsync
## @invariants
##   - context пустой → skip IMP:8 (bootstrap без контекста = легаси single-node, no-op)
##   - файл отсутствует локально → skip IMP:8 (single-node канон: отсутствие placement.yaml =
##     легитимное состояние, поведение байт-идентично легаси)
##   - dry_run печатает rsync-команду без мутаций
##   - путь назначения ТОЛЬКО через deploy_paths.placement_remote_path (единый резолвер)
def deliver_placement(
    host: str,
    node_configs_dir: str,
    context: str,
    remote_user: str = "root",
    ncb: str | None = None,
    dry_run: bool = False,
    runner: CommandRunner | None = None,
) -> bool:
    """Rsync node-configs/{context}/placement.yaml → {ncb}/{context}/placement.yaml (T1.B).

    @raises CoreDeliveryError  On rsync failure.
    """
    if not context:
        logger.info("[IMP:8][deliver_placement][skip] no --context — legacy single-node flow, no-op")
        return True
    ncb = ncb or resolve_node_configs_base()
    src = pathlib.Path(node_configs_dir) / context / "placement.yaml"
    if not src.is_file():
        logger.info(
            "[IMP:8][deliver_placement][skip] no placement.yaml at %s — single-node canon (no-op)",
            src,
        )
        return True
    dest_path = placement_remote_path(context)
    cmd = [
        "rsync",
        "-avz",
        "-e",
        build_rsync_ssh_opts(),
        str(src),
        f"{remote_user}@{host}:{dest_path}",
    ]
    logger.info("[IMP:9][deliver_placement][exec] Phase 2b: %s → %s:%s", src, host, dest_path)
    if dry_run:
        logger.info("[IMP:8][deliver_placement][dry-run] DRY-RUN: %s", " ".join(cmd))
        return True
    r = _run_cmd(cmd, RSYNC_TIMEOUT, runner)
    if r.returncode != 0:
        logger.info("[IMP:10][deliver_placement][error] FATAL: rsync placement.yaml failed for %s", host)
        msg = f"rsync placement.yaml failed for {host} (exit={r.returncode}): {r.stderr.strip()}"
        raise CoreDeliveryError(msg)
    logger.info("[IMP:9][deliver_placement][done] Phase 2b: placement.yaml delivered to %s", dest_path)
    return True


# endregion FUNC_deliver_placement


# region FUNC_deliver_secrets
## @purpose  Phase 3/4: rsync node-configs/{node}/secrets/ → {ncb}/secrets/ с 1 exclude (.git).
##           Skip если per-node secrets/ директория отсутствует.
## @io  input: host, node, node_configs_dir, remote_user, ncb, dry_run, runner; output: bool True (done или skip)
## @complexity  O(F) where F = number of secrets files
## ⚠️ TRAP[BUG] · 2026-07-23 · P0 · Phase 3 искал secrets в node-configs/secrets/ (top-level) —
##   структура изменена на per-node: node-configs/{node}/secrets/ (мигрирован из scp-deliver.sh:224).
##   Результат: Phase 3 всегда SKIP, encrypted secrets не доставлялись на VPS,
##   decrypt-secrets падал с «No encrypted secrets file».
## · Fix: источник node-configs/{node}/secrets/, назначение /opt/node-configs/secrets/
##   (куда смотрят decrypt-скрипты).
def deliver_secrets(
    host: str,
    node: str,
    node_configs_dir: str,
    remote_user: str = "root",
    ncb: str | None = None,
    dry_run: bool = False,
    runner: CommandRunner | None = None,
) -> bool:
    """Rsync node-configs/{node}/secrets/ → {ncb}/secrets/ with 1 exclude (.git).

    @raises CoreDeliveryError  On rsync failure.
    """
    ncb = ncb or resolve_node_configs_base()
    src_dir = f"{node_configs_dir}/{node}/secrets"
    if not pathlib.Path(src_dir).is_dir():
        logger.info("[IMP:8][deliver_secrets][skip] Phase 3/4: SKIP — no secrets/ directory at %s", src_dir)
        return True
    cmd = [
        "rsync",
        "-avz",
        "--delete",
        "-e",
        build_rsync_ssh_opts(),
        *RSYNC_EXCLUDES_SECRETS,
        f"{src_dir}/",
        f"{remote_user}@{host}:{ncb}/secrets/",
    ]
    logger.info("[IMP:9][deliver_secrets][exec] Phase 3/4: Rsyncing %s/ → %s:%s/secrets/", src_dir, host, ncb)
    if dry_run:
        logger.info("[IMP:8][deliver_secrets][dry-run] DRY-RUN: %s", " ".join(cmd))
        return True
    r = _run_cmd(cmd, RSYNC_TIMEOUT, runner)
    if r.returncode != 0:
        logger.info("[IMP:10][deliver_secrets][error] FATAL: rsync secrets/ failed for %s", host)
        msg = f"rsync secrets/ failed for {host} (exit={r.returncode}): {r.stderr.strip()}"
        raise CoreDeliveryError(msg)
    logger.info("[IMP:9][deliver_secrets][done] Phase 3/4: secrets/ rsync complete")
    return True


# endregion FUNC_deliver_secrets


# region FUNC_deliver_ci
## @purpose  REF-0112: CI core-deploy step «Rsync core + config to VPS» — МОДУЛЬНЫЙ вызов вместо
##           inline-rsync в workflow. Единый владелец exclude-set'ов — константы этого модуля
##           (RSYNC_EXCLUDES_CORE/NODE): раньше CI дублировал exclude-логику shell'ом с ДРУГИМ
##           набором (.git/__pycache__/*.pyc без .pytest_cache/docker-compose.test.yml) →
##           чередование каналов переписывало прод-дерево (13 docker-compose.test.yml +
##           .pytest_cache попадали в /opt/platform/core основным каналом).
##           Фазы (parity с прежним workflow-шагом): ssh mkdir {base}/core+scripts → guard'ed
##           rsync --delete core/ (6 excludes) → platform-env.yaml + Makefile + makefiles/
##           (combined-guard) → scripts/ → node-configs/ (conditional, БЕЗ --delete — орг-репо).
## @io  input: host, core_dir, remote_user, base, dry_run, runner; output: bool True on success
## @complexity  O(F_total) — суммарно по фазам; guard'ы источников — TRAP[BUG] DevPlan 125 T4 parity
## @invariants
##   - Exclude-set'ы ТОЛЬКО из констант модуля (один owner — REF-0112); workflow НЕ содержит rsync
##   - core/ пуст/отсутствует на раннере → --delete-rsync ПРОПУЩЕН (guard против уноса прод-дерева)
##   - node-configs синк БЕЗ --delete (инвариант core-deliver: орг-репозиторий не перетирается)
##   - provision/node-update НЕ входят (отдельные workflow-шаги) — это файловая фаза канала Core
def deliver_ci(
    host: str,
    core_dir: str,
    remote_user: str = "root",
    base: str | None = None,
    dry_run: bool = False,
    runner: CommandRunner | None = None,
) -> bool:
    """CI core-deploy delivery: mkdir + guarded rsync phases with module-owned exclude-sets."""
    base = base or resolve_remote_base()
    ncb = resolve_node_configs_base()
    repo_root = pathlib.Path(os.path.normpath(pathlib.Path(core_dir) / ".."))
    logger.info(
        "[IMP:9][deliver_ci][start] CI channel file-delivery → %s:%s (REF-0112 single-owner excludes)", host, base
    )

    # ── mkdir (parity CI: до --delete-rsync; без node/secrets-директоров ensure_remote_dirs —
    # это update-путь, полный bootstrap делает deliver_all) ──
    mkdir_cmd = ["ssh", *SSH_OPTS, f"{remote_user}@{host}", f"mkdir -p {base}/core {base}/scripts"]
    if dry_run:
        logger.info("[IMP:8][deliver_ci][dry-run] DRY-RUN: %s", " ".join(mkdir_cmd))
    else:
        r = _run_cmd(mkdir_cmd, FILE_OP_TIMEOUT, runner)
        if r.returncode != 0:
            msg = f"ssh mkdir -p failed for {host} (exit={r.returncode}): {r.stderr.strip()}"
            raise CoreDeliveryError(msg)

    # ── Phase 1: core/ с --delete + guard непустого источника (TRAP[BUG] DevPlan 125 T4 parity) ──
    core_src = pathlib.Path(core_dir)
    if core_src.is_dir() and any(core_src.iterdir()):
        deliver_core(host, core_dir, remote_user, base, dry_run, runner)
    else:
        logger.info(
            "[IMP:8][deliver_ci][skip] core/ отсутствует/пуст на раннере — rsync --delete ПРОПУЩЕН "
            "(предотвращение уноса %s)",
            f"{base}/core",
        )

    # ── Phase 1b-1e: root-level config (combined-guard parity с прежним workflow-шагом) ──
    if (
        (repo_root / "platform-env.yaml").is_file()
        and (repo_root / "Makefile").is_file()
        and (repo_root / "makefiles").is_dir()
    ):
        deliver_platform_env(host, core_dir, remote_user, base, dry_run, runner)
        deliver_makefile(host, core_dir, remote_user, base, dry_run, runner)
        deliver_makefiles(host, core_dir, remote_user, base, dry_run, runner)
    else:
        logger.info("[IMP:8][deliver_ci][skip] platform-env.yaml/Makefile/makefiles отсутствуют на раннере — пропуск")

    # ── Phase 1d: scripts/ (собственный is_dir-guard внутри deliver_scripts) ──
    deliver_scripts(host, core_dir, remote_user, base, dry_run, runner)

    # ── Phase 2 (conditional): node-configs целиком, БЕЗ --delete (орг-репозиторий gitignored —
    # на раннере обычно отсутствует; доставляется bootstrap'ом оператора) ──
    nc_src = repo_root / "node-configs"
    if nc_src.is_dir():
        cmd = [
            "rsync",
            "-avz",
            "-e",
            build_rsync_ssh_opts(),
            *RSYNC_EXCLUDES_NODE,
            f"{nc_src}/",
            f"{remote_user}@{host}:{ncb}/",
        ]
        logger.info("[IMP:9][deliver_ci][node-configs] Rsyncing node-configs/ → %s:%s/", host, ncb)
        if dry_run:
            logger.info("[IMP:8][deliver_ci][dry-run] DRY-RUN: %s", " ".join(cmd))
        else:
            r = _run_cmd(cmd, RSYNC_TIMEOUT, runner)
            if r.returncode != 0:
                msg = f"rsync node-configs/ failed for {host} (exit={r.returncode}): {r.stderr.strip()}"
                raise CoreDeliveryError(msg)
    else:
        logger.info(
            "[IMP:8][deliver_ci][skip] node-configs/ отсутствует (gitignored, орг-репозиторий) — "
            "пропуск (доставлен bootstrap'ом)"
        )

    logger.info("[IMP:9][deliver_ci][done] CI file-delivery complete")
    return True


# endregion FUNC_deliver_ci


# region FUNC_deliver_all
## @purpose  Оркестрация Core-доставки (полный цикл bootstrap): ensure_remote_dirs → 6 rsync-фаз
##           (1/4 core, 1b platform-env, 1c Makefile, 2 node-configs, 2b placement, 3 secrets).
##           DevPlan 16 T1.B: deliver_placement исполняется ДО фаз-потребителей (φ8 deploy-modules/
##           φ12 читают {ncb}/{context}/placement.yaml) — порядок в этой последовательности
##           гарантирует наличие файла к началу деплоя.
##           Fail-fast: первая упавшая фаза → CoreDeliveryError (эквивалент shell || return 1).
## @io  input: host, node, node_configs_dir, core_dir, context="", remote_user, dry_run, runner;
##      output: bool True on success
## @complexity  O(F_total) — суммарно по всем фазам
def deliver_all(
    host: str,
    node: str,
    node_configs_dir: str,
    core_dir: str,
    context: str = "",
    remote_user: str = "root",
    dry_run: bool = False,
    runner: CommandRunner | None = None,
) -> bool:
    """Full Core delivery: mkdir + rsync phases incl. placement (T1.B), fail-fast on error."""
    base = resolve_remote_base()
    ncb = resolve_node_configs_base()
    ensure_remote_dirs(host, node, remote_user, base, ncb, dry_run=dry_run, runner=runner)
    deliver_core(host, core_dir, remote_user, base, dry_run, runner)
    deliver_platform_env(host, core_dir, remote_user, base, dry_run, runner)
    deliver_makefile(host, core_dir, remote_user, base, dry_run, runner)
    deliver_scripts(host, core_dir, remote_user, base, dry_run, runner)
    deliver_makefiles(host, core_dir, remote_user, base, dry_run, runner)
    deliver_root_compose(host, core_dir, remote_user, base, dry_run, runner)
    deliver_node_configs(host, node, node_configs_dir, remote_user, ncb, dry_run, runner)
    deliver_placement(host, node_configs_dir, context, remote_user, ncb, dry_run, runner)
    deliver_secrets(host, node, node_configs_dir, remote_user, ncb, dry_run, runner)
    return True


# endregion FUNC_deliver_all


# region FUNC_deliver_fallback
## @purpose  Fallback-деплой core (142 W5, A6): rsync-фазы (core, platform-env, Makefile,
##           makefiles, scripts, root compose) → ssh provision → ssh node-update.
##           Зеркало core-deploy.yml CI-воркфлоу для GitHub Outage / ручного деплоя.
##           НЕ трогает /opt/node-configs (орг-репозиторий, gitignored — инвариант core-deliver).
## @io  input: host, node, core_dir, remote_user, dry_run;
##           output: bool True on success, False on step failure (ssh-шаги fail-fast)
## @complexity  O(F) — rsync-фазы + 2 ssh-шага
## @invariants
##   - rsync-фазы делегируют в deliver_core/deliver_platform_env/deliver_makefile/
##     deliver_scripts/deliver_root_compose (guard'ы источников — TRAP[BUG] 125 T4)
##   - REF-0007: AGE_SECRET_KEY уходит в remote ТОЛЬКО через ssh-stdin prelude
##     (`bash -s`) — вне argv и вне логов; stderr error-путей redact'ится (redact_secrets)
##   - QA C5 (DevPlan 14 T1.4): ключ детектируется ВНУТРИ Python через detect_age_key()
##     (env AGE_SECRET_KEY → SOPS_AGE_KEY → AGE_SECRET_KEY_FILE → default files) —
##     CLI argv-канал удалён, /proc cmdline локального python не содержит секрета;
##     отсутствие ключа → явный FATAL return False (не тихий skip φ9)
##   - redact-before-truncate: redact_secrets применяется к ПОЛНОМУ stderr ДО окна [-500:]
##     (QA R6/T1.4 — суффикс ключа на границе окна больше не попадает в лог)
##   - dry_run: печатает ssh-команды без мутаций (R5 142 W5); stdin-скрипт — только размер
##   - ssh-команды через SSH_OPTS (shared/ssh_opts.py — единый SoT, DevPlan 116 B5 T2)
def deliver_fallback(
    host: str,
    node: str,
    core_dir: str,
    remote_user: str = "root",
    dry_run: bool = False,
    runner: CommandRunner | None = None,
) -> bool:
    """Fallback core delivery: rsync phases + provision + node-update (core-deploy.yml mirror)."""
    # QA C5 (DevPlan 14 T1.4): детекция ВНУТРИ Python — та же цепочка, что у shell-версии
    # (env→SOPS_AGE_KEY→FILE→default files), но БЕЗ argv-транспорта значения.
    age_secret_key = detect_age_key() or ""
    if not age_secret_key and not dry_run:
        # F-038 план 011: fail-fast ТОЛЬКО для реального прогона; dry-run обязан давать
        # полный WOULD-preview до любой детекции секретов (контракт P1-20, тест
        # test_dry_run_without_age_full_preview)
        logger.error(
            "[IMP:10][deliver_fallback][age] FATAL: AGE master key not found "
            "(node_detect chain: AGE_SECRET_KEY env → SOPS_AGE_KEY env → AGE_SECRET_KEY_FILE → "
            "~/.config/age/keys.txt) — φ9 secrets-update на ноде упадёт; доставка прервана fail-fast"
        )
        return False
    if age_secret_key:
        logger.info("[IMP:8][deliver_fallback][age] AGE key detected in-process (not via argv)")
    else:
        logger.warning(
            "[IMP:7][deliver_fallback][age] AGE key not found — dry-run preview continues "
            "(реальный прогон без ключа прервётся на φ9)"
        )
    base = resolve_remote_base()
    # ── 1. rsync-фазы (guard'ы внутри каждой функции, TRAP[BUG] 125 T4) ──
    deliver_core(host, core_dir, remote_user, base, dry_run, runner)
    deliver_platform_env(host, core_dir, remote_user, base, dry_run, runner)
    deliver_makefile(host, core_dir, remote_user, base, dry_run, runner)
    deliver_scripts(host, core_dir, remote_user, base, dry_run, runner)
    deliver_makefiles(host, core_dir, remote_user, base, dry_run, runner)
    deliver_root_compose(host, core_dir, remote_user, base, dry_run, runner)
    logger.info("[IMP:9][deliver_fallback][rsync] Core + config + makefiles rsync complete")

    # ── 2. Provision networks + volumes (инвариант 1, канон core-deploy.yml step 5) ──
    provision_cmd = ["ssh", *SSH_OPTS, f"{remote_user}@{host}", f"cd {base} && make provision SCOPE=networks,volumes"]
    logger.info("[IMP:9][deliver_fallback][provision] Provisioning networks+volumes on %s", host)
    if dry_run:
        logger.info("[IMP:8][deliver_fallback][dry-run] WOULD run: %s", " ".join(provision_cmd))
    else:
        r = _run_cmd(provision_cmd, SSH_CMD_TIMEOUT, runner)
        if r.returncode != 0:
            logger.error(
                "[IMP:10][deliver_fallback][provision] FATAL: provision failed (exit=%d): %s",
                r.returncode,
                r.stderr.strip()[-500:],
            )
            return False

    # ── 3. Node update (канон core-deploy.yml step 6: DEPLOY_PARALLEL) ──
    # REF-0007 (11-DevPlan Волна 1): AGE_SECRET_KEY ВНЕ argv и ВНЕ логов — remote-скрипт
    # (export + make node-update) уходит в ssh-stdin (`bash -s`); в argv только `bash -s`.
    # 🧐 TRAP[DECISION] · 2026-08-24 · — · stdin→bash -s вместо env-префикса в ssh-команде
    # · Rejected: `AGE_SECRET_KEY='...' make node-update` внутри ssh argv (статус-кво)
    # · Reason: ключ светился в /proc/<pid>/cmdline локального ssh И remote shell весь прогон
    # ·   (~30 мин, любой локальный аккаунт включая ci-deploy) и в dry-run логах
    # · Rev: если появится не-bash remote shell — экранирование через shlex.quote пересмотреть
    update_cmd = ["ssh", *SSH_OPTS, f"{remote_user}@{host}", "bash -s"]
    remote_script = ""
    if age_secret_key:
        remote_script += f"export AGE_SECRET_KEY={shlex.quote(age_secret_key)}\n"
    remote_script += f"cd {base} && exec env DEPLOY_PARALLEL=true make node-update NODE={shlex.quote(node)}\n"
    logger.info(
        "[IMP:9][deliver_fallback][node-update] Running node-update NODE=%s on %s (AGE key via stdin prelude)",
        node,
        host,
    )
    if dry_run:
        logger.info(
            "[IMP:8][deliver_fallback][dry-run] WOULD run: %s <<< stdin(script=%dB [redacted])",
            " ".join(update_cmd),
            len(remote_script),
        )
        return True
    r = _run_cmd_stdin(update_cmd, remote_script, SSH_CMD_TIMEOUT, runner)
    if r.returncode != 0:
        # QA R6/T1.4: redact-before-truncate — redact по ПОЛНОМУ stderr, окно [-500:]
        # берётся УЖЕ от redact'ированного текста (иначе суффикс ключа на границе окна
        # уходил в лог неповреждённым).
        logger.error(
            "[IMP:10][deliver_fallback][node-update] FATAL: node-update failed (exit=%d): %s",
            r.returncode,
            redact_secrets(r.stderr, age_secret_key).strip()[-500:],
        )
        return False
    logger.info("[IMP:9][deliver_fallback][done] core-deliver COMPLETE (NODE=%s)", node)
    return True


# endregion FUNC_deliver_fallback


# region FUNC_deliver_root_compose
## @purpose  Доставка root docker-compose.yml (RC 121, U-49 regression fix): модульный деплой
##           docker_orchestrator использует root compose первым -f (volumes/network SoT в root,
##           DevPlan 116 B3 T4 U-49). Без него изолированный `docker compose -f modules/<m>/base.yml`
##           падает: "refers to undefined volume backup-spool".
## @io  input: host, core_dir, remote_user, base, dry_run, runner; output: bool True (done или skip)
## @complexity  O(1) — single-file rsync
def deliver_root_compose(
    host: str,
    core_dir: str,
    remote_user: str = "root",
    base: str | None = None,
    dry_run: bool = False,
    runner: CommandRunner | None = None,
) -> bool:
    """Rsync docker-compose.yml (core_dir/../) → {base}/. Skip if file absent.

    @raises CoreDeliveryError  On rsync failure.
    """
    base = base or resolve_remote_base()
    src = os.path.normpath(pathlib.Path(core_dir) / ".." / "docker-compose.yml")
    if not pathlib.Path(src).is_file():
        logger.info("[IMP:8][deliver_root_compose][skip] SKIP — docker-compose.yml not found at %s", src)
        return True
    cmd = [
        "rsync",
        "-avz",
        "-e",
        build_rsync_ssh_opts(),
        src,
        f"{remote_user}@{host}:{base}/docker-compose.yml",
    ]
    logger.info("[IMP:9][deliver_root_compose][exec] Rsyncing docker-compose.yml → %s:%s/", host, base)
    if dry_run:
        logger.info("[IMP:8][deliver_root_compose][dry-run] DRY-RUN: %s", " ".join(cmd))
        return True
    r = _run_cmd(cmd, RSYNC_TIMEOUT, runner)
    if r.returncode != 0:
        msg = f"rsync docker-compose.yml failed for {host} (exit={r.returncode}): {r.stderr.strip()}"
        raise CoreDeliveryError(msg)
    logger.info("[IMP:9][deliver_root_compose][done] docker-compose.yml delivered")
    return True


# endregion FUNC_deliver_root_compose


# region FUNC_cli
## @purpose  CLI entrypoint: `deliver` — полная Core-доставка (фасад scp-deliver.sh → python3).
## @io  input: argv (argparse; W4d: None → sys.argv), runner: CommandRunner | None (DI),
##      output: sys.exit(0) на успех | sys.exit(1) на CoreDeliveryError
## @complexity  O(1) — dispatch-only, делегирует в deliver_all()
def cli(argv: list[str] | None = None, runner: CommandRunner | None = None) -> int:
    """CLI entrypoint: deliver — full Core channel delivery. Exit 0 on success, 1 on failure."""
    p = argparse.ArgumentParser(description="core_deliverer — Core channel delivery (SCP/rsync, NO git)")
    sp = p.add_subparsers(dest="command", required=True)
    dp = sp.add_parser(
        "deliver", help="Deliver core/ + platform-env + Makefile + node-configs + placement + secrets to VPS"
    )
    dp.add_argument("--host", required=True)
    dp.add_argument("--node", required=True)
    dp.add_argument("--node-configs-dir", required=True)
    dp.add_argument("--core-dir", required=True)
    dp.add_argument(
        "--context",
        default="",
        help="Context name for placement.yaml delivery (DevPlan 16 T1.B): node-configs/{context}/placement.yaml "
        "→ {ncb}/{context}/placement.yaml; пусто → skip (legacy single-node)",
    )
    dp.add_argument("--remote-user", default="root")
    dp.add_argument("--dry-run", action="store_true")
    fp = sp.add_parser(
        "fallback-deliver",
        help="Fallback core delivery (142 W5): rsync phases + provision + node-update (core-deploy.yml mirror)",
    )
    fp.add_argument("--host", required=True)
    fp.add_argument("--node", required=True)
    fp.add_argument("--core-dir", required=True)
    # QA C5 (DevPlan 14 T1.4): --age-secret-key флаг УДАЛЁН — ключ детектируется
    # внутри deliver_fallback через node_detect (argv-канал секрета закрыт).
    fp.add_argument("--remote-user", default="root")
    fp.add_argument("--dry-run", action="store_true")
    cp = sp.add_parser(
        "ci-deliver",
        help=(
            "CI core-deploy file-delivery (REF-0112): mkdir + guarded rsync phases "
            "(core/, platform-env, Makefile, makefiles/, scripts/, node-configs conditional) — "
            "single-owner exclude-sets; provision/node-update остаются workflow-шагами"
        ),
    )
    cp.add_argument("--host", required=True)
    cp.add_argument("--core-dir", required=True)
    cp.add_argument("--remote-user", default="root")
    cp.add_argument("--dry-run", action="store_true")

    class _CliArgs(argparse.Namespace):
        """Типизированный argparse-Namespace (W11-G3)."""

        def __init__(self) -> None:
            super().__init__()
            self.command: str
            self.host: str
            self.node: str
            self.node_configs_dir: str
            self.core_dir: str
            self.remote_user: str
            self.dry_run: bool

    args = p.parse_args(argv, namespace=_CliArgs())
    try:
        if args.command == "deliver":
            deliver_all(
                args.host,
                args.node,
                args.node_configs_dir,
                args.core_dir,
                context=getattr(args, "context", ""),
                remote_user=args.remote_user,
                dry_run=args.dry_run,
                runner=runner,
            )
        elif args.command == "ci-deliver":
            deliver_ci(args.host, args.core_dir, args.remote_user, None, args.dry_run, runner)
        elif not deliver_fallback(args.host, args.node, args.core_dir, args.remote_user, args.dry_run, runner):
            return 1
    except CoreDeliveryError as exc:
        logger.info("[IMP:10][cli][error] %s", exc)
        return 1
    return 0


# endregion FUNC_cli


if __name__ == "__main__":
    sys.exit(cli())
