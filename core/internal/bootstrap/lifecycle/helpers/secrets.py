#!/usr/bin/env python3
# GREP_SUMMARY: secrets-helpers, decrypt-secrets, ensure-secrets-exist, age, sops, secrets-env, autogen-secrets, postcondition-required-sops, module-aware
# STRUCTURE: ▶ decrypt_secrets ┌lib/secrets.sh step_10_decrypt_secrets (FATAL)┐ → ⚡ ensure_secrets_exist ┌secrets.env check → source (file-wins) → autogen → postcondition required∧sops ∧ (∅ node.yaml | consumed-by-enabled) ⚡┐ → ⎋
# region MODULE_CONTRACT
## @purpose  Secrets-provisioning I/O-хелперы bootstrap-фаз (decrypt + ensure/autogen) —
##           извлечены из state_machine (B9 T1, U-08). Все функции публичные.
## @scope    secrets.py: decrypt_secrets, ensure_secrets_exist, verify_required_sops_secrets.
##           Используются phases.py (φ4 secrets_provision, φ9 secrets_update).
## @invariants
##   - decrypt_secrets FATAL при сбое расшифровки (TRAP[BUG] 2026-07-23 P0 — non_fatal снят)
##   - ensure_secrets_exist: отсутствие secrets.env + НЕТ enc-файла → SKIP до autogen
##     (нода без операторских секретов — валидное состояние); enc ЕСТЬ + env нет → FATAL
##   - REF-0013 fail-fast: ошибки autogen/manifest БОЛЬШЕ НЕ глотаются как WARN → done;
##     manifest missing/malformed и merge-guard прокидываются наверх → φ4 PlatformFatalError.
##     Narrow excepts — только ImportError ленивых импортов (wide/bare except удалены)
##   - Postcondition (DATA-1006): parsed ⊇ {required ∧ source=sops} после decrypt+autogen,
##     enforced ТОЛЬКО когда enc-файл существует (autogen-only ноды не блокируются — TRAP[BUG] 2026-07-31)
##   - File-wins после decrypt (REF-0013): значения secrets.env перезаписывают stale os.environ,
##     кроме protected lifecycle-переменных (allowlist в secrets_manager)
##   - Autogen через lifecycle/secrets_manager (source_secrets_env / ensure_secrets)
## @rationale Strangler-Fig: извлечение I/O из state_machine-монолита (DevPlan 116 B9 D1).
##            REF-0013: φ4 рапортовала успех с пустым результатом («WARN → фаза done → skip
##            навсегда») — системный паттерн «success-marker до доказательства» устранён.
## @changes  2026-08-01 · Extracted from state_machine (B9 T1)
## @changes  2026-08-24 · REF-0013 (Волна 0) — narrow excepts (wide Exception снят), file-wins
##             sourcing через apply_env_file_to_osenv, postcondition verify_required_sops_secrets
## @changes  2026-08-31 · launch-validation asi-team-vps (P0) — postcondition module-aware:
##             verify_required_sops_secrets получает enabled_modules (из node.yaml через
##             shared/enabled_modules); required∧sops требуется только при consumers ∩
##             enabled_modules ≠ ∅; пустой consumers → SKIP; None → легаси-глобальная проверка
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
from core.internal.shared.enabled_modules import resolve_enabled_modules
from core.internal.shared.exceptions import ConfigNotFoundError, ConfigValidationError
from core.internal.shared.secrets_manifest_reader import consumers as manifest_consumers
from core.internal.shared.secrets_manifest_reader import iter_secrets
from core.internal.shared.secrets_manifest_reader import tier as manifest_tier
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
##           REF-0013: после decrypt — file-wins sourcing; в конце — postcondition
##           verify_required_sops_secrets (parsed ⊇ {required ∧ source=sops}).
## @io       ⇥ core_dir: str, env: Mapping | None (DI — SECRETS_ENV_FILE/NODE_NAME/NODE_CONFIGS_DIR,
##           DevPlan 160 E2) → ⎋ None (raises ConfigNotFoundError/ConfigValidationError/
##           FileNotFoundError on fail-fast conditions)
## @complexity O(N) where N = secrets in manifest
## @invariants
##   - Чистая нода без enc-файла: env отсутствует + НЕТ enc → SKIP до autogen
##   - env отсутствует + enc ЕСТЬ → decrypt FAILED → FATAL (ConfigNotFoundError)
##   - REF-0013: source/autogen ошибки НЕ глотаются (wide except снят) — manifest
##     missing/malformed → исключение → φ4 PlatformFatalError через phase-обёртку
##   ⚠️ TRAP[BUG] · 2026-07-31 · P1 · Чистая нода без secrets не могла забутстрапиться
##   · Symptom: φ4 secrets_provision FATAL на ноде без AGE-секретов: "secrets.env not found:
##   ·   /var/lib/platform/run/secrets.env" — decrypt SKIP (нет enc-файла) → env не создан →
##   ·   ensure падал ConfigNotFoundError. E2E DevPlan 095 T6.
##   · Fix: env отсутствует + НЕТ enc-файла → нода без операторских секретов → SKIP до autogen.
##   · Prevention: no-secrets нода (modules=[], без secrets/) — валидное состояние; FATAL только
##   ·   при реальном сбое расшифровки. Postcondition (REF-0013) уважает этот кейс: gated на enc.
def ensure_secrets_exist(core_dir: str, *, env: Mapping[str, str] | None = None) -> None:
    """Ensure secrets.env exists AND all autogen secrets are generated."""
    source: Mapping[str, str] = os.environ if env is None else env
    secrets_env = source.get("SECRETS_ENV_FILE", str(_secrets_env_file()))
    node_name = source.get("NODE_NAME", "")
    configs_dir = source.get("NODE_CONFIGS_DIR", str(node_configs_remote()))
    enc_file = pathlib.Path(configs_dir) / "secrets" / f"{node_name}.enc.yaml"

    # Step 1: Check file exists (after decrypt)
    if not pathlib.Path(secrets_env).is_file():
        if pathlib.Path(enc_file).is_file():
            logger.error("[IMP:9][ensure_secrets] %s not found after decrypt — cannot generate secrets", secrets_env)
            msg = f"secrets.env not found: {secrets_env}"
            raise ConfigNotFoundError(msg)
        logger.info("[IMP:8][ensure_secrets] No encrypted secrets for node='%s' — autogen-only secrets.env", node_name)

    # Step 2: Source secrets.env into os.environ (file-wins — REF-0013)
    # Narrow except (REF-0013): только ImportError ленивого импорта. Ошибки парсинга
    # НЕ глотаются здесь: source_secrets_env возвращает {} при I/O-сбое, а отсутствие
    # required∧sops переменных ловит postcondition-verifier (Step 4).
    from core.internal.bootstrap.lifecycle.secrets_manager import apply_env_file_to_osenv, source_secrets_env

    env_vars = source_secrets_env(secrets_env)
    applied = apply_env_file_to_osenv(env_vars, label=secrets_env)
    logger.info(
        "[IMP:9][ensure_secrets] Sourced %d vars from %s (%d file-wins overrides applied)",
        len(env_vars),
        secrets_env,
        applied,
    )

    # Step 3: Generate missing autogen secrets (REF-0013: ошибки БОЛЬШЕ НЕ глотаются как WARN;
    # manifest missing/malformed → исключение → φ4 PlatformFatalError через phase-обёртку).
    manifest_path = pathlib.Path(core_dir) / "secrets-manifest.yaml"
    from core.internal.bootstrap.lifecycle.secrets_manager import ensure_secrets as do_ensure

    generated = do_ensure(str(manifest_path), secrets_env)
    if generated:
        logger.info("[IMP:9][ensure_secrets] Generated %d secrets", len(generated))

    # Step 4: Postcondition (DATA-1006): parsed ⊇ {required ∧ source=sops} — REF-0013.
    # launch-validation asi-team-vps (P0): module-aware — при наличии node.yaml ноды
    # required∧sops требуется только для enabled consumer-модулей; None → легаси-глобально.
    enabled_modules = resolve_enabled_modules(node_name=node_name, env=source)
    if enabled_modules is not None:
        logger.info(
            "[IMP:8][ensure_secrets] Module-aware postcondition for node=%s: %d enabled module(s)",
            node_name,
            len(enabled_modules),
        )
    verify_required_sops_secrets(
        manifest_path=str(manifest_path),
        secrets_env=secrets_env,
        enc_file=str(enc_file),
        enabled_modules=enabled_modules,
    )


# endregion FUNC_ensure_secrets_exist


# region FUNC__consumed_by_enabled
## @purpose  Module-aware предикат postcondition: манифестный секрет требуется если хотя бы
##           один его consumer-модуль enabled в node.yaml. Пустой consumers → False (никто
##           не потребляет — минимальному контексту не требуется).
## @io       ⇥ entry: dict (запись манифеста), enabled: set[str] → ⎋ bool
## @complexity O(C), C = число consumer-модулей записи
def _consumed_by_enabled(entry: dict[str, object], enabled: set[str]) -> bool:
    """True if the secret's consumer modules intersect the enabled module set."""
    consumers = manifest_consumers(entry)  # typed accessor: [] if absent/non-list
    if not consumers:
        return False
    return bool(enabled.intersection(consumers))


# endregion FUNC__consumed_by_enabled


# region FUNC_verify_required_sops_secrets
## @purpose  Postcondition-verifier (REF-0013 / DATA-1006): после decrypt+autogen каждый
##           манифестный секрет tier=required ∧ source=sops обязан присутствовать с непустым
##           значением в secrets.env ИЛИ os.environ. Отсутствие → ConfigValidationError →
##           φ4 PlatformFatalError (fail-fast вместо отложенного взрыва на первом использовании).
##           launch-validation asi-team-vps (P0): module-aware — enabled_modules ≠ None →
##           секрет требуется ТОЛЬКО если его consumer-модуль enabled (consumers из манифеста);
##           пустой consumers → SKIP; None → легаси-глобальная проверка всех required∧sops.
## @io       ⇥ manifest_path: str, secrets_env: str, enc_file: str,
##             enabled_modules: set[str] | None (None = легаси) → ⎋ None ⚡ ConfigValidationError
## @complexity O(N) where N = entries in secrets-manifest.yaml
## @invariants
##   - Gated на существование enc-файла: нет enc → autogen-only нода → verifier no-op
##     (TRAP[BUG] 2026-07-31: чистая нода без операторских секретов остаётся валидной)
##   - Проверка по объединению parsed(secrets.env) ∪ os.environ — autogen-значения,
##     попавшие только в os.environ, тоже засчитываются
##   - Манифест читается строгим ридером shared.secrets_manifest_reader (STRICT)
##   - enabled_modules=None → прежняя глобальная проверка (обратная совместимость тестов)
def verify_required_sops_secrets(
    *,
    manifest_path: str,
    secrets_env: str,
    enc_file: str,
    enabled_modules: set[str] | None = None,
) -> None:
    """Postcondition: every required∧sops manifest secret (consumed by enabled module) has a value."""
    if not pathlib.Path(enc_file).is_file():
        logger.info(
            "[IMP:8][ensure_secrets] No encrypted secrets file (%s) — required∧sops postcondition skipped (autogen-only node)",
            enc_file,
        )
        return

    entries = iter_secrets(manifest_path)
    required: list[str] = []
    for entry in entries:
        if not entry.get("name"):
            continue
        if manifest_tier(entry) != "required" or str(entry.get("source", "")) != "sops":
            continue
        if enabled_modules is not None and not _consumed_by_enabled(entry, enabled_modules):
            logger.info(
                "[IMP:7][ensure_secrets] SKIP postcondition %s: no enabled consumer module (module-aware minimal context)",
                entry["name"],
            )
            continue
        required.append(str(entry["name"]))
    if not required:
        logger.info(
            "[IMP:8][ensure_secrets] Manifest has no required∧sops secrets for enabled modules — postcondition trivially satisfied"
        )
        return

    parsed: dict[str, str] = {}
    if pathlib.Path(secrets_env).is_file():
        try:
            from core.internal.shared.secrets_env_parser import parse as parse_secrets_env

            parsed = parse_secrets_env(secrets_env)
        except (OSError, ValueError) as e:
            # Парсинг упал — все required∧sops считаются отсутствующими: fail-fast ниже
            # даст читаемое сообщение со списком имён вместо тихого continue.
            logger.warning("[IMP:7][ensure_secrets] Cannot parse %s for postcondition: %s", secrets_env, e)

    def _has_value(name: str) -> bool:
        return bool((parsed.get(name, "") or "").strip()) or bool((os.environ.get(name, "") or "").strip())

    missing = [name for name in required if not _has_value(name)]
    if missing:
        logger.error(
            "[IMP:10][ensure_secrets] POSTCONDITION FAILED: required∧sops secrets missing after "
            "decrypt+autogen (%d/%d present): %s",
            len(required) - len(missing),
            len(required),
            ", ".join(missing),
        )
        msg = (
            f"Required SOPS secrets missing from {secrets_env} after decrypt+autogen: {', '.join(missing)} — "
            "check enc-file contents and secrets-manifest drift"
        )
        raise ConfigValidationError(msg)

    logger.info(
        "[IMP:9][ensure_secrets] Postcondition OK: %d/%d required∧sops secrets present in %s/os.environ",
        len(required),
        len(required),
        secrets_env,
    )


# endregion FUNC_verify_required_sops_secrets
