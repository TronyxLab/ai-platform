# 129-debt-test-infra — 02-DevPlan.md

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Исполнить 15 задач 01-Brief (129): детерминировать полный прогон тестов (устранить 1276.8s-зависания, reload-гонки, env-утечки, xdist-race), закрыть TEST-DEBT T2-T6 и P3-5/D18, снять устранённые TRAP[DEBT] тест-инфраструктуры.
DESCRIPTION:           5 волн (см. Brief): W1 T2-T6; W2 xdist-race (живой + снятие устранённых); W3 env/flaky/volume/networks; W4 reload-гонка + pytest-timeout; W5 D13/D14 закрытие.
RATIONALE:             См. Brief 129. Ключевые числа: 1276.8s зависание, 300s timeout preflight, 0.1-0.6s в изоляции — дефект проявляется только в полном прогоне (порядок/состояние).
ACCEPTANCE_CRITERIA:   См. Brief 129 (6 пунктов).
IMPLEMENTS:            01-Brief.md (129); test-env-leak-and-flakes.md (Rev 2026-08-09).
IMPACTS:               См. Brief IMPACTS.
REQUIRES:              Локальный Docker-стек для smoke-проверок; make check зелёный до старта.
$END_ARTIFACT_CONTRACT

## 0. Draft Code Graph (XML)

```xml
<graph>
  <entity name="tests_conftest" TYPE="PACKAGE"
    keywords="env-guard,os-environ-scan,monkeypatch,cleanup"
    annotation="W3/W4: env-скан-хелпер (детект os.environ мутаций без monkeypatch в тестах); канон reload-безопасности (не удалять модули из sys.modules)."
    CrossLinks="tests/_conftest/skip_gate.py; tests/_conftest/networks.py"/>
  <entity name="core_check_suite_yaml" TYPE="CONFIG"
    keywords="pytest-timeout,static-audit"
    annotation="W4: pytest-timeout подключён к статическому прогону — висящий тест падает быстро."
    CrossLinks="core/check-suite.yaml"/>
  <entity name="core_entrypoints_check_file_lines_sh" TYPE="SHELL"
    keywords="xdist-race,wc,find"
    annotation="W2: защита от исчезновения файла между find и wc (переписать на Python или устойчивый цикл)."
    CrossLinks="core/internal/check_suite.py"/>
</graph>
```

## 1. Data Flow (шаг за шагом)

```
W1 ── T2: снять TRAP[DEBT] (mitigated retry+backoff) ─► T3: spool_volume для
     litellm/langfuse/infra-metrics (compose) ИЛИ канон теста ─► T4: Vacuous Check 3
     (честный assert или удаление) ─► T5: cleanup /opt/node-configs (tmp_path) ─►
     T6: _handle_e2e_error uniform
W2 ── check-file-lines.sh:54 (живой xdist-race) ─► устойчивый подсчёт строк ─►
     снять TRAP[DEBT] в marker_location/cross_layer/timeout_literals (уже tmp_path)
W3 ── test_shared_timeouts: monkeypatch.setenv ─► test_check_suite: git-мок ─►
     smoke_monitoring: volume ─► networks.py: защита shared networks
W4 ── reload-гонка: воспроизвести (sys.modules del в test_status_page/
     test_platform_export_metrics) ─► канон: test-хелперы НЕ удаляют модули из
     sys.modules; патчи через monkeypatch на фабриках/импорт-точках ─►
     pytest-timeout в check-suite static_audit
W5 ── D13: снять TRAP (include-архитектура канон, тест адаптирован) ─►
     D14: снять (superseded — после cleanup 131 stale-comments не нужен)
```

## 2. File Manifest

| Файл | Действие | Волна |
|------|----------|-------|
| `tests/test_smoke_litellm.py` | T2: снять TRAP[DEBT], обновить prevention | W1 |
| `tests/unit/test_spool_dir.py` + модульные compose (litellm/langfuse/infra-metrics) | T3 | W1 |
| `tests/test_volume_spool_consistency.py` | T4: честная проверка | W1 |
| `tests/test_lib_node_resolver.py` | T5: cleanup fixture | W1 |
| `tests/_conftest/skip_gate.py` + e2e-тесты | T6: uniform _handle_e2e_error | W1 |
| `core/entrypoints/check-file-lines.sh` | W2: устойчивый wc | W2 |
| `tests/gates/test_gate_marker_location.py`, `tests/test_cross_layer_imports.py`, `tests/gates/test_gate_timeout_literals.py` | W2: снять TRAP[DEBT] | W2 |
| `tests/unit/test_shared_timeouts.py` | W3: monkeypatch.setenv | W3 |
| `tests/unit/test_check_suite.py` | W3: git-мок под xdist | W3 |
| `tests/test_smoke_monitoring.py` + compose мониторинга | W3: volume prometheus-config-gen | W3 |
| `tests/_conftest/networks.py` | W3: защита shared networks | W3 |
| `tests/test_deploy_mk_chain.py`, `tests/test_orchestrator_receive_version.py`, `tests/test_status_page.py`, `tests/test_platform_export_metrics.py` | W4: reload-гонка | W4 |
| `core/check-suite.yaml` (или pyproject/конфиг pytest) | W4: pytest-timeout | W4 |
| `tests/gates/test_gate_compose_no_base_image.py`, `tests/gates/test_gate_dead_code.py` | W5: D13/D14 | W5 |

## 3. Волны

### W1 — TEST-DEBT T2-T6
1. **T2** (litellm first-start crash): retry+backoff уже реализован (2026-07-23 P0);
   root-cause (Dep 017 DNS-alias pgbouncer) — вне скоупа (домен 126/прод). Снять
   TRAP[DEBT] :73, оставить TRAP[BUG] с prevention-условием (retry>1 в >50% CI →
   расследование).
2. **T3** (3 модуля без spool_volume): проверить актуальность (модули могли получить
   spool в волнах 116+). Оставшиеся без spool — добавить volume в compose ИЛИ
   обновить канон теста (список SoT — тест-инвариант: где определён перечень).
3. **T4** (Vacuous Check 3): проверить, что проверяет check 3; реализовать честный
   assert (grep по фазам с fixture) ИЛИ удалить vacuous-проверку с обоснованием.
4. **T5** (cleanup /opt/node-configs): тест-фикстура пишет в реальный каталог —
   перевести на tmp_path (Zero Hardcode Rule) + teardown-удаление.
5. **T6** (_handle_e2e_error): унифицировать обработку ошибок e2e-тестов через
   хелпер (все тесты, где pattern различается).

**Acceptance W1:** T2-T6 закрыты; TRAP[DEBT] сняты/обновлены; тесты зелёные.

### W2 — xdist-race
1. `check-file-lines.sh:54`: файл исчезает между find и wc (воркеры xdist) —
   переписать цикл: читать список один раз, при отсутствии файла — пропуск с
   IMP:7-логом (уже есть fallback, строка 66) — сделать надёжным: подсчёт строк
   через Python-хелпер (check_suite.py) ИЛИ `[ -f "$file" ]` перед wc в одном
   subprocess.
2. Снять TRAP[DEBT] в test_gate_marker_location.py:147, test_cross_layer_imports.py
   :1742/1780, test_gate_timeout_literals.py:67 — probe-файлы уже в tmp_path
   (устранено DevPlan 119 H); комментарии-ссылки на TRAP[DEBT] обновить.

**Acceptance W2:** check-file-lines стабилен под xdist; 4 TRAP[DEBT] сняты.

### W3 — env-утечки, flaky git, volume, networks
1. **test_shared_timeouts.py:145** (D-11): тест читал PLATFORM_DEPLOY_TIMEOUT
   dev-машины — заменить на monkeypatch.setenv (автооткат).
2. **test_check_suite.py:340**: git транзиентно недоступен под xdist-нагрузкой —
   мок git-вызовов (fixture) или tolerant-проверка (skip при git недоступен,
   только если это не логика теста).
3. **test_smoke_monitoring.py:67**: volume prometheus-config-gen не объявлен в
   test-стеке — добавить в monitoring compose (test overlay).
4. **networks.py:90** (P3-5/D18): teardown уничтожает shared external networks —
   защита: НЕ удалять сети, созданные вне теста (label-фильтр, master-семантика
   под xdist); частично решено B10 T5 — довести до конца.

**Acceptance W3:** 4 фикса; повторный полный static_audit без env-артефактов.

### W4 — reload-гонка + pytest-timeout
1. Воспроизвести: test_status_page.py/test_platform_export_metrics.py удаляют
   модули из sys.modules → monkeypatch патчит новый объект, _deliver держит
   старый globals → реальный HealthcheckPoller/ForcedCommandChannel.
2. Канон: тест-хелперы НЕ делают `del sys.modules[...]`; при необходимости
   reload — через importlib.reload + патч ПОСЛЕ reload; единый хелпер
   `tests/_conftest/reload_safe.py` (документированный паттерн).
3. pytest-timeout: подключить к статическому прогону (check-suite static_audit),
   таймаут ~300s/тест — висящий тест падает быстро вместо 1276.8s.
4. Regression: полный static_audit серийно + xdist — 0 зависаний, 0 флейков.

**Acceptance W4:** полный прогон детерминирован; reload-канон задокументирован;
pytest-timeout активен.

### W5 — D13/D14 закрытие
1. **D13** (test_gate_compose_no_base_image.py:235): include-архитектура —
   канон (root compose include-based); тест адаптирован
   (test_root_compose_uses_context_image_var). Снять TRAP[DEBT] (keep by design).
2. **D14** (test_gate_dead_code.py:649 «Future: implement test_gate_stale_comments»):
   после cleanup 131 (удаление TRAP[DEBT]/реестров) механизм stale-комментариев
   не нужен — снять TODO как SUPERSEDED с пометкой причины.

**Acceptance W5:** D13/D14 закрыты; make check + gate зелёные.

## 4. Критерии приёмки волн — сводка

| Волна | Критерий |
|-------|----------|
| W1 | T2-T6 закрыты (mitigated/FIXED), TRAP обновлены |
| W2 | check-file-lines стабилен; 4 TRAP сняты |
| W3 | env/flaky/volume/networks фиксы + регресс-прогон |
| W4 | 0 зависаний; reload-канон; pytest-timeout активен |
| W5 | D13/D14 закрыты; check+gate зелёные |

## 5. Риски и митигации

| Риск | Митигация |
|------|-----------|
| T3: добавление spool_volume меняет compose-стек (volume SoT гейт) | Согласовать с test_gate_volumes_sot.py; изменения только в модульных compose |
| W4: reload-канон не устранит гонку полностью | Сначала воспроизвести на CI-прогоне; при неудаче — зафиксировать needs-investigation и оставить TRAP[DEBT] с новой датой (не молча) |
| pytest-timeout ложно режет медленные (легитимные) тесты | Таймаут 300s — выше max легитимного (1276.8s-зависание в 4× выше); allowlist при необходимости |

$END_DEVPLAN
