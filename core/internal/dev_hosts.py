#!/usr/bin/env python3
# GREP_SUMMARY: dev-hosts, etc-hosts, hosts-manager, marker-block, dry-run, apply, sudo, atomic, idempotent, node-yaml, dev-certs, macos, 127.0.0.1
# STRUCTURE: ▶ collect_hosts ┌node.yaml server_names (dev-mode FQDN) + dev-certs SAN base┐ → ◇ block_diff (BEGIN/END marker merge, foreign lines preserved) → ◇ --print | --dry-run (exit 1 on diff) | --apply (atomic tmp+mv, sudo для /etc/hosts) → ⎋ exit 0/1/2/3
# region MODULE_CONTRACT
## @purpose  Idempotent /etc/hosts manager for local dev (macOS) — mirrors the nginx vhost
##           server_names emitted by vhost_renderer in dev mode (<project>.<suffix>) plus the
##           base domains derived from dev-cert SAN wildcards, into a managed marker block.
##           `make dev-hosts` (makefiles/dev.mk, DevPlan 136 W4) keeps the local machine's
##           /etc/hosts in sync with the dev FQDN scheme without manual editing.
## @scope    core/internal/dev_hosts.py — invoked via `make dev-hosts`; collect_hosts/block_diff/
##           apply are pure functions (unit-testable without root); CLI is a thin boundary.
## @invariants
##   I1: Marker-block ownership — only lines between BEGIN/END markers are managed;
##       foreign /etc/hosts lines are preserved verbatim (never rewritten).
##   I2: Idempotency — applying an already-synced state is a byte-level no-op.
##   I3: Atomicity — apply writes to a temp file then os.replace (same-fs) / sudo mv,
##       never partial /etc/hosts.
##   I4: Sudo boundary — /etc/hosts (or any unwritable parent) → sudo mv; writable parent
##       → direct os.replace. Business functions never run sudo themselves — only _sudo_move.
##   I5: Exit contract (core/AGENTS.md) — 0 ok · 1 diff (dry-run) / generic error ·
##       2 ConfigNotFoundError (/etc/hosts missing) · 3 ConfigParseError (malformed block).
##   I6: Empty host set → managed block removed if present (stale cleanup); no empty block written.
##   I7: Dev FQDN = <project.name>.<suffix> (vhost_renderer dev-mode parity); suffix defaults
##       to PLATFORM_DOMAIN, base domains come from dev-cert SAN wildcards (*.X → X).
## @rationale  Local dev needs dev-domains (<project>.ai-platform.local) resolvable to 127.0.0.1 —
##             the same FQDN set the nginx vhost_renderer emits in dev mode. Single source:
##             node.yaml projects (via vhost_renderer.read_node_yaml_projects) + dev-cert SAN
##             wildcard base domains (via dev_cert_generator.get_cert_sans). Both readers are
##             REUSED (DRY) instead of re-parsing YAML/certs — verified via cross-layer probe
##             (dotted public import internal→modules passes tests/test_cross_layer_imports.py).
## @rationale (D1) Marker block format: single `127.0.0.1 <hosts...>` line between BEGIN/END
##             comments — standard hosts(5) style, trivially diff-able, self-identifying for
##             operators. Rejected alternatives: per-host lines (noisy) and full-file rewrite
##             without markers (destructive to foreign entries).
## @rationale (D2) sudo mv (not `sudo tee`): rename is atomic on the same filesystem (macOS
##             /tmp + /etc share APFS volume); rejected in-place write — partial /etc/hosts
##             on failure would break DNS resolution system-wide.
## @changes  2026-08-05 · DevPlan 136 W4 (T4.1) — Created
## @modulemap
##   ┌collect_hosts()┐        → set[str]  — server_names (dev-mode FQDN) + dev-cert base domains
##   ┌block_diff()┐           → (new_content, changed) — merge marker block into hosts content
##   ┌apply()┐                → bool — atomic write (tmp+mv / sudo mv), no-op when unchanged
##   ┌_sans_to_hosts()┐       → set[str]  — DNS SAN entries → hostnames (wildcard → base domain)
##   ┌_atomic_write()┐        → None — tmp + os.replace | sudo mv (per I4)
##   ┌_sudo_move()┐           → None — the ONLY sudo invocation in the module (I4)
##   ┌_render_diff()┐         → str — unified diff for dry-run output
##   ┌main()┐                 → int — --print | --dry-run (default) | --apply; exit 0/1/2/3
# endregion MODULE_CONTRACT

import argparse
import difflib
import logging
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

# Canonical sys.path bootstrap (pattern: config_renderer.py:44-45, class-A defect W2):
# repo root needed for `core.internal.*` / `core.modules.*` imports under direct-script
# invocation (`python3 core/internal/dev_hosts.py`, make dev-hosts). File is at
# core/internal/dev_hosts.py → root = 3 levels up. `python3 -m` already has root on path.
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from core.internal.scaffold.vhost_renderer import read_node_yaml_projects
from core.internal.shared.exceptions import ConfigNotFoundError, ConfigParseError, PlatformError
from core.modules.nginx.dev_cert_generator import DEFAULT_DEV_CERTS_DIR, get_cert_sans

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────

# Marker block — self-identifying managed region in /etc/hosts (canonical format, DevPlan 136 W4).
# Only lines between BEGIN_MARKER and END_MARKER are owned by dev-hosts; everything else is
# preserved verbatim (invariant I1).
BEGIN_MARKER = "# BEGIN ai-platform dev-hosts"
END_MARKER = "# END ai-platform dev-hosts"

# Managed entry maps every collected hostname to the loopback address (local dev scheme).
HOSTS_ENTRY_IP = "127.0.0.1"

DEFAULT_PLATFORM_DOMAIN = "ai-platform.local"  # matches dev_cert_generator / .env.example
DEFAULT_NODE_NAME = "test-node"  # local dev node (matches .env.example, node-configs/test-node)
DEFAULT_ETC_HOSTS = "/etc/hosts"


# ─────────────────────────────────────────────────────────────────────
# COLLECT_HOSTS
# ─────────────────────────────────────────────────────────────────────

# region FUNC_collect_hosts


def collect_hosts(
    node_configs_dir: str,
    node_name: str,
    dev_suffix: str | None = None,
    dev_certs_dir: str | None = None,
    *,
    get_cert_sans_fn: Callable[[Path], list[str]] | None = None,
) -> set[str]:
    """Collect the full dev-host set: server_names + dev-cert SAN base domains.

    ▶ ┌node_configs_dir + node_name + dev_suffix┐
    → ❶ read_node_yaml_projects → server_names (dev FQDN = <name>.<suffix>)
    → ❷ _sans_to_hosts(dev cert fullchain.pem) → base domains from wildcard SANs
    → ⊕ union → ⎋ set[str]

    ## @purpose — Single source of the dev FQDN scheme (DevPlan 136 §2.2):
    ##            (a) vhost_renderer server_names in dev mode — every node.yaml project
    ##            becomes <name>.<dev_suffix> (parity with render_vhost dev-mode rewrite);
    ##            (b) dev-cert SANs — wildcard `*.X` contributes base domain X, concrete
    ##            DNS names contribute themselves (IP/localhost skipped). Missing node.yaml
    ##            or cert degrade gracefully to an empty contribution, never an error.
    ## @param get_cert_sans_fn  DI (DevPlan 167 D3): fake SAN-ридер для тестов; None → канон.
    ## 🧐 TRAP[DI-SEAM] · 2026-08-14 · — · get_cert_sans_fn на collect_hosts (SAN-reader seam)
    ## · Rejected: прямой вызов dev_cert_generator.get_cert_sans
    ## · Reason: seam = тестируемость реального SAN-контракта (wildcard → base, IP skip) без
    ## ·   глобального патча module-атрибута; поведение по умолчанию (None → канон) неизменно
    ## · Rev: появление второго потребителя get_cert_sans → общий SAN-reader-объект
    ## @io — ⇥ node_configs_dir: str — node-configs/ directory
    ##       ⇥ node_name: str — node directory name (contains node.yaml)
    ##       ⇥ dev_suffix: str | None — dev FQDN suffix (default PLATFORM_DOMAIN via main)
    ##       ⇥ dev_certs_dir: str | None — dev cert dir (default dev_cert_generator constant)
    ##       → ⎋ set[str] — sorted-able hostnames for the 127.0.0.1 managed line
    ## @complexity — O(P + S) where P = projects, S = SAN entries
    ## @invariants
    ##   - Missing node.yaml → empty server_names contribution (warning only)
    ##   - Missing dev cert → empty SAN contribution (warning only)
    ##   - dev_suffix falsy → raw entry.domain used (vhost_renderer parity)
    ##   - localhost and IP SANs are NEVER emitted (already resolvable / not hostnames)
    """
    hosts: set[str] = set()

    # ── Step 1: server_names via vhost_renderer (DRY — canonical node.yaml reader) ──
    node_yaml_path = Path(node_configs_dir) / node_name / "node.yaml"
    if not node_yaml_path.is_file():
        logger.warning("[IMP:7][collect_hosts] node.yaml not found: %s — server_names empty", node_yaml_path)
    else:
        entries = read_node_yaml_projects(str(node_yaml_path))
        for entry in entries:
            if dev_suffix:
                fqdn = f"{entry.name}.{dev_suffix}".lower()
                logger.debug("[IMP:6][collect_hosts] dev FQDN (suffix): %s → %s", entry.domain, fqdn)
            else:
                fqdn = entry.domain.lower()
            hosts.add(fqdn)
        logger.info("[IMP:8][collect_hosts] server_names: %d project(s) → %d FQDN(s)", len(entries), len(hosts))

    # ── Step 2: dev-cert SAN base domains (DRY — canonical cert SAN reader) ──
    cert_file = Path(dev_certs_dir or DEFAULT_DEV_CERTS_DIR) / "fullchain.pem"
    if not cert_file.is_file():
        logger.warning("[IMP:7][collect_hosts] dev cert not found: %s — SAN base empty", cert_file)
    else:
        sans_hosts = _sans_to_hosts(cert_file, get_cert_sans_fn=get_cert_sans_fn)
        hosts |= sans_hosts
        logger.info("[IMP:8][collect_hosts] dev-cert SAN base: %d hostname(s)", len(sans_hosts))

    logger.info("[IMP:9][collect_hosts] Collected %d hostname(s) total", len(hosts))
    return hosts


# endregion FUNC_collect_hosts


# ─────────────────────────────────────────────────────────────────────
# SAN → HOSTS
# ─────────────────────────────────────────────────────────────────────

# region FUNC__sans_to_hosts


def _sans_to_hosts(cert_file: Path, *, get_cert_sans_fn: Callable[[Path], list[str]] | None = None) -> set[str]:
    """Derive hostnames from a dev certificate's SAN entries.

    ▶ ┌cert_file┐ → ◇ get_cert_sans → ○ strip DNS:/IP: prefix → ◇ IP|localhost? skip →
    → ◇ *.X wildcard → base X · else literal → ⊕ set → ⎋ set[str]

    ## @purpose — Translate cert SAN entries into /etc/hosts-valid names:
    ##            wildcard `*.ai-platform.local` cannot be a hosts entry, so its BASE
    ##            domain `ai-platform.local` is added (I7). IP SANs and localhost are
    ##            skipped (already resolvable / not useful in the managed block).
    ## @param get_cert_sans_fn  DI (DevPlan 167 D3): fake SAN-ридер; None → канон.
    ## @io — ⇥ cert_file: Path — PEM certificate (e.g. <dev-certs>/fullchain.pem)
    ##       → ⎋ set[str] — hostnames derived from DNS SAN entries
    ## @complexity — O(S) where S = SAN entries (get_cert_sans runs openssl once)
    ## @invariants
    ##   - DNS SAN without wildcard → added verbatim (lowercased)
    ##   - IP SAN → skipped; localhost → skipped
    ##   - Empty/unparseable cert → empty set (get_cert_sans already warns)
    """
    sans_fn = get_cert_sans if get_cert_sans_fn is None else get_cert_sans_fn
    hosts: set[str] = set()
    for entry in sans_fn(cert_file):  # get_cert_sans требует Path (dev_cert_generator контракт)
        if entry.startswith("IP:"):
            continue  # IP SAN — not a hostname for hosts(5)
        name = entry[len("DNS:") :] if entry.startswith("DNS:") else entry
        name = name.strip().lower()
        if not name or name == "localhost":
            continue
        if name.startswith("*."):
            hosts.add(name[2:])  # *.X → base domain X
        else:
            hosts.add(name)
    return hosts


# endregion FUNC__sans_to_hosts


# ─────────────────────────────────────────────────────────────────────
# BLOCK DIFF / MERGE
# ─────────────────────────────────────────────────────────────────────

# region FUNC__render_block


def _render_block(hosts: set[str]) -> list[str]:
    """Render the managed marker block lines for a host set.

    ▶ ┌hosts┐ → ◇ empty? → [] · else ⊕ [BEGIN, 127.0.0.1 <sorted>, END] → ⎋ list[str]

    ## @purpose — Deterministic block rendering: one 127.0.0.1 line with sorted hostnames
    ##            (hosts(5) multi-host syntax), framed by BEGIN/END markers. Empty set →
    ##            no block (stale-block removal path, I6).
    ## @io — ⇥ hosts: set[str] → ⎋ list[str] — block lines (may be empty)
    ## @complexity — O(H log H) — sorted() over H hostnames
    """
    if not hosts:
        return []
    joined = " ".join(sorted(hosts))
    return [BEGIN_MARKER, f"{HOSTS_ENTRY_IP} {joined}", END_MARKER]


# endregion FUNC__render_block


# region FUNC_block_diff


def block_diff(etc_hosts: str, hosts: set[str]) -> tuple[str, bool]:
    """Merge the managed marker block into /etc/hosts content; report change.

    ▶ ┌etc_hosts + hosts┐ → ◇ locate BEGIN/END markers → ◇ both present? replace span ·
    → ◇ neither? append · ◇ one only? ConfigParseError → ⊕ normalize trailing NL →
    → ◇ new != normalized-original ? changed → ⎋ (new_content, changed)

    ## @purpose — Pure merge of the managed block (I1): foreign lines before/after the
    ##            marker span are preserved verbatim; a missing block is appended at the
    ##            end (blank-line separated); a stale block (hosts now empty) is removed.
    ##            Idempotency (I2): merging an already-synced content is a no-op.
    ## @io — ⇥ etc_hosts: str — current /etc/hosts content ("" if absent)
    ##       ⇥ hosts: set[str] — target host set for the managed line
    ##       → ⎋ tuple[str, bool] — (merged content, changed vs original)
    ## @complexity — O(L + H log H) where L = file lines, H = hostnames
    ## @throws — ConfigParseError (exit 3) if exactly one of BEGIN/END is present —
    ##           a half-written block means manual intervention, not auto-repair.
    ## @invariants
    ##   - Line matching is whitespace-trimmed (robust to trailing spaces)
    ##   - Output always ends with exactly one "\n" (normalized — I2 depends on this)
    ##   - Empty hosts + no block → (etc_hosts unchanged, changed=False)
    """
    lines = etc_hosts.splitlines()
    begin_idx: int | None = None
    end_idx: int | None = None
    for i, line in enumerate(lines):
        if line.strip() == BEGIN_MARKER:
            begin_idx = i
        elif line.strip() == END_MARKER:
            end_idx = i

    block = _render_block(hosts)

    if begin_idx is not None and end_idx is not None:
        if end_idx < begin_idx:
            msg = "dev-hosts marker block malformed: END before BEGIN in hosts file"
            raise ConfigParseError(msg)
        # Replace the managed span in place — foreign prefix/suffix preserved (I1)
        new_lines = lines[:begin_idx] + block + lines[end_idx + 1 :]
    elif begin_idx is None and end_idx is None:
        if not block:
            return etc_hosts, False  # nothing to manage and nothing present — no-op
        # Append block at the end, blank-line separated from the tail (hosts(5) style)
        new_lines = lines + ([""] if lines else []) + block
    else:
        # Exactly one marker — half-written block (I1 violation) — fail loud, never guess
        msg = (
            "dev-hosts marker block malformed: only one of BEGIN/END markers present — "
            "fix /etc/hosts manually (remove the orphan marker)"
        )
        raise ConfigParseError(msg)

    new_content = "\n".join(new_lines)
    if new_content:
        new_content += "\n"
    norm_orig = etc_hosts if etc_hosts.endswith("\n") else (etc_hosts + "\n" if etc_hosts else "")
    return new_content, new_content != norm_orig


# endregion FUNC_block_diff


# ─────────────────────────────────────────────────────────────────────
# APPLY (atomic write)
# ─────────────────────────────────────────────────────────────────────

# region FUNC_apply


def apply(path: str, hosts: set[str]) -> bool:
    """Apply the managed block to a hosts file atomically; no-op when in sync.

    ▶ ┌path + hosts┐ → ◇ _read_file (missing → ConfigNotFoundError) → ◇ block_diff →
    → ◇ changed? _atomic_write → ⎋ True (applied) · False (no-op)

    ## @purpose — Idempotent application (I2): diff first, write only on change.
    ##            Write path delegates to _atomic_write (I3) which picks direct os.replace
    ##            or sudo mv based on parent writability (I4). Returns whether a write
    ##            happened — callers use it for telemetry and exit semantics.
    ## @io — ⇥ path: str — hosts file path (/etc/hosts or a test tmp_path)
    ##       ⇥ hosts: set[str] — target host set
    ##       → ⎋ bool — True if the file was rewritten, False if already in sync
    ## @complexity — O(L + H log H) — one read + one merge + one write
    ## @throws — ConfigNotFoundError (exit 2) if the file does not exist
    ## @throws — PlatformError (exit 1) if sudo mv fails (surfaced from _sudo_move)
    """
    target = Path(path)
    content = _read_file(target)
    new_content, changed = block_diff(content, hosts)
    if not changed:
        logger.info("[IMP:9][apply] No diff — %s already in sync (idempotent no-op)", target)
        return False
    _atomic_write(target, new_content)
    logger.info("[IMP:9][apply] Applied %d host(s) → %s (atomic tmp+mv)", len(hosts), target)
    return True


# endregion FUNC_apply


# region FUNC__read_file


def _read_file(target: Path) -> str:
    """Read a hosts file, failing loud (ConfigNotFoundError) if absent.

    ▶ ┌target┐ → ◇ is_file? → ⊕ read_text → ⎋ str · ✗ ConfigNotFoundError (exit 2)

    ## @purpose — Uniform read with the repo's typed error contract: /etc/hosts absence
    ##            on a dev machine is a configuration problem, not a silent empty file.
    ## @io — ⇥ target: Path → ⎋ str — file content
    ## @complexity — O(L)
    ## @throws — ConfigNotFoundError (exit 2)
    """
    if not target.is_file():
        msg = f"hosts file not found: {target}"
        raise ConfigNotFoundError(msg)
    return target.read_text(encoding="utf-8")


# endregion FUNC__read_file


# region FUNC__atomic_write


def _atomic_write(target: Path, content: str, *, sudo_move_fn: Callable[[Path, Path], None] | None = None) -> None:
    """Write content to target atomically: tmp file + os.replace (writable parent)
    or sudo mv (unwritable parent, e.g. /etc).

    ▶ ┌target + content┐ → ◇ os.access(parent, W_OK) ?
    → ⊕ tmp in parent + os.replace (same-fs atomic rename)
    → ✗ ⊕ tmp in gettempdir + chmod 0644 + _sudo_move (sudo mv, atomic on APFS)
    → ⎋ None

    ## @purpose — Crash-safe hosts update (I3): a tmp file is fully written first, then
    ##            atomically renamed over the target. The sudo branch keeps the tmp file
    ##            OUT of the protected directory (no write permission there) and delegates
    ##            the final rename to _sudo_move — the single sudo point (I4).
    ## @param sudo_move_fn  DI (DevPlan 167 D3): fake sudo mv для тестов; None → _sudo_move.
    ## 🧐 TRAP[DI-SEAM] · 2026-08-14 · — · sudo_move_fn на _atomic_write (sudo-boundary seam)
    ## · Rejected: прямой вызов _sudo_move (субпроцесс sudo)
    ## · Reason: seam = тестируемость реальной sudo-ветки (I4: unwritable parent → sudo mv)
    ## ·   без патча module-атрибута; поведение по умолчанию (None → канон) неизменно
    ## · Rev: появление второго sudo-потребителя → общий privilege-объект
    ## @io — ⇥ target: Path — destination (may require root)
    ##       ⇥ content: str — normalized block content
    ##       → ⎋ None
    ## @complexity — O(content) I/O
    ## @throws — PlatformError (exit 1) if the sudo mv fails
    ## @rationale — Linux /tmp may be tmpfs → sudo mv becomes copy+unlink (not atomic);
    ##              macOS (the dev-hosts target platform, plan AC W4) shares APFS between
    ##              /tmp and /etc, so the rename stays atomic. Documented trade-off.
    """
    parent_writable = os.access(str(target.parent), os.W_OK)
    if parent_writable:
        tmp = target.parent / f".{target.name}.dev-hosts.tmp"
        tmp.write_text(content, encoding="utf-8")
        Path(tmp).replace(target)
        logger.debug("[IMP:6][_atomic_write] direct os.replace → %s", target)
        return

    fd, tmp_name = tempfile.mkstemp(prefix="dev-hosts-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.chmod(tmp_name, 0o644)  # sudo mv preserves the source mode — hosts(5) expects 644
        move_impl = _sudo_move if sudo_move_fn is None else sudo_move_fn
        move_impl(Path(tmp_name), target)
    finally:
        if Path(tmp_name).exists():
            os.unlink(tmp_name)


# endregion FUNC__atomic_write


# region FUNC__sudo_move


def _sudo_move(src: Path, dest: Path) -> None:
    """Move a temp file into place via sudo — the ONLY sudo invocation in this module.

    ▶ ┌src + dest┐ → ⚡ subprocess sudo mv → ◇ rc==0? ⎋ None · ✗ RuntimeError

    ## @purpose — Isolate the privilege boundary (I4) so tests can monkeypatch exactly
    ##            one function instead of patching subprocess site-wide. `sudo mv` keeps
    ##            the atomic-rename property for the protected-dir path.
    ## @io — ⇥ src: Path — fully-written temp file (mode 0644)
    ##       ⇥ dest: Path — /etc/hosts (or any unwritable parent target)
    ##       → ⎋ None
    ## @complexity — O(1) subprocess
    ## @throws — PlatformError (exit 1) with rc on sudo failure — never swallowed
    """
    logger.info("[IMP:8][_sudo_move] sudo mv %s → %s", src, dest)
    try:
        subprocess.run(["sudo", "mv", str(src), str(dest)], check=True)
    except subprocess.CalledProcessError as exc:
        # Типизированная иерархия (U-12): bare RuntimeError запрещён гейтом no-bare-raise
        msg = f"sudo mv failed (rc={exc.returncode}) — /etc/hosts not updated: {exc}"
        raise PlatformError(msg) from exc


# endregion FUNC__sudo_move


# ─────────────────────────────────────────────────────────────────────
# DIFF RENDERING (dry-run output)
# ─────────────────────────────────────────────────────────────────────

# region FUNC__render_diff


def _render_diff(etc_hosts: str, hosts: set[str]) -> str:
    """Render a unified diff of current vs managed /etc/hosts for dry-run output.

    ▶ ┌etc_hosts + hosts┐ → ◇ block_diff → ⊕ difflib.unified_diff → ⎋ str

    ## @purpose — Human/agent-readable dry-run report: shows exactly which lines would
    ##            change, so `make dev-hosts` exit 1 is actionable without --apply.
    ## @io — ⇥ etc_hosts: str — current content
    ##       ⇥ hosts: set[str] — target host set
    ##       → ⎋ str — unified diff text ("" when unchanged)
    ## @complexity — O(L) difflib pass
    ## @throws — ConfigParseError propagates from block_diff (malformed block)
    """
    new_content, _ = block_diff(etc_hosts, hosts)
    diff_lines = list(
        difflib.unified_diff(
            etc_hosts.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile="current /etc/hosts",
            tofile="managed /etc/hosts",
        )
    )
    return "".join(diff_lines)


# endregion FUNC__render_diff


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────

# region FUNC__default_platform_root


def _default_platform_root() -> Path:
    """Repo root resolved from this file's location (canon, config_renderer.py pattern).

    ▶ ┌_PROJECT_ROOT (module constant)┐ → ⎋ Path

    ## @purpose — Self-bootstrap of the platform root (class A defect class): dev_hosts
    ##            must resolve node-configs/ regardless of the caller's CWD (make always
    ##            runs from the repo root, but `python3 -m` may not). Single definition —
    ##            the module-level sys.path bootstrap constant is the source of truth.
    ## @io — ⇥ None → ⎋ Path — repo root (core/internal/dev_hosts.py → 3 parents up)
    ## @complexity — O(1)
    """
    return Path(_PROJECT_ROOT)


# endregion FUNC__default_platform_root


# region FUNC_build_parser


def build_parser() -> argparse.ArgumentParser:
    """Build the dev-hosts CLI parser.

    ▶ ┌None┐ → ⊕ argparse.ArgumentParser (mutually-exclusive mode group) → ⎋ parser

    ## @purpose — Three mutually exclusive modes: --print (dump hostnames), --dry-run
    ##            (default; exit 1 on diff), --apply (write). Path/env overrides keep the
    ##            CLI testable without touching /etc/hosts (tmp_path in unit tests).
    ## @io — ⇥ None → ⎋ argparse.ArgumentParser
    ## @complexity — O(1)
    """
    parser = argparse.ArgumentParser(
        description="Idempotent /etc/hosts manager for local dev (managed marker block)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--print", action="store_true", help="Print collected hostnames (one per line) and exit 0")
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Show unified diff vs the hosts file; exit 1 if a diff exists (default mode)",
    )
    mode.add_argument("--apply", action="store_true", help="Apply the managed block (sudo for /etc/hosts)")
    parser.add_argument("--etc-hosts", default=None, help=f"Hosts file path (default {DEFAULT_ETC_HOSTS})")
    parser.add_argument("--node-configs-dir", default=None, help="node-configs/ directory")
    parser.add_argument("--node", default=None, help="Node name (directory containing node.yaml)")
    parser.add_argument("--dev-suffix", default=None, help="Dev FQDN suffix (default PLATFORM_DOMAIN)")
    parser.add_argument(
        "--dev-certs-dir", default=None, help="Dev cert directory (default <repo>/core/modules/nginx/dev-certs)"
    )
    return parser


# endregion FUNC_build_parser


# region FUNC_main


class _DevHostsArgs(argparse.Namespace):
    """Typed argparse namespace (W11: Namespace attribute access is Any).

    ClassVar-аннотации БЕЗ значений (только типы) — значения ломают hasattr/parser-дефолты.
    """

    print: ClassVar[bool]
    dry_run: ClassVar[bool]
    apply: ClassVar[bool]
    etc_hosts: ClassVar[str | None]
    node_configs_dir: ClassVar[str | None]
    node: ClassVar[str | None]
    dev_suffix: ClassVar[str | None]
    dev_certs_dir: ClassVar[str | None]


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: --print | --dry-run (default) | --apply; exit 0/1/2/3.

    ▶ ┌argv┐ → ◇ parse → ◇ resolve env chain (arg > env > default) → ◇ collect_hosts →
    → ◇ --print? dump → 0 · ◇ --apply? apply() → 0 · ◇ --dry-run? block_diff → exit 0|1
    → ⎋ int exit code

    ## @purpose — Thin boundary over the pure functions. Env chain mirrors the dev-certs
    ##            canon (TRAP[BUG] 2026-07-16): CLI arg > env var > platform default;
    ##            the make target feeds .env values as env vars (recipe-level extraction).
    ## @io — ⇥ argv: list[str] | None → ⎋ int — 0 ok · 1 diff (dry-run)/error · 2/3 typed
    ## @complexity — O(P + S + L) — collect + merge + (diff | write)
    ## @invariants
    ##   - Business functions never call sys.exit — only main returns the exit code
    ##   - apply mode is idempotent: second run with a synced file → exit 0 no-op
    ##   - dry-run prints the unified diff to stdout; logs go to stderr
    ##   - PlatformError hierarchy maps to exit codes 2/3 (core/AGENTS.md contract)
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    args = build_parser().parse_args(argv, namespace=_DevHostsArgs())

    # ruff: ignore[PLW0717] — try-тело содержит return-ветки с fall-through (после-try код) — извлечение небезопасно
    try:
        platform_root = _default_platform_root()
        node_configs_dir = (
            args.node_configs_dir or os.environ.get("NODE_CONFIGS_DIR") or str(platform_root / "node-configs")
        )
        node_name = args.node or os.environ.get("NODE_NAME") or DEFAULT_NODE_NAME
        dev_suffix = (
            args.dev_suffix
            or os.environ.get("DEV_DOMAIN_SUFFIX")
            or os.environ.get("PLATFORM_DOMAIN")
            or DEFAULT_PLATFORM_DOMAIN
        )
        dev_certs_dir = args.dev_certs_dir or os.environ.get("DEV_CERTS_DIR") or DEFAULT_DEV_CERTS_DIR
        hosts_path = args.etc_hosts or DEFAULT_ETC_HOSTS
        hosts = collect_hosts(node_configs_dir, node_name, dev_suffix, dev_certs_dir)
        logger.info("[IMP:8][main] Collected %d hostname(s) (node=%s, suffix=%s)", len(hosts), node_name, dev_suffix)
        if args.print:
            for hostname in sorted(hosts):
                print(hostname)
            logger.info("[IMP:9][main] --print: %d hostname(s) printed", len(hosts))
            return 0
        if args.apply:
            applied = apply(hosts_path, hosts)
            logger.info("[IMP:9][main] --apply: %s", "hosts updated" if applied else "no diff — idempotent no-op")
            return 0
        content = _read_file(Path(hosts_path))
        _, changed = block_diff(content, hosts)
        if changed:
            sys.stdout.write(_render_diff(content, hosts))
            logger.info(
                "[IMP:9][main] --dry-run: DIFF detected — %d host(s) out of sync (exit 1)",
                len(hosts),
            )
            return 1
        logger.info("[IMP:9][main] --dry-run: no diff — hosts in sync (exit 0)")

    except PlatformError as exc:
        logger.error("[IMP:10][main] %s (exit %d)", exc, exc.exit_code)
        return exc.exit_code
    # ruff: ignore[BLE001] — top-level CLI handler (unexpected)
    except Exception as exc:  # noqa: EXC — top-level CLI handler (vhost_renderer parity)
        logger.error("[IMP:10][main] Unexpected error: %s", exc)
        return 1
    else:
        return 0


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
