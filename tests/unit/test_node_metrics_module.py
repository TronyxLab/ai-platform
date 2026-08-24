# GREP_SUMMARY: test-node-metrics-module module-contract log-collector node-metrics service-exporters job-name prometheus-targets devplan-010-t3-1-t3-2
# STRUCTURE: ▶ 3 новых модуля (T3.1/T3.2) → ◇ файловый контракт + profiles + module.yaml → ⊕ job_name file_sd specs → ⎋ asserts (TEST_SPEC строка 436)
# region MODULE_CONTRACT
## @purpose  DevPlan 010 T3.1/T3.2 (TEST_SPEC): новые модули log-collector / node-metrics /
##           service-exporters проходят канонический модульный контракт (файловый состав,
##           profiles, module.yaml) и сохранение job_name scrape-целей Prometheus.
## @scope    core/modules/{log-collector,node-metrics,service-exporters} +
##           core/internal/monitoring/prometheus_targets.py
## @invariants
##   - Native imports; пути через Path(__file__) relative
##   - job_name 1:1 — ЛОВУШКА T3.3: дрейф имени job'а молча ломает дашборды/алерты
# endregion MODULE_CONTRACT

from __future__ import annotations

from pathlib import Path

from core.internal.monitoring import prometheus_targets

_MODULES_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "modules"
_NEW_MODULES: tuple[str, ...] = ("log-collector", "node-metrics", "service-exporters")

# Канонические job_name (прежние static_configs): переименование = silent break дашбордов/алертов
# REF-0010 (Волна 0, 2026-08-24): +pgbouncer-exporter, +langfuse-redis-exporter, +minio —
# легитимное расширение канона (новые scrape-цели); rename существующих по-прежнему запрещён.
_CANONICAL_JOB_NAMES: frozenset[str] = frozenset({
    "node-exporter",
    "cadvisor",
    "postgres-exporter",
    "redis-exporter",
    "nginx-exporter",
    "pgbouncer-exporter",
    "langfuse-redis-exporter",
    "minio",
})


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# 🧪 TRAP[TEST] · 2026-08-24 · REGRESSION · TEST_SPEC строка 436 — канонический контракт новых модулей
# · Scenario: split infra-metrics (T3.2) и выделение log-collector (T3.1) обязаны сохранить
# ·   файловый состав docker-модуля (module.yaml, base.yml с профилем=имени каталога,
# ·   healthcheck.sh, Makefile, .dockerignore symlink)
# · Last fail: N/A — guard против деградации структуры при будущих сплитах
# · Remove if: структура модуля (core/modules/AGENTS.md) пересмотрена каноном
def test_module_contract_files_and_profiles() -> None:
    """log-collector/node-metrics/service-exporters: файловый состав + profiles = имя каталога."""
    for module in _NEW_MODULES:
        module_dir = _MODULES_DIR / module
        assert module_dir.is_dir(), f"модуль {module} обязан существовать (DevPlan 010 §3)"
        for required in ("module.yaml", "docker-compose.base.yml", "healthcheck.sh", "Makefile"):
            assert (module_dir / required).is_file(), f"{module}: отсутствует {required}"
        compose_text = _read(module_dir / "docker-compose.base.yml")
        assert f"profiles: [{module}]" in compose_text, (
            f"{module}: base.yml обязан объявлять profiles: [{module}] (DD1 pluggability)"
        )
        # x-logging anchor — канон каждого docker-модуля
        assert "x-logging:" in compose_text and "&default-logging" in compose_text
        module_yaml = _read(module_dir / "module.yaml")
        assert f"name: {module}" in module_yaml, f"{module}: module.yaml name обязан совпадать с каталогом"


# 🧪 TRAP[TEST] · 2026-08-24 · REGRESSION · TEST_SPEC строка 436 — job_name scrape-целей сохранены
# · Scenario: миграция static→file_sd (T3.3) обязана сохранить имена job'ов 1:1
# ·   (ЛОВУШКА T3.3: rename = молчаливая поломка дашбордов/алертов Grafana)
# · Last fail: N/A
# · Remove if: job-модель Prometheus пересмотрена (тогда — синхронный перенос алертов/дашбордов)
def test_prometheus_job_names_preserved() -> None:
    """_NODE_TARGET_JOBS содержит все канонические job_name 1:1 (файл = job_name)."""
    actual = {job.file_name.removesuffix(".json") for job in prometheus_targets._NODE_TARGET_JOBS}
    missing = _CANONICAL_JOB_NAMES - actual
    assert not missing, f"prometheus_targets потерял канонические job_name (ЛОВУШКА T3.3): {sorted(missing)}"
    assert len(prometheus_targets._NODE_TARGET_JOBS) == len(_CANONICAL_JOB_NAMES)


# 🧪 TRAP[TEST] · 2026-08-24 · SCENARIO · SERVICE_BIND_HOST на публикуемых портах новых модулей
# · Scenario: node-metrics (9100/8080) и service-exporters (9187/9121/9113) биндят host-порты
# ·   через ${SERVICE_BIND_HOST:-127.0.0.1} (T2.2); log-collector НЕ публикует host-портов
# ·   (push-only коллектор — поверхность атаки минимальна)
# · Last fail: N/A
# · Remove if: порт-матрица T2.2 пересмотрена (TRAP[DECISION] firewall)
def test_split_modules_bind_contract() -> None:
    """node-metrics/service-exporters: bind через SERVICE_BIND_HOST; log-collector — без ports."""
    nm = _read(_MODULES_DIR / "node-metrics" / "docker-compose.base.yml")
    se = _read(_MODULES_DIR / "service-exporters" / "docker-compose.base.yml")
    lc = _read(_MODULES_DIR / "log-collector" / "docker-compose.base.yml")
    for name, text, port in (("node-metrics", nm, 9100), ("service-exporters", se, 9187)):
        assert "${SERVICE_BIND_HOST:-127.0.0.1}" in text, f"{name}: обязателен параметризованный bind (T2.2)"
        assert f":{port}" in text, f"{name}: ожидается публикация scrape-порта {port}"
    assert "ports:" not in lc, "log-collector — push-only, host-порты не публикуются"
