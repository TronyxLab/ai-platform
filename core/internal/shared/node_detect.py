#!/usr/bin/env python3
# GREP_SUMMARY: node-detect, detect-age-key, auto-detect-node-name, AGE_SECRET_KEY, SOPS_AGE_KEY, AGE_SECRET_KEY_FILE, default-key-files, node-configs, NodeDetectionError, shared, cli
# STRUCTURE: ▶ detect_age_key → ◇ AGE_SECRET_KEY env? → ◇ SOPS_AGE_KEY env? → ◇ AGE_SECRET_KEY_FILE? → ◇ default key file (~/.config/age/keys.txt)? → ◇ /etc/age/key.txt (restore-first fallback)? → ⊕ masked log → ⎋ str|None ── ▶ auto_detect_node_name → ∋ scan node-configs/*/ (skip scripts|secrets) → ◇ count==1? → ⎋ name | ✗ NodeDetectionError ── ▶ CLI → ◇ --detect-age-key | --detect-node-name → ⎋ exit 0|3|1 (3 = key absent)
# region MODULE_CONTRACT
## @purpose  Canonical single-source-of-truth for AGE secret key detection and node name
##           auto-detection. Consolidates duplicate shell implementations from
##           bootstrap.sh (detect_age_key + auto_detect_node_name), node-update.sh
##           (detect_age_key) and converge.sh (auto_detect_node_name) into one
##           testable Python module (DevPlan 104).
## @scope    Called from entrypoint shell scripts via `python3 -m core.internal.shared.node_detect`
##           (--detect-age-key / --detect-node-name subcommands). Pure env/file/dir I/O — no subprocess.
##           Consumers of --detect-node-name (DevPlan 116 B3 T2, U-38): bootstrap.sh, converge.sh,
##           node-update.sh, platform-export-metrics.sh (metrics wrapper), core-deploy CI workflow —
##           scripts/ and secrets/ are ALWAYS excluded (single detector canon).
## @invariants
##   1. detect_age_key chain: AGE_SECRET_KEY → SOPS_AGE_KEY → AGE_SECRET_KEY_FILE →
##      default key file ~/.config/age/keys.txt → /etc/age/key.txt (Check 5, ПОСЛЕДНИЙ —
##      restore-first fallback, W4 DevPlan 140). Первый непустой источник побеждает. Default —
##      стандартная age CLI локация; на dev-машине оператора это symlink на ~/.ssh/age-key-personal.txt.
##      Используется ТОЛЬКО когда env-цепочка пуста (CI/production не затронуты: ключ
##      всегда передаётся через env/файл)
##   1a. /etc/age/key.txt (Check 5) — НЕ канон для φ4: φ4 (phases/secrets.py) НЕ персистит
##      ключ на диск (W4 DevPlan 140, W12-on-node-age-key); канон — env → tmpfs decrypt-only
##      (S-13). Check 5 читает файл только если env-цепочка пуста И default key file не найден —
##      ручной перенос ключа оператором (restore-first при восстановлении ноды).
##   2. detect_age_key returns None (never empty string) when no key found
##   3. detect_age_key logs masked (first 8 chars) key source at IMP:8 — no plaintext key in logs
##   4. auto_detect_node_name skips "scripts" and "secrets" subdirectories
##   5. auto_detect_node_name raises NodeDetectionError on missing dir, 0 or >1 candidates
##   6. CLI: --detect-age-key → stdout key + exit 0 | exit 3 = module OK, key absent |
##      exit 1 = unexpected error; --detect-node-name [--node-configs-dir PATH] → stdout name + exit 0 | exit 1
##   7. No side effects — neither function exports env vars or mutates state
##   8. Exit 3 (not 1) for "key absent" — lets shell callers distinguish "module/python3 missing"
##      (FATAL, exit 1/127) from "ran fine, no key" (non-fatal) WITHOUT inline python3 probes
##      (language policy — check-no-new-inline-python3 hook, TRAP[DECISION] at exit-3 site)
## @rationale DevPlan 104 P1/P2/P3: two copies of detect_age_key (bootstrap.sh + node-update.sh)
##            and two of auto_detect_node_name (bootstrap.sh + converge.sh) violated
##            single-source-of-truth. age_key.py (DevPlan 078) reduced to compat-re-export shim;
##            node_detect.py becomes the canonical implementation.
## @changes  2026-07-31 | DevPlan 104 — Created (consolidates age_key.py logic + shell functions)
## @changes  2026-07-31 | Final-gate fix — key-absent exit code 1→3 (language policy: no inline probe)
## @changes  2026-08-02 | E2E-канал — 4-е звено: default key files для бесшовного локального запуска
##            make test-node без ручного export (test-VPS пересоздана, 103.88.243.151)
## @changes  2026-08-03 | Один default-путь: ~/.config/age/keys.txt (age CLI default) — на dev-машине
##            symlink на ~/.ssh/age-key-personal.txt (решение пользователя: единая стандартная локация)
## @changes  2026-08-06 | DevPlan 140 W4 — Check 5 (/etc/age/key.txt) → restore-first fallback
##            (не канон): persist удалён из phases/secrets.py; путь через модульную константу
##            _ETC_AGE_KEY_FILE (тестируемость, monkeypatch на tmp_path в unit-тестах)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# B3: канонический node-configs base — shared/deploy_paths (литерал /opt/node-configs удалён)
from core.internal.shared.deploy_paths import node_configs_remote

logger = logging.getLogger(__name__)

DEFAULT_NODE_CONFIGS_DIR = str(node_configs_remote())
SKIP_DIRS = frozenset({"scripts", "secrets"})
# W4 (DevPlan 140): /etc/age/key.txt — restore-first fallback (ручной перенос ключа
# оператором при восстановлении ноды), НЕ канон для φ4. Модульная константа — путь
# тестируемый (тесты monkeypatch-ят её на tmp_path вместо чтения реального /etc/age).
_ETC_AGE_KEY_FILE = "/etc/age/key.txt"


# region CLASS_NodeDetectionError
## @purpose — Typed error raised when node name auto-detection cannot resolve a unique node
##            (missing configs dir, zero candidates, or multiple candidates).
## @io — ⇥ message: str → ⎋ NodeDetectionError instance
## @complexity — O(1)
class NodeDetectionError(Exception):
    """Raised when node name auto-detection cannot resolve a unique node."""


# endregion CLASS_NodeDetectionError


# region FUNC_detect_age_key
## @purpose  Detect AGE secret key from env chain (logic inherited from age_key.py, DevPlan 078):
##            AGE_SECRET_KEY env → SOPS_AGE_KEY env → AGE_SECRET_KEY_FILE content →
##            default key file ~/.config/age/keys.txt.
##            Mirrors the removed shell detect_age_key() from bootstrap.sh/node-update.sh.
## @io       ⇥ None → ⎋ str | None (None = not found)
## @complexity O(1) — 3 env lookups + 1 default file probe
## @invariants
##   - Returns None (never empty string) on not found — caller distinguishes "not set" from "empty"
##   - Logs masked (first 8 chars) at IMP:8 for audit
##   - AGE_SECRET_KEY_FILE is read as first line (head -1 equivalent, strip newline)
##   - Default key file is probed ONLY after the full env chain is empty — env (CI/production)
##     always wins; default file is an operator convenience for local E2E (test-node)
def detect_age_key() -> str | None:
    """Detect AGE secret key from env chain.

    ▶ ◇ AGE_SECRET_KEY env? → ◇ SOPS_AGE_KEY env? → ◇ AGE_SECRET_KEY_FILE? → ◇ ~/.config/age/keys.txt? → ◇ /etc/age/key.txt (restore-first fallback)? → ⎋ str | None
    """
    # ── Check 1: AGE_SECRET_KEY env ──
    # Returns key in canonical AGE-SECRET-KEY-xxxxxxxx… format (with prefix)
    key = os.environ.get("AGE_SECRET_KEY", "")
    if key:
        _log_masked("AGE_SECRET_KEY", key, "environment")
        return key

    # ── Check 2: SOPS_AGE_KEY env (deprecated fallback, same canonical format) ──
    key = os.environ.get("SOPS_AGE_KEY", "")
    if key:
        _log_masked("AGE_SECRET_KEY", key, "SOPS_AGE_KEY env fallback")
        return key

    # ── Check 3: AGE_SECRET_KEY_FILE content ──
    # File contains the raw key (first line, no prefix — same AGE-SECRET-KEY-xxxxxxxx… format)
    file_path = os.environ.get("AGE_SECRET_KEY_FILE", "")
    if file_path:
        try:
            with open(file_path) as f:
                key = f.readline().strip()
            if key:
                _log_masked("AGE_SECRET_KEY", key, f"file {file_path}")
                return key
            logger.warning("[IMP:8][node_detect] AGE_SECRET_KEY_FILE=%s is empty", file_path)
        except OSError as e:
            logger.warning("[IMP:8][node_detect] Cannot read AGE_SECRET_KEY_FILE=%s: %s", file_path, e)

    # ── Check 4: default key file (operator convenience, local E2E) ──
    # Probing Path.home() at call time (not import time) so tests can monkeypatch HOME.
    # Env chain above is empty here — CI/production always set AGE_SECRET_KEY(_FILE), so
    # this path is a no-op there (single-source-of-truth chain extension, 2026-08-02).
    # Единственный default: ~/.config/age/keys.txt — стандартная age CLI локация; на dev-машине
    # оператора это symlink на ~/.ssh/age-key-personal.txt (решение пользователя 2026-08-03).
    # Файл ключа несёт comment-строки ВЫШЕ ключа (# created / # public key — .zshrc использует
    # `tail -1` по той же причине), поэтому берётся ПЕРВАЯ строка с каноническим префиксом
    # AGE-SECRET-KEY-, а не слепой readline().
    home = Path.home()
    candidate = home / ".config" / "age" / "keys.txt"
    if candidate.is_file():
        try:
            with open(candidate) as f:
                key = next((line.strip() for line in f if line.strip().startswith("AGE-SECRET-KEY-")), "")
        except OSError as e:
            logger.warning("[IMP:8][node_detect] Cannot read default key file %s: %s", candidate, e)
        else:
            if key:
                _log_masked("AGE_SECRET_KEY", key, f"default file {candidate}")
                return key
            logger.warning("[IMP:8][node_detect] Default key file %s has no AGE-SECRET-KEY- line", candidate)

    # ── Check 5: /etc/age/key.txt — restore-first fallback (W4, DevPlan 140) ──
    # НЕ канон для φ4: φ4 (phases/secrets.py) НЕ персистит ключ на диск — канон env →
    # tmpfs decrypt-only (S-13, decrypt_secrets.py). /etc/age/key.txt читается ТОЛЬКО если
    # вся env-цепочка пуста и default key file не найден — ручной перенос ключа оператором
    # при восстановлении ноды (restore-first). Ключ приходит env (CI: AGE_SECRET_KEY).
    node_key = Path(_ETC_AGE_KEY_FILE)
    if node_key.is_file():
        try:
            with open(node_key) as f:
                key = next((line.strip() for line in f if line.strip().startswith("AGE-SECRET-KEY-")), "")
        except OSError as e:
            logger.warning("[IMP:8][node_detect] Cannot read node key file %s: %s", node_key, e)
        else:
            if key:
                _log_masked("AGE_SECRET_KEY", key, f"node file {node_key} (restore-first fallback)")
                return key
            logger.warning("[IMP:8][node_detect] Node key file %s has no AGE-SECRET-KEY- line", node_key)

    logger.warning(
        "[IMP:8][node_detect] AGE_SECRET_KEY not found — Docker modules requiring secrets will fail to deploy"
    )
    return None


# endregion FUNC_detect_age_key


# region FUNC__log_masked
## @purpose — Log AGE key discovery with masked value (first 8 chars).
##            Prevents full key leakage in logs.
## @io — ⇥ key_name: str, key_value: str, source: str → ⎋ None
## @complexity — O(1)
def _log_masked(key_name: str, key_value: str, source: str) -> None:
    """Log AGE key discovery with masked value."""
    masked = key_value[:8] if len(key_value) >= 8 else key_value
    logger.info("[IMP:8][node_detect] %s found in %s (%s...)", key_name, source, masked)


# endregion FUNC__log_masked


# region FUNC_auto_detect_node_name
## @purpose  Auto-detect the single node name from a node-configs directory.
##            Skips "scripts" and "secrets" subdirectories; exactly 1 valid dir → name.
##            Mirrors the removed shell auto_detect_node_name() from bootstrap.sh/converge.sh.
## @io       ⇥ node_configs_dir: str = DEFAULT_NODE_CONFIGS_DIR → ⎋ str (node name)
## @raises   NodeDetectionError on missing dir, 0 candidates, or >1 candidates
## @complexity O(N) — N = number of entries in the node-configs directory
## @invariants
##   - "scripts" and "secrets" are never treated as node candidates
##   - Deterministic diagnostic: candidates listed sorted on ambiguity
##   - Success logged at IMP:9 (business logic checkpoint)
def auto_detect_node_name(node_configs_dir: str = DEFAULT_NODE_CONFIGS_DIR) -> str:
    """Detect the unique node name in the node-configs directory.

    ▶ scan node-configs/*/ → ∋ skip scripts|secrets → ◇ count==1? → ⎋ name | ✗ NodeDetectionError
    """
    configs_path = Path(node_configs_dir)
    if not configs_path.is_dir():
        raise NodeDetectionError(f"{node_configs_dir} does not exist")

    candidates: list[str] = []
    for entry in configs_path.iterdir():
        if not entry.is_dir():
            continue
        if entry.name in SKIP_DIRS:
            continue
        candidates.append(entry.name)

    if not candidates:
        raise NodeDetectionError("No node directories found")
    if len(candidates) > 1:
        raise NodeDetectionError(f"Multiple directories: {', '.join(sorted(candidates))} — use --node <name>")

    node_name = candidates[0]
    logger.info("[IMP:9][node_detect] Auto-detected node: %s", node_name)
    return node_name


# endregion FUNC_auto_detect_node_name


# region FUNC__build_parser
## @purpose — Build the CLI argument parser for the node_detect module.
## @io — ⇥ None → ⎋ argparse.ArgumentParser
## @complexity — O(1)
def _build_parser() -> argparse.ArgumentParser:
    """Build CLI parser with mutually exclusive detection flags."""
    parser = argparse.ArgumentParser(
        prog="core.internal.shared.node_detect",
        description="Detect AGE secret key or auto-detect node name (DevPlan 104).",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--detect-age-key",
        action="store_true",
        help="Print AGE key to stdout; exit 3 with stderr diagnostic if not found.",
    )
    group.add_argument(
        "--detect-node-name",
        action="store_true",
        help="Print auto-detected node name to stdout; exit 1 on ambiguous/missing node.",
    )
    parser.add_argument(
        "--node-configs-dir",
        default=DEFAULT_NODE_CONFIGS_DIR,
        help=f"Directory containing node configs (default: {DEFAULT_NODE_CONFIGS_DIR}).",
    )
    return parser


# endregion FUNC__build_parser


# region FUNC_main
## @purpose — CLI entrypoint. Dispatches --detect-age-key / --detect-node-name.
## @io — ⇥ argv: list[str] | None → ⎋ int (0 = success, 3 = key absent (module OK),
##       1 = detection failure / unexpected error, 2 = argparse usage error)
## @complexity — O(N) — delegates to detect_age_key (O(1)) or auto_detect_node_name (O(N))
## @invariants
##   - stdout carries ONLY the detected value (key or node name) — machine-parseable
##   - Diagnostics go to stderr with [IMP:8]/[IMP:9]/[IMP:10] LDD tags
##   - exit 3 on "key absent" — module ran fine; shell callers treat 0/3 as expected,
##     any other non-zero as FATAL (missing python3/module). See TRAP[DECISION] below.
## ⚠️ TRAP[DECISION] · 2026-07-31 · — · Key-absent exit 1→3 — no inline python3 probe needed
## · Rejected: shell `python3 -c "import core.internal.shared.node_detect"` probe to distinguish
##   "module missing" from "key absent" (F1 fix-coder attempt — blocked by no-new-inline-python3 hook)
## · Reason: exit code IS the distinction — 0=key, 3=key absent (module OK), other non-zero=FATAL.
##   Shell: `python3 -m ... || { rc=$?; [[ $rc -eq 3 ]] → non-fatal; else → FATAL; }`.
##   Single source of truth stays in Python; no shell-side import probe (language policy Tier 1).
## · Rev: if a future caller needs more granularity → extend exit codes, don't reintroduce probes.
def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return the process exit code."""
    args = _build_parser().parse_args(argv)

    if args.detect_age_key:
        key = detect_age_key()
        if key:
            print(key)
            return 0
        print("AGE_SECRET_KEY not found", file=sys.stderr)
        return 3

    try:
        node_name = auto_detect_node_name(args.node_configs_dir)
    except NodeDetectionError as e:
        logger.error("[IMP:10][node_detect] %s", e)
        return 1

    print(node_name)
    return 0


# endregion FUNC_main


# region FUNC_CLI
## @purpose — __main__ entrypoint: configure stderr logging, propagate exit code.
## @io — ⇥ sys.argv → ⎋ process exit code
## @complexity — O(N)
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )
    sys.exit(main())
# endregion FUNC_CLI
