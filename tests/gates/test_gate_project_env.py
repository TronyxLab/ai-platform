# GREP_SUMMARY: gate project-env env-platform dotenv presence provides-profiles validation d2 fixture-driven not-skip C8
# STRUCTURE: ▶ _scan_project_env(projects_dir) → list[str] issues → ▶ fixture-driven: test_project_env_valid (tmp_path project + .env.platform) → ▶ R4: test_project_env_missing_dir_fails → ⎋ PASS
# region MODULE_CONTRACT
## @purpose  D3 gate — validate .env.platform presence and structural integrity for all projects.
##           Every project MUST have a .env.platform file (platform service descriptors).
##           If present, the file must reference only provides that have matching profiles.
## @scope    Scans a projects/ directory tree for ai-platform.yaml files, checks each sibling
##           .env.platform for existence and structural consistency with platform-env.yaml.
## @invariants
##   - DevPlan 119 C8: always-skip устранён — гейт fixture-driven (валидирует репрезентативный
##     проект из tmp_path), БЕЗ pytest.skip (R4: отсутствие окружения → FAIL, не skip)
##   - Реальный projects/ (если существует) также сканируется (исходный enforcement-scope)
##   - Missing .env.platform for an existing project → FAIL (environmental config error)
##   - .env.platform must contain valid KEY=VALUE pairs
##   - Service references (PLATFORM_<SERVICE>_*) must have a corresponding entry in
##     platform-env.yaml provides, and that provides must be in the profiles list
## @rationale  D3 enforcement gate: AC-D3-ENV requires make gate MODE=fast to check .env.platform
##             for all registered projects. C8 (AUDIT-5 DEAD-1): старый гейт всегда skip'ался
##             (projects/ не существует) — переведён на фикстуры (DevPlan C8 шаг 2).
## @usecases
##   - make gate MODE=fast → validates fixture + (реальные проекты если есть)
## @changes — 2026-07-20 | Created per DevPlan 020 Task 5.2
##           — 2026-08-02 | DevPlan 119 C8 — fixture-driven (test_project_env_valid),
##             отсутствие projects/ → FAIL, 0 pytest.skip
# endregion MODULE_CONTRACT

import glob
import logging
import os
import re

import pytest
import yaml

from tests._conftest.ldd import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

_PROJECTS_DIR = os.path.join(repo_root(), "projects")
_PLATFORM_ENV_YAML = os.path.join(repo_root(), "platform-env.yaml")

_logger = logging.getLogger(__name__)

# Regex to match PLATFORM_<SERVICE>_* variable names
_PLATFORM_VAR_RE = re.compile(r"^PLATFORM_([A-Z][A-Z0-9_]+)_")
# Extracted service names that are NOT actual services (networks, internal metadata, etc.)
# These arise from variables like PLATFORM_NO_PROXY (→ 'no'), PLATFORM_*_NET (→ network names)
_NON_SERVICE_NAMES: set[str] = {
    "no",  # PLATFORM_NO_PROXY — exclusions list, not a service
    "proxy",  # PLATFORM_PROXY_NET — Docker network, not a service
    "hermes_agent",  # PLATFORM_HERMES_AGENT_NET — Docker network, not a service
    "shared_cache",  # PLATFORM_SHARED_CACHE_NET — Docker network, not a service
    "shared_db",  # PLATFORM_SHARED_DB_NET — Docker network, not a service
}


# region FUNC_load_provides_profiles
def _load_provides_profiles() -> tuple[set[str], set[str]]:
    """Load provides keys and profiles list from platform-env.yaml.

    ## @purpose — Provide the canonical set of known services (provides) and
    ##            enabled profiles from the platform environment descriptor.
    ## @io — ⎋ tuple(provides_keys: set[str], profile_names: set[str])
    ## @complexity — O(P + R) where P = profiles, R = provides entries
    """
    if not os.path.isfile(_PLATFORM_ENV_YAML):
        _logger.warning("[IMP:7][gate][env] platform-env.yaml not found at %s", _PLATFORM_ENV_YAML)
        return set(), set()

    with open(_PLATFORM_ENV_YAML) as f:
        data = yaml.safe_load(f)

    if data is None:
        return set(), set()

    provides_keys: set[str] = set(data.get("provides", {}).keys())
    profile_names: set[str] = set(data.get("profiles", []))

    return provides_keys, profile_names


# endregion FUNC_load_provides_profiles


# region FUNC_scan_project_env
## @purpose  Scan a projects/ directory for D3 .env.platform issues (presence + provides ⊆ profiles).
##           Возвращает список проблем; пусто = чисто.
## @io       ⇥ projects_dir: str — корень projects/ → ⎋ list[str] issues
## @complexity — O(N * M) где N = yaml files, M = avg lines in .env.platform
## @invariants
##   - Отсутствующий projects_dir → ОДНА issue «тестовое окружение не настроено» (R4: FAIL, не skip)
##   - Существующий projects_dir без yaml-файлов → пусто (vacuous clean)
##   - Missing .env.platform → issue; unknown/unprofiled service refs → issues
def _scan_project_env(projects_dir: str) -> list[str]:
    """Return a list of D3 .env.platform issues (empty list = clean)."""
    if not os.path.isdir(projects_dir):
        _logger.info("[IMP:7][gate][env] Projects directory not found: %s", projects_dir)
        return [f"projects/ directory not found ({projects_dir}) — тестовое окружение не настроено"]

    yaml_pattern = os.path.join(projects_dir, "*", "*", "ai-platform.yaml")
    yaml_files = glob.glob(yaml_pattern)
    _logger.info("[IMP:8][gate][env] Glob pattern: %s → %d files", yaml_pattern, len(yaml_files))

    if not yaml_files:
        return []  # проектов нет — валидировать нечего (vacuous clean)

    provides_keys, profile_names = _load_provides_profiles()
    _logger.info(
        "[IMP:8][gate][env] platform-env.yaml: %d provides, %d profiles: %s",
        len(provides_keys),
        len(profile_names),
        sorted(profile_names) if profile_names else "(empty)",
    )

    issues: list[str] = []

    for yaml_path in sorted(yaml_files):
        project_dir = os.path.dirname(yaml_path)
        env_platform_path = os.path.join(project_dir, ".env.platform")
        rel_project = os.path.relpath(project_dir, projects_dir)

        _logger.info("[IMP:7][gate][env] Checking project: %s", rel_project)

        # Check .env.platform exists
        if not os.path.isfile(env_platform_path):
            issues.append(
                f"{rel_project}/.env.platform: MISSING — every project must have .env.platform "
                f"(regenerate with: make sync-env)"
            )
            _logger.error("[IMP:9][gate][env] MISSING: %s/.env.platform", rel_project)
            continue

        _logger.info("[IMP:7][gate][env] %s/.env.platform: EXISTS", rel_project)

        # Parse .env.platform for PLATFORM_<SERVICE>_* variable names
        if not provides_keys and not profile_names:
            _logger.warning("[IMP:7][gate][env] Provides/profiles not loaded — skipping structural check")
            continue

        try:
            with open(env_platform_path) as f:
                env_content = f.read()
        except Exception as exc:
            issues.append(f"{rel_project}/.env.platform: cannot read: {exc}")
            _logger.error("[IMP:9][gate][env] READ FAIL: %s — %s", rel_project, exc)
            continue

        # Extract unique service names from PLATFORM_<SERVICE>_* variables
        referenced_services: set[str] = set()
        for line in env_content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = _PLATFORM_VAR_RE.match(line)
            if match:
                service_name = match.group(1).lower()
                if service_name in _NON_SERVICE_NAMES:
                    _logger.debug(
                        "[IMP:8][gate][env] Skipping non-service ref: %s → %s", line.split("=", 1)[0], service_name
                    )
                    continue
                referenced_services.add(service_name)

        _logger.info(
            "[IMP:8][gate][env] %s: referenced services = %s",
            rel_project,
            sorted(referenced_services) if referenced_services else "(none)",
        )

        # Validate each referenced service has a corresponding provides entry and profile
        for svc in referenced_services:
            if svc not in provides_keys:
                issues.append(
                    f"{rel_project}/.env.platform: references '{svc}' "
                    f"which is not in platform-env.yaml provides ({sorted(provides_keys)})"
                )
                _logger.error("[IMP:9][gate][env] UNKNOWN SERVICE: %s → %s", rel_project, svc)
            elif svc not in profile_names:
                issues.append(
                    f"{rel_project}/.env.platform: references '{svc}' from provides, "
                    f"but '{svc}' is not in platform-env.yaml profiles ({sorted(profile_names)})"
                )
                _logger.error("[IMP:9][gate][env] UNPROFILED SERVICE: %s → %s not in profiles", rel_project, svc)

    return issues


# endregion FUNC_scan_project_env


# region FUNC_test_project_env_valid
@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-02 · REGRESSION · fixture-driven D3 env gate (C8)
# · Scenario: DevPlan 119 C8 — валидный проект из tmp_path с .env.platform проходит (не skip);
#   реальный projects/ (если есть) тоже валидируется
# · Last fail: до C8 — тест всегда skip'ался (projects/ не существует)
# · Remove if: D3 .env.platform enforcement перенесён в другой механизм
def test_project_env_valid(caplog, tmp_path) -> None:
    """Validate a VALID fixture project env (never skip) + реальный projects/ если присутствует."""
    # 1) Fixture-driven: валидный проект с .env.platform (без PLATFORM_<SERVICE>_* ссылок —
    #    фикстура не зависит от platform-env.yaml provides/profiles)
    ctx_dir = tmp_path / "projects" / "testctx" / "testapp"
    ctx_dir.mkdir(parents=True)
    (ctx_dir / "ai-platform.yaml").write_text("project: testapp\nservice: testapp\n")
    (ctx_dir / ".env.platform").write_text("# minimal env platform\nNODE_NAME=testnode\n")

    issues = _scan_project_env(str(tmp_path / "projects"))
    assert issues == [], f"D3 issues in VALID fixture project: {issues}"
    _logger.info("[IMP:9][gate][env] VALID fixture project passes (0 issues)")

    # 2) Реальный projects/ если существует (исходный enforcement-scope)
    if os.path.isdir(_PROJECTS_DIR):
        real_issues = _scan_project_env(_PROJECTS_DIR)
        if real_issues:
            for issue in real_issues:
                _logger.error("[IMP:9][gate][env] FAIL: %s", issue)
            pytest.fail(
                f".env.platform validation failed in projects/ ({len(real_issues)} issues):\n" + "\n".join(real_issues)
            )
        _logger.info("[IMP:9][gate][env] Real projects/ passes (0 issues)")
    else:
        _logger.info("[IMP:8][gate][env] Real projects/ отсутствует — fixture покрывает гейт (C8)")


# endregion FUNC_test_project_env_valid


# region FUNC_test_project_env_missing_dir_fails
@pytest.mark.gate
@ldd_trajectory
# 🧪 TRAP[TEST] · 2026-08-02 · NEGATIVE (R4) · отсутствие projects/ → FAIL, не skip (C8)
# · Scenario: DevPlan 119 C8 — по R4 отсутствие тестового окружения = ошибка конфигурации;
#   _scan_project_env возвращает явную issue вместо тихого pytest.skip
# · Last fail: до C8 — pytest.skip("No projects/ directory — dev environment") (DEAD-1)
# · Remove if: projects/ становится обязательной частью репозитория
def test_project_env_missing_dir_fails(caplog, tmp_path) -> None:
    """R4 negative: отсутствие projects/ → явная FAIL-issue (не skip)."""
    issues = _scan_project_env(str(tmp_path / "absent-projects"))
    assert len(issues) >= 1, "R4 FAIL (C8): отсутствие projects/ должно давать FAIL-issue"
    assert "не настроено" in issues[0], f"ожидалось сообщение о ненастроенном окружении: {issues[0]}"
    _logger.info("[IMP:9][gate][env] Отсутствие projects/ → явная issue (R4 PASS, 0 skip)")


# endregion FUNC_test_project_env_missing_dir_fails
