#!/usr/bin/env python3
# GREP_SUMMARY: core-deliverer deliver_core deliver_platform_env deliver_makefile deliver_node_configs deliver_secrets ensure_remote_dirs rsync ssh mkdir-p core-channel strangler scp-to-server
# STRUCTURE: ▶ ┌resolve_remote_base/env chain┐ → ⚡ ensure_remote_dirs(ssh mkdir -p) → ⚡ Phase 1/4 deliver_core(rsync core/) → ⚡ 1b deliver_platform_env → ⚡ 1c deliver_makefile → ⚡ Phase 2/4 deliver_node_configs → ◇ secrets dir? → ⚡ Phase 3/4 deliver_secrets → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Core delivery channel (push-based SCP/rsync, NO git) — Python-порт scp-deliver.sh
##           scp_to_server(): ensure_remote_dirs (ssh mkdir -p) + 5 rsync фаз (core/,
##           platform-env.yaml, Makefile, node-configs/{node}/, secrets/). Standalone — НЕ
##           импортирует overlay_deliverer (разрыв цикла импорта overlay→core, DevPlan 108 D2).
## @scope    Вызывается: (1) scp-deliver.sh фасад — CLI `deliver` (bootstrap.sh путь, AC3);
##           (2) overlay_deliverer.sync_core_to_vps() — делегирование deliver_core() (AC4).
##           Канал Core per root AGENTS.md «Три канала доставки кода на VPS».
## @invariants
##   - SSH_OPTS — единый SoT из shared/ssh_opts.py (DevPlan 116 B5 T2, D1) — НЕ mirror lib/ssh.sh
##   - RSYNC_EXCLUDES_CORE 5 паттернов / _NODE 3 / _SECRETS 1 — точное соответствие таблице AC7
##   - Fail-fast: первая упавшая фаза → CoreDeliveryError → CLI exit 1 (эквивалент shell || return 1)
##   - Timeouts: mkdir=30 (parity ssh_exec scp-deliver.sh:142), rsync=600 (deploy-дефолт lib/ssh.sh:119)
##   - DRY_RUN: печать команд в stderr (IMP:8), 0 subprocess-вызовов, успех
##   - subprocess.run с list-args (НЕ string concat) — unit-тестируемость (P3 DevPlan 108)
## @rationale SRP (DevPlan 108 D1): ДВА раздельных канала доставки — Core (push SCP/rsync) vs
##            Context-overlay (git pull-based). core_deliverer.py = канал Core; overlay_deliverer.py
##            = overlay-канал. Standalone-конвенция (D2): SSH_OPTS из shared/ssh_opts.py — единый
##            SoT (5 дублирующих копий SSH_OPTS заменены импортом, DevPlan 116 B5 T2 D1).
## @changes 2026-07-31 | DevPlan 108 — Strangler-Fig Tier 2: scp-deliver.sh 251→≤60 LOC,
##           вся rsync/ssh оркестрация scp_to_server() → настоящий Python-модуль
##           2026-08-01 | DevPlan 116 B5 T2 — SSH_OPTS → импорт из shared/ssh_opts.py (D1);
##                      _ssh_e → build_rsync_ssh_opts() (единственная реализация)
# endregion MODULE_CONTRACT

import argparse
import logging
import os
import subprocess
import sys

# DevPlan 118 C7: remote-пути (/opt/platform, /opt/node-configs) — единые резолверы
# shared/deploy_paths (литералы удалены из канона путей доставки).
from core.internal.shared.deploy_paths import node_configs_remote, platform_remote_base

# DevPlan 116 B5 T2 (D1): SSH_OPTS — единый SoT shared/ssh_opts.py; дублирующие копии устранены.
from core.internal.shared.ssh_opts import SSH_OPTS, build_rsync_ssh_opts

logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stderr)
logger = logging.getLogger(__name__)

# Rsync exclude-паттерны — дословное соответствие scp-deliver.sh (таблица AC7 DevPlan 108).
RSYNC_EXCLUDES_CORE: list[str] = [  # Phase 1 core/ — 5 паттернов
    "--exclude=.git",
    "--exclude=__pycache__",
    "--exclude=.pytest_cache",
    "--exclude=default-user.xml",
    "--exclude=.env",
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
MKDIR_TIMEOUT = 30
RSYNC_TIMEOUT = 600


# region EXC_CoreDeliveryError
class CoreDeliveryError(Exception):
    """Raised when any Core delivery phase (ssh mkdir / rsync) fails."""


# endregion EXC_CoreDeliveryError


# region FUNC_resolve_remote_base
## @purpose  Единая точка резолюции remote platform base: PLATFORM_REMOTE_BASE → PLATFORM_ROOT → /opt/platform.
## @io  input: env PLATFORM_REMOTE_BASE / PLATFORM_ROOT, output: str remote platform base
## @complexity  O(1) — env chain lookup
## ⚠️ TRAP[BUG] · 2026-07-31 · P1 · Единый источник remote base (мигрирован из overlay_deliverer.py:197)
## · Symptom: `make node-update NODE=<host>` доставлял core в /opt/platform/core, а bootstrap —
## ·   в ${PLATFORM_REMOTE_BASE:-${PLATFORM_ROOT:-/opt/platform}}/core → на VPS ДВЕ копии core;
## ·   update-фазы выполнялись из чужого дерева (state.json от init не находил скриптов).
## · Fix: ЕДИНАЯ функция резолюции — resolve_remote_base() (та же цепочка, что scp-deliver.sh:129).
## ·   sync_core_to_vps (overlay) и scp_to_server (фасад) делегируют сюда через deliver_core().
## · Prevention: любой код, доставляющий core на VPS, использует resolve_remote_base() из core_deliverer.
## @invariants  Та же цепочка, что scp-deliver.sh:129 и build_ssh_cmd (remote-cmd.sh:40-43)
def resolve_remote_base() -> str:
    """Resolve remote platform base: PLATFORM_REMOTE_BASE → PLATFORM_ROOT → /opt/platform (C7)."""
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
## @io  input: host, node, user, base, ncb, dry_run; output: bool True on success
## @complexity  O(1) — single ssh call
## ⚠️ TRAP[BUG] · 2026-07-16 · FIXED (D2) · Bare server: mkdir -p отсутствовал (мигрирован из scp-deliver.sh:133)
## · Symptom: `rsync: mkdir /opt/platform/core/ failed: No such file or directory` на bare VPS
## · Root: rsync не создаёт родительские директории на удалённом сервере без --rsync-path="mkdir -p ..."
## ·   Сисадмин вручную создавал /opt/platform/ — правка не попала в diff.
## · Fix: явный ssh mkdir -p для всех целевых директорий перед rsync — bare metal safe.
## · Rev: если появятся новые target-директории — добавить их сюда.
def ensure_remote_dirs(
    host: str,
    node: str,
    user: str = "root",
    base: str | None = None,
    ncb: str | None = None,
    dry_run: bool = False,
) -> bool:
    """Create remote dir hierarchy {base}/core {ncb}/{node} {ncb}/secrets via ssh mkdir -p.

    @raises CoreDeliveryError  On ssh mkdir failure.
    """
    base = base or resolve_remote_base()
    ncb = ncb or resolve_node_configs_base()
    cmd = ["ssh", *SSH_OPTS, f"{user}@{host}", f"mkdir -p {base}/core {ncb}/{node} {ncb}/secrets"]
    logger.info("[IMP:8][ensure_remote_dirs][exec] Ensuring remote directories exist on %s", host)
    if dry_run:
        logger.info("[IMP:8][ensure_remote_dirs][dry-run] DRY-RUN: %s", " ".join(cmd))
        return True
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=MKDIR_TIMEOUT)
    if r.returncode != 0:
        logger.info("[IMP:10][ensure_remote_dirs][error] FATAL: ssh mkdir -p failed for %s", host)
        raise CoreDeliveryError(f"ssh mkdir -p failed for {host} (exit={r.returncode}): {r.stderr.strip()}")
    logger.info("[IMP:9][ensure_remote_dirs][done] Remote directories confirmed")
    return True


# endregion FUNC_ensure_remote_dirs


# region FUNC_deliver_core
## @purpose  Phase 1/4: rsync core/ → {base}/core/ с 5 exclude-паттернами (AC7). Чистый rsync-фаз —
##           БЕЗ ensure_remote_dirs (mkdir живёт в deliver_all; sync-путь overlay не получает mkdir — D3).
## @io  input: host, core_dir, remote_user, base, dry_run; output: bool True on success
## @complexity  O(F) where F = number of files transferred
## @rationale  Делегируется из overlay_deliverer.sync_core_to_vps() — DRY-унификация двойного
##             core/ rsync (P2/D3 DevPlan 108). RSYNC_TIMEOUT=600 = deploy-дефолт ssh.sh (D5, R3).
def deliver_core(
    host: str,
    core_dir: str,
    remote_user: str = "root",
    base: str | None = None,
    dry_run: bool = False,
) -> bool:
    """Rsync core/ → {base}/core/ with 5 exclude patterns. Pure rsync phase.

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
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=RSYNC_TIMEOUT)
    if r.returncode != 0:
        logger.info("[IMP:10][deliver_core][error] FATAL: rsync core/ failed for %s", host)
        raise CoreDeliveryError(f"rsync core/ failed for {host} (exit={r.returncode}): {r.stderr.strip()}")
    logger.info("[IMP:9][deliver_core][done] Phase 1/4: core/ rsync complete")
    return True


# endregion FUNC_deliver_core


# region FUNC_deliver_platform_env
## @purpose  Phase 1b/4: rsync platform-env.yaml (root-level, core_dir/..) → {base}/. Skip если файл отсутствует.
## @io  input: host, core_dir, remote_user, base, dry_run; output: bool True (done или skip)
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
) -> bool:
    """Rsync platform-env.yaml (core_dir/../) → {base}/. Skip if file absent.

    @raises CoreDeliveryError  On rsync failure.
    """
    base = base or resolve_remote_base()
    src = os.path.normpath(os.path.join(core_dir, "..", "platform-env.yaml"))
    if not os.path.isfile(src):
        logger.info("[IMP:8][deliver_platform_env][skip] Phase 1b/4: SKIP — platform-env.yaml not found at %s", src)
        return True
    cmd = ["rsync", "-avz", "-e", build_rsync_ssh_opts(), src, f"{remote_user}@{host}:{base}/platform-env.yaml"]
    logger.info("[IMP:9][deliver_platform_env][exec] Phase 1b/4: Rsyncing platform-env.yaml → %s:%s/", host, base)
    if dry_run:
        logger.info("[IMP:8][deliver_platform_env][dry-run] DRY-RUN: %s", " ".join(cmd))
        return True
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=RSYNC_TIMEOUT)
    if r.returncode != 0:
        logger.info("[IMP:10][deliver_platform_env][error] FATAL: rsync platform-env.yaml failed for %s", host)
        raise CoreDeliveryError(f"rsync platform-env.yaml failed for {host} (exit={r.returncode}): {r.stderr.strip()}")
    logger.info("[IMP:9][deliver_platform_env][done] Phase 1b/4: platform-env.yaml rsync complete")
    return True


# endregion FUNC_deliver_platform_env


# region FUNC_deliver_makefile
## @purpose  Phase 1c/4: rsync Makefile (root-level, core_dir/..) → {base}/. Skip если файл отсутствует.
## @io  input: host, core_dir, remote_user, base, dry_run; output: bool True (done или skip)
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
) -> bool:
    """Rsync Makefile (core_dir/../) → {base}/. Skip if file absent.

    @raises CoreDeliveryError  On rsync failure.
    """
    base = base or resolve_remote_base()
    src = os.path.normpath(os.path.join(core_dir, "..", "Makefile"))
    if not os.path.isfile(src):
        logger.info("[IMP:8][deliver_makefile][skip] Phase 1c/4: SKIP — Makefile not found at %s", src)
        return True
    cmd = ["rsync", "-avz", "-e", build_rsync_ssh_opts(), src, f"{remote_user}@{host}:{base}/Makefile"]
    logger.info("[IMP:9][deliver_makefile][exec] Phase 1c/4: Rsyncing Makefile → %s:%s/", host, base)
    if dry_run:
        logger.info("[IMP:8][deliver_makefile][dry-run] DRY-RUN: %s", " ".join(cmd))
        return True
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=RSYNC_TIMEOUT)
    if r.returncode != 0:
        logger.info("[IMP:10][deliver_makefile][error] FATAL: rsync Makefile failed for %s", host)
        raise CoreDeliveryError(f"rsync Makefile failed for {host} (exit={r.returncode}): {r.stderr.strip()}")
    logger.info("[IMP:9][deliver_makefile][done] Phase 1c/4: Makefile rsync complete")
    return True


# endregion FUNC_deliver_makefile


# region FUNC_deliver_node_configs
## @purpose  Phase 2/4: rsync node-configs/{node}/ → {ncb}/{node}/ с 3 exclude-паттернами (AC7).
## @io  input: host, node, node_configs_dir, remote_user, ncb, dry_run; output: bool True on success
## @complexity  O(F) where F = number of files transferred
def deliver_node_configs(
    host: str,
    node: str,
    node_configs_dir: str,
    remote_user: str = "root",
    ncb: str | None = None,
    dry_run: bool = False,
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
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=RSYNC_TIMEOUT)
    if r.returncode != 0:
        logger.info("[IMP:10][deliver_node_configs][error] FATAL: rsync node-configs/%s/ failed for %s", node, host)
        raise CoreDeliveryError(
            f"rsync node-configs/{node}/ failed for {host} (exit={r.returncode}): {r.stderr.strip()}"
        )
    logger.info("[IMP:9][deliver_node_configs][done] Phase 2/4: node-configs/%s/ rsync complete", node)
    return True


# endregion FUNC_deliver_node_configs


# region FUNC_deliver_secrets
## @purpose  Phase 3/4: rsync node-configs/{node}/secrets/ → {ncb}/secrets/ с 1 exclude (.git).
##           Skip если per-node secrets/ директория отсутствует.
## @io  input: host, node, node_configs_dir, remote_user, ncb, dry_run; output: bool True (done или skip)
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
) -> bool:
    """Rsync node-configs/{node}/secrets/ → {ncb}/secrets/ with 1 exclude (.git).

    @raises CoreDeliveryError  On rsync failure.
    """
    ncb = ncb or resolve_node_configs_base()
    src_dir = f"{node_configs_dir}/{node}/secrets"
    if not os.path.isdir(src_dir):
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
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=RSYNC_TIMEOUT)
    if r.returncode != 0:
        logger.info("[IMP:10][deliver_secrets][error] FATAL: rsync secrets/ failed for %s", host)
        raise CoreDeliveryError(f"rsync secrets/ failed for {host} (exit={r.returncode}): {r.stderr.strip()}")
    logger.info("[IMP:9][deliver_secrets][done] Phase 3/4: secrets/ rsync complete")
    return True


# endregion FUNC_deliver_secrets


# region FUNC_deliver_all
## @purpose  Оркестрация Core-доставки (полный цикл bootstrap): ensure_remote_dirs → 5 rsync-фаз
##           (1/4 core, 1b platform-env, 1c Makefile, 2 node-configs, 3 secrets).
##           Fail-fast: первая упавшая фаза → CoreDeliveryError (эквивалент shell || return 1).
## @io  input: host, node, node_configs_dir, core_dir, remote_user, dry_run; output: bool True on success
## @complexity  O(F_total) — суммарно по всем фазам
def deliver_all(
    host: str,
    node: str,
    node_configs_dir: str,
    core_dir: str,
    remote_user: str = "root",
    dry_run: bool = False,
) -> bool:
    """Full Core delivery: mkdir + 5 rsync phases, fail-fast on first CoreDeliveryError."""
    base = resolve_remote_base()
    ncb = resolve_node_configs_base()
    ensure_remote_dirs(host, node, remote_user, base, ncb, dry_run)
    deliver_core(host, core_dir, remote_user, base, dry_run)
    deliver_platform_env(host, core_dir, remote_user, base, dry_run)
    deliver_makefile(host, core_dir, remote_user, base, dry_run)
    deliver_node_configs(host, node, node_configs_dir, remote_user, ncb, dry_run)
    deliver_secrets(host, node, node_configs_dir, remote_user, ncb, dry_run)
    return True


# endregion FUNC_deliver_all


# region FUNC_cli
## @purpose  CLI entrypoint: `deliver` — полная Core-доставка (фасад scp-deliver.sh → python3).
## @io  input: sys.argv (argparse), output: sys.exit(0) на успех | sys.exit(1) на CoreDeliveryError
## @complexity  O(1) — dispatch-only, делегирует в deliver_all()
def cli() -> int:
    """CLI entrypoint: deliver — full Core channel delivery. Exit 0 on success, 1 on failure."""
    p = argparse.ArgumentParser(description="core_deliverer — Core channel delivery (SCP/rsync, NO git)")
    sp = p.add_subparsers(dest="command", required=True)
    dp = sp.add_parser("deliver", help="Deliver core/ + platform-env + Makefile + node-configs + secrets to VPS")
    dp.add_argument("--host", required=True)
    dp.add_argument("--node", required=True)
    dp.add_argument("--node-configs-dir", required=True)
    dp.add_argument("--core-dir", required=True)
    dp.add_argument("--remote-user", default="root")
    dp.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    try:
        deliver_all(args.host, args.node, args.node_configs_dir, args.core_dir, args.remote_user, args.dry_run)
    except CoreDeliveryError as exc:
        logger.info("[IMP:10][cli][error] %s", exc)
        return 1
    return 0


# endregion FUNC_cli


if __name__ == "__main__":
    sys.exit(cli())
