# GREP_SUMMARY: test-module-dsn-multinode langfuse litellm cross-node dsn postgres-host clickhouse-native-port docker-compose static-audit
# STRUCTURE: ▶ fixtures(langfuse_compose/litellm_compose) → (a) x-langfuse-env DATABASE_URL ${POSTGRES_HOST} + worker наследует якорь →
#            (b) CLICKHOUSE_MIGRATION_URL ${CLICKHOUSE_HOST}:${CLICKHOUSE_NATIVE_PORT} → (c) litellm DATABASE_URL ${POSTGRES_HOST} →
#            (d) S3-семантика комментарий (S3: 10.8.0.11:19000) → ⎋ 4 asserts + LDD
# region MODULE_CONTRACT
## @purpose  Кросс-нодовые DSN модулей (DevPlan 010 T2.7): langfuse/litellm docker-compose.base.yml
##           параметризованы ${POSTGRES_HOST:-pgbouncer}:6432 и
##           ${CLICKHOUSE_HOST:-clickhouse}:${CLICKHOUSE_NATIVE_PORT:-9000}; single-node дефолты —
##           Docker DNS (байт-идентично прежнему), multi-node provision подставит host data-ноды.
## @scope    Статический парс core/modules/{langfuse,litellm}/docker-compose.base.yml. Без Docker.
## @invariants
##   - (a) x-langfuse-env DATABASE_URL содержит ${POSTGRES_HOST:-pgbouncer}:6432; worker наследует
##     якорь БЕЗ собственного переопределения DSN (в файле ровно одно определение DATABASE_URL)
##   - (b) CLICKHOUSE_MIGRATION_URL = ${CLICKHOUSE_HOST:-clickhouse}:${CLICKHOUSE_NATIVE_PORT:-9000}
##   - (c) litellm DATABASE_URL содержит ${POSTGRES_HOST:-pgbouncer}:6432 (+environment-дефолт)
##   - (d) S3-семантика multi-node задокументирована комментарием (S3: 10.8.0.11:19000)
## @rationale T2.7 — кросс-нодовая адресация: единая точка правки (якорь), single-node поведение
##            неизменно; DRIFT-гейт против возврата к литералам pgbouncer/clickhouse:9000.
# endregion MODULE_CONTRACT

import logging
import pathlib
from pathlib import Path

import pytest
import yaml

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LANGFUSE_COMPOSE_PATH = _REPO_ROOT / "core" / "modules" / "langfuse" / "docker-compose.base.yml"
LITELLM_COMPOSE_PATH = _REPO_ROOT / "core" / "modules" / "litellm" / "docker-compose.base.yml"

# Кросс-нодовые DSN-маркеры (DevPlan 010 T2.7) — совпадают 1:1 с дефолтами compose
POSTGRES_DSN_MARKER = "${POSTGRES_HOST:-pgbouncer}:6432"
CH_MIGRATION_URL_MARKER = "${CLICKHOUSE_HOST:-clickhouse}:${CLICKHOUSE_NATIVE_PORT:-9000}"
# (d) S3-семантика: сценарий S3 «data/agent/apps+obs» — data-нода 10.8.0.11, CH native peer 19000
S3_COMMENT_MARKER = "S3: 10.8.0.11:19000"


@pytest.fixture(scope="module")
def langfuse_compose() -> dict:
    """Загрузить langfuse docker-compose.base.yml (YAML-якоря резолвятся в dict)."""
    with LANGFUSE_COMPOSE_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="module")
def litellm_compose() -> dict:
    """Загрузить litellm docker-compose.base.yml."""
    with LITELLM_COMPOSE_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# region FUNC_test_langfuse_database_url_cross_node
## @purpose  (a) langfuse DATABASE_URL в якоре x-langfuse-env параметризован; worker наследует якорь
## @io       ⇥ langfuse_compose: dict → ⎋ None (asserts)
## @complexity O(1) — чтение env-якоря + raw-text count
@pytest.mark.static_audit
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-08-22 · REGRESSION · (a) DATABASE_URL langfuse кросс-нодовая (DevPlan 010 T2.7)
# · Scenario: якорь x-langfuse-env обязан нести ${POSTGRES_HOST:-pgbouncer}:6432; worker не имеет
# ·   собственного переопределения DSN — наследует тот же якорь (правка одного места)
# · Last fail: N/A (preventive — T2.7 первичная реализация)
# · Remove if: langfuse DSN перестаёт быть кросс-нодово параметризованным (возврат к литералу)
def test_langfuse_database_url_cross_node(langfuse_compose: dict, caplog) -> None:
    """(a) x-langfuse-env DATABASE_URL @${POSTGRES_HOST:-pgbouncer}:6432; worker — без своего DSN."""
    logger.info("[IMP:7][test_langfuse_db_url] Parsing %s", LANGFUSE_COMPOSE_PATH.name)
    assert pathlib.Path(LANGFUSE_COMPOSE_PATH).is_file()

    env_anchor = langfuse_compose["x-langfuse-env"]
    database_url = env_anchor["DATABASE_URL"]
    logger.info("[IMP:8][test_langfuse_db_url] anchor DATABASE_URL=%s", database_url)
    assert POSTGRES_DSN_MARKER in database_url, (
        f"langfuse DATABASE_URL должен содержать {POSTGRES_DSN_MARKER}, got: {database_url}"
    )
    assert env_anchor["POSTGRES_HOST"] == "${POSTGRES_HOST:-pgbouncer}", (
        f"POSTGRES_HOST env-дефолт не задан, got: {env_anchor['POSTGRES_HOST']}"
    )

    # Worker наследует тот же якорь — ровно ОДНО определение DATABASE_URL в файле
    worker_env = langfuse_compose["services"]["langfuse-worker"]["environment"]
    assert worker_env["DATABASE_URL"] == database_url, (
        "langfuse-worker обязан наследовать DSN из якоря x-langfuse-env без переопределения"
    )
    raw_text = LANGFUSE_COMPOSE_PATH.read_text(encoding="utf-8")
    assert raw_text.count("DATABASE_URL:") == 1, (
        f"langfuse compose обязан определять DATABASE_URL только в якоре (worker наследует), "
        f"found {raw_text.count('DATABASE_URL:')} definitions"
    )

    logger.info("[IMP:9][test_langfuse_db_url] PASS: DATABASE_URL кросс-нодовый, worker наследует якорь")


# endregion FUNC_test_langfuse_database_url_cross_node


# region FUNC_test_langfuse_clickhouse_migration_url_cross_node
## @purpose  (b) CLICKHOUSE_MIGRATION_URL = ${CLICKHOUSE_HOST:-clickhouse}:${CLICKHOUSE_NATIVE_PORT:-9000}
## @io       ⇥ langfuse_compose: dict → ⎋ None (asserts)
## @complexity O(1) — чтение env-якоря
@pytest.mark.static_audit
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-08-22 · REGRESSION · (b) CLICKHOUSE_MIGRATION_URL кросс-нодовая (DevPlan 010 T2.7, TRAP §3)
# · Scenario: host/port параметризованы; multi-node provision подставит CH native peer 19000,
# ·   локально дефолт 9000 (коллизия с minio:9000 — host≠container прецедент, TRAP §3)
# · Last fail: N/A (preventive — T2.7 первичная реализация)
# · Remove if: CH native cross-node публикация отменяется (вернуть литерал clickhouse:9000)
def test_langfuse_clickhouse_migration_url_cross_node(langfuse_compose: dict, caplog) -> None:
    """(b) CLICKHOUSE_MIGRATION_URL использует ${CLICKHOUSE_HOST} и ${CLICKHOUSE_NATIVE_PORT}."""
    logger.info("[IMP:7][test_langfuse_ch_url] Checking CLICKHOUSE_MIGRATION_URL parametrization")
    env_anchor = langfuse_compose["x-langfuse-env"]
    migration_url = env_anchor["CLICKHOUSE_MIGRATION_URL"]
    logger.info("[IMP:8][test_langfuse_ch_url] CLICKHOUSE_MIGRATION_URL=%s", migration_url)
    assert CH_MIGRATION_URL_MARKER in migration_url, (
        f"CLICKHOUSE_MIGRATION_URL должен содержать {CH_MIGRATION_URL_MARKER}, got: {migration_url}"
    )
    # host/port обязаны быть параметризованы, а не литералами
    assert "clickhouse:9000" not in migration_url.replace("${CLICKHOUSE_HOST:-clickhouse}", "").replace(
        "${CLICKHOUSE_NATIVE_PORT:-9000}", ""
    ), f"CLICKHOUSE_MIGRATION_URL содержит литерал clickhouse:9000: {migration_url}"
    assert env_anchor["CLICKHOUSE_HOST"] == "${CLICKHOUSE_HOST:-clickhouse}"
    assert env_anchor["CLICKHOUSE_NATIVE_PORT"] == "${CLICKHOUSE_NATIVE_PORT:-9000}"

    logger.info("[IMP:9][test_langfuse_ch_url] PASS: CLICKHOUSE_MIGRATION_URL кросс-нодовая (host+port)")


# endregion FUNC_test_langfuse_clickhouse_migration_url_cross_node


# region FUNC_test_litellm_database_url_cross_node
## @purpose  (c) litellm DATABASE_URL = ${POSTGRES_HOST:-pgbouncer}:6432 (+environment-дефолт)
## @io       ⇥ litellm_compose: dict → ⎋ None (asserts)
## @complexity O(1) — чтение env-сервиса litellm
@pytest.mark.static_audit
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-08-22 · REGRESSION · (c) litellm DATABASE_URL кросс-нодовая (DevPlan 010 T2.7)
# · Scenario: DATABASE_URL несёт ${POSTGRES_HOST:-pgbouncer}:6432 + environment POSTGRES_HOST
# · Last fail: N/A (preventive — T2.7 первичная реализация)
# · Remove if: litellm DSN перестаёт быть кросс-нодово параметризованным (возврат к литералу)
def test_litellm_database_url_cross_node(litellm_compose: dict, caplog) -> None:
    """(c) litellm DATABASE_URL @${POSTGRES_HOST:-pgbouncer}:6432 + POSTGRES_HOST env-дефолт."""
    logger.info("[IMP:7][test_litellm_db_url] Parsing %s", LITELLM_COMPOSE_PATH.name)
    assert pathlib.Path(LITELLM_COMPOSE_PATH).is_file()

    env = litellm_compose["services"]["litellm"]["environment"]
    database_url = env["DATABASE_URL"]
    logger.info("[IMP:8][test_litellm_db_url] DATABASE_URL=%s", database_url)
    assert POSTGRES_DSN_MARKER in database_url, (
        f"litellm DATABASE_URL должен содержать {POSTGRES_DSN_MARKER}, got: {database_url}"
    )
    assert env["POSTGRES_HOST"] == "${POSTGRES_HOST:-pgbouncer}", (
        f"POSTGRES_HOST env-дефолт не задан, got: {env['POSTGRES_HOST']}"
    )

    logger.info("[IMP:9][test_litellm_db_url] PASS: litellm DATABASE_URL кросс-нодовый")


# endregion FUNC_test_litellm_database_url_cross_node


# region FUNC_test_s3_multinode_semantics_documented
## @purpose  (d) S3-семантика multi-node задокументирована комментарием (S3: 10.8.0.11:19000)
## @io       ⇥ caplog → ⎋ None (asserts)
## @complexity O(1) — raw-text substring
@pytest.mark.static_audit
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-08-22 · REGRESSION · (d) S3-семантика multi-node задокументирована (DevPlan 010 §6.1, W2)
# · Scenario: комментарий в langfuse compose фиксирует сценарий S3 «data/agent/apps+obs» —
# ·   data-нода 10.8.0.11, CH native peer 19000 (Acceptance W2: langfuse получает host 10.8.0.11, порт 19000)
# · Last fail: N/A (preventive — T2.7 первичная реализация)
# · Remove if: комментарий-документация перестаёт требоваться (семантика описана в другом SoT)
def test_s3_multinode_semantics_documented(caplog) -> None:
    """(d) Комментарий с S3-семантикой (S3: 10.8.0.11:19000) присутствует в langfuse compose."""
    raw_text = LANGFUSE_COMPOSE_PATH.read_text(encoding="utf-8")
    logger.info("[IMP:7][test_s3_semantics] Scanning %s for S3-семантики multi-node", LANGFUSE_COMPOSE_PATH.name)
    assert S3_COMMENT_MARKER in raw_text, (
        f"langfuse compose должен документировать S3-семантику multi-node комментарием "
        f"'{S3_COMMENT_MARKER}' (сценарий S3: data-нода 10.8.0.11, CH native peer 19000)"
    )
    assert "19000" in raw_text, "langfuse compose обязан упоминать CH native peer 19000"

    logger.info("[IMP:9][test_s3_semantics] PASS: S3-семантика задокументирована (%s)", S3_COMMENT_MARKER)


# endregion FUNC_test_s3_multinode_semantics_documented
