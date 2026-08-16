#!/usr/bin/env python3
# GREP_SUMMARY: secrets-helpers, decrypt-secrets, ensure-secrets-exist, age, sops, secrets-env, autogen-secrets
# STRUCTURE: ▶ decrypt_secrets ┌lib/secrets.sh step_10_decrypt_secrets (FATAL)┐ → ⚡ ensure_secrets_exist ┌secrets.env check → source → autogen┐ → ⎋
# region MODULE_CONTRACT
## @purpose  Secrets-provisioning I/O-хелперы bootstrap-фаз (decrypt + ensure/autogen) —
##           извлечены из state_machine (B9 T1, U-08). Все функции публичные.
## @scope    secrets.py: decrypt_secrets, ensure_secrets_exist.
##           Используются phases.py (φ4 secrets_provision, φ9 secrets_update).
## @invariants
##   - decrypt_secrets FATAL при сбое расшифровки (TRAP[BUG] 2026-07-23 P0 — non_fatal снят)
##   - ensure_secrets_exist: отсутствие secrets.env + НЕТ enc-файла → SKIP до autogen
##     (нода без операторских секретов — валидное состояние); enc ЕСТЬ + env нет → FATAL
##   - Autogen через lifecycle/secrets_manager (source_secrets_env / ensure_secrets)
## @rationale Strangler-Fig: извлечение I/O из state_machine-монолита (DevPlan 116 B9 D1).
## @changes  2026-08-01 · Extracted from state_machine (B9 T1)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import pathlib
import shlex
from collections.abc import Mapping

# Канонический node-configs base — shared/deploy_paths (литерал /opt/node-configs не используется)
from core.internal.shared.deploy_paths import node_configs_remote

# 142 W2: secrets.env → persistent /var/lib/platform/run (резолвер shared/deploy_paths)
from core.internal.shared.deploy_paths import secrets_env_file as _secrets_env_file
from core.internal.shared.exceptions import ConfigNotFoundError
from core.internal.shared.subprocess_io import run_subprocess

logger = logging.getLogger(__name__)


# region FUNC_decrypt_secrets
## @purpose  Decrypt AGE-encrypted secrets. Delegates to lib/secrets.sh. FATAL on failure.
## @io       ⇥ core_dir → ⎋ None (raises RuntimeError on decryption failure)
## @complexity O(1)
## @invariants
##   - Decryption failure is FATAL — secrets are critical infrastructure
##   - step_10_decrypt_secrets handles "no encrypted file" as graceful skip (exit 0)
##   ⚠️ TRAP[BUG] · 2026-07-23 · P0 · non_fatal=True swallowed decrypt failures
##   · Symptom: bootstrap continued with ci_default placeholders (test-access-key),
##   ·   checkpoint .done created despite failure → --resume skipped decrypt forever.
##   · Fix: removed non_fatal=True — decrypt failure is now FATAL (RuntimeError).
##   · Test: unit/contract tests verify decrypt exit 1 → RuntimeError propagation.
def decrypt_secrets(core_dir: str) -> None:
    """Decrypt AGE-encrypted secrets. Delegates to lib/secrets.sh."""
    secrets_lib = pathlib.Path(core_dir) / "lib" / "secrets.sh"
    if pathlib.Path(secrets_lib).is_file():
        # lib/secrets.sh requires CORE_DIR, logging.sh (log_step). step_start/done/skip —
        # self-contained via declare -f stub-guard (secrets.sh L117-121); checkpoint.sh
        # больше не sourced (его функциональность в state machine).
        # ⚠️ TRAP[BUG] · 2026-07-23 · P0 · source secrets.sh без зависимостей
        # · Symptom: step_start/log_step: command not found, CORE_DIR/internal/... : No such file
        # · Root: bash -c "source secrets.sh" не имел CORE_DIR и не подгружал checkpoint/logging libs
        # · Fix: export CORE_DIR, source logging.sh перед secrets.sh (checkpoint.sh удалён в 091)
        logging_lib = pathlib.Path(core_dir) / "lib" / "logging.sh"
        # B4: единый канон shared/subprocess_io (check=True = lifecycle raise-семантика;
        # decrypt FATAL при сбое — TRAP[BUG] 2026-07-23 P0)
        run_subprocess(
            [
                "bash",
                "-c",
                (
                    f"export CORE_DIR={shlex.quote(core_dir)}"
                    f" && source {shlex.quote(str(logging_lib))}"
                    f" && source {shlex.quote(str(secrets_lib))}"
                    " && step_10_decrypt_secrets"
                ),
            ],
            check=True,
        )


# endregion FUNC_decrypt_secrets


# region FUNC_ensure_secrets_exist
## @purpose  Ensure secrets.env exists AND all autogen secrets are generated.
## @io       ⇥ core_dir: str, env: Mapping | None (DI — SECRETS_ENV_FILE/NODE_NAME/NODE_CONFIGS_DIR,
##           DevPlan 160 E2) → ⎋ None (raises RuntimeError if secrets.env missing with enc present)
## @complexity O(N) where N = secrets in manifest
## @invariants
##   - Чистая нода без enc-файла: env отсутствует + НЕТ enc → SKIP до autogen
##   - env отсутствует + enc ЕСТЬ → decrypt FAILED → FATAL (ConfigNotFoundError)
##   ⚠️ TRAP[BUG] · 2026-07-31 · P1 · Чистая нода без secrets не могла забутстрапиться
##   · Symptom: φ4 secrets_provision FATAL на ноде без AGE-секретов: "secrets.env not found:
##   ·   /var/lib/platform/run/secrets.env" — decrypt SKIP (нет enc-файла) → env не создан →
##   ·   ensure падал ConfigNotFoundError. E2E DevPlan 095 T6.
##   · Fix: env отсутствует + НЕТ enc-файла → нода без операторских секретов → SKIP до autogen.
##   · Prevention: no-secrets нода (modules=[], без secrets/) — валидное состояние; FATAL только
##   ·   при реальном сбое расшифровки.
def ensure_secrets_exist(core_dir: str, *, env: Mapping[str, str] | None = None) -> None:
    """Ensure secrets.env exists AND all autogen secrets are generated."""
    source: Mapping[str, str] = os.environ if env is None else env
    secrets_env = source.get("SECRETS_ENV_FILE", str(_secrets_env_file()))

    # Step 1: Check file exists (after decrypt)
    if not pathlib.Path(secrets_env).is_file():
        node_name = source.get("NODE_NAME", "")
        configs_dir = source.get("NODE_CONFIGS_DIR", str(node_configs_remote()))
        enc_file = pathlib.Path(configs_dir) / "secrets" / f"{node_name}.enc.yaml"
        if pathlib.Path(enc_file).is_file():
            logger.error("[IMP:9][ensure_secrets] %s not found after decrypt — cannot generate secrets", secrets_env)
            msg = f"secrets.env not found: {secrets_env}"
            raise ConfigNotFoundError(msg)
        logger.info("[IMP:8][ensure_secrets] No encrypted secrets for node='%s' — autogen-only secrets.env", node_name)

    # Step 2: Source secrets.env into os.environ
    try:
        from core.internal.bootstrap.lifecycle.secrets_manager import source_secrets_env

        env_vars = source_secrets_env(secrets_env)
        for k, v in env_vars.items():
            if k not in os.environ:
                os.environ[k] = v
        logger.info("[IMP:9][ensure_secrets] Sourced %d vars from %s", len(env_vars), secrets_env)
    # ruff: ignore[BLE001] — lazy-import + secrets-парсинг — recoverable, широкий спектр (DEPLOY_BEST_EFFORT)
    except Exception as e:  # noqa: EXC — non-fatal: secrets source failure is recoverable (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.warning("[IMP:7][ensure_secrets] Failed to source secrets.env: %s", e)

    # Step 3: Generate missing autogen secrets
    manifest_path = pathlib.Path(core_dir) / "secrets-manifest.yaml"
    try:
        from core.internal.bootstrap.lifecycle.secrets_manager import ensure_secrets as do_ensure

        generated = do_ensure(str(manifest_path), secrets_env)
        if generated:
            logger.info("[IMP:9][ensure_secrets] Generated %d secrets: %s", len(generated), generated)
    # ruff: ignore[BLE001] — lazy-import + autogen — recoverable, широкий спектр (DEPLOY_BEST_EFFORT)
    except Exception as e:  # noqa: EXC — non-fatal: autogen failure is recoverable (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.warning("[IMP:7][ensure_secrets] Autogen failed: %s", e)


# endregion FUNC_ensure_secrets_exist
