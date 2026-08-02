# 08-DevPlan — Бриф G: Manifest & Low-Priority Cleanup

<!-- $ARTIFACT_CONTRACT
PURPOSE:          Косметические правки: thin_wrapper allowlist, документирование системных исключений .PHONY,
                  bootstrap→deploy import direction, sync_env_defaults fallback literals.
DESCRIPTION:      4 задачи (G1–G4). Низкий приоритет, делается после всех остальных волн.
                  Не меняет поведение — только документация и allowlist.
RATIONALE:        Allowlist thin_wrapper устарел после 117/118 (размеры скриптов изменились).
                  Системные исключения не задокументированы. Направление импортов не зафиксировано.
ACCEPTANCE_CRITERIA:
  - AC-G-1: `make gate MODE=fast` зелёный
  - AC-G-2: thin_wrapper allowlist актуален (размеры скриптов проверены)
  - AC-G-3: `make check-manifests` зелёный
IMPLEMENTS:       Бриф G из 01-Brief.md (волна 119) — Manifest & Low-Priority Cleanup.
IMPACTS:          tests/gates/test_gate_thin_wrapper.py, entrypoint-manifest.yaml (комментарий),
                  core/AGENTS.md, core/internal/scripts/sync_env_defaults.py,
                  core/internal/shared/ci_default.py.
REQUIRES:         Результаты аудита 6 (манифест F2-F3) и аудита 4 (C1, S1).
-->

# DevPlan G — Manifest & Low-Priority Cleanup

## $START_DEVPLAN

### Контекст

Волна 119, бриф G. Седьмая волна — косметика и документация. После всех миграций и декомпозиций размеры shell-фасадов изменились, allowlist нужно обновить. Системные исключения .PHONY задокументировать. Направление импортов зафиксировать.

---

## $TASKS

### TASK-G1: thin_wrapper allowlist update

| Поле | Значение |
|------|----------|
| **ID** | G1 |
| **Sev** | MED |
| **Сложность** | 2/10 |
| **Файлы** | `tests/gates/test_gate_thin_wrapper.py` |
| **Зависимости** | D4, D5, D7 (размеры фасадов изменились) |
| **Риск** | LOW — обновление комментариев и цифр |

**Описание:**
`test_gate_thin_wrapper.py` allowlist устарел:
- `lint.sh` (40 LOC) → под лимитом, удалить из allowlist
- `check-doc-headers.sh` (17) → удалить из allowlist
- `converge.sh` (100) → удалить из allowlist (под лимитом)
- `bootstrap.sh` (160, комментарий «T15») → обновить комментарий на актуальный
- `deploy.sh` (172) → обновить комментарий (после D7 TRAP-update)

**Шаги:**
1. Удалить `lint.sh`, `check-doc-headers.sh`, `converge.sh` из allowlist (размер < лимита после 117/118).
2. Обновить комментарий для `bootstrap.sh` — актуальный размер и причина.
3. Обновить комментарий для `deploy.sh` — после D7.

**Acceptance Criteria:**
- AC-G1.1: `test_gate_thin_wrapper.py` проходит
- AC-G1.2: allowlist содержит только скрипты > лимита с обоснованными исключениями

---

### TASK-G2: Системные исключения .PHONY — документирование

| Поле | Значение |
|------|----------|
| **ID** | G2 |
| **Sev** | LOW |
| **Сложность** | 1/10 |
| **Файлы** | `entrypoint-manifest.yaml` (комментарий), `core/AGENTS.md` |
| **Зависимости** | нет |
| **Риск** | LOW — документация |

**Описание:**
Системные .PHONY таргеты (`help`, `venv`, `pre-commit-install`, `pre-commit-run`) не задокументированы как исключения в манифесте.

**Шаги:**
1. Добавить комментарий в `entrypoint-manifest.yaml` §system_exceptions — почему эти таргеты не в глоссарии.
2. Обновить `core/AGENTS.md` — секция о системных исключениях.

**Acceptance Criteria:**
- AC-G2.1: `entrypoint-manifest.yaml` содержит комментарий о системных исключениях
- AC-G2.2: `make check-manifests` зелёный

---

### TASK-G3: Bootstrap→deploy import direction — документирование

| Поле | Значение |
|------|----------|
| **ID** | G3 |
| **Sev** | LOW |
| **Сложность** | 1/10 |
| **Файлы** | `core/AGENTS.md` |
| **Зависимости** | нет |
| **Риск** | LOW — документация |

**Описание:**
Направление импортов bootstrap→deploy не задокументировано. Bootstrap импортирует deploy, не наоборот. Зафиксировать в core/AGENTS.md.

**Шаги:**
1. Добавить в core/AGENTS.md §Cross-layer import rules: «bootstrap/ → deploy/ разрешён, deploy/ → bootstrap/ запрещён».

**Acceptance Criteria:**
- AC-G3.1: core/AGENTS.md содержит правило bootstrap→deploy import direction

---

### TASK-G4: sync_env_defaults fallback literals → ci_default

| Поле | Значение |
|------|----------|
| **ID** | G4 |
| **Sev** | LOW |
| **Сложность** | 2/10 |
| **Файлы** | `scripts/sync_env_defaults.py`, `shared/ci_default.py` |
| **Зависимости** | нет |
| **Риск** | LOW — замена дублирующих констант |

**Описание:**
`sync_env_defaults.py` — fallback-литералы AGE/TELEGRAM дублируют `ci_default.py`. Импортировать из `shared/ci_default` или вынести в общий модуль.

**Шаги:**
1. Проверить, что ci_default.py содержит нужные константы.
2. Заменить литералы на импорт.

**Acceptance Criteria:**
- AC-G4.1: `sync_env_defaults.py` импортирует AGE/TELEGRAM defaults из shared

---

## $PARALLEL_GROUPS

### Wave 1 (независимые)
```
coder Read .ai/plans/119-wave2-synthesis/08-DevPlan.md, implement Wave 1: G1, G2, G3, G4
```

Все задачи не пересекаются по файлам — можно параллелить.

---

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/gates/test_gate_thin_wrapper.py` | `test_allowlist_current` | Allowlist содержит только актуальные исключения | thin_wrapper gate |
| `tests/gates/test_gate_system_exceptions.py` | `test_system_exceptions_documented` | Системные .PHONY задокументированы | manifest docs |

---

## Acceptance Criteria Summary

| Критерий | Проверка |
|----------|----------|
| AC-G-ALL | `make gate MODE=fast && make check-manifests && ruff check .` зелёные |
| AC-G-ALLOWLIST | thin_wrapper allowlist актуален |
| AC-G-DOCS | Системные исключения и bootstrap→deploy направление задокументированы |

---

## Next Steps

### Wave 1
```
coder Read .ai/plans/119-wave2-synthesis/08-DevPlan.md, implement Wave 1: G1, G2, G3, G4
```

После завершения:
```
make fix-gate && git add -u && make gate MODE=fast && make check-manifests
```

## $END_DEVPLAN
