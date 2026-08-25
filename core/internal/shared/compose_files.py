#!/usr/bin/env python3
# GREP_SUMMARY: compose-files, COMPOSE_FILENAMES, PROJECT_COMPOSE_FILENAMES, PROJECT_PAYLOAD_FILENAMES, resolve-compose-file, requires-compose-project, SoT, shared, docker-compose-base
# STRUCTURE: ▶ COMPOSE_FILENAMES (canonical order) → PROJECT_COMPOSE_FILENAMES (payload subset) →
#            PROJECT_PAYLOAD_FILENAMES (единый file-list tar-payload: compose + platform-файлы) →
#            ◇ resolve_compose_file ┌module_dir┐ → ⚡ scan canon order → ⎋ Path|None →
#            ◇ requires_compose_project ┌module_dir┐ → ⎋ bool
# region MODULE_CONTRACT
## @purpose  Единый SoT списков compose-файлов и резолва compose-файла в директории модуля/проекта
##           (DevPlan 118 A2). Устраняет 6 расходящихся кортежей (docker_orchestrator, converge/runtime,
##           converge/volumes, orphan_reconciler, payload_deliverer, project_adopter) с разными
##           наборами имён и порядком — источник дрейфа «converge лечит то, что deploy не деплоит».
## @scope    Импортируется всеми Python-потребителями compose-резолва. Чистые функции без состояния.
## @invariants
##   1. COMPOSE_FILENAMES — ЕДИНСТВЕННЫЙ канонический кортеж порядка резолва compose-файла
##   2. Порядок канона (верифицирован по ФС+git-истории, DevPlan 118 A2):
##      compose.yaml → docker-compose.yaml → docker-compose.yml → docker-compose.base.yml
##      — покрывает оба сценария: проектные payload'ы (docker-compose.yml/compose.yaml) и
##        модульные base-compose (docker-compose.base.yml, деплой с --profile)
##   3. compose.yml — НЕ канонический: 0 реальных модулей с compose.yml (ФС core/modules/ + git-история
##      пусты) — удалён из канона (converge лечил фантомные имена)
##   4. Реальные модули имеют ТОЛЬКО docker-compose.base.yml (+ docker-compose.test.yml/dev.yml) —
##      converge теперь видит их (раньше пропускал все docker-модули как «not docker»)
##   5. PROJECT_COMPOSE_FILENAMES — подмножество для tar-payload'ов (whitelist deliver-канала):
##      docker-compose.yml, compose.yaml. docker-compose.base.yml НЕ в payload — модульный паттерн.
##   6. resolve_compose_file: первый существующий файл в порядке COMPOSE_FILENAMES → Path | None
##   7. requires_compose_project: True iff resolve_compose_file находит файл (docker-модуль)
## @rationale U-13/парадигма sole-path: 6 копий списков = правка канона в 6 местах. Калибровочный
##            grep по core/internal/*.py выявил 6 потребителей (5 из DevPlan + orphan_reconciler).
##            ФС-проверка core/modules/* показала: все 14 docker-модулей — только docker-compose.base.yml;
##            git-история не содержит ни одного модуля с compose.yml (не-каноническое имя подтверждено).
## @changes  2026-08-02 | DevPlan 118 A2 — Created (SoT compose-списков)
## @changes  2026-08-24 | REF-0105 (meta-refactoring В1) — +PROJECT_PAYLOAD_FILENAMES:
##           единый payload file-list (compose-подмножество + ai-platform.yaml +
##           .env.platform + practices.lock); payload_deliverer и CI-workflow потребляют
##           одну константу (triple-sync, structural test)
# endregion MODULE_CONTRACT

from __future__ import annotations

from pathlib import Path

# ── Canonical compose filename order (DevPlan 118 A2) ─────────────────────────

COMPOSE_FILENAMES: tuple[str, ...] = (
    "compose.yaml",  # Compose spec name (modern)
    "docker-compose.yaml",  # Compose spec name (hyphen)
    "docker-compose.yml",  # default (project payloads)
    "docker-compose.base.yml",  # Module base compose (deployed with --profile)
)
"""## @invariant Единственный канонический кортеж порядка резолва compose-файла (гейт compose_files_sole_path)."""

# ── Project-payload compose subset (deliver-канал whitelist) ─────────────────

PROJECT_COMPOSE_FILENAMES: tuple[str, ...] = (
    "docker-compose.yml",
    "compose.yaml",
)
"""## @invariant Подмножество compose-имён, допустимых в tar-payload (receive-канал whitelist).
##            docker-compose.base.yml — модульный паттерн, в проектные payload'ы не входит."""

# ── Единый payload file-list (REF-0105 triple-sync) ──────────────────────────

PROJECT_PAYLOAD_FILENAMES: tuple[str, ...] = (
    *PROJECT_COMPOSE_FILENAMES,
    "ai-platform.yaml",
    ".env.platform",
    "practices.lock",
)
"""## @invariant ЕДИНСТВЕННАЯ константа file-list tar-payload'а (REF-0105, triple-sync):
##            (1) Python-сторона — payload_deliverer.WHITELIST_FILES/_PAYLOAD_FILE_NAMES
##            выводятся отсюда; receive_flow удаляет stale compose по PROJECT_COMPOSE_FILENAMES;
##            (2) CI-сторона — deploy-project.yml (+templates deploy.yml) собирает FILES из
##            ровно этих имён (structural test: tests/unit/test_payload_whitelist_triple_sync.py).
##            Расширение списка = правка ТОЛЬКО здесь + CI-шаг синхронно (гейт ловит дрейф)."""

# ── Override compose-имена (сканируемый слой verify_contracts) ───────────────

PROJECT_OVERRIDE_FILENAMES: tuple[str, ...] = (
    "docker-compose.override.yml",
    "compose.override.yaml",
)
"""## @invariant Имена override-compose файлов проекта (DevPlan 16 T1.E / P1-11): verify_contracts
##            сканирует их ВДОБАВКУ к каноническим (override может содержать security_opt/
##            devices/gpus — слепая зона single-file скана аудита 15). НЕ входят в payload и
##            в COMPOSE_FILENAMES (не участвуют в резолве «главного» файла)."""


# region FUNC_resolve_compose_file
## @purpose  Найти первый существующий compose-файл в директории (канонический порядок).
## @io       ⇥ module_dir: str | Path → ⎋ Path | None (resolved compose file, или None)
## @complexity O(4) — линейный скан фиксированного кортежа
## @invariants
##   - Порядок — строго COMPOSE_FILENAMES (canon, DevPlan 118 A2)
##   - None при отсутствии любого канонического файла (не docker-модуль)
def resolve_compose_file(module_dir: str | Path) -> Path | None:
    """Resolve the first existing compose file in a directory (canonical order)."""
    base = Path(module_dir)
    for fname in COMPOSE_FILENAMES:
        candidate = base / fname
        if candidate.is_file():
            return candidate
    return None


# endregion FUNC_resolve_compose_file


# region FUNC_requires_compose_project
## @purpose  Проверить, является ли директория compose-проектом (docker-модулем).
## @io       ⇥ module_dir: str | Path → ⎋ bool (True = есть канонический compose-файл)
## @complexity O(4) — делегирование в resolve_compose_file
## @invariants
##   - True iff resolve_compose_file находит файл
##   - Используется converge (runtime/volumes) для пропуска не-docker модулей
def requires_compose_project(module_dir: str | Path) -> bool:
    """Return True if the directory contains a canonical compose file (docker module)."""
    return resolve_compose_file(module_dir) is not None


# endregion FUNC_requires_compose_project
