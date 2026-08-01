# 08-DevPlan — Бриф G: Python-декомпозиция монолитов

$ARTIFACT_CONTRACT
- PURPOSE: Реализация задач 51–58 программного брифа 117 — декомпозиция 12 Python-монолитов (суммарно ~11 565 LOC) на модули с единственной ответственностью, БЕЗ изменения поведения и БЕЗ нового функционала (AC5 программы).
- DESCRIPTION: 8 задач: (51) node_yaml.py CLI (420 LOC) → node_yaml_cli.py, (52) agent_watchdog.py 1088 → watchdog/: circuit_breaker + docker_ops + удаление дублей AuditLogger/TelegramNotifier, (53) vhost_renderer.py nginx_t_harness (~194 LOC) → nginx_harness.py, (54) monitoring_config_renderer.py split по 7 генераторам (~554 LOC) → monitoring/ подпакет, (55) status-page/app.py collectors (~450 LOC) + renderer (~270 LOC) → status_page/ модули, (56) port-scanner (~210 LOC) → port_scanner.py, (57) sync_env_defaults.py generate_env_example (450 LOC) → секционные функции, (58) 5 точечных экстракций (github_ops 80, bulk_restore 67, htpasswd 120, cron_installer 136, llm_provision 60).
- RATIONALE: Монолиты с >600 LOC затрудняют навигацию, тестирование и параллельную разработку. Каждый файл с >1 ответственностью нарушает AI-First принцип «одна ответственность на модуль». Декомпозиция без изменения поведения снижает coupling, повышает grep-ability и testability без регрессионного риска.
- ACCEPTANCE_CRITERIA:
  - AC-G1: Все 12 исходных файлов уменьшены до роли тонкого оркестратора (≤60% исходного LOC); вынесенные модули ≤300 LOC каждый.
  - AC-G2: Все существующие тесты проходят без изменений (поведение не меняется).
  - AC-G3: Новые unit-тесты покрывают вынесенные модули (≥80% branch coverage).
  - AC-G4: `make gate MODE=fast` и `make check-manifests` зелёные.
  - AC-G5: Lazy import для вынесенных модулей — start-up время не увеличивается.
  - AC-G6: Дубли AuditLogger/TelegramNotifier в agent_watchdog.py удалены → делегирование в shared модули (задача 19 брифа C).
  - AC-G7: Ноль новых глаголов/механизмов; все изменения — перемещение кода между файлами.
- IMPLEMENTS: 117 01-Brief задачи 51–58.
- IMPACTS: core/internal/shared/node_yaml.py, core/modules/hermes-agent/watchdog/ (4 новых файла), core/internal/scaffold/vhost_renderer.py + nginx_harness.py, core/internal/monitoring_config_renderer.py + monitoring/ (7 новых), core/modules/status-page/app.py + collectors.py + renderer.py, core/internal/scripts/generate_platform_env.py + port_scanner.py, core/internal/scripts/sync_env_defaults.py, core/internal/scaffold/project_scaffolder.py + github_ops.py, core/internal/bootstrap/s3_ssl_cache.py, core/internal/bootstrap/lifecycle/secrets_manager.py + htpasswd.py, core/internal/bootstrap/cert_orchestrator.py + cron_installer.py, core/internal/bootstrap/deploy/context_deployer.py + llm_provision.py, tests/unit/ (новые тесты для вынесенных модулей).
- REQUIRES: 117 01-Brief (реестр), результаты верификации монолитов 2026-08-01, зелёный gate после брифов A–F.

---

## 0. Коррекции к исходному брифу (по результатам верификации)

| Задача | Исходный вердикт брифа | Фактический вердикт | Действие |
|--------|----------------------|---------------------|----------|
| 51 (MED) | node_yaml.py CLI 420 LOC (L1470-1890) | **Подтверждено.** CLI = L1459-1889 (~430 LOC): _build_arg_parser + 10 _cli_* функций + main(). Точное совпадение. | Без изменений. |
| 52 (MED) | agent_watchdog.py → watchdog/: circuit_breaker, docker_ops, notifier, watchdog | **Уточнение.** AuditLogger (L267) и TelegramNotifier (L567) — дубли shared/audit_logger.py и shared/telegram_notifier.py (задача 19 брифа C). Вынос их в отдельные файлы watchdog/ — неправильно: нужно УДАЛИТЬ дубли и делегировать в shared. | notifier НЕ выносится как отдельный файл — удаляется в пользу shared.telegram_notifier. AuditLogger — удаляется в пользу shared.audit_logger. watchdog.py остаётся оркестратором. |
| 53 (MED) | vhost_renderer.py nginx-harness ~200 (L696-900) | **Подтверждено.** nginx_t_harness = L696-889 (~194 LOC). Точное совпадение. | Без изменений. |
| 54 (MED) | monitoring_config_renderer.py split по генераторам (L257-810) | **Уточнение диапазона.** Генераторы занимают L454-847 (~554 LOC для 7 генераторов), не L257-810. L257-370 — это config loading (merge + load), который остаётся в основном файле. L374-410 — template rendering (тоже остаётся). | Диапазон extraction: L454-847 (Prometheus, Grafana, Loki, Langfuse, Alert rules, Catalog, Service reload). Config loading (L152-370) остаётся в monitoring_config_renderer.py. |
| 55 (MED) | status-page/app.py collectors/renderer/server (L228-1053) | **Подтверждено.** collectors: ~450 LOC, renderer: ~270 LOC, server: ~310 LOC. | Без изменений. |
| 56 (MED) | generate_platform_env.py порт-сканер (L188-321) | **Уточнение диапазона.** Порт-сканер = extract_host_port (L187-232) + scan_compose_ports (L236-316) + scan_test_ports (L320-402) = ~210 LOC. Бриф указал только L188-321, но scan_test_ports (L320-402) — часть того же домена. | Расширить диапазон до L187-402. Вынести все 3 функции в port_scanner.py. |
| 57 (MED) | sync_env_defaults.py функция 450 LOC (L85-538) | **Подтверждено.** generate_env_example() = L85-534 (~450 LOC). Монолитная функция без внутренних абстракций. | Разбить на секционные функции в том же файле (не отдельный модуль). |
| 58 (LOW) | 5 точечных экстракций | **Подтверждено.** project_scaffolder github_ops (L356-435, 80 LOC), s3_ssl_cache bulk_restore (L666-732, 67 LOC), secrets_manager htpasswd (_write_htpasswd_file + _ensure_htpasswd + _extract_apr1_salt = ~120 LOC), cert_orchestrator cron_install (_install_cron + migrate_cron_if_needed = ~136 LOC), context_deployer LLM (_render_and_provision_llm = ~60 LOC). | Без изменений. |

---

## 1. Технический анализ и решения

### Задача 51 (MED) — node_yaml.py: вынос CLI + типизированные reader'ы

**Факты (верифицированы):**
- `core/internal/shared/node_yaml.py` = 1890 LOC.
- CLI-секция (L1459-1889): `_build_arg_parser()` (66 LOC), `_cli_get()` (18), `_traverse_dotted_list_aware()` (26), `_cli_get_many()` (46), `_cli_domain_config()` (12), `_cli_find_project()` (21), `_cli_validate()` (10), `_cli_validate_schema()` (10), `_cli_resolve()` (17), `_cli_typed_json()` (25), `main()` (132). **Итого ~430 LOC CLI.**
- NodeYaml class (L294-1458): ~1165 LOC — основной фасад. Все CLI-функции используют NodeYaml через параметр (DI), а не self → вынос безопасен.
- Dataclasses (L71-255): ContextEntry, NodeDeclaration, FirewallConfig, SecretEntry, SecretsConfig, TorConfig, ModuleEntry, ProjectEntry, ReposConfig — остаются в node_yaml.py (публичное API).
- CLI НЕ импортируется никем извне (вызывается только через `python3 -m core.internal.shared.node_yaml` из shell-фасадов).
- Существующие тесты: `tests/unit/test_node_yaml.py` (136 строк) — тестирует NodeYaml class, НЕ CLI. CLI практически не покрыт тестами.

**Решение D51:** Вынести CLI в `core/internal/shared/node_yaml_cli.py`.
- Все 10 `_cli_*` функций + `_build_arg_parser()` + `main()` → `node_yaml_cli.py`.
- В `node_yaml.py`: заменить `if __name__ == "__main__"` на lazy-импорт `from core.internal.shared.node_yaml_cli import main; sys.exit(main())`.
- `__main__.py` или `python3 -m core.internal.shared.node_yaml` продолжает работать через обратную совместимость.
- Новые тесты: `tests/unit/test_node_yaml_cli.py` — unit-тесты на каждую CLI-команду через mock NodeYaml.

**Файлы:**
- **Создать:** `core/internal/shared/node_yaml_cli.py` (~430 LOC)
- **Изменить:** `core/internal/shared/node_yaml.py` (L1459-1889 заменить на lazy-import, −430 LOC)
- **Создать:** `tests/unit/test_node_yaml_cli.py` (ожидаемо ~200-300 LOC тестов)

**Риск:** LOW. CLI не импортируется извне — только shell-фасады через `python3 -m`. NodeYaml class не меняется.

---

### Задача 52 (MED) — agent_watchdog.py: декомпозиция watchdog/

**Факты (верифицированы):**
- `core/modules/hermes-agent/watchdog/agent_watchdog.py` = 1088 LOC.
- Структура ответственностей (верифицирована grep-ом классов):

| Класс | Строки | LOC | Ответственность | Статус |
|-------|--------|-----|-----------------|--------|
| `CircuitBreakerService` | L59-103 | ~45 | Конфигурация CB-сервиса | Вынести |
| `WatchdogConfig` | L105-195 | ~91 | Конфигурация watchdog | Оставить |
| `PendingUpdate` | L196-247 | ~52 | Состояние pending update | Оставить |
| `CircuitEvent` | L248-259 | ~12 | CB-событие | Вынести |
| `AuditLogger` | L266-292 | ~27 | **ДУБЛЬ shared/audit_logger.py** | **Удалить** |
| `CircuitBreaker` | L299-507 | ~209 | Основная CB-логика | Вынести |
| `HealthChecker` | L513-561 | ~49 | Health check | Оставить |
| `TelegramNotifier` | L567-629 | ~63 | **ДУБЛЬ shared/telegram_notifier.py** | **Удалить** |
| `DockerManager` | L635-836 | ~202 | Docker-операции | Вынести |
| `Watchdog` | L842-1040 | ~199 | Оркестратор watchdog | Оставить |
| `main()` | L1046-1082 | ~37 | CLI entrypoint | Оставить |

- AuditLogger (L267) — упрощённая копия `core/internal/shared/audit_logger.py`. Бриф C задача 19 предписывает удаление дубля.
- TelegramNotifier (L567) — упрощённая копия `core/internal/shared/telegram_notifier.py`. Тот же бриф C.
- CircuitBreaker (~260 LOC с учётом CircuitBreakerService + CircuitEvent) — полностью самодостаточный, импортирует только stdlib.
- DockerManager (~200 LOC) — subprocess-обёртки над docker compose. Самодостаточный.
- Существующие тесты: `tests/unit/test_agent_watchdog.py` (507 строк) — мокируют AuditLogger/TelegramNotifier/DockerManager.

**Решение D52:**
1. **Удалить дубли:** AuditLogger → заменить на `from core.internal.shared.audit_logger import write_audit_entry`, адаптировать вызовы (Watchdog.write_audit_entry → write_audit_entry напрямую). TelegramNotifier → заменить на `from core.internal.shared.telegram_notifier import send_telegram`.
2. **Вынести CircuitBreaker** → `core/modules/hermes-agent/watchdog/circuit_breaker.py`: классы `CircuitBreakerService`, `CircuitEvent`, `CircuitBreaker`. ~266 LOC.
3. **Вынести DockerManager** → `core/modules/hermes-agent/watchdog/docker_ops.py`: класс `DockerManager`. ~202 LOC.
4. **agent_watchdog.py остаётся:** WatchdogConfig, PendingUpdate, HealthChecker, Watchdog (оркестратор), main(). Импортирует circuit_breaker и docker_ops через lazy import. ~400 LOC.

**Файлы:**
- **Создать:** `core/modules/hermes-agent/watchdog/circuit_breaker.py` (~266 LOC)
- **Создать:** `core/modules/hermes-agent/watchdog/docker_ops.py` (~202 LOC)
- **Изменить:** `core/modules/hermes-agent/watchdog/agent_watchdog.py` (~1088 → ~400 LOC)
- **Обновить:** `tests/unit/test_agent_watchdog.py` (адаптировать моки под новые импорты)
- **Создать:** `tests/unit/test_watchdog_circuit_breaker.py` (~150 LOC)
- **Создать:** `tests/unit/test_watchdog_docker_ops.py` (~120 LOC)

**Риск:** MEDIUM. Удаление дублей меняет сигнатуру вызовов — AuditLogger → write_audit_entry (формат отличается: agent_watchdog использует упрощённый формат). Нужна адаптация. Тесты требуют обновления моков.

---

### Задача 53 (MED) — vhost_renderer.py: вынос nginx_t_harness

**Факты (верифицированы):**
- `core/internal/scaffold/vhost_renderer.py` = 1189 LOC.
- `nginx_t_harness()` = L696-889 (~194 LOC). Полностью самодостаточная функция: создаёт Docker-окружение, генерирует stub nginx.conf, меняет SSL-пути, запускает `docker run nginx -t`. Импортирует: `tempfile`, `re`, `subprocess`, `shutil`, `pathlib`. Все — stdlib.
- Вызывается из `render_all()` (L900+) и CLI main(). Обе точки вызова — в том же файле.
- Существующие тесты: `tests/unit/test_vhost_renderer.py` (988 строк) — тестируют render_all, generate_vhost_body, и т.д. nginx_t_harness тестируется опосредованно через render_all с моком docker.

**Решение D53:** Вынести `nginx_t_harness()` в `core/internal/scaffold/nginx_harness.py`.
- Функция переносится «как есть» с сохранением всех LDD-логов и TRAP-комментариев.
- В `vhost_renderer.py`: lazy-импорт `from core.internal.scaffold.nginx_harness import nginx_t_harness`.
- Обратная совместимость: `render_all()` и CLI продолжают работать без изменений.
- Новые тесты: `tests/unit/test_nginx_harness.py` — прямые тесты nginx_t_harness с tmp_path (без render_all-опосредования).

**Файлы:**
- **Создать:** `core/internal/scaffold/nginx_harness.py` (~194 LOC)
- **Изменить:** `core/internal/scaffold/vhost_renderer.py` (L685-890 заменить на import, −194 LOC)
- **Создать:** `tests/unit/test_nginx_harness.py` (~100-150 LOC)

**Риск:** LOW. Функция самодостаточна, не имеет внутренних зависимостей от vhost_renderer.

---

### Задача 54 (MED) — monitoring_config_renderer.py: split по генераторам

**Факты (верифицированы):**
- `core/internal/monitoring_config_renderer.py` = 938 LOC.
- Фактическая структура:

| Секция | Строки | LOC | Статус |
|--------|--------|-----|--------|
| Constants | L56-90 | ~35 | Оставить |
| Dataclasses | L93-148 | ~56 | Оставить |
| Config loading + merge | L152-370 | ~219 | Оставить (оркестратор) |
| Template rendering | L374-410 | ~37 | Оставить |
| Retention parsing | L413-450 | ~38 | Оставить |
| **generate_prometheus_target** | L454-507 | ~54 | **Вынести** |
| **generate_grafana_dashboard** | L511-563 | ~53 | **Вынести** |
| **update_loki_retention** | L567-645 | ~79 | **Вынести** |
| **create_langfuse_project** | L649-714 | ~66 | **Вынести** |
| **generate_alert_rules** | L718-766 | ~49 | **Вынести** |
| **refresh_catalog** | L770-803 | ~34 | **Вынести** |
| **reload_monitoring_services** | L807-847 | ~41 | **Вынести** |
| CLI + main() | L851-938 | ~88 | Оставить |

- Все 7 генераторов — независимые функции, каждая принимает typed dataclass/config и возвращает RenderResult. Не вызывают друг друга. Используют общие constants + load_yaml_config + _render_template из основного файла.
- Существующие тесты: `tests/unit/test_monitoring_config_renderer.py` (943 строки) — интеграционные тесты через main().

**Решение D54:** Создать подпакет `core/internal/monitoring/` (7 модулей + `__init__.py`):
- `monitoring/__init__.py` — пустой (пакетный контракт)
- `monitoring/prometheus_targets.py` — `generate_prometheus_target()` (~70 LOC с импортами)
- `monitoring/grafana_dashboards.py` — `generate_grafana_dashboard()` (~70 LOC)
- `monitoring/loki_retention.py` — `update_loki_retention()` (~100 LOC)
- `monitoring/langfuse_projects.py` — `create_langfuse_project()` (~90 LOC)
- `monitoring/alert_rules.py` — `generate_alert_rules()` (~70 LOC)
- `monitoring/catalog_refresh.py` — `refresh_catalog()` (~55 LOC)
- `monitoring/service_reload.py` — `reload_monitoring_services()` (~65 LOC)

В `monitoring_config_renderer.py`: заменить тела функций на lazy-импорт + делегирование. Константы (L56-90) вынести в `monitoring/constants.py` для переиспользования генераторами.
- Обратная совместимость: `main()` и внешние вызовы (`python3 -m core.internal.monitoring_config_renderer`) не меняются.

**Файлы:**
- **Создать:** `core/internal/monitoring/__init__.py`
- **Создать:** `core/internal/monitoring/constants.py` (~40 LOC)
- **Создать:** `core/internal/monitoring/prometheus_targets.py` (~70 LOC)
- **Создать:** `core/internal/monitoring/grafana_dashboards.py` (~70 LOC)
- **Создать:** `core/internal/monitoring/loki_retention.py` (~100 LOC)
- **Создать:** `core/internal/monitoring/langfuse_projects.py` (~90 LOC)
- **Создать:** `core/internal/monitoring/alert_rules.py` (~70 LOC)
- **Создать:** `core/internal/monitoring/catalog_refresh.py` (~55 LOC)
- **Создать:** `core/internal/monitoring/service_reload.py` (~65 LOC)
- **Изменить:** `core/internal/monitoring_config_renderer.py` (938 → ~380 LOC)
- **Создать:** `tests/unit/test_monitoring_prometheus_targets.py` (~80 LOC)
- **Создать:** `tests/unit/test_monitoring_grafana_dashboards.py` (~80 LOC)
- **Создать:** `tests/unit/test_monitoring_loki_retention.py` (~100 LOC)
- **Создать:** `tests/unit/test_monitoring_langfuse_projects.py` (~100 LOC)
- **Создать:** `tests/unit/test_monitoring_alert_rules.py` (~80 LOC)
- **Создать:** `tests/unit/test_monitoring_catalog_refresh.py` (~60 LOC)
- **Создать:** `tests/unit/test_monitoring_service_reload.py` (~70 LOC)
- **Обновить:** `tests/unit/test_monitoring_config_renderer.py` (интеграционные тесты без изменений)

**Риск:** MEDIUM. Создание подпакета `monitoring/` может сломать `sys.path`-манипуляции в L43-52 (dual import: `from core.internal.template_engine import render_template` vs `from template_engine import render_template`). Каждый генератор должен использовать тот же dual-import паттерн.

---

### Задача 55 (MED) — status-page/app.py: collectors/renderer/server

**Факты (верифицированы):**
- `core/modules/status-page/app.py` = 1075 LOC.
- Это Docker-модуль (`core/modules/status-page/`). Импортирует только stdlib + `jinja2`. НЕ может импортировать `core/internal/` (cross-layer violation).
- Фактическая группировка:

| Группа | Функции | Строки | LOC | Статус |
|--------|---------|--------|-----|--------|
| **Config + Jinja2** | LISTEN_PORT, PLATFORM_SERVICES, _jinja_env | L65-120 | ~56 | Оставить в app.py |
| **Collectors** | load_node_yaml, _load_status_metrics, get_vhosts, get_modules, _curl_vhost, _curl_platform_service, _check_container, _compute_staleness, get_all_checks | L126-733 | ~450 | **Вынести** |
| **Renderer** | _enrich_projects, _enrich_containers, _compute_uptime_human, _format_bytes, _render_html | L464-834 | ~270 | **Вынести** |
| **Server** | StatusPageHandler, main() | L843-1075 | ~232 | Оставить в app.py |

- `get_all_checks()` (L650-733) вызывает все коллекторы и возвращает агрегированный dict — это точка интеграции.
- `_render_html()` (L742-834) принимает dict из get_all_checks — чистая функция рендеринга.
- Статус: существующих тестов для status-page НЕТ (test_status_page.py не найден). Тесты упоминаются в brief F как `test_status_page.py:260` (77× sys.path.insert), но файл отсутствует в репо — вероятно, был удалён или перемещён.

**Решение D55:**
1. **Вынести collectors** → `core/modules/status-page/collectors.py`: все 9 функций от load_node_yaml до get_all_checks. ~450 LOC.
2. **Вынести renderer** → `core/modules/status-page/renderer.py`: _enrich_projects, _enrich_containers, _compute_uptime_human, _format_bytes, _render_html. ~270 LOC.
3. **app.py остаётся:** Config, Jinja2 env, StatusPageHandler, main(). Импортирует collectors и renderer.
   - **lazy import НЕ нужен** — это Docker-контейнер с одним процессом, стартовое время некритично.
4. **Новые тесты:** `tests/unit/test_status_collectors.py` и `tests/unit/test_status_renderer.py`.

**Файлы:**
- **Создать:** `core/modules/status-page/collectors.py` (~450 LOC)
- **Создать:** `core/modules/status-page/renderer.py` (~270 LOC)
- **Изменить:** `core/modules/status-page/app.py` (1075 → ~355 LOC)
- **Создать:** `tests/unit/test_status_collectors.py` (~200 LOC)
- **Создать:** `tests/unit/test_status_renderer.py` (~150 LOC)

**Риск:** LOW. Module-изоляция: `status-page/` не импортирует `core/internal/`, вынесенные модули — соседи по директории. Jinja2 env импортируется из renderer.py через `from app import _jinja_env` или передаётся параметром.

---

### Задача 56 (MED) — generate_platform_env.py: порт-сканер

**Факты (верифицированы):**
- `core/internal/scripts/generate_platform_env.py` = 863 LOC.
- Порт-сканер = 3 функции: `extract_host_port()` (L187-232, 46 LOC), `scan_compose_ports()` (L236-316, 81 LOC), `scan_test_ports()` (L320-402, 83 LOC). **Итого ~210 LOC.**
- `extract_host_port()` — чистая функция (regex → int | None), без зависимостей.
- `scan_compose_ports()` — зависит от `extract_host_port` + yaml + pathlib.
- `scan_test_ports()` — зависит от yaml + pathlib + собственный OverrideLoader.
- Используются только в `generate_platform_env_yaml()` (L451) — один потребитель.
- Существующие тесты: `tests/unit/test_generate_platform_env.py` (317 строк) — тестируют generate_platform_env_yaml с моками.

**Решение D56:** Вынести 3 функции в `core/internal/scripts/port_scanner.py`.
- `port_scanner.py`: extract_host_port, scan_compose_ports, scan_test_ports + _PORT_NAME_MAP (общий с generate_platform_env.py).
- В `generate_platform_env.py`: удалить _PORT_NAME_MAP и 3 функции, заменить на `from core.internal.scripts.port_scanner import scan_compose_ports, scan_test_ports`.
- Новые тесты: `tests/unit/test_port_scanner.py` — прямые тесты extract_host_port (5+ форматов портов), scan_compose_ports (tmp_path с mock base.yml), scan_test_ports.

**Файлы:**
- **Создать:** `core/internal/scripts/port_scanner.py` (~210 LOC)
- **Изменить:** `core/internal/scripts/generate_platform_env.py` (863 → ~653 LOC после удаления функций + добавления import)
- **Создать:** `tests/unit/test_port_scanner.py` (~120 LOC)

**Риск:** LOW. _PORT_NAME_MAP нужно вынести в port_scanner.py (единственный источник). generate_platform_env.py больше не использует его напрямую.

---

### Задача 57 (MED) — sync_env_defaults.py: секционные функции

**Факты (верифицированы):**
- `core/internal/scripts/sync_env_defaults.py` = 626 LOC.
- `generate_env_example()` = L85-534 (~450 LOC). Монолитная функция: header → 18 секций → footer.
- Каждая секция — это несколько `lines.append()` с вызовами `get_val()`/`get_val_required()` и опциональными CONSTRAINT-комментариями.
- Все секции разделены комментариями `# ── Section Name ──`, но логически не выделены в отдельные функции.
- Внутренние хелперы: `get_val()` (L93-94), `get_val_required()` (L96-110), `sd_get()` (L112-115) — замыкания внутри generate_env_example.
- Существующие тесты: `tests/unit/test_sync_env_defaults.py` (270 строк) — тестируют generate_env_example целиком.

**Решение D57:** Разбить `generate_env_example()` на секционные функции в том же файле.
- `get_val()` / `get_val_required()` / `sd_get()` → вынести на уровень модуля как `_get_env_val()` / `_get_env_val_required()` / `_get_secret_def_field()` с параметром `env_defaults` вместо замыкания.
- Создать секционные функции:
  - `_section_header()` → header + MODULE_CONTRACT docstring (~50 LOC)
  - `_section_platform_context()` (~35 LOC)
  - `_section_platform_secrets()` (~40 LOC)
  - `_section_postgres()` (~35 LOC)
  - `_section_pgbouncer()` (~15 LOC)
  - `_section_redis()` (~15 LOC)
  - `_section_clickhouse()` (~30 LOC)
  - `_section_minio()` (~40 LOC)
  - `_section_s3_backup()` (~30 LOC)
  - `_section_llm_provider()` (~15 LOC)
  - `_section_litellm()` (~35 LOC)
  - `_section_langfuse()` (~40 LOC)
  - `_section_hermes_dashboard()` (~35 LOC)
  - `_section_hermes_api()` (~20 LOC)
  - `_section_telegram()` (~25 LOC)
  - `_section_nginx()` (~30 LOC)
  - `_section_ssl_dns()` (~20 LOC)
  - `_section_proxy()` (~30 LOC)
  - `_section_monitoring()` (~30 LOC)
  - `_section_compose_profiles()` (~15 LOC)
  - `_section_misc()` (~15 LOC)
  - `_section_github_actions()` (~30 LOC) — только комментарии, не генерирует переменные
- `generate_env_example()` → оркестратор (~30 LOC): вызывает все секции, собирает lines, join.
- Обратная совместимость: сигнатура `generate_env_example(env_defaults, secret_defs) -> str` не меняется.

**Файлы:**
- **Изменить:** `core/internal/scripts/sync_env_defaults.py` (626 LOC → реструктуризация, LOC примерно тот же)
- **Обновить:** `tests/unit/test_sync_env_defaults.py` (добавить тесты на отдельные секции)

**Риск:** LOW. Чистая реструктуризация внутри файла — внешние потребители (`make sync-env-defaults`) не затрагиваются.

---

### Задача 58 (LOW) — 5 точечных экстракций

#### D58.1 — project_scaffolder: github_ops

**Факты:** `create_github_repo()` (L356-435, ~80 LOC) — полностью самодостаточная функция (subprocess gh + git). Используется только в `main()`.

**Решение:** Вынести в `core/internal/scaffold/github_ops.py`. Функция `create_github_repo()`. В project_scaffolder.py — lazy import.

**Файлы:** Создать `github_ops.py` (~80 LOC), изменить `project_scaffolder.py`, создать `tests/unit/test_github_ops.py` (~80 LOC).

#### D58.2 — s3_ssl_cache: bulk_restore (уже изолирован)

**Факты:** `bulk_restore()` (L666-732, 67 LOC) — уже отдельная public-функция в секции PUBLIC API. Не требует выноса.

**Решение:** Без изменений. Задача закрыта — bulk_restore уже имеет чёткие границы.

**Файлы:** Нет изменений.

#### D58.3 — secrets_manager: htpasswd

**Факты:** `_write_htpasswd_file()` (L486-559, 75 LOC) + `_ensure_htpasswd()` (L563-598, 36 LOC) + `_extract_apr1_salt()` (L462-482, 21 LOC) = ~132 LOC. Все три функции — приватные, вызываются из `ensure_secrets()` и CLI.

**Решение:** Вынести в `core/internal/bootstrap/lifecycle/htpasswd.py`. Три функции становятся публичными: `write_htpasswd_file()`, `ensure_htpasswd()`, `extract_apr1_salt()`. В secrets_manager.py — lazy import.

**Файлы:** Создать `lifecycle/htpasswd.py` (~132 LOC), изменить `secrets_manager.py`, создать `tests/unit/test_htpasswd.py` (~100 LOC).

#### D58.4 — cert_orchestrator: cron_installer

**Факты:** `_install_cron()` (L566-626, 62 LOC) + `migrate_cron_if_needed()` (L630-703, 74 LOC) = ~136 LOC. Обе функции — приватные, вызываются из `orchestrate_certs()`.

**Решение:** Вынести в `core/internal/bootstrap/cron_installer.py`. Функции: `install_acme_cron()` и `migrate_acme_cron_if_needed()`. В cert_orchestrator.py — lazy import.

**Файлы:** Создать `cron_installer.py` (~136 LOC), изменить `cert_orchestrator.py`, создать `tests/unit/test_cron_installer.py` (~100 LOC).

#### D58.5 — context_deployer: LLM provision

**Факты:** `_render_and_provision_llm()` (L514-569, ~56 LOC) — приватная функция, subprocess-вызовы config_renderer + provision-llm. Вызывается из `deploy_context()`.

**Решение:** Вынести в `core/internal/bootstrap/deploy/llm_provision.py`. Функция: `render_and_provision_llm()`. В context_deployer.py — lazy import.

**Файлы:** Создать `deploy/llm_provision.py` (~60 LOC), изменить `context_deployer.py`, создать `tests/unit/test_llm_provision.py` (~70 LOC).

---

## 2. Порядок реализации

### Волна 1 — Низкий риск, нет зависимостей (7 задач)
1. **D53** (nginx_harness.py) — 1 файл, самодостаточная функция.
2. **D56** (port_scanner.py) — 1 файл, чистые функции.
3. **D58.1** (github_ops.py) — 1 файл.
4. **D58.2** (s3_ssl_cache bulk_restore) — закрыта без действий.
5. **D58.3** (htpasswd.py) — 1 файл.
6. **D58.4** (cron_installer.py) — 1 файл.
7. **D58.5** (llm_provision.py) — 1 файл.

### Волна 2 — Средний риск, декомпозиция с сохранением контрактов (3 задачи)
8. **D51** (node_yaml_cli.py) — CLI полностью изолирован.
9. **D57** (sync_env_defaults секции) — реструктуризация внутри файла.
10. **D55** (status-page collectors/renderer) — 2 новых файла, module-уровень.

### Волна 3 — Высокий объём, много новых файлов (2 задачи)
11. **D52** (watchdog/) — удаление дублей + 2 новых файла.
12. **D54** (monitoring/ подпакет) — 8 новых файлов, критично для CI.

### Волна 4 — Верификация
13. `make gate MODE=fast` — зелёный.
14. `make check-manifests` — зелёный.
15. Полный прогон тестов: `make test MARKER=all`.
16. `rg "from core\.internal\.shared\.node_yaml import" core/` — проверка обратной совместимости импортов.

---

## 3. Критерии приёмки

### AC-G1: LOC-редукция монолитов
| Файл | До | После | Дельта |
|------|-----|-------|--------|
| node_yaml.py | 1890 | ~1470 | −420 |
| agent_watchdog.py | 1088 | ~400 | −688 (включая удаление дублей) |
| vhost_renderer.py | 1189 | ~995 | −194 |
| monitoring_config_renderer.py | 938 | ~380 | −558 |
| status-page/app.py | 1075 | ~355 | −720 |
| generate_platform_env.py | 863 | ~653 | −210 |
| sync_env_defaults.py | 626 | ~626 | ~0 (реструктуризация) |
| project_scaffolder.py | 767 | ~687 | −80 |
| secrets_manager.py | 685 | ~553 | −132 |
| cert_orchestrator.py | 775 | ~639 | −136 |
| context_deployer.py | 853 | ~793 | −60 |

**Целевые показатели:** ~8000 LOC монолитов → ~3500 LOC оркестраторов + ~3500 LOC вынесенных модулей + ~1500 LOC дублей удалено.

### AC-G2: Существующие тесты
- `tests/unit/test_node_yaml.py` (136) — PASS
- `tests/unit/test_agent_watchdog.py` (507) — PASS (с адаптацией моков)
- `tests/unit/test_vhost_renderer.py` (988) — PASS
- `tests/unit/test_monitoring_config_renderer.py` (943) — PASS
- `tests/unit/test_generate_platform_env.py` (317) — PASS
- `tests/unit/test_sync_env_defaults.py` (270) — PASS
- `tests/unit/test_s3_ssl_cache.py` (464) — PASS
- `tests/unit/test_secrets_manager.py` (523) — PASS
- `tests/unit/test_cert_orchestrator.py` (406) — PASS
- `tests/unit/test_context_deployer.py` (289) — PASS

### AC-G3: Новые тесты
| Тест-файл | Ожидаемый LOC | Покрываемый модуль |
|-----------|---------------|-------------------|
| test_node_yaml_cli.py | 200-300 | node_yaml_cli.py |
| test_watchdog_circuit_breaker.py | ~150 | circuit_breaker.py |
| test_watchdog_docker_ops.py | ~120 | docker_ops.py |
| test_nginx_harness.py | ~120 | nginx_harness.py |
| test_monitoring_prometheus_targets.py | ~80 | prometheus_targets.py |
| test_monitoring_grafana_dashboards.py | ~80 | grafana_dashboards.py |
| test_monitoring_loki_retention.py | ~100 | loki_retention.py |
| test_monitoring_langfuse_projects.py | ~100 | langfuse_projects.py |
| test_monitoring_alert_rules.py | ~80 | alert_rules.py |
| test_monitoring_catalog_refresh.py | ~60 | catalog_refresh.py |
| test_monitoring_service_reload.py | ~70 | service_reload.py |
| test_status_collectors.py | ~200 | collectors.py |
| test_status_renderer.py | ~150 | renderer.py |
| test_port_scanner.py | ~120 | port_scanner.py |
| test_github_ops.py | ~80 | github_ops.py |
| test_htpasswd.py | ~100 | htpasswd.py |
| test_cron_installer.py | ~100 | cron_installer.py |
| test_llm_provision.py | ~70 | llm_provision.py |

**Всего:** ~18 новых тест-файлов, ~2080 LOC тестов.

### AC-G4–G7: Gate / Manifest / Lazy import / No new verbs
- `make gate MODE=fast` зелёный
- `make check-manifests` зелёный (entrypoint-manifest НЕ меняется)
- Lazy import: grep `import.*node_yaml_cli\|import.*circuit_breaker\|import.*docker_ops\|import.*nginx_harness` — все импорты внутри функций/методов, не на уровне модуля
- 0 новых глаголов в entrypoint-manifest.yaml

---

## 4. Риски и митигации

| # | Риск | Вероятность | Влияние | Митигация |
|----|------|------------|---------|-----------|
| R1 | D52: удаление AuditLogger/TelegramNotifier ломает формат логов | MED | HIGH | Сравнить форматы write_audit_entry в shared vs watchdog; адаптировать watchdog-вызовы. Если форматы несовместимы — добавить adapter в watchdog. |
| R2 | D52: тесты agent_watchdog.py (507 строк) требуют массовой адаптации моков | HIGH | MED | Обновлять тесты параллельно с кодом; запускать `pytest tests/unit/test_agent_watchdog.py -x` после каждого изменения. |
| R3 | D54: sys.path дуал-импорт ломается при переносе генераторов в monitoring/ | MED | HIGH | Каждый генератор использует тот же try/except паттерн (L43-52). Проверить импорты через `python3 -c "from core.internal.monitoring.prometheus_targets import generate_prometheus_target"`. |
| R4 | D55: Jinja2 env (_jinja_env) — циклический импорт между app.py и renderer.py | LOW | MED | Передавать jinja_env через параметр `render_html(data, jinja_env)` вместо импорта из app. |
| R5 | D54: мониторинг-генераторы используют общие constants — дублирование | MED | LOW | Вынести constants в `monitoring/constants.py`. Все генераторы импортируют из него. |
| R6 | D51: `python3 -m core.internal.shared.node_yaml` перестаёт работать после переноса main() | LOW | HIGH | Оставить `if __name__ == "__main__"` в node_yaml.py с lazy-импортом main из node_yaml_cli. Двойной `python3 -m` (и node_yaml, и node_yaml_cli) работают. |
| R7 | D58.2: bulk_restore уже изолирована — задача закрыта без действий | N/A | N/A | Зафиксировать в DevPlan. |
| R8 | Отсутствие тестов для status-page/app.py — нет baseline | HIGH | MED | Создать collectors/renderer тесты до рефакторинга (characterization tests), затем рефакторить. |

---

## 5. Оценка

- **Изменяемых файлов:** 12 исходных (модификация).
- **Новых Python-модулей:** ~19 (node_yaml_cli, circuit_breaker, docker_ops, nginx_harness, 7 monitoring/*, collectors, renderer, port_scanner, github_ops, htpasswd, cron_installer, llm_provision).
- **Новых тест-файлов:** ~18.
- **Общий охват файлов:** ~49 файлов → **LARGE** (>20 файлов, архитектурные изменения).
- **LOC-бюджет:** ~11 565 → ~11 000 (незначительное увеличение за счёт импортов/boilerplate в новых файлах). Чистое сокращение за счёт удаления дублей AuditLogger/TelegramNotifier (~90 LOC).
- **Трудозатраты:** ~1.5–2 дня агент-времени (3 волны).
- **Размер:** LARGE → требуется Brief.md + DevPlan.md. **Настоящий DevPlan — единственный артефакт (Brief.md программы уже создан как 01-Brief.md).**

### $TASKS

| ID | Задача | Владелец | Артефакт | Зависимости | Сложность | Волна |
|----|--------|----------|----------|-------------|-----------|-------|
| T51 | node_yaml.py CLI → node_yaml_cli.py | Coder | node_yaml_cli.py + тесты | — | 3 | 2 |
| T52 | agent_watchdog.py → watchdog/: circuit_breaker, docker_ops, удаление дублей | Coder | circuit_breaker.py, docker_ops.py, agent_watchdog.py (модиф.) + тесты | — | 6 | 3 |
| T53 | vhost_renderer.py nginx_t_harness → nginx_harness.py | Coder | nginx_harness.py + тесты | — | 2 | 1 |
| T54 | monitoring_config_renderer.py → monitoring/ подпакет (7 генераторов) | Coder | monitoring/*.py + тесты | — | 7 | 3 |
| T55 | status-page/app.py → collectors.py + renderer.py | Coder | collectors.py, renderer.py, app.py (модиф.) + тесты | — | 5 | 2 |
| T56 | generate_platform_env.py port-scanner → port_scanner.py | Coder | port_scanner.py + тесты | — | 2 | 1 |
| T57 | sync_env_defaults.py generate_env_example → секционные функции | Coder | sync_env_defaults.py (модиф.) + тесты | — | 3 | 2 |
| T58.1 | project_scaffolder create_github_repo → github_ops.py | Coder | github_ops.py + тесты | — | 1 | 1 |
| T58.2 | s3_ssl_cache bulk_restore — уже изолирована (без действий) | — | — | — | 0 | 1 |
| T58.3 | secrets_manager htpasswd → htpasswd.py | Coder | htpasswd.py + тесты | — | 1 | 1 |
| T58.4 | cert_orchestrator cron_install → cron_installer.py | Coder | cron_installer.py + тесты | — | 1 | 1 |
| T58.5 | context_deployer LLM → llm_provision.py | Coder | llm_provision.py + тесты | — | 1 | 1 |
| T-GATE | Финальная верификация: gate + тесты + grep-проверки | Coder | Отчёт о прохождении | T51–T58.5 | 2 | 4 |

### $PARALLEL_GROUPS

#### Волна 1 (независимые, нет общих файлов)
- Задачи: T53, T56, T58.1, T58.2, T58.3, T58.4, T58.5
- Команда: `coder Read 08-DevPlan.md, implement Wave 1: T53, T56, T58.1-T58.5`

#### Волна 2 (независимые между собой, нет общих файлов)
- Задачи: T51, T55, T57
- Команда: `coder Read 08-DevPlan.md, implement Wave 2: T51, T55, T57`

#### Волна 3 (независимые между собой, нет общих файлов)
- Задачи: T52, T54
- Команда: `coder Read 08-DevPlan.md, implement Wave 3: T52, T54`

#### Волна 4 (финальная верификация)
- Задачи: T-GATE
- Команда: `coder Read 08-DevPlan.md, implement Wave 4: T-GATE (gate + full test suite)`

---

## 6. Отклонения от исходного брифа

| Задача | Отклонение | Причина |
|--------|-----------|---------|
| 52 | notifier НЕ выносится отдельным файлом — удаляется в пользу shared.telegram_notifier | AuditLogger и TelegramNotifier — дубли shared-модулей (задача 19 брифа C). Вынос дубля в отдельный файл усугубит проблему. Правильное решение: удалить дубли, использовать shared. |
| 52 | AuditLogger (L267) удаляется — shared.audit_logger | Та же причина. |
| 54 | Диапазон extraction L257-810 → L454-847 | L257-370 — config loading (НЕ генераторы), остаётся в monitoring_config_renderer.py. Генераторы начинаются с L454. |
| 56 | Диапазон L188-321 → L187-402 | scan_test_ports (L320-402) — часть порт-сканера. Бриф не включил её в диапазон, но функция использует тот же домен. |
| 58.2 | s3_ssl_cache bulk_restore — задача закрыта без действий | bulk_restore() (L666-732, 67 LOC) — уже отдельная public-функция в PUBLIC API секции. Не требует выноса. |

---

## $TEST_SPEC

| Тест-файл | Тест-функция | Сценарий | Модуль под тестом |
|-----------|-------------|----------|-------------------|
| tests/unit/test_node_yaml_cli.py | test_cli_get_returns_value | --get с существующим ключом → stdout | node_yaml_cli.py |
| tests/unit/test_node_yaml_cli.py | test_cli_get_missing_key_exit1 | --get с отсутствующим ключом → exit 1 | node_yaml_cli.py |
| tests/unit/test_node_yaml_cli.py | test_cli_get_many_batch | --get-many с валидным spec → TAB-вывод | node_yaml_cli.py |
| tests/unit/test_node_yaml_cli.py | test_cli_get_many_empty_spec_exit4 | --get-many с пустым spec → exit 4 | node_yaml_cli.py |
| tests/unit/test_node_yaml_cli.py | test_cli_resolve_prints_path | --resolve → stdout с путём | node_yaml_cli.py |
| tests/unit/test_node_yaml_cli.py | test_cli_typed_json_output | --typed-contexts → JSON на stdout | node_yaml_cli.py |
| tests/unit/test_watchdog_circuit_breaker.py | test_cb_service_from_config_entry | CircuitBreakerService.from_config_entry парсит валидный entry | circuit_breaker.py |
| tests/unit/test_watchdog_circuit_breaker.py | test_cb_check_all_no_failures | CircuitBreaker.check_all с мок-subprocess успешных проверок | circuit_breaker.py |
| tests/unit/test_watchdog_circuit_breaker.py | test_cb_threshold_exceeded | CircuitBreaker: превышение max_failures → True | circuit_breaker.py |
| tests/unit/test_watchdog_circuit_breaker.py | test_cb_window_reset | CircuitBreaker: сброс после window_seconds | circuit_breaker.py |
| tests/unit/test_watchdog_docker_ops.py | test_docker_manager_pull | DockerManager.pull вызывает subprocess docker compose pull | docker_ops.py |
| tests/unit/test_watchdog_docker_ops.py | test_docker_manager_up | DockerManager.up с мок-subprocess | docker_ops.py |
| tests/unit/test_nginx_harness.py | test_harness_creates_structure | nginx_t_harness создаёт harness_dir + vhosts/ | nginx_harness.py |
| tests/unit/test_nginx_harness.py | test_harness_ssl_path_swap | SSL-пути заменены на dev-certs | nginx_harness.py |
| tests/unit/test_nginx_harness.py | test_harness_no_docker_skip | docker отсутствует → True (graceful skip) | nginx_harness.py |
| tests/unit/test_nginx_harness.py | test_harness_no_vhosts_skip | Нет .conf файлов → True | nginx_harness.py |
| tests/unit/test_monitoring_prometheus_targets.py | test_generate_target_json | Выходной JSON соответствует схеме {targets, labels} | prometheus_targets.py |
| tests/unit/test_monitoring_loki_retention.py | test_retention_idempotent_insert | Повторный вызов не дублирует selector | loki_retention.py |
| tests/unit/test_monitoring_langfuse_projects.py | test_create_project_409_skip | HTTP 409 → skip (idempotent) | langfuse_projects.py |
| tests/unit/test_status_collectors.py | test_load_node_yaml_reads_file | load_node_yaml возвращает dict | collectors.py |
| tests/unit/test_status_collectors.py | test_curl_vhost_timeout | _curl_vhost с недоступным доменом → {"reachable": false} | collectors.py |
| tests/unit/test_status_collectors.py | test_get_all_checks_structure | get_all_checks возвращает правильные ключи | collectors.py |
| tests/unit/test_status_renderer.py | test_format_bytes_units | _format_bytes: 1024 → "1.0 KB" | renderer.py |
| tests/unit/test_status_renderer.py | test_enrich_containers_domain_mapping | _enrich_containers добавляет domains из projects | renderer.py |
| tests/unit/test_status_renderer.py | test_render_html_contains_tables | _render_html содержит 3 таблицы | renderer.py |
| tests/unit/test_port_scanner.py | test_extract_host_port_bare | "8080:8080" → 8080 | port_scanner.py |
| tests/unit/test_port_scanner.py | test_extract_host_port_ip_var | "127.0.0.1:${PORT:-5432}:5432" → 5432 | port_scanner.py |
| tests/unit/test_port_scanner.py | test_scan_compose_ports_from_dir | tmp_path с docker-compose.base.yml → корректный port_map | port_scanner.py |
| tests/unit/test_github_ops.py | test_create_repo_dry_run | dry_run=True → True без subprocess | github_ops.py |
| tests/unit/test_github_ops.py | test_create_repo_no_gh | gh отсутствует → True (graceful skip) | github_ops.py |
| tests/unit/test_htpasswd.py | test_write_htpasswd_creates_file | _write_htpasswd_file создаёт файл | htpasswd.py |
| tests/unit/test_htpasswd.py | test_write_htpasswd_idempotent | Повторный вызов не перезаписывает файл с теми же credentials | htpasswd.py |
| tests/unit/test_cron_installer.py | test_install_cron_already_present | crontab уже содержит s3_ssl_cache → True (no-op) | cron_installer.py |
| tests/unit/test_cron_installer.py | test_migrate_cron_no_crontab | Нет crontab → True (nothing to migrate) | cron_installer.py |
| tests/unit/test_llm_provision.py | test_render_and_provision_success | Оба subprocess возвращают 0 → INFO:9 логи | llm_provision.py |
| tests/unit/test_llm_provision.py | test_render_missing_script_non_fatal | config_renderer.py отсутствует → WARN, не исключение | llm_provision.py |

---

## Next Steps

### Волна 1
```
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/117-post-refactor-drift/08-DevPlan.md, implement Wave 1: T53, T56, T58.1, T58.3, T58.4, T58.5 (T58.2 is no-op — skip)
```

### Волна 2
```
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/117-post-refactor-drift/08-DevPlan.md, implement Wave 2: T51, T55, T57
```

### Волна 3
```
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/117-post-refactor-drift/08-DevPlan.md, implement Wave 3: T52, T54
```

### Волна 4 (верификация)
```
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/117-post-refactor-drift/08-DevPlan.md, implement Wave 4: T-GATE. Run `make gate MODE=fast && make check-manifests && make test MARKER=all`, report pass/fail for each monolith's test suite.
```
