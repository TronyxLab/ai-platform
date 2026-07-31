# 11-Brief — B10: Тестовый хардненинг

<!-- GREP_SUMMARY: test-hardening pass-tests assert-True grep-asserts mock e2e requires_node conftest ldd inventory R1 -->
<!-- STRUCTURE: ┌scope┐ → ◇ R1-чистка → ◇ контрактные тесты → ◇ инфраструктура → ⊕ критерии → ⎋ зависимости -->
# region MODULE_CONTRACT
## @purpose  Волна B10: привести тесты к Test Honesty (R1-R5) — поведение вместо реализации, контракты вместо grep.
## @scope    U-69..U-78, U-81
## @invariants
##   - R1: 0 pass-тестов; R2: 0 unfalsifiable asserts; тест, который не может упасть, — не тест.
##   - Grep-ассерты на исходники заменяются контрактными тестами поведения.
##   - Канонические хелперы (ldd) — один импорт, 0 копий.
# endregion MODULE_CONTRACT

$ARTIFACT_CONTRACT:
  PURPOSE: Очистить тестовую базу от иллюзорного покрытия и стабилизировать тестовую инфраструктуру.
  DESCRIPTION: Удаление 30 assert True, замена 3 grep-ассертов контрактными, сокращение mock-цепочек, слияние 2 пар дублей, стабилизация conftest, перевод фаз на enum, снятие PyYAML-skipif, консолидация _print_ldd_trajectory, чистка stale TRAP, регресс-защита e2e (локальный прогон).
  RATIONALE: RC6: тесты фиксируют реализацию (grep на исходники, mock call_args_list), блокируют легитимный рефакторинг и консервируют мёртвый код (CERT_SCRIPTS, resume_phase). 19/28 assert True в одном файле — R1-RED по собственным правилам проекта.
  ACCEPTANCE_CRITERIA: (1) 0 assert True в tests/ (гейт); (2) test_monitoring_config_renderer: реальные ассерты deep_merge/build_merged_config; (3) grep-ассерты (cert_backup_gap, node_lifecycle_static, ssl_s3_cache) заменены поведенческими (вызов функции + проверка результата); (4) mock-сокращение: тесты проверяют observable-результат, а не call_args_list (deploy_engine 37 @patch → ≤ 5); (5) дубли Dora/VPS слиты в один файл на модуль; (6) conftest: subprocess при импорте убран (infra.py:271 — ленивая инициализация), teardown-гонка (networks.py:90) задокументирована/исправлена; (7) node.py импортирует BootstrapPhase; (8) PyYAML-skipif снят (PyYAML — hard dep); (9) 14 копий _print_ldd_trajectory → 1 импорт; (10) stale TRAP test_add_vhost:29 удалён; (11) e2e-набор (11 requires_node) зелёный локально через make test-node (без CI-джобы — решение пользователя); (12) LDD-покрытие поднято до ≥ 80% файлов тестов.
  IMPLEMENTS: U-69 (pass-тесты), U-70 (grep-ассерты), U-71 (mock-heavy), U-72 (e2e вне CI — локальный прогон), U-73 (дубли), U-74 (conftest), U-75 (фазы строками), U-76 (skipif), U-77 (ldd ×14), U-78 (LDD 63%), U-81 (stale TRAP)
  IMPACTS: tests/ (unit, gates, integration, e2e), tests/_conftest/, tests/AGENTS.md
  REQUIRES: B8 (удаление мёртвого кода вместе с консервирующими тестами — согласовано), B6/B9 (новые границы фиксируются контрактными тестами)

---

## Scope

| U | Проблема | Ключевые файлы |
|---|----------|----------------|
| U-69 | 19/28 assert True (renderer) + fixture_schema:48; всего 30 в tests/ | tests/unit/test_monitoring_config_renderer.py, tests/gates/test_gate_fixture_schema.py |
| U-70 | Grep-ассерты на подстроки исходников | tests/test_cert_backup_gap.py:386, tests/test_node_lifecycle_static.py:169,173,420, tests/test_ssl_s3_cache.py:387-397 |
| U-71 | Mock-heavy: deploy_engine 37 @patch, status_page 16, export_metrics 11, python_deps | tests/unit/test_deploy_engine.py, tests/test_status_page.py, tests/test_platform_export_metrics.py |
| U-72 | 11 requires_node вне CI (test-node ci.mk:107) | tests/e2e/test_bootstrap_pipeline.py (8), test_failure_scenarios.py (3) |
| U-73 | Дубли: Dora 10/7, VPS 15/10 | tests/test_unit_validate_dora_dashboard.py vs tests/unit/test_validate_dora_dashboard.py; vps пара |
| U-74 | conftest 21 файл: subprocess на импорте, teardown-гонка | tests/_conftest/infra.py:271, networks.py:90 |
| U-75 | Фазы строками вместо enum | tests/_conftest/node.py:46-63 |
| U-76 | PyYAML skipif ×5 — unfalsifiable | tests/unit/test_project_adopter.py:107,148,183,424,451 |
| U-77 | 14 копий _print_ldd_trajectory | 14 файлов, канон tests/_conftest/ldd.py:34 |
| U-78 | LDD-покрытие 63% | 113/306 файлов без LDD |
| U-81 | Stale TRAP «все 7 падают» при 7 passed | tests/test_add_vhost.py:29 |

## Ключевые артефакты

1. Гейт R1: ast-скан tests/ на assert True / assert-константы (расширение test_gate_* или новый gate) — RED при появлении.
2. test_monitoring_config_renderer: переписывание на реальные ассерты (deep_merge, build_merged_config, render).
3. Контрактные замены grep-ассертов: cert_orchestrator (вызов orchestrate_certs + проверка результата), node_lifecycle (запуск --parse в dry-run), s3_ssl_cache (вызов upload/download с моком S3).
4. Слияние дублей: один файл на модуль (native-подход unit/test_*), subprocess-CLI-версии удаляются.
5. conftest: ленивая инициализация _TestInfra (без subprocess при импорте), teardown-сериализация сетей.
6. LDD: единый импорт из _conftest/ldd.py; локальные определения удаляются.
7. e2e: локальный прогон make test-node в CI-preflight чек-листе (documented), 11 тестов зелёные.
8. Stale TRAP'ы: test_add_vhost:29 удалён; обновление tests/AGENTS.md при необходимости.

## Гейт самоверификации волны

- Гейт R1: 0 assert True, 0 grep-ассертов (паттерн `in content` на исходники).
- Полный `make gate MODE=full` + `make test-node` зелёные.

## Зависимости

- От: B8 (удаление консерваторов), B6/B9 (новые контракты).
- К: B11 (процесс — гейты включаются в CI Pre-flight).
