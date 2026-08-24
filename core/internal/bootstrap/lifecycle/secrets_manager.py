#!/usr/bin/env python3
# GREP_SUMMARY: secrets-manager, autogen-secrets, manifest, ensure-secrets, sops, htpasswd, cleanup-proxy, tor-enabled, secrets-env-parser, salt-idempotent
# STRUCTURE: ▶ ensure_secrets → source_secrets_env → _read_manifest → _generate_secret → _persist_to_sops → _ensure_master_credentials → _ensure_derived_passwords → _ensure_htpasswd → ⎋ CLI
#            ▶ _ensure_master_credentials → ◇ оба заданы? → ⎋ no-op → ⊕ email=admin@<домен> → ⊕ pwd=secrets.token_urlsafe(32) → ⊕ os.environ + merge + atomic write
#            ▶ _ensure_derived_passwords → ◇ задан (env/файл)? → ⎋ skip → ⊕ HERMES/GF/LANGFUSE = token_urlsafe(32) per-secret → ⊕ os.environ + merge + atomic write
#            ▶ cleanup_secrets_env → ◇ parse → ◇ TOR_ENABLED≠"true"? → ⊕ filter proxy → ⊕ atomic write (0o600) → ⎋ dict
#            ▶ _write_htpasswd_file → ◇ existing? → ⊕ extract $apr1$SALT$ → ⊕ recompute fixed-salt → ◇ match? → ⎋ no-op|write
# region MODULE_CONTRACT
## @purpose  Auto-generate missing tier=generated secrets from secrets-manifest.yaml or fallback hardcoded list.
##           Port of core/lib/secrets.sh:step_12b_ensure_secrets() lines 298-411 plus source_secrets_env()
##           (канон — Python; shell-фасад step_12b_ensure_secrets не используется)
##           and htpasswd generation. Designed for bootstrap pipeline step 12b.
## @scope    core/internal/bootstrap/lifecycle/ — secrets management for bootstrap pipeline.
##           Responsibilities: (1) read manifest and fill gaps, (2) parse secrets.env,
##           (3) generate htpasswd from platform credentials, (4) proxy-var cleanup of secrets.env
##           (DevPlan 102 — cleanup_secrets_env + htpasswd CLI for thin shell facades).
## @invariants
##   1. Non-fatal: returns partial list on failure, NEVER raises exceptions
##   2. Existing secrets are NOT overwritten — only missing (empty) secrets are generated
##   3. gen_command executed via subprocess (bash -c) with 30s timeout
##   4. sops --set persistence is non-fatal on failure
##   5. htpasswd generation called after secrets (requires PLATFORM_MASTER_PASSWORD)
##   6. sourced secrets.env values take precedence over manifest/hardcoded defaults
##   7. htpasswd idempotency: existing file salt ($apr1$SALT$) is reused for deterministic
##      comparison — never rewrites on unchanged credentials (TRAP[BUG] 2026-07-31)
##   8. cleanup_secrets_env: no-op on missing file (returns {}), never raises — logs warnings
## @rationale  Python port of shell secrets logic. Enables unit-testing, typed returns,
##             and consistent error handling without relying on bash eval() for secret generation.
## @changes  2026-07-25 | W5-E6 secrets_manager — created from secrets.sh step_12b decomposition
## @changes  2026-07-30 | DevPlan 086 — source_secrets_env() delegates to shared secrets_env_parser.parse()
## @changes  2026-07-31 | DevPlan 102 — cleanup_secrets_env(), htpasswd CLI, salt-extraction idempotency fix;
##             import: canonical core.internal.shared form kept (gate test_gate_secrets_parser_import) +
##             ModuleNotFoundError fallback to shared-dir bootstrap so the script runs standalone as CLI
##             (the bare package import previously crashed outside pytest — ModuleNotFoundError)
## @changes  2026-08-12 | DevPlan 156 W1 — +_ensure_master_credentials (autogen PLATFORM_MASTER_EMAIL/
##             PLATFORM_MASTER_PASSWORD при первом bootstrap: admin@<домен платформы> +
##             test-master-password-<dd.mm.YYYY>; идемпотентно, persist в secrets.env; решение
##             пользователя 2026-08-12 — закрывает инцидент htpasswd asi-team-vps)
## @changes  2026-08-16 | DevPlan 176 B.3/B.8 (security hardening) — random-autogen per-secret:
##             PLATFORM_MASTER_PASSWORD генерируется secrets.token_urlsafe(32) ВМЕСТО
##             test-master-password-<dd.mm.YYYY> (решение пользователя 2026-08-12 инвертировано
##             решением 2026-08-15 — H3: предсказуемый пароль ≤31 попытки закрыт);
##             +_ensure_derived_passwords — HERMES_DASHBOARD_PASSWORD/GF_SECURITY_ADMIN_PASSWORD/
##             LANGFUSE_INIT_USER_PASSWORD получают СОБСТВЕННЫЕ случайные значения при первом
##             bootstrap (M7/B.8 — разрыв unified-auth конвенции, единый пароль = blast radius)
## @changes  2026-08-24 | REF-0013 (Волна 0) — merge-guard Step 3.5/_persist_new_vars: непустой
##             secrets.env, распарсившийся в 0 записей, БОЛЬШЕ НЕ перезаписывается набором
##             `{} + generated` (ConfigValidationError/ValueError ДО atomic write — операторские
##             секреты GHCR_PULL_TOKEN/TELEGRAM_*/PLATFORM_MASTER_* не уничтожаются);
##             +apply_env_file_to_osenv — file-wins после decrypt с protected-allowlist
##             жизненного цикла (свежий decrypt больше не проигрывает stale os.environ)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import secrets
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, cast

# 142 W2 (B21): канонические резолверы run-артефактов — persistent /var/lib/platform/run
# (tmpfs /run/platform не переживает reboot; env-оверрайды сохраняют dev-локали macOS).
# ⚠️ TRAP[BUG] · 2026-08-12 · HI · bare-импорт deploy_paths ДО sys.path-bootstrap ломал standalone CLI
# · Symptom: `python3 core/internal/bootstrap/lifecycle/secrets_manager.py htpasswd|cleanup|ensure`
# ·   → ModuleNotFoundError: No module named 'core' (воспроизведено 2026-08-12,
# ·   `make dev-metrics` htpasswd CLI — helpers.mk:81); под pytest (conftest addsitedir)
# ·   и на ноде (node-lifecycle.sh:13 export PYTHONPATH) не проявлялось.
# · Root: DevPlan 142 W2 (commit bdaa3f6d) добавил два bare-импорта deploy_paths ВЫШЕ
# ·   безусловного bootstrap _PLATFORM_ROOT — вопреки контракту TRAP[BUG] 2026-08-01 ниже
# ·   («безусловный bootstrap project root — канонический импорт работает всегда»).
# · Fix: оба импорта перенесены в try/except-блок НИЖЕ sys.path-bootstrap (каноническая
# ·   форма + shared-dir fallback) — standalone CLI работает, gate-форма импорта сохранена.
# · Rev: если secrets_manager.py переезжает из core/internal/bootstrap/lifecycle/ —
# ·   пересчитать _SHARED_DIR/_PLATFORM_ROOT (тройной dirname).

# ── Shared modules import ──
# Canonical package import (DevPlan 086 — gate test_gate_secrets_parser_import enforces the
# `core.internal.shared.secrets_env_parser` form for all direct consumers).
# Fallback: standalone CLI execution (shell facades: python3 .../secrets_manager.py) runs
# outside pytest where the `core` package is NOT importable — bootstrap the shared dir and
# import the module directly (pattern: decrypt_secrets.py L44-54).
# ⚠️ TRAP[BUG] · 2026-07-31 · P1 · Module-level `core.internal` import crashed standalone CLI
# · Symptom: `python3 secrets_manager.py cleanup|htpasswd|ensure` → ModuleNotFoundError:
# ·   No module named 'core' (script sys.path[0] = script dir, `core` package unreachable).
# ·   The plan's shell facades (step_10 cleanup, htpasswd) depend on standalone invocation.
# · Root: (a) bare `from core.internal.shared.secrets_env_parser import` — works only under
# ·   pytest (rootdir in sys.path); (b) `_ensure_htpasswd` sys.path bootstrap used
# ·   4× dirname from lifecycle/ → `core/shared` (NONEXISTENT) → `from crypto import ...`
# ·   failed whenever _ensure_htpasswd was actually invoked (production step_12b).
# · Fix: canonical import kept for the gate + ModuleNotFoundError fallback to shared-dir
# ·   bootstrap; _SHARED_DIR computed with 3× dirname (core/internal/shared) and reused
# ·   by _write_htpasswd_file.
# · Rev: if secrets_manager.py moves out of core/internal/bootstrap/lifecycle/, recompute
# ·   _SHARED_DIR relative path; if the gate test's import pattern changes, sync both arms.
# ⚠️ TRAP[BUG] · 2026-08-01 · P1 · bare-fallback загрузка shared-модулей ломает их канонические
# · импорты (T2: shared-модули импортируют core.internal.shared.exceptions на module level)
# · Symptom: `python3 secrets_manager.py htpasswd` → ModuleNotFoundError: No module named 'core'
# ·   внутри secrets_env_parser.py (bare-имя из shared dir не даёт core на sys.path).
# · Fix: безусловный bootstrap project root (паттерн deploy_orchestrator TRAP[BUG] 2026-07-31) —
# ·   канонический импорт работает всегда; fallback остаётся defensive.
_SHARED_DIR = os.path.join(
    Path(Path(Path(Path(__file__).resolve()).parent).parent).parent,
    "shared",
)
_PLATFORM_ROOT = os.path.join(
    Path(Path(Path(Path(Path(Path(__file__).resolve()).parent).parent).parent).parent).parent,
)
if _PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, _PLATFORM_ROOT)

# ruff: ignore[PLW0717] — извлечение небезопасно (no-enclosing-def)
try:
    # Канонический node-configs base — shared/deploy_paths (литерал /opt/node-configs не используется)
    from core.internal.shared.deploy_paths import htpasswd_file as _resolve_htpasswd
    from core.internal.shared.deploy_paths import node_configs_remote
    from core.internal.shared.deploy_paths import secrets_env_file as _resolve_secrets_env
    from core.internal.shared.exceptions import (
        ConfigNotFoundError,
        ConfigParseError,
        ConfigValidationError,
    )
    from core.internal.shared.secrets_env_parser import parse as parse_secrets_env
    from core.internal.shared.secrets_env_parser import write as write_secrets_env
    from core.internal.shared.secrets_manifest_reader import iter_secrets as _iter_manifest_secrets

    # W1-A1 (план 170): timeout=30 литералы (bash gen_command / sops --set) → канон SoT
    # CONVERGE_DOCKER_TIMEOUT (30, системные команды) — AMBER-зачистка research-D §D1.
    from core.internal.shared.timeouts import CONVERGE_DOCKER_TIMEOUT
except ModuleNotFoundError:
    if _SHARED_DIR not in sys.path:
        sys.path.insert(0, _SHARED_DIR)
    # 🧐 TRAP[DECISION] · 2026-08-14 · — · importlib вместо `from exceptions import ...` (script-mode fallback) · Rejected: явный `from exceptions import X` · Reason: появление lifecycle/exceptions.py (170 W5-core) сделало неявно-относительный импорт неоднозначным для pyright (resolves к lifecycle.exceptions, где классов нет); importlib резолвит против sys.path (script-mode: _SHARED_DIR) — runtime-семантика 1:1 · Rev: отказ от standalone-режима secrets_manager → вернуть явный импорт
    import importlib

    from deploy_paths import (  # pyright: ignore[reportMissingImports] — W11-G1 cross-file: script-mode fallback import
        htpasswd_file as _impl_htpasswd,  # pyright: ignore[reportUnknownVariableType] — W11-G1 cross-file: script-mode fallback import
    )
    from deploy_paths import (  # pyright: ignore[reportMissingImports] — W11-G1 cross-file: script-mode fallback import
        node_configs_remote as _impl_node_configs,  # pyright: ignore[reportUnknownVariableType] — W11-G1 cross-file: script-mode fallback import
    )
    from deploy_paths import (  # pyright: ignore[reportMissingImports] — W11-G1 cross-file: script-mode fallback import
        secrets_env_file as _impl_secrets_env,  # pyright: ignore[reportUnknownVariableType] — W11-G1 cross-file: script-mode fallback import
    )

    _resolve_htpasswd = cast(Callable[[], Path], _impl_htpasswd)
    node_configs_remote = cast(Callable[[], Path], _impl_node_configs)
    _resolve_secrets_env = cast(Callable[[], Path], _impl_secrets_env)

    _shared_exceptions = importlib.import_module("exceptions")
    ConfigNotFoundError = cast(
        "type[_CanonConfigNotFoundError]", _shared_exceptions.ConfigNotFoundError
    )  # W11-G3: importlib-атрибут (ModuleType → Any), script-mode defensive fallback
    ConfigParseError = cast("type[_CanonConfigParseError]", _shared_exceptions.ConfigParseError)  # W11-G3: см. выше
    ConfigValidationError = cast(
        "type[_CanonConfigValidationError]", _shared_exceptions.ConfigValidationError
    )  # W11-G3: см. выше
    from secrets_env_parser import (  # pyright: ignore[reportMissingImports] — W11-G1 cross-file: script-mode fallback import
        parse as _impl_parse,  # pyright: ignore[reportUnknownVariableType] — W11-G1 cross-file: script-mode fallback import
    )
    from secrets_env_parser import (  # pyright: ignore[reportMissingImports] — W11-G1 cross-file: script-mode fallback import
        write as _impl_write,  # pyright: ignore[reportUnknownVariableType] — W11-G1 cross-file: script-mode fallback import
    )
    from secrets_manifest_reader import (  # pyright: ignore[reportMissingImports] — W11-G1 cross-file: script-mode fallback import
        iter_secrets as _impl_iter,  # pyright: ignore[reportUnknownVariableType] — W11-G1 cross-file: script-mode fallback import
    )
    from timeouts import (  # pyright: ignore[reportMissingImports] — W11-G1 cross-file: script-mode fallback import
        CONVERGE_DOCKER_TIMEOUT as _impl_timeout,  # pyright: ignore[reportUnknownVariableType] — W11-G1 cross-file: script-mode fallback import
    )

    parse_secrets_env = cast(Callable[[str], dict[str, str]], _impl_parse)
    write_secrets_env = cast(Callable[[str, dict[str, str]], None], _impl_write)
    _iter_manifest_secrets = cast(
        "Callable[[str], list[ManifestSecret]]", _impl_iter
    )  # string-форма: ManifestSecret определён ниже по модулю (forward-ref)
    CONVERGE_DOCKER_TIMEOUT = cast(int, _impl_timeout)  # pyright: ignore[reportConstantRedefinition] — W11-G3: script-mode fallback rebinding той же константы (try-ветка — канонический импорт)

logger = logging.getLogger(__name__)


# ── REF-0013: protected allowlist для file-wins применения secrets.env ──
# Эти переменные управляются оркестрацией жизненного цикла (bootstrap/CI/dev-машина) и
# НЕ перезаписываются значениями из secrets.env: AGE-ключ, использованный для расшифровки,
# пути артефактов и идентификатор ноды. Операторские секреты (GHCR_PULL_TOKEN, TELEGRAM_*,
# PLATFORM_MASTER_*, пароли сервисов) — file-wins: свежий decrypt ПЕРЕЗАПИСЫВАЕТ stale
# os.environ (инверсия прежнего `if k not in os.environ`).
_LIFECYCLE_PROTECTED_ENV_VARS: frozenset[str] = frozenset({
    "AGE_SECRET_KEY",
    "AGE_SECRET_KEY_FILE",
    "SOPS_AGE_KEY",
    "SECRETS_FILE",
    "SECRETS_ENV_FILE",
    "NODE_NAME",
    "NODE_CONFIGS_DIR",
    "NODE_YAML",
    "CORE_DIR",
    "PLATFORM_ROOT",
    "TOR_ENABLED",
})


# region FUNC_apply_env_file_to_osenv
## @purpose — Применить распарсенный secrets.env к os.environ в режиме file-wins (REF-0013):
##            значение файла ПЕРЕЗАПИСЫВАЕТ os.environ (свежий decrypt выигрывает у stale env),
##            КРОМЕ _LIFECYCLE_PROTECTED_ENV_VARS — там os.environ сохраняется.
## @io — ⇥ env_vars: Mapping[str, str], label: str (для логов) → ⎋ int (число применённых override)
## @complexity — O(N) where N = vars in env_vars
## @invariants
##   - file-wins: secrets.env значение сильнее существующего os.environ (кроме protected)
##   - Protected-переменные никогда не перезаписываются из файла (AGE_SECRET_KEY и др.)
##   - Значение применяется только если отличается (безшумный no-op при совпадении)
def apply_env_file_to_osenv(env_vars: Mapping[str, str], *, label: str = "secrets.env") -> int:
    """Apply parsed secrets to os.environ with file-wins semantics + protected allowlist."""
    overridden = 0
    protected_kept = 0
    for key, value in env_vars.items():
        if key in _LIFECYCLE_PROTECTED_ENV_VARS:
            if os.environ.get(key, "") != value:
                protected_kept += 1
                logger.info(
                    "[IMP:7][secrets_manager] %s is lifecycle-controlled — keeping os.environ value (not overridden by %s)",
                    key,
                    label,
                )
            continue
        if os.environ.get(key, "") != value:
            os.environ[key] = value
            overridden += 1
    logger.info(
        "[IMP:9][secrets_manager] %s → os.environ: %d applied (file-wins), %d protected kept (%d total)",
        label,
        overridden,
        protected_kept,
        len(env_vars),
    )
    return overridden


# endregion FUNC_apply_env_file_to_osenv


# region FUNC_has_unparsed_content
## @purpose — Проверка «в файле есть значимый нераспарсенный контент» для merge-guard'ов
##            (REF-0013): строки вне комментариев и пустых строк. Comment-only/whitespace
##            файл семантически ПУСТ (парсер даёт {}, терять нечего) — guard НЕ триггерит;
##            мусорные/непарсабельные строки — данные под риском потери → guard триггерит.
## @io — ⇥ path: Path → ⎋ bool
## @complexity — O(N) where N = lines in file
def _has_unparsed_content(path: Path) -> bool:
    """True if file contains non-blank, non-comment lines (meaningful content at risk)."""
    try:
        for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = raw_line.strip()
            if stripped and not stripped.startswith("#"):
                return True
    except OSError:
        return False
    return False


# endregion FUNC_has_unparsed_content


# region FUNC_source_secrets_env
## @purpose — Parse a secrets.env file into a dict. Delegates to shared secrets_env_parser.parse()
##            (DevPlan 086). Preserves backward-compat: returns empty dict on failure (never raises).
## @io — ⇥ secrets_env: path to secrets.env file → ⎋ dict[str, str]
## @complexity — O(N) where N = lines in file (delegated)
## @invariants
##   - Returns empty dict if file not found or unreadable (backward compat wrapper)
##   - Actual parsing logic in shared secrets_env_parser module
def source_secrets_env(secrets_env: str) -> dict[str, str]:
    """Parse secrets.env key=value file into dict. Returns empty dict on failure.
    Delegates to shared secrets_env_parser.parse() (DevPlan 086)."""
    logger.info("[IMP:7][secrets_manager] Delegating source_secrets_env to shared secrets_env_parser.parse()")
    try:
        result = parse_secrets_env(secrets_env)
        logger.info(
            "[IMP:9][secrets_manager] source_secrets_env: parsed %d entries via shared module",
            len(result),
        )
    except FileNotFoundError:
        logger.info("[IMP:7][secrets_manager] Secrets env file not found: %s — returning empty dict", secrets_env)
        return {}
    except (OSError, ValueError) as e:
        logger.warning("[IMP:7][secrets_manager] Cannot read %s: %s — returning empty dict", secrets_env, e)
        return {}
    else:
        return result


# endregion FUNC_source_secrets_env


# region FUNC_cleanup_secrets_env
## @purpose — Read secrets.env, conditionally strip HTTP_PROXY/HTTPS_PROXY when
##            TOR_ENABLED != "true", write back atomically (tmp+rename, 0o600).
##            DevPlan 102 TASK-2 — replaces shell source+sed logic in step_10_decrypt_secrets.
## @io — ⇥ secrets_env_path: str, tor_enabled: str (default "false") → ⎋ dict[str, str]
##       (parsed secrets AFTER cleanup; {} if file missing)
## @complexity — O(N) where N = vars in secrets.env (parse + write delegated)
## @invariants
##   - No-op if file doesn't exist — returns {} without error
##   - Never raises — logs warnings on parse/write I/O errors
##   - Only HTTP_PROXY/HTTPS_PROXY (uppercase) are removed, matching sed behavior
##   - Atomic write via shared secrets_env_parser.write() (tempfile + os.replace, 0o600)
##   - File is NOT rewritten when nothing is removed (byte-identical preservation)
## @rationale — Proxy cleanup was shell sed logic in step_10 (DevPlan 102 P1). Moving to
##              Python makes it testable and reuses the canonical secrets_env_parser.
def cleanup_secrets_env(
    secrets_env_path: str,
    tor_enabled: str = "false",
) -> dict[str, str]:
    """Read secrets.env, conditionally strip proxy vars, write back atomically.

    ▶ ┌secrets_env_path┐ → ◇ parse → ◇ TOR_ENABLED≠"true"? → filter proxy →
      ⊕ atomic write (tmp+rename, 0o600) → ⎋ dict[str, str]

    Returns: parsed secrets dict AFTER cleanup.
    No-op if file doesn't exist (returns empty dict).
    Never raises — logs warnings on I/O errors.
    """
    # DevPlan 123 T6: вход нормализуется в единой точке (Python-bool "True" из node_yaml CLI
    # → lowercase) — сравнение ниже и логи оперируют нормализованным значением.
    tor_enabled = (tor_enabled or "").strip().lower()
    env_path = Path(secrets_env_path)
    if not env_path.is_file():
        logger.info("[IMP:7][secrets_manager] cleanup: %s not found — no-op", secrets_env_path)
        return {}

    try:
        env_vars = parse_secrets_env(str(env_path))
    except (OSError, ValueError) as e:
        logger.warning("[IMP:7][secrets_manager] cleanup: cannot parse %s: %s", secrets_env_path, e)
        return {}

    removed: list[str] = []
    if tor_enabled != "true":
        for proxy_var in ("HTTP_PROXY", "HTTPS_PROXY"):
            if proxy_var in env_vars:
                del env_vars[proxy_var]
                removed.append(proxy_var)

    if removed:
        try:
            write_secrets_env(str(env_path), env_vars)
        except (OSError, TypeError) as e:
            logger.warning("[IMP:7][secrets_manager] cleanup: cannot write %s: %s", secrets_env_path, e)
            return {}
        logger.info(
            "[IMP:9][secrets_manager] cleanup: removed %s from %s (TOR_ENABLED=%s)",
            ", ".join(removed),
            secrets_env_path,
            tor_enabled,
        )
    else:
        logger.info(
            "[IMP:8][secrets_manager] cleanup: no proxy vars to remove in %s (TOR_ENABLED=%s)",
            secrets_env_path,
            tor_enabled,
        )

    return env_vars


# endregion FUNC_cleanup_secrets_env


if TYPE_CHECKING:
    # W11-G3: канонические классы исключений — target типов для script-mode cast'ов ниже
    from core.internal.shared.exceptions import (
        ConfigNotFoundError as _CanonConfigNotFoundError,
    )
    from core.internal.shared.exceptions import (
        ConfigParseError as _CanonConfigParseError,
    )
    from core.internal.shared.exceptions import (
        ConfigValidationError as _CanonConfigValidationError,
    )


# region TYPEDEF_ManifestSecret
class ManifestSecret(TypedDict):
    """Одна запись secrets-manifest.yaml (tier=generated), W11-G3.

    ## @purpose — Типизированная граница YAML-манифеста (замена list[dict[str, Any]]).
    ## @complexity — O(1) — декларация
    """

    name: str
    gen_command: str
    tier: str


# endregion TYPEDEF_ManifestSecret


# region FUNC__read_manifest
## @purpose — Read secrets-manifest.yaml and extract tier=generated secrets as a list of dicts.
##            Delegates to shared secrets_manifest_reader.iter_secrets (DevPlan 116 T4, U-33).
##            STRICT: missing/malformed manifest RAISES — hardcoded fallback list removed
##            (invariant 7 — fail-visible instead of silent divergence).
## @io — ⇥ manifest_path: str → ⎋ list[dict[str, Any]] ⚡ raise FileNotFoundError/ValueError
## @complexity — O(N) where N = YAML document size (delegated)
## @invariants
##   - Raises on missing/malformed manifest (no `return []` fallback)
##   - Filters only entries with tier == "generated"
##   - Each entry requires name and gen_command keys
def _read_manifest(manifest_path: str) -> list[ManifestSecret]:
    """Read secrets-manifest.yaml, return tier=generated secrets. Raises on missing manifest."""
    logger.info("[IMP:8][secrets_manager] Reading generated secrets from manifest: %s", manifest_path)
    secrets = _iter_manifest_secrets(manifest_path)  # W11-G1 cross-file: iter_secrets → list[dict[str, Any]]
    generated = cast(
        "list[ManifestSecret]",
        [s for s in secrets if s.get("tier") == "generated" and s.get("name") and s.get("gen_command")],
    )  # W11-G1 cross-file: reader возвращает dict[str, Any] (G1-модуль не правим)
    logger.info("[IMP:9][secrets_manager] Manifest has %d tier=generated secrets", len(generated))
    return generated


# endregion FUNC__read_manifest


# region FUNC__generate_secret
## @purpose — Generate a single secret value via subprocess. Executes gen_command as a bash command.
## @io — ⇥ var_name: str, gen_command: str → ⎋ str | None (None on failure)
## @complexity — O(1) + subprocess
## @invariants
##   - Returns None on any failure (never raises)
##   - Uses bash -c with 30s timeout
##   - Strips trailing newline from output
def _generate_secret(var_name: str, gen_command: str) -> str | None:
    """Generate a secret value via subprocess. Returns None on failure."""
    logger.info("[IMP:8][secrets_manager] Generating %s via: %s", var_name, gen_command)
    # ruff: ignore[PLW0717] — тело try присваивает имена, читаемые except/после — извлечение ломает видимость
    try:
        result = subprocess.run(
            ["bash", "-c", gen_command], capture_output=True, text=True, timeout=CONVERGE_DOCKER_TIMEOUT, check=False
        )
        if result.returncode != 0:
            logger.warning(
                "[IMP:7][secrets_manager] gen_command for %s failed (exit=%d): %s",
                var_name,
                result.returncode,
                result.stderr.strip()[:200],
            )
            return None
        value = result.stdout.strip()
        if not value:
            logger.warning("[IMP:7][secrets_manager] gen_command for %s returned empty", var_name)
            return None
        logger.info("[IMP:9][secrets_manager] Generated %s successfully", var_name)
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:7][secrets_manager] gen_command for %s timed out", var_name)
        return None
    except FileNotFoundError as e:
        logger.warning("[IMP:7][secrets_manager] Command not found for %s: %s", var_name, e)
        return None
    except OSError as e:
        logger.warning("[IMP:7][secrets_manager] OS error generating %s: %s", var_name, e)
        return None
    else:
        return value


# endregion FUNC__generate_secret


# region FUNC__persist_to_sops
## @purpose — Persist a single generated secret to the SOPS-encrypted file via sops --set.
##            Non-fatal: logs warning on failure.
## @io — ⇥ var_name: str, var_value: str, enc_file: str → ⎋ bool
## @complexity — O(1) + subprocess
## @invariants
##   - Returns False on any failure (never raises)
##   - Requires enc_file to exist and sops binary to be available
def _persist_to_sops(var_name: str, var_value: str, enc_file: str) -> bool:
    """Persist a secret to SOPS via sops --set. Returns True on success."""
    if not var_value:
        return False
    if not os.path.isfile(enc_file):
        logger.warning(
            "[IMP:7][secrets_manager] sops enc file not found: %s — generated secrets NOT persisted",
            enc_file,
        )
        return False
    try:
        result = subprocess.run(
            ["sops", "--set", f'["{var_name}"] "{var_value}"', enc_file],
            capture_output=True,
            text=True,
            timeout=CONVERGE_DOCKER_TIMEOUT,
            check=False,
        )
        if result.returncode != 0:
            logger.warning(
                "[IMP:7][secrets_manager] sops --set failed for %s — value in env but NOT persisted: %s",
                var_name,
                result.stderr.strip()[:200],
            )
            return False
        logger.info("[IMP:9][secrets_manager] sops --set succeeded for %s", var_name)
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:7][secrets_manager] sops --set timed out for %s", var_name)
        return False
    except FileNotFoundError:
        logger.warning("[IMP:7][secrets_manager] sops binary not found — generated secrets NOT persisted")
        return False
    except OSError as e:
        logger.warning("[IMP:7][secrets_manager] sops --set OS error for %s: %s", var_name, e)
        return False
    else:
        return True


# endregion FUNC__persist_to_sops


# region FUNC_ensure_secrets
## @purpose — Main entrypoint: read manifest (or fallback), generate missing tier=generated secrets,
##            set them in os.environ, persist to secrets.env and optionally to SOPS.
##            Autogen master credentials when missing (DevPlan 156 W1, before htpasswd).
##            After all secrets, calls _ensure_htpasswd() for status-page auth.
## @io — ⇥ manifest_path: str, secrets_env: str, persist_to_sops: bool → ⎋ list[str] (generated var names)
## @complexity — O(N * M) where N = secrets to check, M = subprocess per generation
## @invariants
##   - Never raises — returns partial list on failure; ЕДИНСТВЕННОЕ исключение (REF-0013):
##     merge-guard Step 3.5 → ConfigValidationError, если secrets.env непуст на диске,
##     но распарсился в 0 записей (перезапись операторских секретов запрещена)
##   - Existing secrets (already in os.environ or secrets.env) are NOT overwritten
##   - Appends generated VAR=VALUE pairs to secrets_env file
##   - sops persistence is optional (persist_to_sops param)
##   - Calls _ensure_htpasswd after all secrets generated
def ensure_secrets(
    manifest_path: str = "",
    secrets_env: str | None = None,
    persist_to_sops: bool = True,
) -> list[str]:
    """Ensure all required secrets exist. Generates missing ones. Returns list of generated names."""
    if secrets_env is None:
        secrets_env = str(_resolve_secrets_env())
    generated: list[str] = []

    # ── Step 1: Source existing secrets.env into os.environ (file-wins — REF-0013) ──
    # ⚠️ TRAP[BUG] · 2026-08-24 · P1 · REF-0013 · stale os.environ обыгрывал свежий decrypt
    # · Symptom: после повторной расшифровки secrets.env содержал НОВЫЕ значения, но
    # ·   `if key not in os.environ: os.environ[key] = value` оставлял в env СТАРЫЕ —
    # ·   downstream-фазы читали протухшие секреты, расходящиеся с файлом на диске.
    # · Root: inverted precedence — env-win вместо file-win.
    # · Fix: apply_env_file_to_osenv — файл выигрывает, кроме protected lifecycle-переменных.
    env_vars = source_secrets_env(secrets_env)
    apply_env_file_to_osenv(env_vars, label=secrets_env)

    # ── Step 2: Read manifest for tier=generated secrets (STRICT — raises if missing) ──
    # Hardcoded fallback list не используется: manifest всегда доставляется с core/ —
    # тихий fallback был drift-вектором («gate зелёный, система врёт»).
    secrets_to_process: list[ManifestSecret] = _read_manifest(manifest_path)
    logger.info(
        "[IMP:9][secrets_manager] Processing %d generated secrets from manifest",
        len(secrets_to_process),
    )

    # ⚠️ TRAP[BUG] · 2026-07-25 · P1 · Append-mode → duplicate secrets on repeated --force runs
    # · Symptom: secrets.env grew with duplicate lines (same VAR=value appended on each run).
    # ·   `source secrets.env` reads the LAST occurrence → first bootstrap's key lost.
    # · Root: `open(secrets_env, "a")` in per-secret loop (line 312, old code). Each generated
    # ·   secret was appended individually. On --force re-run, os.environ was empty → all 7
    # ·   secrets regenerated → appended AGAIN. After 3 runs: 21 lines, 3 values per key.
    # · Fix (DevPlan 072): collect all generated values → merge with existing env_vars →
    # ·   atomic write (tmp + rename). Single `open(..., "w")`, not per-secret append.
    # · Prevention: test_ensure_secrets_idempotent verifies file unchanged after 3 calls.
    # ── Step 3: For each secret, check if present; if not, generate ──
    # 💼 TRAP[BUSINESS] · 2026-07-25 · HI · Secrets overwrite MUST preserve non-generated entries
    generated_vars: dict[str, str] = {}
    for secret in secrets_to_process:
        var_name: str = secret["name"]
        gen_command: str = secret.get("gen_command", "")

        # Check existing env var
        current = os.environ.get(var_name, "")
        if current:
            logger.info("[IMP:8][secrets_manager] %s already set — skipping", var_name)
            continue

        if not gen_command:
            logger.warning("[IMP:7][secrets_manager] %s has no gen_command — skipping", var_name)
            continue

        value = _generate_secret(var_name, gen_command)
        if value is None:
            logger.warning("[IMP:7][secrets_manager] Failed to generate %s — continuing", var_name)
            continue

        # Set in os.environ
        os.environ[var_name] = value
        generated.append(var_name)
        generated_vars[var_name] = value

        logger.info(
            "[IMP:9][secrets_manager] Auto-generated %s (MUST be added to SOPS for production)",
            var_name,
        )

    # ── Step 3.5: Atomic overwrite — merge existing + generated → write once ──
    if generated_vars:
        # ── Merge-guard (REF-0013): непустой файл, распарсившийся в 0 записей ──
        # ⚠️ TRAP[BUG] · 2026-08-24 · P0 · REF-0013 · merge-from-parsed-copy уничтожал операторские секреты
        # · Symptom: decrypt/source вернул {} при непустом secrets.env на диске (сбой парсинга,
        # ·   пустой результат расшифровки) → Step 3.5 атомарно записывал `{} + generated` —
        # ·   GHCR_PULL_TOKEN/TELEGRAM_*/PLATFORM_MASTER_* необратимо терялись.
        # · Root: merge строился от parsed-копии без сверкой с фактом файла на диске.
        # · Fix: guard ДО записи — не-empty файл + 0 распарсенных записей → abort (ConfigValidationError);
        # ·   файл остаётся нетронутым, φ4 получает FATAL через phase-обёртку.
        # · Prevention: tests/unit/test_secrets_merge_guard.py.
        secrets_path = Path(secrets_env)
        file_has_content = secrets_path.is_file() and _has_unparsed_content(secrets_path)
        if not env_vars and file_has_content:
            logger.error(
                "[IMP:10][secrets_manager] MERGE-GUARD: %s has unparsed non-comment content but parsed to 0 entries — "
                "aborting BEFORE atomic overwrite (existing secrets preserved)",
                secrets_env,
            )
            msg = (
                f"Merge-guard: {secrets_env} is non-empty but parsed to 0 entries — "
                "refusing to overwrite operator secrets with generated-only set"
            )
            raise ConfigValidationError(msg)

        # ruff: ignore[PLW0717] — try вложен в условный блок внутри функции — после-try чтение локалей неанализируемо
        try:
            secrets_path.parent.mkdir(parents=True, exist_ok=True)

            # Build the complete env file content: existing + newly generated
            # env_vars from Step 1 already contains ALL existing entries
            merged: dict[str, str] = dict(env_vars)  # copy existing (non-generated + previously generated)
            merged.update(generated_vars)  # add/overwrite newly generated

            # Atomic write: write to tmp, then rename
            tmp_path = secrets_path.with_suffix(".env.tmp")
            with Path(tmp_path).open("w", encoding="utf-8") as f:
                for key, val in merged.items():
                    f.write(f"{key}={val}\n")

            # Preserve file permissions if file exists
            if secrets_path.exists():
                existing_mode = secrets_path.stat().st_mode
                tmp_path.chmod(existing_mode)
            else:
                tmp_path.chmod(0o600)

            tmp_path.replace(secrets_path)
            logger.info(
                "[IMP:9][secrets_manager] Atomic write: %d entries → %s (%d new)",
                len(merged),
                secrets_env,
                len(generated_vars),
            )
        except OSError as e:
            logger.warning(
                "[IMP:7][secrets_manager] Cannot write secrets.env: %s — "
                "secrets are in os.environ but NOT persisted to file",
                e,
            )

    # ── Step 4: sops --set persistence (optional, non-fatal) ──
    if persist_to_sops and generated:
        node_configs_dir = os.environ.get("NODE_CONFIGS_DIR", str(node_configs_remote()))
        node_name = os.environ.get("NODE_NAME", "")
        if node_name:
            enc_file = os.path.join(node_configs_dir, "secrets", f"{node_name}.enc.yaml")
            for gvar in generated:
                gval = os.environ.get(gvar, "")
                if gval:
                    _persist_to_sops(gvar, gval, enc_file)
        else:
            logger.info(
                "[IMP:7][secrets_manager] NODE_NAME not set — skipping sops persistence",
            )

    # ── Step 4.5: Autogen master credentials (DevPlan 156 W1; 176 B.3 random-autogen) ──
    # Дефолты при первом разворачивании (решение пользователя 2026-08-15 инвертирует решение
    # 2026-08-12 — H3): мастер-креды tier=required/source=sops отсутствуют в SOPS → autogen
    # (admin@<домен> + СЛУЧАЙНЫЙ пароль secrets.token_urlsafe(32), не дата-префикс) +
    # persist в secrets.env (идемпотентно).
    # Step 5 теперь гарантированно находит креды → создаёт .htpasswd-platform.
    _ensure_master_credentials(secrets_env)

    # ── Step 4.6: Per-secret autogen сервис-паролей (DevPlan 176 B.8, M7) ──
    # HERMES_DASHBOARD_PASSWORD/GF_SECURITY_ADMIN_PASSWORD/LANGFUSE_INIT_USER_PASSWORD
    # получают СОБСТВЕННЫЕ случайные значения при первом bootstrap (разрыв unified-auth
    # конвенции — единый пароль = blast radius). LANGFUSE_INIT_USER_PASSWORD обычно уже
    # сгенерирован manifest-механизмом (Step 3, tier=generated) → skip (no-op).
    _ensure_derived_passwords(secrets_env)

    # ── Step 5: Generate htpasswd (needs PLATFORM_MASTER_PASSWORD) ──
    _ensure_htpasswd(secrets_env)

    if generated:
        logger.info(
            "[IMP:9][secrets_manager] Generated %d secrets: %s",
            len(generated),
            ", ".join(generated),
        )
        logger.info(
            "[IMP:7][secrets_manager] These are EPHEMERAL — re-encrypt SOPS with real values for production",
        )
    else:
        logger.info("[IMP:9][secrets_manager] All required secrets present — nothing to generate")

    return generated


# endregion FUNC_ensure_secrets


# region FUNC__write_htpasswd_file
def _write_htpasswd_file(
    email: str,
    password: str,
    htpasswd_file: str | None = None,
) -> bool:
    """Lazy facade for htpasswd.write_htpasswd_file (DevPlan 117 G T58.3)."""
    from core.internal.bootstrap.lifecycle.htpasswd import write_htpasswd_file as _impl

    if htpasswd_file is None:
        htpasswd_file = str(_resolve_htpasswd())
    return _impl(email, password, htpasswd_file)


# endregion FUNC__write_htpasswd_file


# region FUNC__resolve_platform_domain
## @purpose — Резолв домена платформы для autogen master-email (DevPlan 156 W1): порядок
##            PLATFORM_DOMAIN env (контекстный, подставлен при деплое) → node.yaml#domain
##            (NODE_YAML env → канон node_resolver.resolve_node_yaml, DevPlan 127 W2) →
##            fallback ai-platform.local (env_defaults канон T3). Best-effort: любая ошибка
##            резолва → warning + fallback (НЕ роняет φ4).
## @io — ⇥ → ⎋ str (непустой домен; "ai-platform.local" — крайний fallback)
## @complexity — O(N) — один YAML parse максимум
## @invariants
##   - PLATFORM_DOMAIN env имеет приоритет (контексты с subdomain-платформой, риск-лист плана)
##   - NODE_YAML env (существующий файл) проверяется ДО node_resolver (план-контракт 3-path)
##   - Ошибки резолва → warning IMP:7 + fallback — никогда не raise (non-fatal дизайн модуля)
def _resolve_platform_domain() -> str:
    """Resolve домен платформы: PLATFORM_DOMAIN env → node.yaml#domain → ai-platform.local."""
    domain = os.environ.get("PLATFORM_DOMAIN", "").strip()
    if domain:
        logger.info("[IMP:8][secrets_manager] Platform domain from PLATFORM_DOMAIN env: %s", domain)
        return domain

    node_yaml_path = os.environ.get("NODE_YAML", "").strip()
    # ruff: ignore[PLW0717] — try-тело содержит return-ветки с fall-through (после-try код) — извлечение небезопасно
    try:
        from core.internal.shared.node_yaml import NodeYaml

        if node_yaml_path and os.path.isfile(node_yaml_path):
            domain = str(NodeYaml(node_yaml_path).get("domain", default="") or "").strip()
            if domain:
                logger.info("[IMP:8][secrets_manager] Platform domain from NODE_YAML: %s", domain)
                return domain
        from core.internal.shared.node_resolver import resolve_node_yaml

        yaml_path = resolve_node_yaml()
        domain = str(NodeYaml(yaml_path).get("domain", default="") or "").strip()
        if domain:
            logger.info("[IMP:8][secrets_manager] Platform domain from node.yaml: %s", domain)
            return domain
        logger.warning("[IMP:7][secrets_manager] node.yaml has no top-level 'domain' — using fallback")
    except (ImportError, ConfigNotFoundError, ConfigParseError, ConfigValidationError, OSError, ValueError) as e:
        logger.warning(
            "[IMP:7][secrets_manager] Cannot resolve platform domain from node.yaml: %s — using fallback",
            e,
        )

    logger.info("[IMP:7][secrets_manager] Platform domain fallback: ai-platform.local")
    return "ai-platform.local"


# endregion FUNC__resolve_platform_domain


# region FUNC__ensure_master_credentials
## @purpose — Autogen PLATFORM_MASTER_EMAIL/PLATFORM_MASTER_PASSWORD при первом bootstrap
##            (DevPlan 156 W1; 176 B.3). Закрывает инцидент 2026-08-12 (asi-team-vps htpasswd не создан:
##            креды tier=required/source=sops, в SOPS их нет → ensure_htpasswd «not set — skipping»).
##            По решению пользователя 2026-08-15 (инвертирует решение 2026-08-12 — H3): дефолты при
##            первом разворачивании — НОРМАЛЬНОЕ поведение; email = admin@«домен платформы»,
##            password = СЛУЧАЙНЫЙ secrets.token_urlsafe(32) (алфавит [A-Za-z0-9_-] ⊂ charset
##            ^[A-Za-z0-9._-]+$ — предсказуемый test-master-password-«дата» закрыт).
##            Идемпотентность: persist в secrets.env → повторный bootstrap = no-op
##            (сгенерированное значение НЕ перегенерируется); ротация — только через SOPS.
## @io — ⇥ secrets_env: str — путь к secrets.env → ⎋ None (non-fatal, никогда не raise)
## @complexity — O(N) — parse + merge + atomic write
## @invariants
##   - Non-fatal: ошибки записи → warning IMP:7, значения остаются в os.environ
##     (Step 5 _ensure_htpasswd читает именно env → htpasswd гарантированно создаётся)
##   - Оба значения уже заданы (env или secrets.env) → no-op (IMP:8), возврат
##   - Email: admin@<PLATFORM_DOMAIN | node.yaml#domain | ai-platform.local> (_resolve_platform_domain)
##   - Password: СЛУЧАЙНЫЙ secrets.token_urlsafe(32) — алфавит ⊂ charset ^[A-Za-z0-9._-]+$
##   - Persist: merge + atomic write (tmp + replace, chmod 0o600 — паттерн Step 3.5) — генерация ОДНОКРАТНАЯ
##   - Существующие значения НЕ перезаписываются (инвариант 2 модуля)
# region FUNC_persist_new_vars
## @purpose  Общий персист autogen-наборов — merge + atomic write (tmp + replace, chmod 0o600).
##           (T3.6: имя _plw_body_* снято — у функции ДВА call-site, это легитимный хелпер,
##           не scaffolding-экстракция.)
##           Общий персист для autogen-функций (master creds, derived passwords) — дублирование
##           merge-логики было бы debt (паттерн Step 3.5 дублируется здесь умышленно — Step 3.5
##           оперирует manifest-generated набором, этот — autogen-наборами).
## @io       ⇥ log_label, new_vars, parse_secrets_env, secrets_env → ⎋ None (persist или raise OSError/ValueError)
## @complexity O(N) — merge + файловый I/O
def _persist_new_vars(
    log_label: str,
    new_vars: dict[str, str],
    parse_secrets_env: Callable[[str], dict[str, str]],
    secrets_env: str,
) -> None:
    secrets_path = Path(secrets_env)
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    env_vars = parse_secrets_env(secrets_env)
    # ── Merge-guard (REF-0013): тот же инвариант, что Step 3.5 — файл со значимым
    # нераспарсенным контентом (вне комментариев) не даёт себя перезаписать autogen-набором.
    # Comment-only/blank файл семантически пуст (парсер-контракт: only-comments → {}) —
    # перезапись легитимна (DevPlan 176 flow). ConfigValidationError (typed hierarchy,
    # static-detector contract): вызывающие ловят (OSError, ConfigValidationError)
    # и остаются non-fatal по дизайну autogen — файл НЕ тронут, значения живут в os.environ,
    # проблема видна громким warning вместо тихой потери содержимого.
    if not env_vars and secrets_path.is_file() and _has_unparsed_content(secrets_path):
        msg = (
            f"Merge-guard: {secrets_env} has unparsed non-comment content but parsed to 0 entries — "
            f"refusing to persist {log_label} over unparsed content"
        )
        raise ConfigValidationError(msg)
    merged: dict[str, str] = dict(env_vars)
    merged.update(new_vars)
    tmp_path = secrets_path.with_suffix(".env.tmp")
    with Path(tmp_path).open("w", encoding="utf-8") as f:
        for key, val in merged.items():
            f.write(f"{key}={val}\n")
    if secrets_path.exists():
        tmp_path.chmod(secrets_path.stat().st_mode)
    else:
        tmp_path.chmod(0o600)
    tmp_path.replace(secrets_path)
    logger.info(
        "[IMP:9][secrets_manager] %s auto-generated (values hidden) — persisted to %s",
        log_label,
        secrets_env,
    )


# endregion FUNC_persist_new_vars


def _ensure_master_credentials(secrets_env: str) -> None:
    """Autogen master credentials при первом bootstrap (СЛУЧАЙНЫЙ пароль, idempotent, non-fatal).

    ▶ ┌secrets_env┐ → ◇ оба заданы? → ⎋ no-op → ⊕ email=admin@<домен> → ⊕ pwd=token_urlsafe(32) →
      ⊕ os.environ + merge + atomic write (0o600)
    """
    current_email = os.environ.get("PLATFORM_MASTER_EMAIL", "")
    current_password = os.environ.get("PLATFORM_MASTER_PASSWORD", "")
    if current_email and current_password:
        logger.info(
            "[IMP:8][secrets_manager] PLATFORM_MASTER_EMAIL/PASSWORD already set — master credentials no-op",
        )
        return

    new_vars: dict[str, str] = {}
    if not current_email:
        new_vars["PLATFORM_MASTER_EMAIL"] = f"admin@{_resolve_platform_domain()}"
    if not current_password:
        new_vars["PLATFORM_MASTER_PASSWORD"] = _random_autogen_password()

    # os.environ обновляется всегда — Step 5 (_ensure_htpasswd) читает только env
    for key, value in new_vars.items():
        os.environ[key] = value

    # Persist в secrets.env: merge + atomic write (тот же паттерн, что Step 3.5)
    try:
        _persist_new_vars("Master credentials", new_vars, parse_secrets_env, secrets_env)
    except (OSError, ConfigValidationError, ValueError) as e:
        logger.warning(
            "[IMP:7][secrets_manager] Cannot persist master credentials to %s: %s — "
            "values are in os.environ but NOT in secrets.env",
            secrets_env,
            e,
        )


# endregion FUNC__ensure_master_credentials


# region FUNC__random_autogen_password
## @purpose — Случайный пароль для autogen-секретов (H3/B.8, DevPlan 176): secrets.token_urlsafe.
##            Алфавит urlsafe-base64 [A-Za-z0-9_-] — подмножество канонического charset
##            ^[A-Za-z0-9._-]+$ (secret-definitions.yaml) — всегда charset-конформен.
## @io — ⇥ nbytes: int (default 32) → ⎋ str (len ≈ 43 для nbytes=32)
## @complexity — O(1) — CSPRNG (secrets)
## @invariants
##   - Возвращаемое значение НЕ содержит +/= (urlsafe-base64 без padding) — charset соблюдён
##   - CSPRNG (secrets) — непредсказуемость (H3: детерминированный test-master-password-«дата»
##     закрыт — ≤31 попытка перебора больше невозможна)
def _random_autogen_password(nbytes: int = 32) -> str:
    """Случайный charset-конформный пароль (secrets.token_urlsafe, DevPlan 176 B.3/B.8).

    ▶ secrets.token_urlsafe(nbytes) → [A-Za-z0-9_-] → ⎋ str (⊂ ^[A-Za-z0-9._-]+$)
    """
    value = secrets.token_urlsafe(nbytes)
    logger.info(
        "[IMP:9][secrets_manager] Random autogen password generated (nbytes=%d, length=%d)",
        nbytes,
        len(value),
    )
    return value


# endregion FUNC__random_autogen_password


# 🧐 TRAP[DECISION] · 2026-08-16 · — · per-secret autogen сервис-паролей локализован в secrets_manager
# (паттерн мастер-кредов) · Rejected: tier=generated + gen_command в secret-definitions.yaml для HERMES/GF ·
# Reason: required/sops секреты НЕ обрабатываются manifest-генератором (только tier=generated); перевод
# в generated меняет семантику env_chain/compose-дефолтов (${VAR:-} → required) ·
# Rev: если сервис-пароли переведут в manifest-конвенцию (tier=generated) — удалить локальный механизм
# region FUNC__ensure_derived_passwords
## @purpose — Per-secret autogen сервис-паролей (DevPlan 176 B.8, M7): HERMES_DASHBOARD_PASSWORD/
##            GF_SECURITY_ADMIN_PASSWORD/LANGFUSE_INIT_USER_PASSWORD получают СОБСТВЕННЫЕ случайные
##            значения при первом bootstrap — разрыв unified-auth конвенции (.env.example:62-69:
##            единый пароль для master/langfuse/hermes/grafana = blast radius). LANGFUSE_INIT_USER_PASSWORD
##            обычно уже сгенерирован manifest-механизмом (tier=generated, Step 3) → skip (no-op).
## @io — ⇥ secrets_env: str — путь к secrets.env → ⎋ None (non-fatal, никогда не raise)
## @complexity — O(N) — parse + merge + atomic write
## @invariants
##   - Non-fatal: ошибки записи → warning IMP:7, значения остаются в os.environ
##   - Существующие значения (env или secrets.env) НЕ перезаписываются (инвариант 2 модуля)
##   - Каждое значение — собственный _random_autogen_password() (не копия PLATFORM_MASTER_PASSWORD)
##   - Persist: merge + atomic write (tmp + replace, chmod 0o600) — генерация ОДНОКРАТНАЯ
_DERIVED_PASSWORD_VARS: tuple[str, ...] = (
    "HERMES_DASHBOARD_PASSWORD",
    "GF_SECURITY_ADMIN_PASSWORD",
    "LANGFUSE_INIT_USER_PASSWORD",
)


def _ensure_derived_passwords(secrets_env: str) -> None:
    """Autogen per-secret паролей HERMES/GF/LANGFUSE при первом bootstrap (idempotent, non-fatal).

    ▶ ┌secrets_env┐ → ○ var ∈ (HERMES, GF, LANGFUSE): ◇ задан (env/файл)? → ⎋ skip →
      ⊕ value = token_urlsafe(32) → ⊕ os.environ + merge + atomic write (0o600)
    """
    new_vars: dict[str, str] = {}
    for var_name in _DERIVED_PASSWORD_VARS:
        if os.environ.get(var_name, ""):
            logger.info("[IMP:8][secrets_manager] %s already set — derived autogen skip", var_name)
            continue
        new_vars[var_name] = _random_autogen_password()
        logger.info("[IMP:9][secrets_manager] Auto-generated %s (per-secret random, DevPlan 176 B.8)", var_name)

    if not new_vars:
        return

    # os.environ обновляется всегда — потребители (compose env_requires) читают env после φ4
    for key, value in new_vars.items():
        os.environ[key] = value

    # Persist в secrets.env: merge + atomic write (тот же паттерн, что master creds)
    try:
        _persist_new_vars("Derived service passwords", new_vars, parse_secrets_env, secrets_env)
    except (OSError, ConfigValidationError, ValueError) as e:
        logger.warning(
            "[IMP:7][secrets_manager] Cannot persist derived passwords to %s: %s — "
            "values are in os.environ but NOT in secrets.env",
            secrets_env,
            e,
        )


# endregion FUNC__ensure_derived_passwords


# region FUNC__ensure_htpasswd
def _ensure_htpasswd(
    secrets_env: str | None = None,
    htpasswd_file: str | None = None,
) -> bool:
    """Lazy facade for htpasswd.ensure_htpasswd (DevPlan 117 G T58.3)."""
    from core.internal.bootstrap.lifecycle.htpasswd import ensure_htpasswd as _impl

    if secrets_env is None:
        secrets_env = str(_resolve_secrets_env())
    if htpasswd_file is None:
        htpasswd_file = str(_resolve_htpasswd())
    return _impl(secrets_env, htpasswd_file)


# endregion FUNC__ensure_htpasswd


# region FUNC_CLI
## @purpose — CLI entrypoint for ensure/source/cleanup/htpasswd actions.
##            Parses argparse subcommands, dispatches to the matching function.
##            Usage:
##              python3 secrets_manager.py ensure [--manifest <path>] [--secrets-env <path>]
##              python3 secrets_manager.py source [--secrets-env <path>]
##              python3 secrets_manager.py cleanup --secrets-env <path> [--tor-enabled <true|false>]
##              python3 secrets_manager.py htpasswd --email <e> --password <p> [--htpasswd-file <path>]
## @io — ⇥ sys.argv → ⎋ None (exits with 0 on success, 1 on error)
## @complexity — O(1) dispatch
## @invariants
##   - cleanup: exit 0 + "OK"/"SKIP" on success; exit 1 on missing/unreadable file
##   - htpasswd: exit 0 on success, exit 1 on generation failure
##   - ensure/source: exit 0 (source prints KEY=VALUE lines to stdout)
if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )

    parser = argparse.ArgumentParser(description="Secrets Manager — ensure/source/cleanup/htpasswd secrets")
    subparsers = parser.add_subparsers(dest="action", required=True)

    ensure_parser = subparsers.add_parser("ensure", help="Generate missing tier=generated secrets")
    ensure_parser.add_argument(
        "--manifest", required=True, help="Path to secrets-manifest.yaml (required — fail-fast, DevPlan 116 T4)"
    )
    ensure_parser.add_argument("--secrets-env", default=None, help="Path to secrets.env")

    source_parser = subparsers.add_parser("source", help="Print parsed secrets.env KEY=VALUE lines to stdout")
    source_parser.add_argument("--secrets-env", default=None, help="Path to secrets.env")

    cleanup_parser = subparsers.add_parser(
        "cleanup", help="Strip HTTP_PROXY/HTTPS_PROXY from secrets.env when TOR_ENABLED != true"
    )
    cleanup_parser.add_argument("--secrets-env", default=None, help="Path to secrets.env")
    cleanup_parser.add_argument("--tor-enabled", default="false", help="TOR_ENABLED flag (true keeps proxy vars)")

    htpasswd_parser = subparsers.add_parser("htpasswd", help="Generate .htpasswd-platform from explicit credentials")
    htpasswd_parser.add_argument("--email", required=True, help="Username/email for htpasswd entry")
    htpasswd_parser.add_argument("--password", required=True, help="Password for htpasswd entry")
    htpasswd_parser.add_argument("--htpasswd-file", default=None, help="Target htpasswd file path")

    class _CliArgs(argparse.Namespace):
        """Типизированный argparse-Namespace (W11-G3): parse_args(namespace=...).

        ## @purpose — Устраняет Any-каскад argparse (Namespace-атрибуты) в CLI-блоке.
        ## @invariants — ТОЛЬКО аннотации БЕЗ значений: argparse заполняет дефолты из
        ##              add_argument (class-значения ломали бы hasattr-defaults).
        ## @complexity — O(1) — декларация полей
        """

        def __init__(self) -> None:
            super().__init__()
            self.action: str
            self.manifest: str  # ensure (required — argparse гарантирует значение)
            self.secrets_env: str | None
            self.tor_enabled: str
            self.email: str  # htpasswd (required)
            self.password: str  # htpasswd (required)
            self.htpasswd_file: str | None

    args = parser.parse_args(namespace=_CliArgs())

    if args.action == "ensure":
        generated = ensure_secrets(args.manifest, args.secrets_env)
        if generated:
            print(f"Generated: {','.join(generated)}")
    elif args.action == "source":
        env_vars = source_secrets_env(args.secrets_env or str(_resolve_secrets_env()))
        for k, v in env_vars.items():
            print(f"{k}={v}")
    elif args.action == "cleanup":
        if not Path(args.secrets_env or str(_resolve_secrets_env())).is_file():
            print(f"SKIP: file not found: {args.secrets_env}", file=sys.stderr)
            sys.exit(1)
        try:
            before = parse_secrets_env(args.secrets_env or str(_resolve_secrets_env()))
        except (OSError, ValueError) as e:
            print(f"ERROR: cannot read {args.secrets_env}: {e}", file=sys.stderr)
            sys.exit(1)
        after = cleanup_secrets_env(args.secrets_env or str(_resolve_secrets_env()), args.tor_enabled)
        # DevPlan 123 T6: args.tor_enabled приходит из shell-строки (lib/secrets.sh --tor-enabled
        # "${TOR_ENABLED:-false}") — нормализуем сравнение вместо строгого ==/!=
        if (args.tor_enabled or "").lower() != "true" and ("HTTP_PROXY" in before or "HTTPS_PROXY" in before):
            if "HTTP_PROXY" in after or "HTTPS_PROXY" in after:
                print(f"ERROR: proxy vars still present after cleanup: {args.secrets_env}", file=sys.stderr)
                sys.exit(1)
            print("OK")
        else:
            print("SKIP")
    elif args.action == "htpasswd":
        ok = _write_htpasswd_file(args.email, args.password, args.htpasswd_file)
        if not ok:
            print("ERROR: htpasswd generation failed", file=sys.stderr)
            sys.exit(1)

    sys.exit(0)
# endregion FUNC_CLI
