# GREP_SUMMARY: test-hermes-best-practices restart-unless-stopped approvals-manual telegram-allowlist insecure-dashboard secrets-env LDD IMP caplog
# STRUCTURE: fixtures(compose_files) → test_restart_unless_stopped → test_approvals_not_off → test_telegram_allowlist_enforced → test_dashboard_insecure_not_one → test_secrets_in_env → test_env_example_no_secrets → test_all_compose_files

# region MODULE_CONTRACT [DOMAIN(TESTING):3; CONCEPT(VALIDATION):2; TECH(PYTEST):2]
## @purpose — Validate that all Docker Compose and config files follow Hermes best
##           practices as defined in the phase-08 plan (section 2.3).
## @scope — Unit tests using native imports only. Validates YAML structure.
## @invariants
##   - restart: unless-stopped (NOT always) in all docker-compose.yml
##   - approvals.mode: NOT off in all config.yaml
##   - TELEGRAM_ALLOWED_USERS required if TELEGRAM_BOT_TOKEN present
##   - HERMES_DASHBOARD_INSECURE NOT set to 1
##   - Secrets in .env, not in git-tracked files
##   - At least one IMP:9 log per §TESTING
## @rationale — Q: Why validate best practices in tests? A: Enforce Hermes security
##              hardening automatically; prevents regression on restart/approval/secret policies
## @changes — LAST_CHANGE: 2026-06-12 | Added MODULE_CONTRACT region markers
def _module_contract():
    pass


# endregion MODULE_CONTRACT

import logging
import os
import re

import pytest
import yaml
from conftest import ldd_trajectory

logger = logging.getLogger(__name__)

# region CONSTANTS

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# template-agent removed per TASK-1.1 — redirecting to modules/hermes-agent/ where possible.
# .env.example and .gitignore now available in modules/hermes-agent/. ENV_EXAMPLE_FILES
# and gitignore_paths fixtures are populated accordingly (TASK-3).
COMPOSE_FILES = [
    ("hermes-agent", os.path.join(BASE_DIR, "core", "modules", "hermes-agent", "docker-compose.base.yml")),
]

CONFIG_FILES: list[tuple[str, str]] = [
    ("hermes-agent", os.path.join(BASE_DIR, "core", "modules", "hermes-agent", "config", "config.yaml")),
]

ENV_EXAMPLE_FILES: list[tuple[str, str]] = [
    ("hermes-agent", os.path.join(BASE_DIR, "core", "modules", "hermes-agent", ".env.example")),
]

# endregion CONSTANTS


# region FIXTURES


@pytest.fixture
def all_compose_files() -> list[tuple[str, str]]:
    """Return list of (name, path) tuples for all docker-compose files."""
    result = []
    for name, path in COMPOSE_FILES:
        if os.path.isfile(path):
            result.append((name, path))
    if not result:
        pytest.skip("No docker-compose.yml files found")
    return result


@pytest.fixture
def all_config_yamls() -> list[tuple[str, str]]:
    """Return list of (name, path) tuples for all config.yaml files."""
    result = []
    for name, path in CONFIG_FILES:
        if os.path.isfile(path):
            result.append((name, path))
    if not result:
        pytest.skip("No config.yaml files found")
    return result


# endregion FIXTURES


# region TESTS: restart: unless-stopped


@pytest.mark.parametrize("check_type", ["unless_stopped", "not_always"])
@ldd_trajectory
def test_restart_policy(check_type, all_compose_files, caplog) -> None:
    # 🧪 TRAP[TEST] · 2026-07-03 · restart: unless-stopped not always
    # · Regression: compose использует restart: always — контейнер перезапускается при docker compose down
    # · Scenario: docker compose down → контейнер останавливается, но Docker daemon перезапускает его (always)
    # · Last fail: never — guard test
    # · Remove if: переход на Kubernetes (restartPolicy)
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_best_practices][restart] START: checking restart policy (%s)", check_type)

        if check_type == "unless_stopped":
            violations = []
            for name, path in all_compose_files:
                with open(path) as f:
                    content = f.read()
                if "restart: always" in content and "restart: unless-stopped" not in content:
                    violations.append(name)
            assert len(violations) == 0, f"Must use restart: unless-stopped: {violations}"
        else:
            violations = []
            for name, path in all_compose_files:
                with open(path) as f:
                    content = f.read()
                for i, line in enumerate(content.split("\n")):
                    if line.strip() == "restart: always":
                        violations.append(f"{name}:{i + 1}")
            assert len(violations) == 0, f"restart: always forbidden: {violations}"

        logger.critical("[IMP:9][test_best_practices][restart] ASSERT: check_type=%s violations=0", check_type)


# endregion TESTS: restart


# region TESTS: approvals mode


@ldd_trajectory
def test_approvals_mode_not_off(all_config_yamls, caplog) -> None:
    # 🧪 TRAP[TEST] · 2026-07-03 · approvals.mode != off
    # · Regression: режим approval выключен — Hermes выполняет опасные команды без подтверждения
    # · Scenario: config.yaml → approvals.mode: off → агент удаляет файлы без спроса
    # · Last fail: never — guard test
    # · Remove if: approval logic перенесён на server-side (не в конфиге агента)
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_best_practices][approvals] START: checking approvals.mode != off")

        violations = []
        for name, path in all_config_yamls:
            with open(path) as f:
                try:
                    data = yaml.safe_load(f)
                except yaml.YAMLError as e:
                    logger.warning("[IMP:4][test_best_practices][approvals] YAML parse error: %s: %s", name, e)
                    continue

            if data and isinstance(data, dict):
                approvals = data.get("approvals", {})
                if isinstance(approvals, dict):
                    mode = approvals.get("mode", "")
                    if mode == "off":
                        violations.append(name)
                        logger.warning("[IMP:4][test_best_practices][approvals] VIOLATION: %s has mode=off", name)

        logger.critical(
            "[IMP:9][test_best_practices][approvals] ASSERT: configs checked=%d violations=%d (expected 0)",
            len(all_config_yamls),
            len(violations),
        )
        assert len(violations) == 0, f"approvals.mode must NOT be 'off': {violations}"


# endregion TESTS: approvals


# region TESTS: Telegram allowlist


@ldd_trajectory
def test_telegram_allowlist_in_env_example(caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_best_practices][telegram_allowlist] START: checking TELEGRAM_ALLOWED_USERS")

        violations = []
        for name, path in ENV_EXAMPLE_FILES:
            if not os.path.isfile(path):
                logger.info("[IMP:8][test_best_practices][telegram_allowlist] SKIP: not found: %s", path)
                continue

            with open(path) as f:
                content = f.read()

            if "TELEGRAM_ALLOWED_USERS" not in content:
                violations.append(name)
                logger.warning(
                    "[IMP:4][test_best_practices][telegram_allowlist] MISSING: %s lacks TELEGRAM_ALLOWED_USERS",
                    name,
                )

        logger.critical(
            "[IMP:9][test_best_practices][telegram_allowlist] ASSERT: violations=%d (expected 0)",
            len(violations),
        )
        assert len(violations) == 0, (
            f"TELEGRAM_ALLOWED_USERS must be in all .env.example files. Missing in: {violations}"
        )


@ldd_trajectory
def test_telegram_bot_token_in_env(caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_best_practices][telegram_token] START: checking TELEGRAM_BOT_TOKEN")

        violations = []
        for name, path in ENV_EXAMPLE_FILES:
            if not os.path.isfile(path):
                continue

            with open(path) as f:
                content = f.read()

            if "TELEGRAM_BOT_TOKEN" not in content:
                violations.append(name)
                logger.warning(
                    "[IMP:4][test_best_practices][telegram_token] MISSING: %s lacks TELEGRAM_BOT_TOKEN",
                    name,
                )

        logger.critical(
            "[IMP:9][test_best_practices][telegram_token] ASSERT: violations=%d (expected 0)",
            len(violations),
        )
        assert len(violations) == 0, f"TELEGRAM_BOT_TOKEN must be in all .env.example files. Missing in: {violations}"


# endregion TESTS: Telegram


# region TESTS: Dashboard security


@ldd_trajectory
def test_no_dashboard_insecure(all_compose_files, caplog) -> None:
    # 🧪 TRAP[TEST] · 2026-07-03 · dashboard insecure mode запрещён
    # · Regression: HERMES_DASHBOARD_INSECURE=1 — dashboard без Basic Auth
    # · Scenario: злоумышленник получает доступ к dashboard без пароля
    # · Last fail: never — guard test
    # · Remove if: dashboard auth вынесен на reverse proxy уровень
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_best_practices][insecure] START: checking no HERMES_DASHBOARD_INSECURE=1")

        violations = []
        for name, path in all_compose_files:
            with open(path) as f:
                content = f.read()

            # Check for HERMES_DASHBOARD_INSECURE anywhere in the file
            if "HERMES_DASHBOARD_INSECURE" in content:
                # Check it's not set to 1/true
                lines = content.split("\n")
                for i, line in enumerate(lines):
                    if "HERMES_DASHBOARD_INSECURE" in line:
                        value = line.split("=")[-1].strip().strip("'").strip('"')
                        if value in ("1", "true", "True"):
                            violations.append(f"{name}:{i + 1}")
                            logger.warning(
                                "[IMP:4][test_best_practices][insecure] VIOLATION: "
                                "%s line %d: HERMES_DASHBOARD_INSECURE=%s",
                                name,
                                i + 1,
                                value,
                            )

        logger.critical(
            "[IMP:9][test_best_practices][insecure] ASSERT: HERMES_DASHBOARD_INSECURE violations=%d (expected 0)",
            len(violations),
        )
        assert len(violations) == 0, f"HERMES_DASHBOARD_INSECURE must NEVER be 1: {violations}"


@ldd_trajectory
def test_dashboard_basic_auth_in_env(all_compose_files, caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_best_practices][basic_auth] START: checking Basic Auth env vars")

        violations = []
        for name, path in all_compose_files:
            with open(path) as f:
                content = f.read()

            has_username = "HERMES_DASHBOARD_BASIC_AUTH_USERNAME" in content
            has_password = "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD" in content

            if not (has_username and has_password):
                violations.append(name)
                logger.warning(
                    "[IMP:4][test_best_practices][basic_auth] MISSING: %s lacks Basic Auth vars "
                    "(username=%s password=%s)",
                    name,
                    has_username,
                    has_password,
                )

        logger.critical(
            "[IMP:9][test_best_practices][basic_auth] ASSERT: violations=%d (expected 0)",
            len(violations),
        )
        assert len(violations) == 0, f"Basic Auth env vars (HERMES_DASHBOARD_BASIC_AUTH_*) required: {violations}"


# endregion TESTS: Dashboard


# region TESTS: Secrets


@ldd_trajectory
def test_no_secrets_in_env_example_values(caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_best_practices][secrets_leak] START: checking no secrets in .env.example")

        secret_patterns = [
            r"sk-[a-zA-Z0-9]{20,}",  # OpenAI/DeepSeek style keys
            r"Bearer\s+[a-zA-Z0-9_\-]{20,}",
            r"token\s*=\s*['\"][a-zA-Z0-9]{20,}",
            r"API_KEY\s*=\s*['\"][a-zA-Z0-9]{20,}",
        ]

        violations = []
        for name, path in ENV_EXAMPLE_FILES:
            if not os.path.isfile(path):
                continue

            with open(path) as f:
                content = f.read()

            for i, pattern in enumerate(secret_patterns):
                matches = re.findall(pattern, content)
                if matches:
                    violations.append(f"{name}: pattern {i}: {len(matches)} matches")
                    logger.warning(
                        "[IMP:4][test_best_practices][secrets_leak] VIOLATION: %s matches secret pattern %d",
                        name,
                        i,
                    )

        logger.critical(
            "[IMP:9][test_best_practices][secrets_leak] ASSERT: secret leak violations=%d (expected 0)",
            len(violations),
        )
        assert len(violations) == 0, f"No secret values should leak in .env.example: {violations}"


@ldd_trajectory
def test_dotenv_in_gitignore(caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_best_practices][gitignore_env] START: checking .env in .gitignore")

        gitignore_paths: list[tuple[str, str]] = [
            ("hermes-agent", os.path.join(BASE_DIR, "core", "modules", "hermes-agent", ".gitignore")),
        ]

        violations = []
        for name, path in gitignore_paths:
            if not os.path.isfile(path):
                violations.append(f"{name}: .gitignore not found")
                logger.warning("[IMP:4][test_best_practices][gitignore_env] MISSING: %s/.gitignore", name)
                continue

            with open(path) as f:
                content = f.read()

            if ".env" not in content:
                violations.append(f"{name}: .env not in .gitignore")
                logger.warning("[IMP:4][test_best_practices][gitignore_env] MISSING: %s/.gitignore lacks .env", name)

        # Also check root .gitignore
        root_gitignore = os.path.join(BASE_DIR, ".gitignore")
        if os.path.isfile(root_gitignore):
            with open(root_gitignore) as f:
                content = f.read()
            if ".env" not in content:
                logger.info(
                    "[IMP:8][test_best_practices][gitignore_env] NOTE: root .gitignore does not list .env "
                    "(expected — secrets in nested agent repos)"
                )

        if not gitignore_paths:
            logger.info(
                "[IMP:8][test_best_practices][gitignore_env] SKIP: no template directories with .gitignore to check"
            )

        logger.critical(
            "[IMP:9][test_best_practices][gitignore_env] ASSERT: violations=%d (expected 0)",
            len(violations),
        )
        assert len(violations) == 0, f".env must be in .gitignore: {violations}"


# endregion TESTS: Secrets


# region TESTS: Healthcheck


@ldd_trajectory
def test_healthcheck_present(all_compose_files, caplog) -> None:
    # 🧪 TRAP[TEST] · 2026-07-03 · healthcheck обязателен во всех compose
    # · Regression: удалён healthcheck из compose → Docker не знает, жив ли контейнер
    # · Scenario: контейнер висит в состоянии (unhealthy) → deploy не замечает → production down
    # · Last fail: never — guard test
    # · Remove if: мониторинг полностью переведён на внешние probes (Prometheus blackbox)
    with caplog.at_level(logging.DEBUG):
        logger.info("[IMP:7][test_best_practices][healthcheck] START: checking healthcheck")

        violations = []
        for name, path in all_compose_files:
            with open(path) as f:
                content = f.read()

            if "healthcheck" not in content:
                violations.append(name)
                logger.warning("[IMP:4][test_best_practices][healthcheck] MISSING: %s has no healthcheck", name)

        logger.critical(
            "[IMP:9][test_best_practices][healthcheck] ASSERT: violations=%d (expected 0)",
            len(violations),
        )
        assert len(violations) == 0, f"All compose files must have healthcheck: {violations}"


# endregion TESTS: Healthcheck
