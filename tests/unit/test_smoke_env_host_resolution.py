# GREP_SUMMARY: smoke-env host-resolve url-template env-defaults compose dns container-port langfuse litellm nextauth REF-0017 R5-negative
# STRUCTURE: ▶ ┌SMOKE_ENV merge + provides templates┐ → ○ URL/dsn значения → ◇ host ∈ compose DNS ∪ port ∈ container-портов владельца → ⊕ violations → ⎋ PASS | RED
# region MODULE_CONTRACT
## @purpose  Host-resolve тест smoke-окружения (REF-0017): каждый URL/dsn, который платформа
##           эмитит потребителям (env_defaults в SMOKE_ENV-мерже + provides.*_template из
##           core/platform-infra.yaml), обязан резолвиться против фактической топологии
##           composes: hostname ∈ Docker DNS namespace (container_name | alias), порт —
##           контейнерно-слушающий (правая часть port-маппинга).
## @scope    Статический анализ: generated platform-env.yaml (env_defaults),
##           core/platform-infra.yaml (provides), core/modules/*/docker-compose.base.yml.
##           Docker daemon не требуется.
## @invariants
##   - Схемы проверки: http/https/postgresql/clickhouse (URL с портом); прочие схемы и
##     placeholder-host'ы (${DOMAIN}/${NAME} после подстановки) пропускаются
##   - Порт сверяется с портами сервиса-владельца hostname, не с глобальным union
##   - Источник env-значений — РЕАЛЬНЫЙ мерж SMOKE_ENV (_conftest.env.get_smoke_env):
##     тест видит то же окружение, что и smoke-тесты (merge env_defaults → static → generated)
##   - R5 negative: дрейф-фикстура langfuse:3001 (host-publish порт вместо слушающего) —
##     исходный вход бага REF-0017 — детектируется реальным резолвером
## @rationale AI-0001/SEC-0034: PLATFORM_LANGFUSE_URL=http://langfuse:3001 целился в
##            host-publish порт (в контейнере слушает 3000) → первый tracing-проект получил
##            бы connection refused при зелёных гейтах. Резолв-тест делает «URL бьёт в живой
##            порт» инвариантом, а не надеждой.
## @changes  2026-08-25 | REF-0017 (meta-refactoring 11-DevPlan, Волна 3) — Created
# endregion MODULE_CONTRACT

import re
from urllib.parse import urlparse

import yaml
from _conftest.env import get_smoke_env

from tests.helpers.gate_helpers import repo_root

ROOT = repo_root()
PLATFORM_INFRA = ROOT / "core" / "platform-infra.yaml"
MODULES_DIR = ROOT / "core" / "modules"

_SCHEMES_CHECKED = {"http", "https", "postgresql", "clickhouse", "redis"}
_PLACEHOLDER_HOSTS = {"ph", "placeholder"}
_CONTAINER_PORT_RE = re.compile(r":(\d+)\s*\"?\s*$")


# region FUNC_build_dns_port_map
def _build_dns_port_map(compose_map: dict[str, dict]) -> tuple[dict[str, set[int]], set[str]]:
    """Карта hostname → слушающих контейнерных портов + union всех имён.

    ## @io — ⇥ {module: {service: service_dict}} → ⎋ ({host: {ports}}, all_names)
    ## @complexity — O(M × S × P)
    ## @invariants
    ##   - Порт берётся из ПРАВОЙ части port-маппинга ("...:${HOST_VAR:-H}:CONTAINER") —
    ##     это то, что слушается внутри сети; левая часть — host-facade (REF-0017 ловушка)
    """
    host_ports: dict[str, set[int]] = {}
    all_names: set[str] = set()
    for services in compose_map.values():
        for svc_name, svc in services.items():
            names = {svc_name}
            if isinstance(svc.get("container_name"), str):
                names.add(svc["container_name"])
            net_cfg = svc.get("networks")
            if isinstance(net_cfg, dict):
                for net_data in net_cfg.values():
                    if isinstance(net_data, dict):
                        names.update(a for a in net_data.get("aliases") or [] if isinstance(a, str))
            all_names |= names
            ports: set[int] = host_ports.setdefault(svc_name, set())
            for mapping in svc.get("ports") or []:
                m = _CONTAINER_PORT_RE.search(str(mapping))
                if m:
                    ports.add(int(m.group(1)))
            for cname in names:
                host_ports.setdefault(cname, set()).update(ports)
    return host_ports, all_names


# endregion FUNC_build_dns_port_map


# region FUNC_resolve_urls
def _resolve_urls(
    urls: dict[str, str],
    host_ports: dict[str, set[int]],
    all_names: set[str],
) -> list[str]:
    """Резолв URL/dsn значений против DNS/port-карты. ⎋ список violations.

    ▶ ┌{var: url}┐ → ◇ strip ${...} → ◇ scheme ∈ checked ∧ host ∈ DNS ∧ port ∈ ports(owner) → ⊕ violations
    ## @purpose — Единый детектор для позитива и R5-negative (без инлайн-копий).
    ## @invariants
    ##   - ${VAR} плейсхолдеры заменяются на 'ph'; хост 'ph' (проект-специфичный домен) — skip
    ##   - URL без явного порта — проверяется только hostname
    ##   - Неизвестный hostname ИЛИ порт вне слушающих у владельца → violation
    """
    violations: list[str] = []
    for var, raw in urls.items():
        substituted = re.sub(r"\$\{[^}]*\}", "ph", str(raw))
        parsed = urlparse(substituted)
        if parsed.scheme not in _SCHEMES_CHECKED or not parsed.hostname:
            continue
        host = parsed.hostname
        if host in _PLACEHOLDER_HOSTS:
            continue
        if host not in all_names:
            violations.append(f"{var}: hostname '{host}' не резолвится ни одним сервисом composes")
            continue
        if parsed.port is not None:
            owner_ports = host_ports.get(host, set())
            if parsed.port not in owner_ports:
                violations.append(
                    f"{var}: порт {parsed.port} не слушается сервисом '{host}' "
                    f"(слушающие контейнерные порты: {sorted(owner_ports)})"
                )
    return violations


# endregion FUNC_resolve_urls


# region FUNC_load_fixtures
def _provides_urls(infra: dict) -> dict[str, str]:
    """URL/dsn шаблоны из provides (url_template + dsn_template)."""
    urls: dict[str, str] = {}
    for svc, data in (infra.get("provides") or {}).items():
        if isinstance(data, dict):
            if data.get("url_template"):
                urls[f"provides.{svc}.url_template"] = str(data["url_template"])
            if data.get("dsn_template"):
                urls[f"provides.{svc}.dsn_template"] = str(data["dsn_template"])
    return urls


def _build_runtime() -> tuple[dict[str, str], dict[str, set[int]], set[str]]:
    """Рабочие фикстуры: provides-шаблоны + SMOKE_ENV URL + DNS/port карта composes."""
    with PLATFORM_INFRA.open(encoding="utf-8") as f:
        infra = yaml.safe_load(f) or {}
    compose_map: dict[str, dict] = {}
    for module_dir in sorted(MODULES_DIR.iterdir()):
        compose = module_dir / "docker-compose.base.yml"
        if not compose.is_file():
            continue
        data = yaml.safe_load(compose.read_text(encoding="utf-8")) or {}
        compose_map[module_dir.name] = {
            name: svc for name, svc in (data.get("services") or {}).items() if isinstance(svc, dict)
        }
    host_ports, all_names = _build_dns_port_map(compose_map)
    smoke_urls = {
        var: val
        for var, val in get_smoke_env().items()
        if isinstance(val, str) and urlparse(re.sub(r"\$\{[^}]*\}", "ph", val)).scheme in _SCHEMES_CHECKED
    }
    return {**_provides_urls(infra), **smoke_urls}, host_ports, all_names


# endregion FUNC_load_fixtures


class TestSmokeEnvHostResolve:
    """REF-0017: эмитируемые URL указывают на живые hostname:порты топологии composes."""

    def test_provides_and_smoke_env_urls_resolve(self):
        """Все provides-шаблоны и URL из SMOKE_ENV резолвятся против composes."""
        urls, host_ports, all_names = _build_runtime()
        assert urls, "Нет URL для проверки — фикстуры пусты (test integrity)"
        violations = _resolve_urls(urls, host_ports, all_names)
        assert not violations, (
            "SMOKE_ENV/provides URL не резолвятся против фактической топологии:\n"
            + "\n".join(f"  {v}" for v in violations)
            + "\n\nИсправь SoT (core/platform-infra.yaml / env_defaults) на слушающий "
            "контейнерный порт и реальный alias, затем make generate-platform-env."
        )

    # 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · исходный вход REF-0017 (langfuse:3001)
    # · Last fail: PLATFORM_LANGFUSE_URL=http://langfuse:3001 — host-publish порт,
    #   в контейнере слушает 3000 → connection refused у первого tracing-проекта
    # · Remove if: резолв-детектор отменяется
    def test_drifted_host_publish_port_detected_negative(self):
        """R5 negative: URL в host-publish порт (не слушающий) → детектор ловит."""
        drifted = {"PLATFORM_LANGFUSE_URL": "http://langfuse:3001"}
        host_ports = {"langfuse": {3000}}
        all_names = {"langfuse"}
        violations = _resolve_urls(drifted, host_ports, all_names)
        assert any("3001" in v for v in violations), (
            "R5 FAIL: детектор не поймал исходный вход REF-0017 (URL в host-publish порт 3001)"
        )

    # 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · фантомный hostname обнаруживается
    # · Last fail: N/A (negative — парный к nginx-proxy классу фантомов)
    # · Remove if: резолв-детектор отменяется
    def test_phantom_hostname_detected_negative(self):
        """R5 negative: hostname вне compose DNS namespace → детектор ловит."""
        phantom = {"PROBE_URL": "http://no-such-service:1234"}
        violations = _resolve_urls(phantom, {}, set())
        assert len(violations) == 1, "R5 FAIL: фантомный hostname не обнаружен резолвером"
