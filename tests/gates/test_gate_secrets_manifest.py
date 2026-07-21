# GREP_SUMMARY: gate secrets-manifest anti-drift env-requires module-yaml workflow ci-secret env-example hardcoded-credentials
# STRUCTURE: ▶ load secrets-manifest → ◇ test_manifest_vs_module_yaml(env_requires↔manifest) → ◇ test_manifest_vs_workflows(secrets.XX↔manifest) → ◇ test_manifest_vs_env_example(CI section↔manifest) → ◇ test_no_hardcoded_secrets_in_core(core/**/*.sh scan) → ⎋ 4 gate tests
# region MODULE_CONTRACT
## @purpose  Gate test suite for secrets-manifest anti-drift (Plan 018).
##            Validates bidirectional consistency between secrets-manifest.yaml and:
##            1. All 13 module.yaml env_requires (every required name must be in manifest)
##            2. All .github/workflows/*.yml ${{ secrets.XXX }} references
##            3. .env.example CI secrets section
##            4. No hardcoded credentials in core/**/*.sh
## @scope    Parses secrets-manifest.yaml, 13 module.yaml files, 9 workflow files, .env.example, and core/**/*.sh
## @invariants
##   - secrets-manifest.yaml — единственный SSoT для всех секретов платформы
##   - Каждый env_requires из любого module.yaml имеет matching entry с tier=required|generated
##   - Каждый secrets.XXX в workflows зарегистрирован в манифесте (любой source)
##   - Все ci-secret source секреты из манифеста задокументированы в .env.example
##   - core/**/*.sh не содержит хардкоженных креденшалов
## @rationale Anti-drift gate — блокирует добавление незарегистрированных секретов
## @usecases
##   - CI gate make gate MODE=fast → проверка консистентности манифеста
##   - Pre-merge проверка: секрет добавлен в module.yaml, но не зарегистрирован → RED
##   - Pre-merge проверка: secrets.XXX в workflow, но не зарегистрирован → RED
# endregion MODULE_CONTRACT

import logging
import pathlib
import re

import pytest

from tests.helpers.gate_helpers import load_yaml, repo_root

logger = logging.getLogger(__name__)

_MANIFEST_PATH: pathlib.Path = repo_root() / "core" / "secrets-manifest.yaml"
_MODULES_DIR: pathlib.Path = repo_root() / "core" / "modules"
_WORKFLOW_DIR: pathlib.Path = repo_root() / ".github" / "workflows"
_ENV_EXAMPLE_PATH: pathlib.Path = repo_root() / ".env.example"
_CORE_DIR: pathlib.Path = repo_root() / "core"

# Паттерн для обнаружения хардкоженных креденшалов (тот же, что в test_gate_ci_env_vars.py)
_HARDCODED_SECRET_PATTERN: re.Pattern = re.compile(
    r"(?:password|secret|token|api_key|apikey|credential|key)\s*[:=]\s*[\"'][^\"'\s]{8,}[\"']",
    re.IGNORECASE,
)

# Паттерн для ${{ secrets.XXX }} в workflow файлах
_SECRETS_REF_PATTERN: re.Pattern = re.compile(r"\$\{\{\s*secrets\.(\w+)\s*\}\}")

# Секреты GitHub Actions, которые не требуют регистрации в манифесте
_BUILT_IN_SECRETS: frozenset[str] = frozenset(
    {
        "GITHUB_TOKEN",
    }
)

# Allowlist для ложных срабатываний credential scan
_CREDENTIAL_ALLOWLIST: list[re.Pattern] = [
    re.compile(r"openssl\s+rand"),
    re.compile(r"sops\s+--set"),
    re.compile(r"\$\{[\w_]+\}"),  # шаблоны переменных
    re.compile(r"SOPS_AGE_KEY"),  # age-ключ (допустимый паттерн)
    re.compile(r"AGE_SECRET_KEY"),  # age-ключ (допустимый паттерн)
    re.compile(r"GIT_MIRROR_TOKEN"),  # переменная окружения
    re.compile(r"DOCKER_HUB_TOKEN"),
    re.compile(r"DOCKER_HUB_USERNAME"),
]

# Паттерн для извлечения имён CI-секретов из .env.example документации
# Формат: "# VAR_NAME — description" или "# VAR_NAME — ..."
_ENV_EXAMPLE_SECRET_PATTERN: re.Pattern = re.compile(
    r"^#\s+([A-Z][A-Z0-9_]+)\s+[—–-]",
    re.MULTILINE,
)


def _get_manifest_secrets() -> dict[str, dict]:
    """Load secrets-manifest.yaml and return a dict of {name: entry}."""
    if not _MANIFEST_PATH.exists():
        logger.warning("[IMP:8][_get_manifest_secrets] Manifest not found at %s", _MANIFEST_PATH)
        return {}

    data = load_yaml(_MANIFEST_PATH)
    secrets_list = data.get("secrets", [])
    result: dict[str, dict] = {}
    for entry in secrets_list:
        name = entry.get("name")
        if name:
            result[name] = entry
    logger.info("[IMP:9][_get_manifest_secrets] Loaded %d secrets from manifest", len(result))
    return result


def _collect_module_env_requires() -> dict[str, set[str]]:
    """Collect all env_requires names from all module.yaml files.

    Returns dict mapping module_name → set of env_requires names.
    """
    result: dict[str, set[str]] = {}
    module_yamls = sorted(_MODULES_DIR.glob("*/module.yaml"))

    for mf in module_yamls:
        module_name = mf.parent.name
        data = load_yaml(mf)
        env_list = data.get("env_requires", []) or []
        names = {e for e in env_list if isinstance(e, str)}
        if names:
            result[module_name] = names
            logger.debug("[IMP:8][env_requires] %s: %s", module_name, sorted(names))

    logger.info("[IMP:9][env_requires] Collected env_requires from %d modules", len(result))
    return result


def _extract_ci_secrets_from_env_example() -> set[str]:
    """Extract CI secret names from .env.example documentation section (lines 210+)."""
    if not _ENV_EXAMPLE_PATH.exists():
        logger.warning("[IMP:8][env_example] .env.example not found")
        return set()

    content = _ENV_EXAMPLE_PATH.read_text()

    # Извлекаем только секцию GitHub Actions secrets (после маркера)
    # Ищем: "# GitHub Actions secrets" и читаем до конца файла
    ci_section_start = content.find("GitHub Actions secrets")
    if ci_section_start == -1:
        logger.warning("[IMP:8][env_example] CI section marker not found")
        return set()

    ci_section = content[ci_section_start:]

    # Извлекаем имена переменных в формате "# VAR_NAME —"
    names = set()
    for match in _ENV_EXAMPLE_SECRET_PATTERN.finditer(ci_section):
        names.add(match.group(1))

    logger.info("[IMP:9][env_example] Extracted %d CI secret names from .env.example", len(names))
    return names


# =============================================================================
# TESTS
# =============================================================================


@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-07-20 · REGRESSION · Gate invariant — every env_requires name
#     must be registered in secrets-manifest.yaml with tier=required|generated
# · Last fail: N/A (preventive)
# · Remove if: secrets-manifest.yaml is replaced by a different SSoT mechanism
def test_manifest_vs_module_yaml(caplog):
    """Each env_requires name from every module.yaml must be in manifest with tier=required|generated.

    Unknown name → FAIL.
    """
    caplog.set_level(logging.INFO)
    manifest_secrets = _get_manifest_secrets()

    if not manifest_secrets:
        pytest.fail("secrets-manifest.yaml not found or empty — cannot validate")

    module_envs = _collect_module_env_requires()
    violations: list[str] = []

    for module_name, env_names in sorted(module_envs.items()):
        for env_name in sorted(env_names):
            if env_name not in manifest_secrets:
                violations.append(f"{module_name}: {env_name} NOT in manifest")
                logger.error(
                    "[IMP:10][manifest_vs_module] %s requires '%s' but it's NOT in secrets-manifest.yaml",
                    module_name,
                    env_name,
                )
            else:
                entry = manifest_secrets[env_name]
                tier = entry.get("tier", "")
                if tier not in ("required", "generated"):
                    violations.append(
                        f"{module_name}: {env_name} in manifest but tier={tier} (expected required|generated)"
                    )
                    logger.warning(
                        "[IMP:8][manifest_vs_module] %s requires '%s' but manifest tier is '%s' (expected required|generated)",
                        module_name,
                        env_name,
                        tier,
                    )
                else:
                    logger.info(
                        "[IMP:9][manifest_vs_module] %s → '%s' tier=%s ✓",
                        module_name,
                        env_name,
                        tier,
                    )

    if violations:
        for v in violations:
            logger.error("[IMP:10][manifest_vs_module] Violation: %s", v)
        pytest.fail(f"Module env_requires not fully covered in manifest: {violations}")

    logger.info("[IMP:9][manifest_vs_module] ALL %d module env_requires are covered in manifest", len(module_envs))


@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-07-20 · REGRESSION · Gate invariant — every ${{ secrets.XXX }}
#     in workflows must be registered in secrets-manifest.yaml
# · Last fail: N/A (preventive)
# · Remove if: secrets-manifest.yaml is replaced by a different SSoT mechanism
def test_manifest_vs_workflows(caplog):
    """Each ${{ secrets.XXX }} in .github/workflows/*.yml must be registered in manifest.

    Unknown name → FAIL.
    Secrets with source != ci-secret → WARNING (non-blocking).
    """
    caplog.set_level(logging.INFO)
    manifest_secrets = _get_manifest_secrets()

    if not manifest_secrets:
        pytest.fail("secrets-manifest.yaml not found or empty — cannot validate")

    unknown_secrets: set[str] = set()
    non_ci_secrets: list[str] = []
    workflow_files = sorted(_WORKFLOW_DIR.glob("*.yml"))

    if not workflow_files:
        logger.warning("[IMP:8][manifest_vs_workflows] No workflow files found at %s", _WORKFLOW_DIR)
        return

    for wf_file in workflow_files:
        content = wf_file.read_text()
        for match in _SECRETS_REF_PATTERN.finditer(content):
            secret_name = match.group(1)

            if secret_name in _BUILT_IN_SECRETS:
                continue

            if secret_name not in manifest_secrets:
                unknown_secrets.add(secret_name)
                logger.error(
                    "[IMP:10][manifest_vs_workflows] %s: unknown secret '%s' — not in manifest",
                    wf_file.name,
                    secret_name,
                )
            else:
                entry = manifest_secrets[secret_name]
                src = entry.get("source", "")
                if src != "ci-secret":
                    non_ci_secrets.append(f"{wf_file.name}: {secret_name} (source={src}, expected ci-secret)")
                    logger.warning(
                        "[IMP:8][manifest_vs_workflows] %s: '%s' source=%s (not ci-secret) — register if CI-only",
                        wf_file.name,
                        secret_name,
                        src,
                    )
                else:
                    logger.info(
                        "[IMP:9][manifest_vs_workflows] %s: '%s' source=ci-secret ✓",
                        wf_file.name,
                        secret_name,
                    )

    if unknown_secrets:
        pytest.fail(f"Unknown secrets in workflow files (not in manifest): {sorted(unknown_secrets)}")

    if non_ci_secrets:
        logger.warning(
            "[IMP:8][manifest_vs_workflows] %d secrets in workflows with source != ci-secret (non-blocking):\n  %s",
            len(non_ci_secrets),
            "\n  ".join(non_ci_secrets),
        )

    logger.info(
        "[IMP:9][manifest_vs_workflows] All workflow secrets registered (source warnings: %d)",
        len(non_ci_secrets),
    )


@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-07-20 · REGRESSION · Gate invariant — CI secrets in .env.example
#     match secrets-manifest.yaml (source=ci-secret)
# · Last fail: N/A (preventive)
# · Remove if: .env.example is no longer the CI secrets documentation source
def test_manifest_vs_env_example(caplog):
    """CI secrets documented in .env.example must match manifest (source=ci-secret).

    CI secret in .env.example but not in manifest → FAIL.
    CI secret in manifest but not documented in .env.example → WARNING (non-blocking).
    """
    caplog.set_level(logging.INFO)
    manifest_secrets = _get_manifest_secrets()

    if not manifest_secrets:
        pytest.fail("secrets-manifest.yaml not found or empty — cannot validate")

    # Collect CI secrets from manifest (source=ci-secret)
    manifest_ci_secrets: set[str] = set()
    for name, entry in manifest_secrets.items():
        if entry.get("source") == "ci-secret":
            manifest_ci_secrets.add(name)

    logger.info("[IMP:8][manifest_vs_env] Manifest has %d ci-secret entries", len(manifest_ci_secrets))

    # Collect documented secrets from .env.example CI section
    env_example_secrets = _extract_ci_secrets_from_env_example()
    logger.info("[IMP:8][manifest_vs_env] .env.example documents %d CI secrets", len(env_example_secrets))

    # Check: every .env.example CI secret must be in manifest
    missing_in_manifest = env_example_secrets - manifest_ci_secrets
    if missing_in_manifest:
        for s in sorted(missing_in_manifest):
            logger.error("[IMP:10][manifest_vs_env] '%s' in .env.example but NOT in manifest (source=ci-secret)", s)
        pytest.fail(f"CI secrets in .env.example not registered in manifest: {sorted(missing_in_manifest)}")

    # Check: every manifest ci-secret should be documented in .env.example
    undocumented = manifest_ci_secrets - env_example_secrets
    if undocumented:
        for s in sorted(undocumented):
            logger.warning(
                "[IMP:8][manifest_vs_env] '%s' in manifest (ci-secret) but NOT documented in .env.example — add documentation",
                s,
            )
        logger.warning(
            "[IMP:8][manifest_vs_env] %d undocumented ci-secrets (non-blocking): %s",
            len(undocumented),
            sorted(undocumented),
        )

    logger.info(
        "[IMP:9][manifest_vs_env] .env.example ↔ manifest CI secrets consistent (undocumented: %d)",
        len(undocumented),
    )


@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-07-20 · REGRESSION · Gate invariant — no hardcoded credentials
#     in core/**/*.sh files (extends test_ci_no_hardcoded_secrets scope)
# · Last fail: N/A (preventive)
# · Remove if: credential scanning is fully automated via gitleaks
def test_no_hardcoded_secrets_in_core(caplog):
    """Scan core/**/*.sh for hardcoded credentials (password=, secret=, token=, etc.).

    Extends existing test_ci_no_hardcoded_secrets scope from .github/workflows to core/.
    False positives excluded: openssl rand, sops --set, template ${VAR} patterns.
    """
    caplog.set_level(logging.INFO)

    sh_files = sorted(_CORE_DIR.rglob("*.sh"))

    if not sh_files:
        logger.warning("[IMP:8][no_hardcoded] No .sh files found in core/")
        return

    violations: list[str] = []

    for sh_file in sh_files:
        content = sh_file.read_text()
        matches = _HARDCODED_SECRET_PATTERN.findall(content)

        if not matches:
            continue

        # Filter false positives using allowlist
        real_violations: list[str] = []
        for match_val in matches:
            # Check each allowlist pattern
            is_allowed = any(allowlist.search(content) for allowlist in _CREDENTIAL_ALLOWLIST)
            if not is_allowed:
                real_violations.append(match_val)

        if real_violations:
            rel_path = sh_file.relative_to(repo_root())
            violations.append(f"{rel_path}: {real_violations}")
            logger.error("[IMP:10][no_hardcoded] Hardcoded credential in %s: %s", rel_path, real_violations)

    if violations:
        for v in violations:
            logger.error("[IMP:10][no_hardcoded] Violation: %s", v)
        pytest.fail(f"Hardcoded credentials found in core/**/*.sh: {violations}")

    logger.info(
        "[IMP:9][no_hardcoded] No hardcoded credentials in %d core/**/*.sh files",
        len(sh_files),
    )
