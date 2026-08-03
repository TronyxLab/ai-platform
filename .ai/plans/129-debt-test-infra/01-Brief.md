# 129-debt-test-infra — 01-Brief.md

$START_BRIEF

$ARTIFACT_CONTRACT
PURPOSE:               Закрыть тестовый долг реестра .ai/debt/001 (TEST-DEBT T2-T6, P3-5/D18, D13, D14) и отдельные долги тест-инфраструктуры (test-env-leak-and-flakes.md, xdist-race probe-файлы, env-утечки, flaky git-чек, volume prometheus-config-gen): сделать полный прогон make test-summary детерминированным и быстрым (без 1276.8s-зависаний и reload-гонок).
DESCRIPTION:          5 волн: W1 — T2 litellm (закрыть как mitigated), T3 spool_volume, T4 Vacuous Check 3, T5 cleanup fixture, T6 _handle_e2e_error uniform. W2 — xdist-race: живой (check-file-lines.sh:54) + снятие устранённых (marker_location:147, cross_layer:1742/1780, timeout_literals). W3 — env-утечки (test_shared_timeouts D-11), flaky git (test_check_suite:340), volume prometheus-config-gen (smoke_monitoring:67), P3-5/D18 networks teardown. W4 — test-env-leak-and-flakes.md: reload-гонка monkeypatch/sys.modules (Rev 2026-08-09) + pytest-timeout для статического прогона. W5 — D13 (keep, снять TRAP), D14 (superseded после cleanup 131).
RATIONALE:             Полный прогон виснет 1276.8s (реальный HealthcheckPoller вместо мока) и флейкает под xdist — Anti-Loop протокол подрывается (счётчик попыток растёт без причины), make preflight/check static_audit недостоверен (timeout 300s < 1276.8s). Env-утечки дают ложные FAIL в серийном прогоне. Всё это — тестовый долг, а не продуктовые баги: фиксы в тест-инфраструктуре.
ACCEPTANCE_CRITERIA:   (1) Полный static_audit прогон (make test-summary MARKER=static_audit) — детерминирован: 0 зависаний >5 мин на тест, 0 env-утечек (гейт или тест-скан os.environ без monkeypatch). (2) xdist-прогон (make check) — 0 probe-файлов в рабочем дереве (все tmp_path). (3) T2-T6, P3-5/D18 закрыты с тестами/решениями; TRAP[DEBT] сняты (или обновлены). (4) pytest-timeout подключён для статического прогона (висящий тест падает быстро, не 1276.8s). (5) reload-гонка monkeypatch/sys.modules исследована и устранена (или задокументирован канон: НЕ удалять модули из sys.modules в тестах). (6) make check + gate зелёные.
IMPLEMENTS:            Решение пользователя 2026-08-03 (закрыть все известные долги); test-env-leak-and-flakes.md Rev 2026-08-09; TEST-DEBT T2-T6 реестра 001.
IMPACTS:               tests/test_smoke_litellm.py, tests/unit/test_spool_dir.py, tests/test_volume_spool_consistency.py, tests/test_lib_node_resolver.py, tests/_conftest/skip_gate.py, tests/_conftest/networks.py, tests/unit/test_shared_timeouts.py, tests/unit/test_check_suite.py, tests/test_smoke_monitoring.py, tests/gates/test_gate_timeout_literals.py, tests/gates/test_gate_marker_location.py, tests/test_cross_layer_imports.py, core/entrypoints/check-file-lines.sh, tests/gates/test_gate_compose_no_base_image.py, tests/gates/test_gate_dead_code.py, core/check-suite.yaml (pytest-timeout), conftest (env-скан).
REQUIRES:              Бейзлайн make check зелёный. Доступ к Docker для smoke-проверок (T2, volume) — локальный стек.
$END_ARTIFACT_CONTRACT

## Scope (закрываемые долги)

| # | Долг | Файл | Действие |
|---|------|------|----------|
| 1 | T2/D19 | tests/test_smoke_litellm.py:73 | Закрыть как mitigated (retry+backoff уже есть, root-cause Dep 017 вне) — снять TRAP[DEBT], оставить TRAP[BUG] prevention |
| 2 | T3/D15 | tests/unit/test_spool_dir.py:18 | Добавить spool_volume в litellm/langfuse/infra-metrics ИЛИ обновить канон теста |
| 3 | T4/D20 | tests/test_volume_spool_consistency.py:82 | Vacuous Check 3 — реализовать честную проверку ИЛИ удалить |
| 4 | T5/D16 | tests/test_lib_node_resolver.py:258 | Fixture cleanup /opt/node-configs (tmp_path/удаление в teardown) |
| 5 | T6/D17 | tests/_conftest/skip_gate.py:36 | _handle_e2e_error uniform по E2E-тестам |
| 6 | xdist-race | check-file-lines.sh:54 | Живой: `wc -l < "$file"` падает если файл исчез — переписать на Python/защиту |
| 7 | xdist-race (снять) | marker_location:147, cross_layer:1742/1780, timeout_literals:67 | Уже устранены (probe в tmp_path) — снять TRAP[DEBT] |
| 8 | env-leak D-11 | tests/unit/test_shared_timeouts.py:145 | monkeypatch.setenv вместо чтения dev-env |
| 9 | flaky git | tests/unit/test_check_suite.py:340 | Git под xdist-нагрузкой — мок/защита |
| 10 | volume | tests/test_smoke_monitoring.py:67 | prometheus-config-gen volume объявить |
| 11 | P3-5/D18 | tests/_conftest/networks.py:90 | Защита shared external networks от teardown |
| 12 | reload-гонка | test_deploy_mk_chain.py, test_orchestrator_receive_version.py | Исследовать sys.modules del vs monkeypatch; канон |
| 13 | pytest-timeout | core/check-suite.yaml / pyproject | Таймаут для статического прогона |
| 14 | D13 | test_gate_compose_no_base_image.py:235 | Keep (include-архитектура канон) — снять TRAP |
| 15 | D14 | test_gate_dead_code.py:649 | Superseded (после 131 stale-comments не нужен) — снять |

## Non-Goals

- Не трогаем продуктовую логику litellm/postgres (это домены других планов).
- Не переписываем весь conftest — только затронутые хелперы.
- Не отключаем xdist — чиним тесты под xdist (DevPlan 124 правило: флак параллельного прогона = баг теста).

$END_BRIEF
