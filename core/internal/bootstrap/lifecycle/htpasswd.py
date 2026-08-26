#!/usr/bin/env python3
# GREP_SUMMARY: htpasswd, apr1, salt, idempotent, write-htpasswd-file, ensure-htpasswd, extract-salt, status-page-auth
# STRUCTURE: ▶ extract_apr1_salt → ◇ parts check → ⎋ salt|"" → ▶ write_htpasswd_file → ◇ is_dir? → ⊕ rmdir (self-heal) → ◇ existing salt? → ⊕ recompute fixed-salt → ◇ match? → ⎋ no-op|write → ▶ ensure_htpasswd → ◇ env set? → ⊕ source secrets.env → ⊕ write_htpasswd_file
# region MODULE_CONTRACT
## @purpose  htpasswd file generation extracted from secrets_manager.py (DevPlan 117 G T58.3).
##           APR1 salt extraction, idempotent .htpasswd-platform generation from explicit
##           credentials, and env-based ensure wrapper (PLATFORM_MASTER_EMAIL/PASSWORD).
## @scope    Consumed by core/internal/bootstrap/lifecycle/secrets_manager.py (lazy import) and
##           the secrets_manager htpasswd CLI. Uses shared crypto.generate_htpasswd_entry().
## @invariants
##   - Non-fatal: write_htpasswd_file returns False on failure, never raises (OSError caught)
##   - Idempotent: existing file salt ($apr1$SALT$) is reused for deterministic comparison
##   - Unparseable existing entry (no apr1 salt) → regenerate with random salt
##   - Exports HTPASSWD_FILE into os.environ on success
##   - ensure_htpasswd requires both PLATFORM_MASTER_EMAIL and PLATFORM_MASTER_PASSWORD
## @rationale  DevPlan 117 G T58.3 — extracted verbatim (_write_htpasswd_file + _ensure_htpasswd +
##            _extract_apr1_salt, ~132 LOC) with all LDD logs, TRAP[BUG] comment and docstrings
##            preserved — no behavior change (AC-G7).
## @changes  2026-08-01 · DevPlan 117 G T58.3 — extracted from secrets_manager.py
## @changes  2026-08-12 · DevPlan 156 W2 — write_htpasswd_file: self-heal пустой директории
##                      по целевому пути (docker bind-mount artifact; TRAP[BUG] 2026-08-12)
## @changes  2026-08-16 · DevPlan 177 W3.6 — sys.path-бутстрап и bare-fallback-импорты удалены:
##                      канонические пакетные импорты core.internal.shared.* (модуль вызывается
##                      только как модуль пакета — standalone `python3 htpasswd.py` не существует)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
from pathlib import Path

# ── Canonical module imports (DevPlan 177 W3.6) ──
# htpasswd.py вызывается ТОЛЬКО как модуль пакета core.internal.bootstrap.lifecycle:
#   - secrets_manager.py (lazy import в _write_htpasswd_file/_ensure_htpasswd)
#   - tests/unit/test_htpasswd.py (канонический пакетный импорт)
#   - φ4 secrets-provision → helpers/secrets.py → secrets_manager.ensure_secrets (модуль)
# Standalone `python3 htpasswd.py` НЕ вызывается ни одним фасадом/фазой (проверено 2026-08-16:
# make dev-metrics → secrets_manager.py htpasswd CLI; lib/secrets.sh → secrets_manager.py).
# → sys.path-бутстрап и bare-fallback-импорты удалены (прежний «standalone»-комментарий — гипотеза).
# crypto импортируется как модуль пакета (module-binding) — тесты патчат
# core.internal.shared.crypto.generate_htpasswd_entry и перехват работает на call-time.
from core.internal.shared import crypto
from core.internal.shared.atomic_writer import atomic_write_text
from core.internal.shared.deploy_paths import htpasswd_file as _resolve_htpasswd
from core.internal.shared.deploy_paths import secrets_env_file as _resolve_secrets_env
from core.internal.shared.secrets_env_parser import parse as parse_secrets_env

logger = logging.getLogger(__name__)


# region FUNC_extract_apr1_salt
## @purpose — Extract salt from an existing APR1 htpasswd entry ($apr1$SALT$HASH).
##            Returns "" when the entry is missing or not an apr1 hash (unparseable).
## @io — ⇥ entry: str → ⎋ str (salt) | "" (unparseable)
## @complexity — O(1) — single split
## @invariants
##   - Empty entry → ""
##   - Only accepts apr1 structure: parts[0]=user, parts[1]="apr1", parts[2]=salt, parts[3]=hash
##   - Non-apr1 hashes (bcrypt $2y$, etc.) → "" → caller regenerates
## @rationale — Port of shell `cut -d'$' -f3` (secrets.sh L225) with added apr1 validation:
##              field-3 extraction without structure check would treat a bcrypt salt as apr1 salt.
_HTPASSWD_PARTS_MIN: int = 4  # htpasswd apr1-запись: $apr1$salt$hash


def extract_apr1_salt(entry: str) -> str:
    """Extract salt from an APR1 htpasswd entry ($apr1$SALT$HASH). '' if unparseable."""
    if not entry:
        return ""
    parts = entry.split("$")
    if len(parts) >= _HTPASSWD_PARTS_MIN and parts[1] == "apr1" and parts[2]:
        return parts[2]
    return ""


# endregion FUNC_extract_apr1_salt


# region FUNC_write_htpasswd_file
## @purpose — Generate .htpasswd-platform from explicit credentials. Core of both
##            ensure_htpasswd() (env-based) and the htpasswd CLI action (DevPlan 102 TASK-1).
##            Idempotent: existing file salt is extracted and reused for deterministic
##            comparison — file is only rewritten when credentials changed.
## @io — ⇥ email: str, password: str, htpasswd_file: str (default /var/lib/platform/run/.htpasswd-platform)
##       → ⎋ bool
## @complexity — O(1) + hash_apr1 from shared.crypto
## @invariants
##   - Non-fatal: returns False on failure, never raises (OSError caught)
##   - First call (no file): random salt via generate_htpasswd_entry(email, password)
##   - Subsequent calls: extract salt from existing file → recompute with fixed salt →
##     skip if identical (idempotent, md5-stable)
##   - Unparseable existing entry (no apr1 salt) → regenerate with random salt
##   - Exports HTPASSWD_FILE into os.environ on success
def write_htpasswd_file(
    email: str,
    password: str,
    htpasswd_file: str | None = None,
) -> bool:
    """Generate .htpasswd-platform from explicit credentials. Returns True on success."""
    # Path resolution: явный аргумент > env HTPASSWD_FILE > прод-дефолт /var/lib/platform/run (142 W2).
    # Dev-локаль (macOS, /run read-only) переопределяет через env — см. .env HTPASSWD_FILE.
    if htpasswd_file is None:
        htpasswd_file = os.environ.get("HTPASSWD_FILE", str(_resolve_htpasswd()))

    # ⚠️ TRAP[BUG] · 2026-07-31 · P1 · Random salt breaks idempotency
    # · Symptom: повторный вызов перезаписывает .htpasswd-platform (md5 меняется).
    # · Root: crypto.generate_htpasswd_entry(email, password) без соли = случайный salt
    # ·   каждый вызов → existing == expected всегда False → вечная перезапись.
    # · Fix: при существующем файле извлекаем соль ($apr1$SALT$...), пересчитываем
    # ·   entry с фиксированной солью, сравниваем.
    # ruff: ignore[PLW0717] — try-тело содержит return-ветки с fall-through (после-try код) — извлечение небезопасно
    try:
        htpasswd_path = Path(htpasswd_file)
        if htpasswd_path.is_dir():
            try:
                htpasswd_path.rmdir()  # only empty directory
                logger.info("[IMP:8][secrets_manager] Removed empty dir at %s (bind-mount artifact)", htpasswd_file)
            except OSError:
                logger.error(
                    "[IMP:7][secrets_manager] %s is a non-empty directory — htpasswd cannot be written",
                    htpasswd_file,
                )
                return False
        existing = ""
        existing_salt = ""
        if htpasswd_path.exists():
            existing = htpasswd_path.read_text(encoding="utf-8").strip()
            existing_salt = extract_apr1_salt(existing)
        if existing_salt:
            expected_entry = crypto.generate_htpasswd_entry(email, password, salt=existing_salt)
            if expected_entry is None:
                logger.warning("[IMP:7][secrets_manager] shared crypto generate_htpasswd_entry failed")
                return False
            if existing == expected_entry:
                logger.info(
                    "[IMP:8][secrets_manager] htpasswd already up-to-date for %s — skipping",
                    email,
                )
                os.environ["HTPASSWD_FILE"] = htpasswd_file
                return True
            logger.info("[IMP:8][secrets_manager] htpasswd credentials changed — regenerating")
        else:
            # No file or unparseable entry — generate with fresh random salt
            expected_entry = crypto.generate_htpasswd_entry(email, password)
            if expected_entry is None:
                logger.warning("[IMP:7][secrets_manager] shared crypto generate_htpasswd_entry failed")
                return False
        htpasswd_path.parent.mkdir(parents=True, exist_ok=True)
        # AI-0008 (DevPlan 17 T3.1): атомарная запись с mode — читатель между созданием и
        # chmod больше не видит ни частичный файл, ни umask-окно 0644 (0600 атомарно)
        atomic_write_text(htpasswd_path, expected_entry + "\n", mode=0o600)
        os.environ["HTPASSWD_FILE"] = htpasswd_file
        logger.info("[IMP:9][secrets_manager] htpasswd generated at %s for %s", htpasswd_file, email)

    except OSError as e:
        logger.warning("[IMP:7][secrets_manager] htpasswd OS error: %s", e)
        return False
    else:
        return True


# endregion FUNC_write_htpasswd_file


# region FUNC_ensure_htpasswd
## @purpose — Generate /var/lib/platform/run/.htpasswd-platform from PLATFORM_MASTER_EMAIL and
##            PLATFORM_MASTER_PASSWORD (env or sourced from secrets.env). Thin wrapper
##            over write_htpasswd_file() — all hashing/idempotency logic in the core.
## @io — ⇥ secrets_env: str (for sourcing PLATFORM_MASTER_* if not in os.environ),
##       htpasswd_file: str (optional override for tests) → ⎋ bool
## @complexity — O(1) + write_htpasswd_file
## @invariants
##   - Non-fatal: returns False on failure, never raises
##   - Idempotent: salt-extraction in write_htpasswd_file prevents rewrite on unchanged creds
##   - Requires both PLATFORM_MASTER_EMAIL and PLATFORM_MASTER_PASSWORD to be set
def ensure_htpasswd(
    secrets_env: str | None = None,
    htpasswd_file: str | None = None,
) -> bool:
    """Generate .htpasswd-platform from platform master credentials. Returns True on success."""
    # Path resolution: явный аргумент > env HTPASSWD_FILE > прод-дефолт /var/lib/platform/run (142 W2).
    # Dev-локаль (macOS, /run read-only) переопределяет через env — см. .env HTPASSWD_FILE.
    if secrets_env is None:
        secrets_env = str(_resolve_secrets_env())
    if htpasswd_file is None:
        htpasswd_file = os.environ.get("HTPASSWD_FILE", str(_resolve_htpasswd()))
    # Source secrets.env into os.environ if not already set
    if not os.environ.get("PLATFORM_MASTER_PASSWORD") or not os.environ.get("PLATFORM_MASTER_EMAIL"):
        env_vars: dict[str, str] = {}
        try:
            env_vars = parse_secrets_env(secrets_env)
        except (FileNotFoundError, OSError, ValueError) as e:
            logger.warning("[IMP:7][secrets_manager] Cannot read %s: %s — returning empty dict", secrets_env, e)
        for key, value in env_vars.items():
            if key not in os.environ:
                os.environ[key] = value

    email = os.environ.get("PLATFORM_MASTER_EMAIL", "")
    password = os.environ.get("PLATFORM_MASTER_PASSWORD", "")

    if not email or not password:
        logger.info(
            "[IMP:7][secrets_manager] PLATFORM_MASTER_EMAIL or PLATFORM_MASTER_PASSWORD not set — "
            "skipping htpasswd generation",
        )
        return False

    return write_htpasswd_file(email, password, htpasswd_file)


# endregion FUNC_ensure_htpasswd
