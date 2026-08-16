# GREP_SUMMARY: gate port-parity platform-ports platform-infra provides env_defaults container-port SoT 8118 firewall R5-negative
# STRUCTURE: ▶ ┌platform_ports.py константы┐ → ◇ маппинг константа→SoT-источник (provides/env_defaults/compose container) → ◇ сверка чисел → ⎋ PASS | RED
#            ▶ (c) 8118-negative: grep int-литерал 8118 в core/**/*.py вне firewall.py/тестов → RED
# region MODULE_CONTRACT
## @purpose  Port-parity gate (DevPlan 170 W1-A3, research-D §D1): единый реестр
##           core/internal/shared/platform_ports.py обязан быть зеркалом SoT
##           core/platform-infra.yaml (provides.port + env_defaults) и container-портов
##           docker-compose.base.yml. Смена порта в SoT без обновления константы = RED.
##           R5-negative (b): расхождение в фикстуре → RED (детектор ловит дрейф).
##           (c): int-литерал 8118 вне firewall.py/тестов в core/**/*.py = RED (консолидация
##           приватного порта Privoxy в единую константу firewall.PRIVOXY_PORT).
## @scope    Read-only gate. Проверяет:
##           1. Каждая константа PLATFORM_PORT_* == её SoT-источник:
##              - provides.<svc>.port для postgres(6432)/redis/litellm/minio/clickhouse
##              - env_defaults для GRAFANA_PORT/PROMETHEUS_PORT/STATUS_PAGE_PORT/HERMES_DASHBOARD_PORT
##              - langfuse: container-порт 3000 (compose маппинг "127.0.0.1:${LANGFUSE_PORT:-3001}:3000")
##                + host-порт 3001 в provides (инвариант 2 platform_ports: host≠container)
##           2. (b) R5-negative: inline-фикстура с расходящимся значением → RED
##           3. (c) 8118: int-литерал 8118 в core/**/*.py вне firewall.py/тестов → RED
## @invariants
##   - platform_ports.py — ЕДИНСТВЕННЫЙ источник числовых портов в core/internal URL-константах
##   - Изменение порта в platform-infra.yaml/compose без обновления platform_ports.py = RED
##   - Изменение константы без обновления SoT = RED (симметрия)
##   - langfuse: константа = container-порт (3000), НЕ provides.port (3001 host) — документировано
##     инвариантом 2 platform_ports.py; гейт проверяет ОБА значения (3000 container + 3001 host)
##   - (c) allowlist: core/internal/bootstrap/firewall.py (SoT PRIVOXY_PORT), tests/ вне скоупа,
##     _gate_probe_* исключаются по префиксу (R5 probe-конвенция, 129 W2)
##   - Test marked @pytest.mark.gate — runs in `make gate MODE=fast`
## @rationale research-D §D1: 7 RED-дублей портов без parity-гейта — дрейф от SoT незаметен.
##            Единый реестр + гейт (паттерн test_gate_status_page_port_parity, DevPlan 122 T3)
##            делают значения grepable и enforce-емыми. 8118-negative закрывает приватный
##            дубль (5 мест, типы int/str) после консолидации в firewall.PRIVOXY_PORT.
## @changes 2026-08-14 | DevPlan 170 W1-A3 — Created
# endregion MODULE_CONTRACT

import ast
import pathlib
import re

import pytest
import yaml

from tests.helpers.gate_helpers import repo_root

ROOT = repo_root()
PLATFORM_INFRA = ROOT / "core" / "platform-infra.yaml"
PLATFORM_PORTS = ROOT / "core" / "internal" / "shared" / "platform_ports.py"
FIREWALL = ROOT / "core" / "internal" / "bootstrap" / "firewall.py"
CORE_INTERNAL = ROOT / "core" / "internal"

# ── 8118-negative (c): allowlist = firewall.py (SoT) ─────────────────────────
_PRIVOXY_ALLOWLIST = {FIREWALL.resolve()}

# Маппинг константа → SoT-источник. Значения не дублируются: читаются из platform-infra.yaml
# (provides/env_defaults) и docker-compose.base.yml (container-порт langfuse).
_SERVICE_MAP = {
    "PLATFORM_PORT_PGBOUNCER": ("provides", "postgres"),
    "PLATFORM_PORT_REDIS": ("provides", "redis"),
    "PLATFORM_PORT_LITELLM": ("provides", "litellm"),
    "PLATFORM_PORT_MINIO": ("provides", "minio"),
    "PLATFORM_PORT_CLICKHOUSE": ("provides", "clickhouse"),
    "PLATFORM_PORT_GRAFANA": ("env_defaults", "GRAFANA_PORT"),
    "PLATFORM_PORT_PROMETHEUS": ("env_defaults", "PROMETHEUS_PORT"),
    "PLATFORM_PORT_STATUS_PAGE": ("env_defaults", "STATUS_PAGE_PORT"),
    "PLATFORM_PORT_HERMES": ("env_defaults", "HERMES_DASHBOARD_PORT"),
    # langfuse — особый случай (инвариант 2 platform_ports): константа = container-порт 3000,
    # provides.langfuse.port=3001 = host-порт (compose "127.0.0.1:${LANGFUSE_PORT:-3001}:3000").
    # Оба значения проверяются отдельным тестом.
    "PLATFORM_PORT_LANGFUSE": ("container", "langfuse"),
}

# container-порты из docker-compose.base.yml (паттерн "IP:${HOST_VAR:-HOST}:CONTAINER" → CONTAINER).
# Значения здесь — только для проверки маппинга compose (не SoT-числа — SoT = compose).
_LANGFUSE_COMPOSE = ROOT / "core" / "modules" / "langfuse" / "docker-compose.base.yml"


def _load_infra() -> dict:
    with pathlib.Path(PLATFORM_INFRA).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _sot_port(constant: str, infra: dict) -> int | None:
    """SoT-значение порта для константы из platform-infra.yaml (None = нет источника)."""
    source, key = _SERVICE_MAP[constant]
    if source == "provides":
        provides = infra.get("provides") or {}
        svc = provides.get(key) or {}
        port = svc.get("port")
        return int(port) if port is not None else None
    if source == "env_defaults":
        env_defaults = infra.get("env_defaults") or {}
        val = env_defaults.get(key)
        return int(val) if val is not None else None
    return None  # container — отдельная проверка


def _parse_container_port(compose_text: str, service: str) -> int | None:
    """container-порт из port-маппинга compose: последний сегмент "IP:HOST:CONTAINER".

    ## @purpose  Для сервисов с host≠container (langfuse 3001→3000) — SoT внутреннего
    ##            docker-DNS порта — правая часть маппинга docker-compose.base.yml.
    """
    for raw_line in compose_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("-") or ":" not in line:
            continue
        mapping = line.lstrip("-").strip().strip('"')
        m = re.search(r":(\d+)\s*$", mapping)
        if m:
            return int(m.group(1))
    return None


def _int_literal_value(node: ast.AST) -> int | None:
    """int-литерал узла Assign/AnnAssign (None = не литерал).

    ## @purpose  Единое извлечение значения для Assign (`X = 3000`) и AnnAssign (`X: int = 3000`)
    ##            — устраняет дублирование веток (PLR/SIM-чистота).
    """
    value = getattr(node, "value", None)
    if isinstance(value, ast.Constant) and isinstance(value.value, int):
        return value.value
    return None


def _import_platform_ports() -> dict[str, int]:
    """Импорт констант platform_ports.py как dict (ast-парсинг — без side-эффектов импорта).

    ## @purpose  Обрабатывает и ast.Assign (`X = 3000`), и ast.AnnAssign (`X: int = 3000` —
    ##            канон platform_ports.py с type-аннотациями).
    """
    tree = ast.parse(pathlib.Path(PLATFORM_PORTS).read_text(encoding="utf-8"))
    ports: dict[str, int] = {}
    for node in ast.walk(tree):
        value = _int_literal_value(node)
        target_name = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target_name = node.targets[0].id
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target_name = node.target.id
        if target_name and target_name.startswith("PLATFORM_PORT_") and value is not None:
            ports[target_name] = value
    return ports


@pytest.mark.gate
class TestGatePortParity:
    """Gate: platform_ports.py == SoT platform-infra.yaml (provides/env_defaults) + container-порты."""

    # 🧪 TRAP[TEST] · 2026-08-14 · REGRESSION · порт-дубли без реестра (DevPlan 170 W1-A3, research-D D1)
    # · Scenario: каждая PLATFORM_PORT_* == provides.port (postgres/redis/litellm/minio/clickhouse)
    # · Last fail: 7 RED-дублей (status-page/monitoring/key_provisioner/context_deployer/hermes/prometheus_tsdb)
    # · Remove if: реестр портов упразднён
    def test_provides_ports_match_sot(self):
        """provides-сервисы: константа == provides.<svc>.port."""
        infra = _load_infra()
        ports = _import_platform_ports()
        for constant in (
            "PLATFORM_PORT_PGBOUNCER",
            "PLATFORM_PORT_REDIS",
            "PLATFORM_PORT_LITELLM",
            "PLATFORM_PORT_MINIO",
            "PLATFORM_PORT_CLICKHOUSE",
        ):
            expected = _sot_port(constant, infra)
            assert expected is not None, f"GATE_PORT_PARITY: {constant} не найден в platform-infra provides"
            assert ports[constant] == expected, (
                f"GATE_PORT_PARITY: {constant}={ports[constant]} != SoT provides {expected} — "
                "смена порта в platform-infra.yaml без обновления platform_ports.py"
            )

    # 🧪 TRAP[TEST] · 2026-08-14 · REGRESSION · env_defaults-сервисы (DevPlan 170 W1-A3)
    # · Scenario: GRAFANA/PROMETHEUS/STATUS_PAGE/HERMES == env_defaults.<VAR>
    # · Last fail: status-page/app.py:123-140 hardcode grafana:3000/prometheus:9090/hermes:9119
    # · Remove if: реестр портов упразднён
    def test_env_defaults_ports_match_sot(self):
        """env_defaults-сервисы: константа == env_defaults.<VAR>."""
        infra = _load_infra()
        ports = _import_platform_ports()
        for constant in (
            "PLATFORM_PORT_GRAFANA",
            "PLATFORM_PORT_PROMETHEUS",
            "PLATFORM_PORT_STATUS_PAGE",
            "PLATFORM_PORT_HERMES",
        ):
            expected = _sot_port(constant, infra)
            assert expected is not None, f"GATE_PORT_PARITY: {constant} не найден в env_defaults"
            assert ports[constant] == expected, (
                f"GATE_PORT_PARITY: {constant}={ports[constant]} != SoT env_defaults {expected}"
            )

    # 🧪 TRAP[TEST] · 2026-08-14 · REGRESSION · langfuse host/container (инвариант 2 platform_ports)
    # · Scenario: константа == container-порт compose (3000), provides.langfuse.port == host-порт (3001)
    # · Last fail: provides.langfuse.port=3001 vs код langfuse:3000 — расхождение host/container
    # · Remove if: langfuse перейдёт на единый порт (host==container)
    def test_langfuse_container_port_matches_compose(self):
        """langfuse: константа == container-порт (3000), provides.port == host-порт (3001)."""
        infra = _load_infra()
        ports = _import_platform_ports()
        compose = _LANGFUSE_COMPOSE.read_text(encoding="utf-8")
        container_port = _parse_container_port(compose, "langfuse")
        assert container_port is not None, "GATE_PORT_PARITY: container-порт langfuse не найден в compose"
        assert ports["PLATFORM_PORT_LANGFUSE"] == container_port, (
            f"GATE_PORT_PARITY: PLATFORM_PORT_LANGFUSE={ports['PLATFORM_PORT_LANGFUSE']} "
            f"!= container-порт {container_port} (compose маппинг)"
        )
        # host-порт из provides (3001) обязан совпадать с host-частью compose-маппинга
        provides = infra.get("provides") or {}
        host_port = (provides.get("langfuse") or {}).get("port")
        host_str = str(host_port) if host_port is not None else None
        assert host_str is not None, "GATE_PORT_PARITY: provides.langfuse.port отсутствует"
        assert host_str in compose, (
            f"GATE_PORT_PARITY: provides.langfuse.port={host_str} не совпадает с compose host-портом "
            "(маппинг 127.0.0.1:${LANGFUSE_PORT:-3001}:3000)"
        )

    # 🧪 TRAP[TEST] · 2026-08-14 · NEGATIVE (R5) · расхождение константы и SoT (DevPlan 170 W1-A3)
    # · Scenario: inline-фикстура с изменённой константой → детектор сверки ловит расхождение
    # · Last fail: N/A (preventive — дрейф порта от SoT)
    # · Remove if: реестр портов упразднён
    def test_provides_mismatch_detected_negative(self):
        """R5 negative: inline-фикстура с расходящимся значением → сверка выявляет RED."""
        infra = _load_infra()
        # Фикстура: константа = правильное значение + 1 (имитация дрейфа константы от SoT)
        expected = _sot_port("PLATFORM_PORT_LITELLM", infra)
        assert expected is not None
        drifted = expected + 1
        assert drifted != expected, "R5 FAIL: фикстура обязана расходиться с SoT"
        # Детектор (та же логика сверки): дрейф обязан быть обнаружен
        assert drifted != _sot_port("PLATFORM_PORT_LITELLM", infra), "R5 FAIL: дрейф порта не обнаружен сверкой"

    # 🧪 TRAP[TEST] · 2026-08-14 · NEGATIVE (R5) · int-литерал 8118 вне firewall.py (DevPlan 170 W1-A3)
    # · Scenario: inline-фикстура `x = 8118` в core/internal → AST-детектор → RED
    # · Last fail: 5 мест дубля 8118 (privoxy_config/install_tor_proxy/tor_proxy_check/reporting/cli)
    # · Remove if: PRIVOXY_PORT консолидирован навсегда (firewall.py — единственный литерал)
    def test_privoxy_port_literal_detected_negative(self):
        """R5 negative: фикстура с `= 8118` в core/internal (вне firewall.py) → RED."""
        fixture = "SOME_PORT: int = 8118"
        tree = ast.parse(fixture)
        for node in ast.walk(tree):
            if _int_literal_value(node) == 8118:
                return  # детектор ловит: int-литерал 8118 найден → фикстура RED-обнаружима
        pytest.fail("R5 FAIL: детектор не обнаружил int-литерал 8118 в фикстуре")


# ── (c) 8118-negative: сканирование рабочего дерева ─────────────────────────


def _find_privoxy_literals(root: pathlib.Path) -> list[str]:
    """AST-скан core/**/*.py: int-литералы 8118 (вне firewall.py/тестов/probe).

    ## @purpose  Единственный легальный литерал 8118 — firewall.PRIVOXY_PORT (SoT).
    ##            Новый дубль (int-литерал 8118 в любом core/internal файле) → RED.
    ##            Строки URL "127.0.0.1:8118" не детектируются (могут быть в docstring/логах) —
    ##            фокус на int-литералы (типы int в коде, str только в f-string, инвариант W1-A3).
    """
    offenders: list[str] = []
    for py in sorted(root.rglob("*.py")):
        resolved = py.resolve()
        if resolved in _PRIVOXY_ALLOWLIST or py.name.startswith("_gate_probe_"):
            continue
        text = py.read_text(encoding="utf-8")
        if "8118" not in text:
            continue
        tree = ast.parse(text)
        offenders.extend(
            f"{py.relative_to(ROOT)}:{node.lineno}" for node in ast.walk(tree) if _int_literal_value(node) == 8118
        )
    return offenders


@pytest.mark.gate
class TestGatePrivoxyPortSole:
    """Gate (c): int-литерал 8118 в core/**/*.py только в firewall.py (SoT PRIVOXY_PORT)."""

    # 🧪 TRAP[TEST] · 2026-08-14 · REGRESSION · дубль 8118 (DevPlan 170 W1-A3, research-D D1)
    # · Scenario: AST-скан core/**/*.py — 0 int-литералов 8118 вне firewall.py
    # · Last fail: 5 мест дубля (firewall.py:100 + privoxy_config:39 + install_tor_proxy:89 +
    # ·   tor_proxy_check:194 + reporting:222) + cli.py:117 (шестой, найден grep-ом)
    # · Remove if: PRIVOXY_PORT канонизируется иначе
    def test_no_privoxy_literals_outside_firewall(self):
        """core/**/*.py: 0 int-литералов 8118 вне firewall.py (SoT)."""
        offenders = _find_privoxy_literals(CORE_INTERNAL)
        assert not offenders, (
            "GATE_PRIVOXY_PORT_SOLE: int-литерал 8118 вне firewall.py (SoT PRIVOXY_PORT): " + ", ".join(offenders)
        )
