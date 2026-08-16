# GREP_SUMMARY: check-project-compose-checks, compose-config, restart-policies, unless-stopped, init-container, restart-justification, stateful-volumes
# STRUCTURE: ▶ compose-config (docker compose config --quiet; no compose file → PASS) → ⊕ restart-policies (yaml.safe_load → per-service restart: init → "no"; long-running → unless-stopped|always+обоснование) → ⎋ CheckResult
# region MODULE_CONTRACT
## @purpose  Compose-специфичные handler-и K1-канала (DevPlan 137 §2.1A, 164 W1-4/S2 для
##           проектов, 170 W10-A декомпозиция): compose-config (docker compose config --quiet,
##           L2: warning в baseline, блок в active-full; docker missing → WARN) и
##           restart-policies (канон W1-4: long-running — restart: unless-stopped, always —
##           только с обоснованием stateful-volumes/комментарий-маркеры; init-контейнеры —
##           restart: "no"; baseline L2).
## @scope    Потребители: checks/__init__.py (реестр), runner (через _run_check). DI-канал
##           facts: EnvironmentFacts (which docker).
## @invariants
##   - compose-config: нет compose-файла → PASS (PROJECT_COMPOSE_FILENAMES из shared/
##     compose_files — гейт compose_files_sole_path: имена НЕ литералятся)
##   - restart-policies: YAML unquoted `restart: no` → False → нормализуется в "no"
##   - init-детекция — эвристика по имени (TRAP[DECISION] W1-4: зависит depends_on-анализ
##     переусложнён для baseline L2); allowlist «always» — stateful (volumes) или комментарий
##   - compose unparseable → WARN (не блок) — pyyaml/синтаксис, не качество проекта
## @rationale Группировка 2 compose-проверок: общий домен (compose-файлы, yaml-семантика)
##            отделён от tool/file-проверок (research-A §2: checks/compose.py).
## @changes  2026-08-15 · DevPlan 170 W10-A — создан (выделен из check_project.py:448-466,
##           875-1040)
# endregion MODULE_CONTRACT

from __future__ import annotations

import time
from pathlib import Path

from core.internal.practices.check_project.exec import subprocess_run, tail
from core.internal.practices.check_project.models import CheckResult
from core.internal.practices.manifest import PracticeCheck
from core.internal.shared.compose_files import PROJECT_COMPOSE_FILENAMES, resolve_compose_file
from core.internal.shared.env_facts import EnvironmentFacts, default_env_facts


# ═══════════════════════════════════════════════════════════════════
# region CHECK_compose_config
def check_compose_config(
    check: PracticeCheck,
    project_dir: Path,
    *,
    fix: bool,  # ruff: ignore[ARG001]
    facts: EnvironmentFacts | None = None,
) -> CheckResult:
    """compose-config: docker compose config --quiet (L2: warning baseline, блок active-full)."""
    if not any((project_dir / name).is_file() for name in PROJECT_COMPOSE_FILENAMES):
        return CheckResult(check.id, "PASS", "no compose file", 0.0)
    if (facts or default_env_facts()).which("docker") is None:
        return CheckResult(check.id, "WARN", "docker not available — compose config skipped", 0.0)
    rc, out, err, dur = subprocess_run(["docker", "compose", "config", "--quiet"], project_dir, check.timeout_sec)
    if rc == 0:
        return CheckResult(check.id, "PASS", "compose config valid", dur)
    return CheckResult(check.id, "FAIL", f"compose config invalid (rc={rc}): {tail(err or out)}", dur)


# endregion CHECK_compose_config


# ═══════════════════════════════════════════════════════════════════
# region CHECK_restart_policies
## @purpose  Канон restart-policies (DevPlan 164 W1-4/S2, для проектов): long-running сервисы —
##           restart: unless-stopped (или always с обоснованием/stateful), init-контейнеры —
##           restart: "no". RED: отсутствующий restart у long-running; always без обоснования;
##           init с always/unless-stopped; неканоническое значение у long-running.
## @io       ⇥ check, project_dir, fix, facts → ⎋ CheckResult
## @complexity O(S * B) — сервисы × строки блока комментариев
## @rationale allowlist «always» — из аудита W1-4 (postgres/redis/backup-cron) + stateful по
##            наличию volumes; обоснование — комментарий в блоке сервиса со словами-маркерами.
##            YAML: unquoted `restart: no` → False — нормализуется в "no".
# 🧐 TRAP[DECISION] · 2026-08-14 · — · restart-policies: init-детекция по имени + обоснование
# · always по словам-маркерам комментария · Rejected: строгий разбор depends_on
# · condition: service_completed_successfully (канон платформы) · Reason: compose-safe_load теряет
# · комментарии; depends_on-анализ переусложнён для baseline L2 (эвристика покрывает канон
# · minio-createbuckets/prometheus-config-init) · Rev: ложная классификация long-running как init
# · (имя с паттерном) — перейти на depends_on condition-разбор
_RESTART_STATEFUL_ALWAYS_HINTS: tuple[str, ...] = ("postgres", "redis", "backup-cron", "cron")
_RESTART_INIT_NAME_HINTS: tuple[str, ...] = (
    "init",
    "setup",
    "migrate",
    "migration",
    "createbuckets",
    "create-buckets",
    "config-init",
    "seed",
    "one-shot",
    "oneoff",
    "bootstrap",
)
_RESTART_JUSTIFICATION_HINTS: tuple[str, ...] = (
    "restart",
    "policy",
    "обоснов",
    "justif",
    "stateful",
    "persist",
    "нужен",
    "необходим",
    "critical",
)


def _restart_normalize(value: object | None) -> str | None:
    """Нормализовать restart-значение compose (YAML `no` → False → "no"; регистр → lower).

    ## @purpose  Единая нормализация restart (канон W1-4): None (отсутствует), "no"/False,
    ##           "unless-stopped"/"always"/"on-failure".
    ## @io       ⇥ value: object | None (сырое YAML-значение: bool | str | None) → ⎋ str | None
    ## @complexity O(1)
    """
    if value is None:
        return None
    if value is False:
        return "no"
    if value is True:
        return "always"  # YAML `restart: yes` (крайний случай) → always
    return str(value).strip().lower()


def _is_init_service(name: str) -> bool:
    """Эвристика init-контейнера: имя сервиса содержит init-паттерн (канон: one-shot).

    ## @purpose  Отделить init/one-shot (restart: "no") от long-running. Эвристика по имени —
    ##           аналог канона платформы (minio-createbuckets, prometheus-config-init).
    ## @io       ⇥ name: str → ⎋ bool
    ## @complexity O(H) — число паттернов
    """
    lower = name.lower()
    return any(hint in lower for hint in _RESTART_INIT_NAME_HINTS)


def _service_block_has_restart_justification(raw: str, name: str) -> bool:
    """True если в блоке сервиса есть комментарий-обоснование restart: always.

    ## @purpose  Детерминированная эвристика: строки блока (от '^  name:' до следующего
    ##           сервиса/top-level ключа), начинающиеся с # (после lstrip), содержащие
    ##           слово-маркер обоснования (restart/policy/stateful/persist/...). Комментарий
    ##           про healthcheck/localhost НЕ является обоснованием restart.
    ## @io       ⇥ raw: str (compose-текст), name: str → ⎋ bool
    ## @complexity O(B) — строки блока
    """
    in_block = False
    block_lines: list[str] = []
    for line in raw.splitlines():
        if line.startswith(f"  {name}:"):
            in_block = True
            block_lines = []
            continue
        if not in_block:
            continue
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            block_lines.append(line)
            continue
        if line.startswith(("    ", "\t")):
            block_lines.append(line)
            continue
        break  # следующий сервис / top-level ключ — блок завершён
    for line in block_lines:
        stripped = line.lstrip()
        if not stripped.startswith("#"):
            continue
        if any(hint in stripped.lower() for hint in _RESTART_JUSTIFICATION_HINTS):
            return True
    return False


def check_restart_policies(
    check: PracticeCheck,
    project_dir: Path,
    *,
    fix: bool,  # ruff: ignore[ARG001]
    facts: EnvironmentFacts | None = None,  # ruff: ignore[ARG001]
) -> CheckResult:
    """restart-policies (baseline L2): unless-stopped по умолчанию; always — с обоснованием."""
    start = time.monotonic()
    compose_path = resolve_compose_file(project_dir)
    if compose_path is None:
        return CheckResult(check.id, "PASS", "no compose file", 0.0)
    try:
        import yaml
    except ImportError:
        return CheckResult(
            check.id, "WARN", "pyyaml not installed — restart-policies skipped", time.monotonic() - start
        )
    try:
        raw = compose_path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw) or {}
    except (OSError, yaml.YAMLError) as exc:
        return CheckResult(check.id, "WARN", f"compose unparseable: {tail(str(exc))}", time.monotonic() - start)
    services = data.get("services")
    if not isinstance(services, dict) or not services:
        return CheckResult(check.id, "PASS", "no services in compose", time.monotonic() - start)

    violations: list[str] = []
    for name, service in services.items():
        if not isinstance(service, dict):
            continue
        restart = _restart_normalize(service.get("restart"))
        if _is_init_service(name):
            if restart not in {None, "no"}:
                violations.append(f'{name}: init-container должен быть restart: "no" (получено {restart!r})')
            continue
        # long-running: канон unless-stopped (default); always — только с обоснованием
        if restart is None:
            violations.append(f"{name}: отсутствует restart (long-running обязан быть restart: unless-stopped)")
            continue
        if restart == "unless-stopped":
            continue
        if restart == "always":
            stateful = any(hint in name.lower() for hint in _RESTART_STATEFUL_ALWAYS_HINTS) or bool(
                service.get("volumes")
            )
            justified = _service_block_has_restart_justification(raw, name)
            if not (stateful or justified):
                violations.append(f"{name}: restart: always без обоснования (stateful-комментарий или unless-stopped)")
            continue
        violations.append(f"{name}: неканонический restart {restart!r} у long-running (канон: unless-stopped)")

    if violations:
        detail = violations[0] + (f" (+{len(violations) - 1} more)" if len(violations) > 1 else "")
        return CheckResult(check.id, "FAIL", f"restart-policies: {detail}", time.monotonic() - start)
    return CheckResult(check.id, "PASS", "restart policies canon-compliant", time.monotonic() - start)


# endregion CHECK_restart_policies
