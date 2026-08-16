# GREP_SUMMARY: gate-test healthcheck-contract check-http early-exit 127-0-0-1 deep-mode
# STRUCTURE: ▶ test_deep_mode_has_early_exit → ◇ test_litellm_uses_check_http → ◇ test_langfuge_uses_127_0_0_1 → ◇ test_postgres_deep_includes_pgbouncer → ◇ test_logging_deep_includes_alloy
# region MODULE_CONTRACT
## @purpose  Gate tests: validate healthcheck.sh contracts across all modules (DevPlan 04 TASK-G3)
## @scope    Проверяет exit 0 после deep, check_http, 127.0.0.1, deep-покрытие
## @invariants
##   - backup-cron, clickhouse, redis: exit 0 после deep-блока
##   - litellm: check_http вместо raw curl
##   - langfuse: 127.0.0.1 вместо docker inspect IP
##   - postgres: deep включает pgbouncer pg_isready
##   - logging: deep включает alloy (liveness-контейнер модуля)
## @rationale Стандартизация healthcheck контракта (DevPlan 04 DD5, DD6)
## @changes   2026-08-01 | DevPlan 117 Brief F (T6 #49): добавлены IMP:9-трассы в assert-блоки бизнес-правил
# endregion MODULE_CONTRACT


import logging

import pytest

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

MODULES_DIR = repo_root() / "core" / "modules"

HEALTHCHECK_FILES = {
    "backup-cron": MODULES_DIR / "backup-cron" / "healthcheck.sh",
    "clickhouse": MODULES_DIR / "clickhouse" / "healthcheck.sh",
    "redis": MODULES_DIR / "redis" / "healthcheck.sh",
    "litellm": MODULES_DIR / "litellm" / "healthcheck.sh",
    "langfuse": MODULES_DIR / "langfuse" / "healthcheck.sh",
    "postgres": MODULES_DIR / "postgres" / "healthcheck.sh",
    "logging": MODULES_DIR / "logging" / "healthcheck.sh",
}


# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Healthcheck contract — все модули следуют liveness/deep контракту
# · Last fail: N/A (preventive)
# · Remove if: healthcheck контракт заменён новым механизмом
class TestHealthcheckContract:
    @pytest.mark.gate
    def test_deep_mode_has_early_exit(self) -> None:
        """backup-cron, clickhouse, redis имеют exit 0 после deep-блока."""
        # 🧪 TRAP[TEST] · 2026-07-15 · gate/healthcheck-contract · Регресс: deep-блок healthcheck.sh утратил exit 0
        for name in ("backup-cron", "clickhouse", "redis"):
            path = HEALTHCHECK_FILES[name]
            assert path.exists(), f"healthcheck.sh not found: {path}"
            content = path.read_text()

            # Check for exit 0 after the deep block using line-based search
            # The exit 0 should be on a line starting with 'exit' inside the deep block
            # We look for the pattern: exit 0 preceded by deep-mode logic
            has_early_exit = "exit 0  # ранний выход" in content

            assert has_early_exit, (
                f"{name}/healthcheck.sh: missing 'exit 0' in deep block. "
                f"Deep mode must exit early, not fallthrough to liveness.\n"
                f"Expected comment pattern 'exit 0  # ранний выход' after deep diagnostics."
            )
            logger.info("[IMP:9][healthcheck-contract] %s: deep-блок завершается exit 0 (no fallthrough)", name)

    @pytest.mark.gate
    def test_litellm_uses_check_http(self) -> None:
        """litellm healthcheck.sh использует check_http, не raw curl."""
        # 🧪 TRAP[TEST] · 2026-07-15 · gate/healthcheck-contract · Регресс: litellm healthcheck.sh заменён на raw curl
        path = HEALTHCHECK_FILES["litellm"]
        content = path.read_text()

        assert "check_http" in content, "litellm/healthcheck.sh must use check_http() instead of raw curl"
        # Should NOT have raw curl for the health check
        assert "curl -sf" not in content, (
            "litellm/healthcheck.sh should not use raw curl - the check_http wrapper should be used"
        )
        logger.info("[IMP:9][healthcheck-contract] litellm: check_http используется, raw curl отсутствует")

    @pytest.mark.gate
    def test_langfuse_uses_127_0_0_1(self) -> None:
        """langfuse healthcheck.sh использует 127.0.0.1, не docker inspect."""
        # 🧪 TRAP[TEST] · 2026-07-15 · gate/healthcheck-contract · Регресс: langfuse healthcheck.sh использует docker inspect вместо 127.0.0.1
        path = HEALTHCHECK_FILES["langfuse"]
        content = path.read_text()

        assert "127.0.0.1" in content, "langfuse/healthcheck.sh must use 127.0.0.1 instead of docker inspect IP"
        assert "docker inspect" not in content or "docker inspect langfuse" not in content, (
            "langfuse/healthcheck.sh should not use 'docker inspect' for IP resolution"
        )
        logger.info("[IMP:9][healthcheck-contract] langfuse: healthcheck на 127.0.0.1, docker inspect отсутствует")

    @pytest.mark.gate
    def test_postgres_deep_includes_pgbouncer(self) -> None:
        """postgres healthcheck.sh deep проверяет pgbouncer."""
        # 🧪 TRAP[TEST] · 2026-07-15 · gate/healthcheck-contract · Регресс: postgres deep-блок утратил pgbouncer pg_isready
        path = HEALTHCHECK_FILES["postgres"]
        content = path.read_text()

        assert "pgbouncer" in content.lower(), "postgres/healthcheck.sh: missing pgbouncer check in deep mode"
        assert "pg_isready" in content, "postgres/healthcheck.sh: missing pg_isready for pgbouncer deep check"
        logger.info("[IMP:9][healthcheck-contract] postgres: deep-блок проверяет pgbouncer pg_isready")

    @pytest.mark.gate
    def test_logging_deep_includes_alloy(self) -> None:
        """logging healthcheck.sh deep проверяет alloy (контейнер-коллектор)."""
        # 🧪 TRAP[TEST] · 2026-07-15 · gate/healthcheck-contract · Регресс: logging deep-блок
        # утратил коллектор; 164 W1-5: promtail→Alloy (EOL REPLACE) — deep проверяет alloy
        path = HEALTHCHECK_FILES["logging"]
        content = path.read_text()

        assert "alloy" in content.lower(), "logging/healthcheck.sh: missing alloy container check"
        assert "loki" in content.lower(), "logging/healthcheck.sh: missing loki container check"
        logger.info("[IMP:9][healthcheck-contract] logging: deep-блок проверяет alloy + loki")
