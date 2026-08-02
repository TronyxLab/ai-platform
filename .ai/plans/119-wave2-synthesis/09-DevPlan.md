# 09-DevPlan — Бриф H: NodeYaml Decomposition (D2 Debt)

<!-- $ARTIFACT_CONTRACT
PURPOSE:          Декомпозиция node_yaml.py (1164 LOC) — крупнейшего монолита shared/-слоя.
                  Разбивка на миксины по поддоменам (domains/secrets/firewall/projects/contexts/certs)
                  с сохранением API .get(). Интеграция с atomic_writer (E5) для _write_back.
DESCRIPTION:      3 задачи (H1–H3). Самый высокий риск волны 119 — ~21 прямой потребитель
                  NodeYaml.get(). Вынесен в последний бриф H, зависит от стабильности B и E.
RATIONALE:        Долг D2 из 09-Debt.md с Rev-датой 2026-08-02. NodeYaml — God Object в shared/:
                  смешивает domains, secrets, firewall, projects, contexts, certs.
                  Декомпозиция снижает риск регресса при изменениях любого поддомена.
ACCEPTANCE_CRITERIA:
  - AC-H-1: `make gate MODE=fast` зелёный
  - AC-H-2: `pytest tests/ -m "not requires_node"` — 0 regressions
  - AC-H-3: 21 потребитель NodeYaml.get() работает без изменений (API сохранён)
  - AC-H-4: NodeYaml — тонкий агрегатор (<300 LOC core logic)
  - AC-H-5: _write_back → atomic_writer (E5)
IMPLEMENTS:       Бриф H из 01-Brief.md (волна 119) — NodeYaml Decomposition.
IMPACTS:          core/internal/shared/node_yaml.py (декомпозиция),
                  core/internal/shared/node_yaml/ (NEW — миксины),
                  core/internal/shared/atomic_writer.py (интеграция),
                  ~21 файл-потребитель NodeYaml.get().
REQUIRES:         E5 (atomic_writer), B1 (project_yaml — паттерн typed-accessor), B3 (deploy_paths).
-->

# DevPlan H — NodeYaml Decomposition

## $START_DEVPLAN

### Контекст

Волна 119, бриф H. Восьмая, последняя волна. Самый высокий риск — NodeYaml (1164 LOC) имеет ~21 прямого потребителя `.get()`. Декомпозиция на миксины с сохранением обратной совместимости API. Зависит от стабильности B (shared модули) и E (atomic_writer, миксин-паттерн из E3 phases).

---

## $TASKS

### TASK-H1: NodeYaml → миксины по поддоменам

| Поле | Значение |
|------|----------|
| **ID** | H1 |
| **Sev** | HIGH |
| **Сложность** | 8/10 |
| **Файлы** | `shared/node_yaml.py` (1164 LOC), `shared/node_yaml/` (NEW — 6+ миксинов) |
| **Зависимости** | B1 (project_yaml typed-accessor pattern), E3 (phases domain split pattern) |
| **Риск** | HIGH — 21 потребитель .get(), обратная совместимость критична |

**Описание:**
Декомпозиция 1164 LOC NodeYaml на миксины по поддоменам:

| Миксин | Ответственность | Поля |
|--------|----------------|------|
| `DomainsMixin` | domain, contexts | get_domain(), get_contexts() |
| `SecretsMixin` | secrets_config, age_keys | get_secrets_config() |
| `FirewallMixin` | firewall rules | get_firewall() |
| `ProjectsMixin` | projects, repos | get_projects(), get_repos() |
| `CertsMixin` | certificates, acme | get_acme_dns_plugin() |
| `NodeMixin` | node declaration, metadata | get_node_declaration(), get_email() |

NodeYaml — тонкий агрегатор, наследующий все миксины.

**Шаги:**
1. Создать `shared/node_yaml/` пакет с `__init__.py`.
2. Создать 6 миксинов, каждый со своими typed-аксессорами.
3. NodeYaml наследует все миксины: `class NodeYaml(DomainsMixin, SecretsMixin, FirewallMixin, ProjectsMixin, CertsMixin, NodeMixin)`.
4. API `.get()` должен работать без изменений — делегирует в миксины.
5. Полный регрессионный прогон: `pytest tests/ -k node_yaml`.
6. R5 negative-тест: `test_node_yaml_mixin_parity` — все 21 потребитель .get() проходят.

**Acceptance Criteria:**
- AC-H1.1: `shared/node_yaml/` пакет существует с 6 миксинами
- AC-H1.2: NodeYaml наследует миксины, API `.get()` сохранён
- AC-H1.3: `node_yaml.py` <300 LOC core logic (миксины импортируются)
- AC-H1.4: Все существующие тесты node_yaml проходят
- AC-H1.5: R5 negative-тест: 21 потребитель проходят parity-проверку

---

### TASK-H2: NodeYaml _write_back → atomic_writer

| Поле | Значение |
|------|----------|
| **ID** | H2 |
| **Sev** | HIGH |
| **Сложность** | 3/10 |
| **Файлы** | `shared/node_yaml.py`, `shared/atomic_writer.py` (создан в E5) |
| **Зависимости** | E5 (atomic_writer должен быть готов), H1 |
| **Риск** | MED — атомарная запись критична для node.yaml |

**Описание:**
`_write_back()` в NodeYaml — ручная реализация атомарной записи. Заменить на `atomic_writer.atomic_write_yaml()`.

**Шаги:**
1. Импортировать `atomic_write_yaml` из `shared/atomic_writer`.
2. Заменить тело `_write_back()` на вызов `atomic_write_yaml(path, data)`.
3. R5 negative-тест: `test_node_yaml_atomic_write` — прерывание не оставляет мусора.

**Acceptance Criteria:**
- AC-H2.1: `_write_back()` использует `atomic_writer.atomic_write_yaml`
- AC-H2.2: R5 negative-тест: нет partial write node.yaml

---

### TASK-H3: NodeYaml consumers verify-then-migrate

| Поле | Значение |
|------|----------|
| **ID** | H3 |
| **Sev** | MED |
| **Сложность** | 5/10 |
| **Файлы** | ~21 файл-потребитель |
| **Зависимости** | H1 |
| **Риск** | MED — много файлов, но изменения минимальны |

**Описание:**
После декомпозиции NodeYaml (H1) верифицировать всех ~21 потребителей `.get()` — импорты и вызовы должны работать без изменений.

**Шаги:**
1. `grep -rn "NodeYaml\|\.get(" core/internal/ | grep -v test_` — найти всех потребителей.
2. Для каждого потребителя: проверить, что вызов `.get(path)` работает через новый агрегатор.
3. При необходимости — поправить импорты (если путь к NodeYaml изменился).
4. Полный регрессионный прогон.

**Acceptance Criteria:**
- AC-H3.1: 21 потребитель работает без изменений API
- AC-H3.2: `pytest tests/ -m "not requires_node"` — 0 regressions

---

## $PARALLEL_GROUPS

### Последовательное выполнение (высокий риск)

```
Wave 1: H1 (декомпозиция — критическая, блокирует H2, H3)
Wave 2: H2, H3 (могут параллелиться после H1)
```

H2 и H3 не пересекаются по файлам (H2 = node_yaml + atomic_writer, H3 = consumers).

---

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/unit/test_node_yaml_domains.py` | `test_get_domain` | get_domain через DomainsMixin | node_yaml/domains |
| `tests/unit/test_node_yaml_secrets.py` | `test_get_secrets_config` | get_secrets_config через SecretsMixin | node_yaml/secrets |
| `tests/unit/test_node_yaml.py` | `test_node_yaml_mixin_parity_negative` | R5: все 21 потребитель .get() проходят | NodeYaml агрегатор |
| `tests/unit/test_node_yaml.py` | `test_write_back_atomic_negative` | R5: нет partial write node.yaml | _write_back → atomic_writer |
| `tests/unit/test_node_yaml_consumers.py` | `test_all_consumers_unchanged` | Все потребители импортируют NodeYaml корректно | node_yaml consumers |

---

## Acceptance Criteria Summary

| Критерий | Проверка |
|----------|----------|
| AC-H-ALL | `make gate MODE=fast && make check-manifests && ruff check .` зелёные |
| AC-H-API | 21 потребитель .get() работает без изменений |
| AC-H-MIXINS | 6 миксинов созданы, NodeYaml — тонкий агрегатор |
| AC-H-ATOMIC | _write_back → atomic_writer |
| AC-H-R5 | Parity-тест для всех потребителей + atomic write тест |

---

## Next Steps

### Wave 1
```
coder Read .ai/plans/119-wave2-synthesis/09-DevPlan.md, implement Wave 1: H1
```
### Wave 2
```
coder Read .ai/plans/119-wave2-synthesis/09-DevPlan.md, implement Wave 2: H2, H3
```

После завершения:
```
make fix-gate && git add -u && make gate MODE=fast && make check-manifests
```

## $END_DEVPLAN
