#!/usr/bin/env python3
# GREP_SUMMARY: gate-test compose external-port-binding 0-0-0-0 127-0-0-1 loopback 80-443 allowlist nginx security docker-compose base.yml DevPlan-162
# STRUCTURE: ○ scan base.yml + root compose → ○ parse ports (str/dict/${VAR}/host_ip) → ◇ host=0.0.0.0|empty|env-var ∧ host-port ∉ {80,443} → ⟦RED: violations⟧ → ⎋ PASS
# region MODULE_CONTRACT
## @purpose  Gate test (DevPlan 162 W2-2): запрет external port bindings в docker-compose.base.yml
##           модулей И root docker-compose.yml. Docker bypass ufw — любой 0.0.0.0-published порт
##           доступен из интернета (Docker вставляет DNAT→FORWARD мимо ufw INPUT). Разрешено:
##           loopback-биндинги (127.0.0.1) и публичные порты nginx {80,443} (allowlist). Runtime-
##           кросс-чек — S9 security_posture.py (подтверждён FAIL в 162 W2-2); этот gate закрывает
##           ДЕКЛАРАТИВНУЮ сторону (compose-контракт) в CI, до деплоя.
## @scope    Статический анализ: core/modules/*/docker-compose.base.yml + docker-compose.yml (root).
##           Парсит строковый ("80:80", "0.0.0.0:80:80", "127.0.0.1:${X:-8080}:8080") и dict-формат
##           ({"published": 80, "host_ip": ...}) портов; толерантен к ${VAR:-default} синтаксису
##           (default-порт извлекается для host-side определения). Docker daemon НЕ требуется.
## @invariants
##   - Запрещённые host-биндинги: "0.0.0.0:", пустой host (""), env-var host-сторона, конкретный
##     non-loopback IP — ЕСЛИ host-порт ∉ {80, 443}
##   - 127.0.0.1 (и ::1) — всегда разрешено (loopback-контроль)
##   - 80/443 — allowlist nginx (public by-design), разрешены с ЛЮБОГО host (0.0.0.0/empty/env)
##   - Строка без host-части ("8080" одна часть) = publish на случайный host-порт всех интерфейсов →
##     нарушение (host-порт неизвестен, не может быть гарантирован ∈ {80,443})
##   - dict {"published": N} без host_ip → external (0.0.0.0 эквивалент) → проверка по порту
##   - Ключ "target" без "published" (container-only expose) → НЕ host-публикация → не флагается
## @rationale Корень проблемы 162 W2-2: compose публикуют 0.0.0.0:<port> для внутренних модулей;
##            S9 детектит runtime (docker-proxy LISTEN), но fix в compose-контракте дешевле —
##            gate блокирует регрессию до деплоя (fail-fast в CI вместо инцидента на проде).
## @changes 2026-08-13 | DevPlan 162 W2-2 — Created (compose-gate 0.0.0.0 вне {80,443})
# endregion MODULE_CONTRACT

import logging
import re
from pathlib import Path

import pytest
import yaml

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
PUBLIC_ALLOW_PORTS: set[int] = {80, 443}
LOOPBACK_HOSTS: tuple[str, ...] = ("127.0.0.1", "::1")
_PORT_RE = re.compile(r"^(\d+)$")
_ENV_DEFAULT_RE = re.compile(r"^\$\{([^}:]+)(?::-([^}]+))?\}$")


def _compose_files() -> list[Path]:
    """Целевые compose-файлы: корневой + все модульные base.yml (отсортированы — детерминизм)."""
    files = [ROOT_DIR / "docker-compose.yml"]
    files.extend(sorted((ROOT_DIR / "core" / "modules").glob("*/docker-compose.base.yml")))
    return [f for f in files if f.is_file()]


def _resolve_host_port(value: str) -> int | None:
    """Host-порт из host-стороны: число или ${VAR:-default} → default; иначе None (неизвестен)."""
    stripped = value.strip()
    if _PORT_RE.match(stripped):
        return int(stripped)
    m = _ENV_DEFAULT_RE.match(stripped)
    if m and m.group(2) is not None and _PORT_RE.match(m.group(2)):
        return int(m.group(2))
    return None


def _split_entry(entry: str) -> list[str]:
    """Split на ':' ВНЕ ${...} конструкций (${VAR:-80} содержит двоеточие в default-части)."""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for ch in entry:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        if ch == ":" and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


def _parse_short_entry(entry: str) -> tuple[str, int | None]:
    """Разобрать строковый port mapping → (host_ip, host_port).

    Формы: "80:80" (host=пусто), ":80:80" (host_ip=пусто), "0.0.0.0:80:80",
    "127.0.0.1:${X:-9119}:9119", "${X:-80}:80", "8080" (container-only → random host port).
    Split — brace-aware: "${X:-80}" содержит ':' в default-части, его нельзя резать.
    """
    parts = _split_entry(entry)
    if len(parts) == 1:
        # container-only publish → случайный host-порт на всех интерфейсах (0.0.0.0)
        return "", None
    if len(parts) == 2:
        host, _container = parts
        # host-сторона: число = bind-all (""), env-var = bind-all, IP (без container?) — 2-я часть
        # по compose-spec всегда container-порт, host_ip не может быть в 2-частной форме
        if _PORT_RE.match(host.strip()) or host.strip().startswith("${"):
            return "", _resolve_host_port(host)
        return host.strip(), _resolve_host_port(host)
    host_ip, host, _container = parts[0], parts[1], parts[2]
    return host_ip.strip(), _resolve_host_port(host)


def _parse_long_entry(entry: dict) -> tuple[str, int | None] | None:
    """Разобрать dict port mapping → (host_ip, host_port); None = нет host-публикации (target-only)."""
    published = entry.get("published")
    if published is None:
        return None  # target-only — container expose, не host-публикация
    host_ip = str(entry.get("host_ip", "")).strip() if entry.get("host_ip") else ""
    try:
        host_port = int(published)
    except (TypeError, ValueError):
        host_port = _resolve_host_port(str(published)) if isinstance(published, str) else None
    return host_ip, host_port


def _check_binding(host_ip: str, host_port: int | None) -> list[str]:
    """Вернуть violation-сообщения для одного bindings (пусто = допустим)."""
    if host_ip in LOOPBACK_HOSTS:
        return []  # loopback — всегда разрешено (127.0.0.1 control)
    if host_port in PUBLIC_ALLOW_PORTS:
        return []  # allowlist nginx 80/443 — public by design
    # External: 0.0.0.0 / empty / env-var / non-loopback IP с портом вне {80,443}
    # (или неизвестным — не можем гарантировать ∈ {80,443} → безопасный fail)
    shown_port = host_port if host_port is not None else "<random/env-unknown>"
    shown_host = host_ip or "<0.0.0.0/empty>"
    return [f"external binding {shown_host}:{shown_port} — запрещён (разрешены 127.0.0.1 или 80/443)"]


def _scan_compose(compose_path: Path) -> list[str]:
    """Сканировать один compose-файл → список violation-строк (пусто = чисто)."""
    try:
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        return [f"{compose_path.name}: unparseable YAML: {e}"]
    if not isinstance(data, dict):
        return []
    violations: list[str] = []
    services = data.get("services", {}) or {}
    for svc_name, svc_config in services.items():
        if not isinstance(svc_config, dict):
            continue
        ports = svc_config.get("ports") or []
        for idx, entry in enumerate(ports, 1):
            if isinstance(entry, str):
                host_ip, host_port = _parse_short_entry(entry)
                problems = _check_binding(host_ip, host_port)
            elif isinstance(entry, dict):
                parsed = _parse_long_entry(entry)
                if parsed is None:
                    continue  # target-only
                host_ip, host_port = parsed
                problems = _check_binding(host_ip, host_port)
            else:
                problems = [f"unsupported ports entry type: {type(entry).__name__}"]
            violations.extend(f"{compose_path.name}:{svc_name}:ports[{idx}]: {problem}" for problem in problems)
    return violations


@pytest.mark.gate

# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · compose external port binding (audit 2026-08-13)
# · Scenario: Docker bypass ufw — любой 0.0.0.0-published порт доступен из интернета (DNAT→FORWARD);
# ·   корень 162 W2-2: compose публикуют 0.0.0.0:<port> для внутренних модулей
# · Last fail: 2026-08-13 — iptables -L DOCKER-USER = 0 правил; внутренние порты модулей
# ·   (postgres/minio/clickhouse/...) в base.yml были 127.0.0.1-bound (уже исправлено); gate
# ·   фиксирует контракт, чтобы регрессия не вернулась
# · Remove if: политика публикации портов изменена через TRAP[DECISION]
def test_no_external_port_binding_outside_80_443() -> None:
    """Все base.yml + root compose: host-bindings вне {80,443} обязаны быть 127.0.0.1."""
    all_violations: list[str] = []
    scanned = 0
    for compose_path in _compose_files():
        scanned += 1
        violations = _scan_compose(compose_path)
        if violations:
            logger.info("[IMP:8][gate] %s: %d violation(s)", compose_path.name, len(violations))
        all_violations.extend(violations)

    assert not all_violations, "GATE_NO_EXTERNAL_PORT_BINDING (162 W2-2):\n  " + "\n  ".join(all_violations)
    logger.info("[IMP:9][gate] PASS: %d compose файлов — все host-bindings 127.0.0.1 или {80,443}", scanned)


@pytest.mark.gate

# 🧪 TRAP[TEST] · NEGATIVE (R5) · 2026-08-13 · парсер ловит 0.0.0.0/empty host-биндинги
# · Scenario: точные входы аудита (0.0.0.0:8080:80, "8080" random-host, dict без host_ip) →
# ·   violation; 127.0.0.1 и 80/443 → allowed
# · Last fail: 2026-08-13 — gate-логика не существовала (корень аудита)
# · Remove if: парсер port-bindings изменён
def test_binding_detection_negative_cases() -> None:
    """Negative (R5): нарушающие входы обязаны детектиться; легальные — нет."""
    assert _check_binding("0.0.0.0", 8080), "0.0.0.0:8080 обязан быть violation"
    assert _check_binding("", 8080), "empty-host 8080 обязан быть violation"
    assert _check_binding("", None), "container-only (random host port) обязан быть violation"
    assert _check_binding("192.168.1.5", 8080), "non-loopback IP 8080 обязан быть violation"
    # loopback + allowlist — допустимы
    assert not _check_binding("127.0.0.1", 8080), "127.0.0.1:8080 — loopback, разрешён"
    assert not _check_binding("::1", 8080), "::1:8080 — loopback, разрешён"
    assert not _check_binding("", 80), "empty-host:80 — nginx allowlist"
    assert not _check_binding("0.0.0.0", 443), "0.0.0.0:443 — nginx allowlist"


@pytest.mark.gate

# 🧪 TRAP[TEST] · REGRESSION · 2026-08-13 · ${VAR} host-сторона парсится через default
# · Scenario: "${NGINX_HTTP_PORT:-80}:80" → host-port 80 (allowlist); "127.0.0.1:${X:-9119}:9119" →
# ·   loopback; "${X:-8080}:80" → 8080 вне allowlist → violation
# · Last fail: N/A (новый кейс DevPlan 162 W2-2)
# · Remove if: ${VAR} синтаксис перестанет поддерживаться compose
def test_env_var_host_side_parsing() -> None:
    host_ip, host_port = _parse_short_entry("${NGINX_HTTP_PORT:-80}:80")
    assert not host_ip and host_port == 80
    assert not _check_binding(host_ip, host_port), "nginx ${NGINX_HTTP_PORT:-80}:80 — allowlist"

    host_ip, host_port = _parse_short_entry("127.0.0.1:${HERMES_DASHBOARD_PORT:-9119}:9119")
    assert host_ip == "127.0.0.1" and host_port == 9119
    assert not _check_binding(host_ip, host_port), "127.0.0.1 env-var — loopback"

    host_ip, host_port = _parse_short_entry("${X:-8080}:80")
    assert not host_ip and host_port == 8080
    assert _check_binding(host_ip, host_port), "${X:-8080}:80 — 8080 вне {80,443} → violation"


@pytest.mark.gate

# 🧪 TRAP[TEST] · REGRESSION · 2026-08-13 · dict-формат: host_ip/published
# · Scenario: {"published": 8080} без host_ip → violation; {"published": 80} → allowlist;
# ·   {"target": 80} (container-only) → не host-публикация → не флагается
# · Last fail: N/A (новый кейс DevPlan 162 W2-2)
# · Remove if: dict-формат портов перестанет поддерживаться
def test_long_syntax_dict_parsing() -> None:
    assert _check_binding(*_parse_long_entry({"published": 8080})), "dict без host_ip:8080 → violation"
    assert not _check_binding(*_parse_long_entry({"published": 80})), "dict published 80 → allowlist"
    assert not _check_binding(*_parse_long_entry({"published": 8080, "host_ip": "127.0.0.1"})), (
        "dict loopback → разрешён"
    )
    assert _check_binding(*_parse_long_entry({"published": 8080, "host_ip": "0.0.0.0"})), (
        "dict 0.0.0.0:8080 → violation"
    )
    assert _parse_long_entry({"target": 80}) is None, "target-only (без published) — не host-публикация"
