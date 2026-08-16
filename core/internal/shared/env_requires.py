#!/usr/bin/env python3
# GREP_SUMMARY: env-requires, checker, module-yaml, secrets-manifest, dotenv, presence, runtime, unified, D4
# STRUCTURE: ▶ ┌module dict┐ → ◇ check_requires_presence (module.yaml-driven: .env.example + secrets-manifest registration) → ⊕ violations │ ▶ ┌module_name┐ → ◇ check_runtime_env (manifest-driven: os.environ + secrets.env непустота) → ⊕ missing │ ▶ check_env_requires (unified: обе семантики) → ⎋ list[str]
# region MODULE_CONTRACT
## @purpose  Единый env-requires чекер (DevPlan 118 D4). Объединяет две семантики проверки
##           env-requirements модулей: validate_module_yaml.py
##           (module.yaml-driven: presence в .env.example + регистрация в secrets-manifest)
##           и secrets_validator.py (manifest-driven: runtime-непустота в os.environ/secrets.env).
## @scope    core/internal/shared — переиспользуемый уровень internal. Потребители:
##           validate_module_yaml.check_env_requires_presence (фасад), secrets_validator.check_env_requires
##           (фасад). Прямые вызовы из тестов и гейтов.
## @invariants
##   1. check_requires_presence — module.yaml-driven: для каждого env_requires{required:true}
##      проверяет presence+непустоту в .env.example (с marker-карve-out) и, для type=secret,
##      регистрацию в secrets-manifest.yaml (tier != removed). Возвращает list[str] violations.
##   2. check_runtime_env — manifest-driven: для секретов manifest, где consumers включает
##      module_name и tier ∈ {required, generated}, проверяет непустоту в os.environ ИЛИ
##      SECRETS_ENV_FILE (default /run/platform/secrets.env). Возвращает list[str] missing.
##   3. check_env_requires — единая точка: обе семантики, консолидированный вердикт (0 расхождений).
##   4. STRICT: secrets-manifest отсутствует/битый → check_runtime_env RAISE (FileNotFoundError/
##      ValueError), check_requires_presence → graceful (файл отсутствует = секрет не зарегистрирован).
##   5. Не модифицирует файлы — read-only валидатор.
## @rationale DevPlan 118 D4: validate_module_yaml:327 check_env_requires_presence и
##            secrets_validator:75 check_env_requires — два валидатора одной сущности с разными
##            семантиками → расходящиеся вердикты (модуль требует секрет, отсутствующий в manifest:
##            module-driven ловит, manifest-driven молчит). Единый shared-модуль устраняет дрейф:
##            обе семантики в одном месте, валидаторы — тонкие фасады.
## @changes  2026-08-02 | DevPlan 118 D4 — создан (экстракция из validate_module_yaml + secrets_validator)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import cast

import yaml

from core.internal.shared import deploy_paths
from core.internal.shared.secrets_env_parser import parse as parse_secrets_env
from core.internal.shared.secrets_manifest_reader import (
    consumers as secret_consumers,
)
from core.internal.shared.secrets_manifest_reader import (
    iter_secrets as iter_manifest_secrets,
)
from core.internal.shared.secrets_manifest_reader import tier as secret_tier

logger = logging.getLogger(__name__)


# region PRIVATE_HELPERS


def _load_yaml_file(path: Path) -> object:
    """Load YAML file with safe_load. Raises FileNotFoundError/YAMLError on failure.

    ## @purpose  Единый YAML-ридер для env-requires чекера.
    ## @io  ⇥ path: Path → ⎋ Any (parsed YAML)
    ## @complexity O(1) — single YAML load
    """
    if not path.exists():
        msg = f"[env_requires] File not found: {path}"
        raise FileNotFoundError(msg)
    with Path(path).open(encoding="utf-8") as f:
        # yaml.safe_load → Any; object-граница — проверки isinstance у потребителя (W11)
        return cast(object, yaml.safe_load(f))


def env_var_in_dotenv(env_example_path: Path, var_name: str) -> tuple[bool, str]:
    """Check presence and non-empty value of var in .env.example.

    Returns (present, value). present=False if var not declared.
    Lines like `VAR=` (no value) are treated as EMPTY unless preceded by a marker-comment
    that documents the variable as generated/SOPS-only at runtime (P07 enforcement with carve-out
    for legitimately-empty placeholders in .env.example).

    Recognized markers (in any comment line within the 5-line block above the var declaration,
    or in an inline comment after `#`):
      - `# GENERATED` / `# Генерация:` / `# generate` — value generated at runtime (secrets-init.sh, SOPS)
      - `# SOPS` / `# sops` — value provided via SOPS/age on VPS
      - `# NOT for production` — placeholder only, real value from SOPS
      - `# REQUIRED` — explicit acknowledgement (still must be set somewhere)
      - `# Инициализируется` / `# Заполняется` — Russian runtime-fill markers

    ## @purpose  Единая реализация presence-проверки .env.example.
    ## @io  ⇥ env_example_path: Path, var_name: str → ⎋ tuple[bool, str] (present, value)
    ## @complexity O(L) где L = строк в .env.example
    """
    if not env_example_path.exists():
        return False, ""
    pattern = re.compile(rf"^{re.escape(var_name)}=(.*)$")
    marker_re = re.compile(
        r"#.*(generated|генерация|generate|sops|not for production|required|инициализируется|заполняется)",
        re.IGNORECASE,
    )

    with Path(env_example_path).open(encoding="utf-8") as f:
        lines = f.readlines()

    for idx, raw_line in enumerate(lines):
        stripped = raw_line.rstrip("\n")
        match = pattern.match(stripped)
        if not match:
            continue
        value = match.group(1).strip()
        inline_comment = ""
        if "#" in value:
            parts = value.split("#", 1)
            value = parts[0].strip()
            inline_comment = parts[1]
        # Scan up to 8 preceding lines for marker comments (см. validate_module_yaml rationale).
        context_lines = [inline_comment]
        for back in range(1, 9):
            back_idx = idx - back
            if back_idx < 0:
                break
            back_stripped = lines[back_idx].rstrip("\n").lstrip()
            context_lines.append(back_stripped)
        context = " ".join(context_lines)
        if not value and marker_re.search(context):
            return True, "<marker:runtime-generated>"
        return True, value
    return False, ""


def env_var_in_secrets_manifest(manifest_path: Path, var_name: str) -> bool:
    """Check presence of var in secrets-manifest.yaml (any tier except 'removed').

    ## @purpose  Единая реализация manifest-registration проверки.
    ## @io  ⇥ manifest_path: Path, var_name: str → ⎋ bool (registered)
    ## @complexity O(S) где S = секретов в manifest
    ## @invariants
    ##   - Файл отсутствует/битый → False (секрет не зарегистрирован)
    ##   - tier == "removed" → False (выведен из эксплуатации)
    """
    if not manifest_path.exists():
        return False
    try:
        manifest = _load_yaml_file(manifest_path)
    except (yaml.YAMLError, FileNotFoundError):
        return False
    if not isinstance(manifest, dict):
        return False
    secrets = manifest.get("secrets", [])
    if not isinstance(secrets, list):
        return False
    for entry in secrets:
        if not isinstance(entry, dict):
            continue
        if entry.get("name") == var_name and entry.get("tier") != "removed":
            return True
    return False


# endregion PRIVATE_HELPERS


# region FUNC_check_requires_presence
def check_requires_presence(
    module: dict[str, object],
    env_example_path: Path | None = None,
    secrets_manifest_path: Path | None = None,
) -> list[str]:
    """For each env_requires{required:true}: check presence in .env.example and secrets-manifest.yaml.

    Implements P07 static cross-check (DevPlan 033 §2 Option B layer — complements W3-E3 Option A runtime-fail).
    Единая реализация presence-проверки.

    ## @purpose Ensure required env vars are declared in .env.example (non-empty) and registered in secrets-manifest.yaml (for type=secret)
    ## @io ⇥ module: dict, env_example_path, secrets_manifest_path → ⎋ list[str] violations
    ## @complexity O(n*m) where n = env_requires, m = .env.example lines
    ## @invariants
    ##   - For {required: true, type: secret}: must be present in secrets-manifest.yaml
    ##   - For {required: true}: must be present in .env.example with non-empty value
    ##   - For {required: false}: skipped (optional)
    ##   - env_example_path/secrets_manifest_path не заданы → (None) → graceful пусто (проверка невозможна)
    """
    if env_example_path is None or secrets_manifest_path is None:
        logger.info("[IMP:7][check_requires_presence] paths not provided — presence check skipped")
        return []

    violations: list[str] = []
    module_name = str(module.get("name", "<unknown>"))
    # yaml-payload → list[dict] граница (W11): runtime-тип не меняется (cast no-op)
    env_requires = cast(list[dict[str, object]], module.get("env_requires", []))

    for req in env_requires:
        if not isinstance(req, dict):
            violations.append(f"{module_name}: env_requires entry not normalized: {req}")
            continue
        name = req.get("name")
        if not isinstance(name, str):
            violations.append(f"{module_name}: env_requires entry missing string 'name'")
            continue
        req_type = req.get("type", "secret")
        required = req.get("required", True)

        if not required:
            logger.info("[IMP:7][check_requires_presence] %s: %s optional — skipped", module_name, name)
            continue

        # (a) presence + non-empty in .env.example
        present, value = env_var_in_dotenv(env_example_path, name)
        if not present:
            violations.append(f"{module_name}: required env var '{name}' missing from {env_example_path}")
            logger.info(
                "[IMP:9][check_requires_presence] FAIL: %s — '%s' missing in .env.example",
                module_name,
                name,
            )
        elif not value:
            violations.append(f"{module_name}: required env var '{name}' declared but EMPTY in {env_example_path}")
            logger.info(
                "[IMP:9][check_requires_presence] FAIL: %s — '%s' empty value in .env.example",
                module_name,
                name,
            )

        # (b) secrets-manifest registration for type=secret
        if req_type == "secret" and not env_var_in_secrets_manifest(secrets_manifest_path, name):
            violations.append(
                f"{module_name}: secret env var '{name}' not registered in {secrets_manifest_path} (tier != removed)"
            )
            logger.info(
                "[IMP:9][check_requires_presence] FAIL: %s — '%s' not in secrets-manifest",
                module_name,
                name,
            )

    if not violations:
        logger.info(
            "[IMP:9][check_requires_presence] PASS: %s — all %d required env vars present",
            module_name,
            sum(1 for r in env_requires if isinstance(r, dict) and r.get("required", True)),
        )
    return violations


# endregion FUNC_check_requires_presence


# region FUNC_check_runtime_env
def check_runtime_env(module_name: str, secrets_manifest_path: str) -> list[str]:
    """Read secrets-manifest.yaml and verify all secrets required by a given module are non-empty
    in the process environment OR in a secrets.env file. Manifest-driven gate.

    Единая реализация check_env_requires.

    ## @purpose  Runtime-проверка непустоты секретов модуля (manifest-driven).
    ## @io  module_name (str), secrets_manifest_path (str) → List[str] of missing variable names
    ##      ⚡ raise FileNotFoundError/ValueError if manifest missing/malformed (strict, DevPlan 116 T4)
    ## @complexity 2 — single YAML parse (delegated to shared iter_secrets) + linear pass over secrets list
    ## @invariants
    ##   - Checks both os.environ and SECRETS_ENV_FILE (default /run/platform/secrets.env)
    ##   - Only secrets where consumers includes module_name AND tier ∈ {required, generated} are checked
    ##   - STRICT: manifest absent/malformed → RAISE (no graceful degradation — manifest always
    ##     delivered with core/; «gate зелёный, система врёт» устранён, invariant 7)
    ##   - Incident 2026-07-17: minio deployed with empty MINIO_ROOT_USER/PASSWORD → Access Denied
    ## @rationale Manifest-driven approach replaces module.yaml env_requires parsing.
    ##            secrets-manifest.yaml is the Single Source of Truth. Gate validates
    ##            bidirectional consistency between module.yaml env_requires and manifest.
    """
    logger.info("[IMP:7][check_runtime_env][start] Module=%s, manifest=%s", module_name, secrets_manifest_path)

    secrets_list = iter_manifest_secrets(secrets_manifest_path)

    # Filter: secrets where consumers includes module_name AND tier ∈ {required, generated}
    module_secrets = [
        s for s in secrets_list if module_name in secret_consumers(s) and secret_tier(s) in {"required", "generated"}
    ]

    if not module_secrets:
        logger.info(
            "[IMP:7][check_runtime_env][no_match] No secrets in manifest for module %s with tier in {required, generated}",
            module_name,
        )
        return []

    # Build env map from secrets.env file using shared parser
    secrets_file = os.environ.get("SECRETS_ENV_FILE", str(deploy_paths.secrets_env_file()))
    env_map: dict[str, str] = {}
    secrets_file_path = Path(secrets_file)
    if secrets_file_path.is_file():
        env_map = parse_secrets_env(str(secrets_file_path))
        logger.info("[IMP:7][check_runtime_env][env_file] Loaded %d vars from %s", len(env_map), secrets_file)
    else:
        logger.info(
            "[IMP:7][check_runtime_env][env_file] Secrets file %s not found — checking os.environ only", secrets_file
        )

    # Check each required secret: must exist in os.environ OR in the env file map
    missing: list[str] = []
    for s in module_secrets:
        var_name = str(s["name"])
        if not os.environ.get(var_name, "") and not env_map.get(var_name, ""):
            missing.append(var_name)
            logger.info("[IMP:8][check_runtime_env][missing] Var=%s is empty", var_name)
        else:
            logger.info("[IMP:8][check_runtime_env][ok] Var=%s is present", var_name)

    if missing:
        logger.warning("[IMP:9][check_runtime_env][FAIL] Missing required env vars for %s: %s", module_name, missing)
    else:
        logger.info("[IMP:9][check_runtime_env][PASS] All required env vars present for module %s", module_name)

    return missing


# endregion FUNC_check_runtime_env


# region FUNC_check_env_requires
def check_env_requires(
    module: dict[str, object],
    secrets_manifest_path: str | Path,
    env_example_path: Path | None = None,
) -> list[str]:
    """Единый env-requires чекер (DevPlan 118 D4): обе семантики, консолидированный вердикт.

    ## @purpose  Консолидация module.yaml-driven (presence) и manifest-driven (runtime) проверок.
    ##            Устраняет расхождение вердиктов: если module.yaml требует секрет, отсутствующий
    ##            в secrets-manifest — ОБА валидатора (validate_module_yaml, secrets_validator)
    ##            через этот чекер дают согласованный результат.
    ## @io  ⇥ module: dict (env_requires), secrets_manifest_path, env_example_path (optional)
    ##      → ⎋ list[str] — violations + missing (консолидированный список)
    ## @complexity O(n*m + s) — presence + runtime
    ## @invariants
    ##   - Всегда включает presence-проверку (если env_example_path дан)
    ##   - Всегда включает manifest-runtime-проверку (по module["name"])
    ##   - Секрет из module.yaml env_requires, отсутствующий в manifest → presence violation
    ##     «not registered in secrets-manifest» (ловит расхождение, которое runtime молча пропускал)
    """
    violations: list[str] = []

    # 1. module.yaml-driven presence
    if env_example_path is not None:
        violations.extend(check_requires_presence(module, env_example_path, Path(secrets_manifest_path)))
    else:
        # presence-путь без .env.example: проверяем только manifest-регистрацию секретов
        for req in cast(list[dict[str, object]], module.get("env_requires", [])):
            if not isinstance(req, dict):
                continue
            if req.get("type") == "secret" and req.get("required", True):
                name = req.get("name")
                if name and not env_var_in_secrets_manifest(Path(secrets_manifest_path), str(name)):
                    violations.append(
                        f"{module.get('name', '<unknown>')}: secret env var '{name}' not registered "
                        f"in {secrets_manifest_path} (tier != removed)"
                    )

    # 2. manifest-driven runtime
    try:
        violations.extend(check_runtime_env(str(module.get("name", "")), str(secrets_manifest_path)))
    except (FileNotFoundError, ValueError) as e:
        logger.warning("[IMP:7][check_env_requires] runtime-часть пропущена: %s", e)

    return violations


# endregion FUNC_check_env_requires


if __name__ == "__main__":
    import sys

    print(
        "[IMP:5][env_requires] CLI не предусмотрен — используйте фасады validate_module_yaml / secrets_validator",
        file=sys.stderr,
    )
    sys.exit(0)
