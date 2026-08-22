#!/usr/bin/env python3
# GREP_SUMMARY: gate-test litellm postgres database_url enforcement sqlite prevention env-chain resolution
# STRUCTURE: ◇ test_litellm_database_url_is_postgres → ◇ test_no_sqlite_in_env → ◇ env-chain resolver (${VAR} → literal) → ◇ test_compose_database_url_is_postgres → ⎋ verdict
# region MODULE_CONTRACT
## @purpose  Gate test: enforce LiteLLM uses PostgreSQL (not SQLite) in all environments.
##           DATABASE_URL must start with postgres:// or postgresql:// — never sqlite:///.
##           W3 T3.2: значение может быть ССЫЛКОЙ на другую переменную (${DB_URL}) —
##           env-цепочка резолвится рекурсивно (глубина ≤ 10, защита от циклов) и финальный
##           литерал обязан быть postgres:// или postgresql://.
## @scope    Validates .env (if exists), docker-compose.test.yml, и модульные compose-файлы
##           LiteLLM (core/modules/litellm/docker-compose.{base,test}.yml) для DATABASE_URL.
##           No Docker daemon required — pure static analysis.
## @invariants
##   - .env DATABASE_URL (if present) must start with postgres:// or postgresql://
##   - docker-compose.test.yml DATABASE_URL (if present) must start with postgres:// or postgresql://
##   - Модульные compose LiteLLM DATABASE_URL — после резолва env-ссылок должен быть postgres://*
##   - No sqlite:/// reference anywhere in LiteLLM config/env (включая просочившийся через цепочку)
##   - Резолвер: ${VAR}, ${VAR:-default}, ${VAR:?msg}; лимит глубины 10; циклы не резолвятся
##   - Существующие проверки НЕ ослаблены (W3 T3.2)
## @rationale LiteLLM inv. #8: PostgreSQL in all environments — never SQLite.
##            SQLite causes silent data loss in multi-process deployments.
##            W3 T3.2 (Protection Gaps): DATABASE_URL=${DB_URL}, где DB_URL=sqlite:///x.db,
##            обходит прямую проверку литерала — цепочка не резолвилась, sqlite просачивался.
## @changes 2026-07-17 | Created per drift-convergence DevPlan T13
## @changes 2026-08-13 | DevPlan 160 W3 T3.2 — env-chain резолвер + модульные compose + R5 negative
# endregion MODULE_CONTRACT

import logging
import re
from pathlib import Path

import pytest
import yaml

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
ENV_PATH = ROOT_DIR / ".env"
COMPOSE_TEST_PATH = ROOT_DIR / "docker-compose.test.yml"
LITELLM_DIR = ROOT_DIR / "core" / "modules" / "litellm"
PLATFORM_ENV_PATH = ROOT_DIR / "platform-env.yaml"

# Модульные compose-файлы LiteLLM — реальные места определения DATABASE_URL
# (root docker-compose.test.yml отсутствует в репозитории, W3 T3.2).
LITELLM_COMPOSE_PATHS: list[Path] = [
    LITELLM_DIR / "docker-compose.base.yml",
    LITELLM_DIR / "docker-compose.test.yml",
]

# Резолвер env-цепочек: ${VAR}, ${VAR:-default}, ${VAR:?msg}
_ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::(-|\?)([^}]*))?\}")
_MAX_ENV_DEPTH = 10


def _parse_env_value(env_path: Path, key: str) -> str | None:
    """Extract value of a key from a .env-style file. Returns None if not found."""
    if not env_path.exists():
        return None
    with Path(env_path).open(encoding="utf-8") as f:
        for line_raw in f:
            line = line_raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                if k.strip() == key:
                    return v.strip()
    return None


def _find_postgres_urls_in_yaml(compose_path: Path) -> list[str]:
    """Find all DATABASE_URL or database_url values in a docker-compose YAML file."""
    urls: list[str] = []
    if not compose_path.exists():
        return urls
    with Path(compose_path).open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        return urls

    # Search in services.*.environment and services.*.env_file
    services = data.get("services", {}) or {}
    for svc_name, svc_config in services.items():
        if not isinstance(svc_config, dict):
            continue
        env = svc_config.get("environment", {}) or {}
        if isinstance(env, dict):
            for k, v in env.items():
                if "database_url" in k.lower():
                    urls.append(f"{svc_name}: {k}={v}")
        elif isinstance(env, list):
            for item in env:
                if isinstance(item, str) and "=" in item:
                    k, v = item.split("=", 1)
                    if "database_url" in k.lower():
                        urls.append(f"{svc_name}: {k}={v}")
    return urls


def _check_sqlite_in_config_file(config_path: Path) -> list[str]:
    """Check a single config file for SQLite database_url references.

    Returns list of violation messages (empty = no violations).
    Used by _negative companion test to verify SQLite detection.
    """
    violations: list[str] = []
    if not config_path.exists():
        return violations
    try:
        content = config_path.read_text(encoding="utf-8")
        for i, line in enumerate(content.splitlines(), 1):
            if "sqlite" in line.lower() and ":///" in line:
                violations.append(f"{config_path.name}:{i}: {line.strip()}")
    except (OSError, UnicodeDecodeError):
        logger.debug("[IMP:7][pg-enforcement] Unreadable config %s — skipping", config_path)
    return violations


@pytest.mark.gate

# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate invariant — first line of defense against drift in platform contracts
# · Last fail: N/A (preventive)
# · Remove if: entire gate category is superseded by a newer mechanism
def test_litellm_env_database_url_is_postgres() -> None:
    """DATABASE_URL in .env (if set) must be PostgreSQL, not SQLite."""
    db_url = _parse_env_value(ENV_PATH, "DATABASE_URL")
    if db_url is None:
        logger.info("[IMP:7][gate] DATABASE_URL not set in .env — skipping env check")
        return

    violations: list[str] = []
    if db_url.startswith("sqlite"):
        violations.append(f"DATABASE_URL uses SQLite: {db_url}")

    if not db_url.startswith("postgres"):
        violations.append(f"DATABASE_URL does not start with postgres://: {db_url}")

    assert not violations, "GATE_LITELLM_PG_ENFORCEMENT:\n  " + "\n  ".join(violations)
    logger.info("[IMP:9][gate] PASS: DATABASE_URL is PostgreSQL (%s)", db_url[:30] + "...")


@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate invariant — compose DATABASE_URL PostgreSQL
# · Last fail: N/A (preventive)
# · Remove if: LiteLLM compose DATABASE_URL перестаёт быть определяющим источником
def test_litellm_compose_test_database_url_is_postgres() -> None:
    """DATABASE_URL in docker-compose.test.yml (if set) must be PostgreSQL, not SQLite."""
    urls = _find_postgres_urls_in_yaml(COMPOSE_TEST_PATH)
    if not urls:
        logger.info("[IMP:7][gate] No DATABASE_URL references in docker-compose.test.yml — skipping compose check")
        return

    violations: list[str] = []
    for url_entry in urls:
        if "sqlite" in url_entry.lower():
            violations.append(f"SQLite reference found: {url_entry}")
        # Only check if value looks like a URL (contains ://)
        if "://" in url_entry:
            # Extract the value after =
            val = url_entry.split("=", 1)[1] if "=" in url_entry else ""
            if val and val.startswith("sqlite"):
                violations.append(f"SQLite URL in compose: {url_entry}")
            elif val and not val.startswith("postgres"):
                violations.append(f"Non-PostgreSQL URL in compose: {url_entry}")

    assert not violations, "GATE_LITELLM_PG_ENFORCEMENT (compose):\n  " + "\n  ".join(violations)
    logger.info("[IMP:9][gate] PASS: docker-compose.test.yml DATABASE_URL is PostgreSQL")


@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate invariant — sqlite:/// отсутствует в LiteLLM config
# · Last fail: N/A (preventive)
# · Remove if: LiteLLM конфиг перестаёт поддерживать DATABASE_URL (полная ликвидация модуля)
def test_no_sqlite_in_litellm_config() -> None:
    """Ensure no sqlite:/// references exist in LiteLLM module config files.

    This is a belt-and-suspenders check: if DATABASE_URL is not set,
    LiteLLM defaults to SQLite — this test catches default-reliance.
    """
    if not LITELLM_DIR.exists():
        # T1.7 триаж (аудит 2026-08-22): core/modules/litellm — статический каталог репо;
        # отсутствие = сломанное дерево → FAIL (R4), не молчаливый skip.
        pytest.fail(f"LiteLLM module directory not found (repo dir must exist): {LITELLM_DIR}")

    sqlite_refs: list[str] = []
    # Check config.yaml and docker-compose files
    for pattern in ["*.yaml", "*.yml", "*.env", "*.env.example"]:
        for f in LITELLM_DIR.rglob(pattern):
            if f.is_symlink() or not f.is_file():
                continue
            try:
                content = f.read_text()
                for i, line in enumerate(content.splitlines(), 1):
                    if "sqlite" in line.lower() and ":///" in line:
                        sqlite_refs.append(f"{f.relative_to(ROOT_DIR)}:{i}: {line.strip()}")
            except (OSError, UnicodeDecodeError):
                continue

    assert not sqlite_refs, (
        "GATE_LITELLM_PG_ENFORCEMENT: SQLite URL references found in LiteLLM config:\n  " + "\n  ".join(sqlite_refs)
    )
    logger.info("[IMP:9][gate] PASS: No SQLite references in LiteLLM module config")


# ══════════════════════════════════════════════════════════════════════════════
# W3 T3.2 — env-chain резолвер (${VAR} → литерал) — защита от sqlite через цепочку
# ══════════════════════════════════════════════════════════════════════════════

# region ENV_CHAIN_RESOLVER


def _load_env_kv(env_path: Path) -> dict[str, str]:
    """Прочитать все KEY=VALUE из .env-стиль файла (комментарии и пустые строки пропускаются).

    ## @purpose — Источник значений для резолва env-цепочек: root .env, платформенные env-дефолты.
    ## @io — ⇥ env_path: Path → ⎋ dict[str, str] (пусто если файла нет)
    ## @complexity — O(L) где L = строки
    """
    result: dict[str, str] = {}
    if not env_path.is_file():
        return result
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            k, v = stripped.split("=", 1)
            result[k.strip()] = v.strip()
    except (OSError, UnicodeDecodeError):
        logger.debug("[IMP:7][pg-enforcement] Unreadable env file %s — skipping", env_path)
    return result


def _load_platform_env_defaults() -> dict[str, str]:
    """env_defaults из platform-env.yaml (GENERATED, CI/test-дефолты) — источник значений цепочки.

    ## @purpose — DATABASE_URL-цепочка может ссылаться на POSTGRES_USER и т.п., определённые
    ##            в env_defaults (SoT — platform-infra.yaml → generate_platform_env).
    ## @io — → ⎋ dict[str, str]
    ## @complexity — O(1) — один YAML
    """
    if not PLATFORM_ENV_PATH.is_file():
        return {}
    try:
        data = yaml.safe_load(PLATFORM_ENV_PATH.read_text(encoding="utf-8"))
        raw = (data or {}).get("env_defaults", {})
        return {k: str(v) for k, v in raw.items() if v is not None} if isinstance(raw, dict) else {}
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        logger.warning("[IMP:7][pg-enforcement] platform-env.yaml unreadable — empty env_defaults")
        return {}


def _compose_env_sources(compose_path: Path) -> dict[str, str]:
    """Собрать плоский dict переменных окружения из compose-файла (services.*.environment + top env).

    ## @purpose — Значения ${DB_URL} ищутся в том же compose-файле (service environment),
    ##            чтобы цепочка DATABASE_URL=${DB_URL}, DB_URL=sqlite:///x.db резолвилась.
    ## @io — ⇥ compose_path: Path → ⎋ dict[str, str] (пусто если файла нет / не парсится)
    ## @complexity — O(S * E) где S = сервисы, E = переменные
    ## @invariants
    ##   - Поддерживает dict- и list-формы environment
    ##   - !override-теги вырезаются (compose merge-маркер)
    ##   - Значения приводятся к str
    """
    sources: dict[str, str] = {}
    if not compose_path.is_file():
        return sources
    try:
        raw = compose_path.read_text(encoding="utf-8")
        raw = re.sub(r":\s*!override\b", ":", raw)
        data = yaml.safe_load(raw)
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        logger.debug("[IMP:7][pg-enforcement] Unreadable compose %s — skipping", compose_path)
        return sources

    if not isinstance(data, dict):
        return sources

    top_env = data.get("environment")
    if isinstance(top_env, dict):
        for k, v in top_env.items():
            sources[str(k)] = str(v) if v is not None else ""

    for svc_config in (data.get("services", {}) or {}).values():
        if not isinstance(svc_config, dict):
            continue
        env = svc_config.get("environment", {}) or {}
        if isinstance(env, dict):
            for k, v in env.items():
                sources[str(k)] = str(v) if v is not None else ""
        elif isinstance(env, list):
            for item in env:
                if isinstance(item, str) and "=" in item:
                    k, v = item.split("=", 1)
                    sources[k.strip()] = v.strip()
    return sources


def resolve_env_value(value: str, sources: dict[str, str], depth: int = 0, seen: set[str] | None = None) -> str:
    """Рекурсивно резолвить ${VAR} / ${VAR:-default} / ${VAR:?msg} ссылки в значении.

    ## @purpose — W3 T3.2: env-цепочка не должна пропускать sqlite. Значение DATABASE_URL
    ##            может быть ${DB_URL}, а DB_URL = sqlite:///x.db — прямой литерал-проверке
    ##            это не видно. Резолвер доводит цепочку до финального литерала.
    ## @io — ⇥ value: str — исходное значение (возможно со ссылками)
    ##       ⇥ sources: dict[str, str] — окружение для подстановки
    ##       ⇥ depth: int — глубина рекурсии (внутренний счётчик)
    ##       ⇥ seen: set[str] | None — защита от циклов (переменные на пути резолва)
    ##       → ⎋ str — значение с раскрытыми ссылками (нерезолвенные оставлены как есть)
    ## @complexity — O(R * D) где R = число ссылок, D = глубина (≤ 10)
    ## @invariants
    ##   - Глубина рекурсии ≤ _MAX_ENV_DEPTH (лимит → ссылка остаётся нерезолвенной)
    ##   - Цикл (A→B→A) не резолвится — переменная из seen не подставляется повторно
    ##   - ${VAR:-default}: default используется ТОЛЬКО если VAR отсутствует в sources
    ##   - ${VAR:?msg}: если VAR есть → значение; нет → остаётся как есть (не крашим скан)
    ##   - Нерезолвенные ссылки сохраняются в исходном виде (не подменяются на пустоту)
    """
    if depth > _MAX_ENV_DEPTH:
        return value
    seen = seen or set()

    def _recurse(resolved: str, var: str, original: str) -> str:
        if var in seen:
            return original  # цикл (A→B→A): не резолвим повторно — возвращаем оригинал
        return resolve_env_value(resolved, sources, depth + 1, seen | {var})

    def _replace(match: re.Match) -> str:
        var = match.group(1)
        operator = match.group(2)
        operand = match.group(3)
        original = match.group(0)

        # ${VAR:-default} — default только при отсутствии VAR
        if operator == "-":
            resolved = sources.get(var)
            if resolved is None:
                return operand if operand is not None else ""
            return _recurse(resolved, var, original)

        # ${VAR:?msg} — отсутствие переменной на этапе статического скана не крашим:
        # оставляем оригинал (в рантайме compose упадёт — это корректное поведение)
        if operator == "?":
            resolved = sources.get(var)
            if resolved is None:
                return original
            return _recurse(resolved, var, original)

        # ${VAR} — прямая ссылка
        resolved = sources.get(var)
        if resolved is None:
            return original
        return _recurse(resolved, var, original)

    return _ENV_REF_RE.sub(_replace, value)


def collect_database_url_values() -> list[tuple[str, str]]:
    """Собрать ВСЕ значения DATABASE_URL: root .env + корневой compose + модульные compose LiteLLM.

    ## @purpose — Единая точка сбора для env-chain проверки: реальные места, где LiteLLM
    ##            получает DATABASE_URL (корень + core/modules/litellm). Ссылки вида
    ##            «postgresql://${POSTGRES_USER}...» тоже собираются — их резолвит цепочка.
    ## @io — → ⎋ list[(source_desc, value)]
    ## @complexity — O(F * S * E) где F = файлы, S = сервисы, E = переменные
    """
    found: list[tuple[str, str]] = []

    # 1. Root .env
    env_value = _parse_env_value(ENV_PATH, "DATABASE_URL")
    if env_value:
        found.append((".env", env_value))

    # 2. Root docker-compose.test.yml (если существует)
    for k, v in _find_postgres_urls_in_yaml(COMPOSE_TEST_PATH):
        found.append((f"docker-compose.test.yml:{k}", v.split("=", 1)[1] if "=" in v else v))

    # 3. Модульные compose LiteLLM (base + test) — реальные определения
    for compose_path in LITELLM_COMPOSE_PATHS:
        sources = _compose_env_sources(compose_path)
        for var, val in sources.items():
            if "database_url" in var.lower():
                found.append((f"{compose_path.name}:{var}", val))

    return found


def check_resolved_url(value: str) -> list[str]:
    """Проверить ФИНАЛЬНЫЙ литерал DATABASE_URL после резолва цепочки.

    ## @purpose — W3 T3.2: финальный литерал обязан быть postgres:// или postgresql://
    ##            и не содержать sqlite:// (даже просочившегося через ${DB_URL}).
    ## @io — ⇥ value: str — значение после resolve_env_value → ⎋ list[str] violations
    ## @complexity — O(1)
    """
    violations: list[str] = []
    lowered = value.lower()
    if "sqlite" in lowered and "://" in lowered:
        violations.append(f"SQLite просочился через env-цепочку: {value[:120]}")
    if not value.startswith(("postgres://", "postgresql://")):
        violations.append(f"Итоговый литерал DATABASE_URL не PostgreSQL: {value[:120]}")
    return violations


# endregion ENV_CHAIN_RESOLVER


# region TESTS_ENV_CHAIN


@pytest.mark.gate

# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · env-цепочка DATABASE_URL обязана резолвиться в PostgreSQL (W3 T3.2)
# · Scenario: DATABASE_URL=${DB_URL} (или postgresql://${POSTGRES_USER}:...), DB_URL=sqlite:///x.db —
# ·   прямая проверка литерала не видит sqlite; резолвер доводит цепочку до литерала
# · Last fail: N/A (preventive — расширение существующего гейта)
# · Remove if: LiteLLM DATABASE_URL перестаёт поддерживать env-ссылки (полностью литерализуется)
def test_litellm_database_url_env_chain_resolves_to_postgres(caplog) -> None:
    """Финальный литерал КАЖДОГО найденного DATABASE_URL (после резолва ${VAR}-цепочек) — PostgreSQL.

    ## @purpose — W3 T3.2: цепочка env-ссылок не должна маскировать sqlite. Каждое найденное
    ##            значение DATABASE_URL резолвится рекурсивно (глубина ≤ 10, защита от циклов),
    ##            финальный литерал обязан быть postgres:// или postgresql://.
    ## @io — ⎋ None (assert no violations)
    ## @complexity — O(F * R * D) где F = найденные URL, R = ссылки, D = глубина
    """
    sources: dict[str, str] = {}
    sources.update(_load_platform_env_defaults())
    sources.update(_load_env_kv(ENV_PATH))
    for compose_path in LITELLM_COMPOSE_PATHS:
        sources.update(_compose_env_sources(compose_path))

    found = collect_database_url_values()
    logger.info("[IMP:8][pg-enforcement][env-chain] Найдено DATABASE_URL: %d", len(found))
    if not found:
        logger.info("[IMP:7][pg-enforcement][env-chain] DATABASE_URL не найден ни в одном источнике — skip")
        return

    violations: list[str] = []
    for source_desc, value in found:
        resolved = resolve_env_value(value, sources)
        logger.info("[IMP:8][pg-enforcement][env-chain] %s: '%s' → '%s'", source_desc, value[:60], resolved[:80])
        violations.extend(f"{source_desc}: {v}" for v in check_resolved_url(resolved))

    assert not violations, "GATE_LITELLM_PG_ENFORCEMENT (env-chain):\n  " + "\n  ".join(violations)
    logger.info("[IMP:9][pg-enforcement][env-chain] PASS: все %d DATABASE_URL резолвятся в PostgreSQL", len(found))


@pytest.mark.gate

# 🧪 TRAP[TEST] · 2026-08-13 · NEGATIVE (R5) · env-chain резолвер — sqlite через ${DB_URL} (W3 T3.2)
# · Last fail: DATABASE_URL=${DB_URL}, DB_URL=sqlite:///x.db — прямая проверка литерала НЕ видела sqlite
# · Remove if: env-ссылки в DATABASE_URL запрещаются полностью (литерал-only)
def test_negative_sqlite_via_env_chain_detected(caplog, tmp_path: Path) -> None:
    """R5 negative: sqlite, просочившийся через цепочку ${DB_URL}, детектируется после резолва.

    ## @purpose — Точный вход, поймавший пробел W3 T3.2: DATABASE_URL=${DB_URL}, где
    ##            DB_URL=sqlite:///x.db. Резолвер раскрывает цепочку → check_resolved_url ловит.
    ## @io — ⇥ tmp_path: pytest fixture (не используется напрямую — синтетика в памяти)
    ## @complexity — O(1)
    """
    sources = {
        "DB_URL": "sqlite:///./data/litellm.db",
        "POSTGRES_USER": "postgres",
    }
    value = "${DB_URL}"
    resolved = resolve_env_value(value, sources)
    logger.info("[IMP:8][pg-enforcement][negative] ${DB_URL} → '%s'", resolved)

    violations = check_resolved_url(resolved)
    assert len(violations) >= 1, (
        f"R5 FAIL: sqlite через env-цепочку не детектирован — resolved={resolved!r}, violations={violations!r}"
    )
    assert any("sqlite" in v.lower() for v in violations), f"R5 FAIL: violation не упоминает sqlite: {violations!r}"
    logger.info("[IMP:9][pg-enforcement][negative] PASS: sqlite через цепочку детектируется")


@pytest.mark.gate

# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · защита от циклов env-цепочек (W3 T3.2)
# · Scenario: A=${B}, B=${A} — бесконечная рекурсия без лимита глубины/seen-набора
# · Last fail: N/A (preventive — лимит глубины 10 + seen-набор)
# · Remove if: резолвер заменяется на внешний механизм с собственной защитой от циклов
def test_env_chain_cycle_does_not_hang(caplog) -> None:
    """Резолвер не зависает на циклических ссылках (A=${B}, B=${A}) — лимит глубины 10.

    ## @purpose — W3 T3.2: защита от циклов обязана работать (иначе gate зависает навсегда).
    ## @io — ⎋ None
    ## @complexity — O(MAX_DEPTH)
    """
    sources = {"A": "${B}", "B": "${A}"}
    resolved = resolve_env_value("${A}", sources)
    logger.info("[IMP:8][pg-enforcement][cycle] ${A} с циклом A↔B → '%s'", resolved[:80])
    # Резолв завершился (не завис); нерезолвенные ссылки остались — это норма
    assert isinstance(resolved, str)
    assert "${" in resolved, f"Цикл должен остаться частично нерезолвленным, got: {resolved!r}"
    logger.info("[IMP:9][pg-enforcement][cycle] PASS: цикл не завис, лимит глубины сработал")


# endregion TESTS_ENV_CHAIN
