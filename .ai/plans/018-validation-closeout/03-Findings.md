# 03-Findings · 018-validation-closeout

## Протокол
- Единственная тестовая команда: make check (+MARKER/TEST_FILE/check-diff); requires_node —
  вне make check/gate, raw pytest → logs/ этой папки.
- Нода tronyx-vps мутируется ТОЛЬКО последовательно из главной сессии.
- Findings чанками ≤40 строк. Восстановление: 01-DevPlan.md + 017-артефакты + logs/latest.log.

---

### F-22 · 2026-08-29 07:35 · W1 · P1 · ROOT CAUSE найден и R5-воспроизведён
- Класс: machine-state env-утечка (тот же паттерн-класс, что PATTERN TRAP[TEST] 017 Фазы A).
- Симптом (017): TestStatusPageMetrics tls-тесты FAIL только в составе make check.
- ФАКТИЧЕСКИЙ фейл (не «HELP отсутствует», а label): 017-лог check_final4 L13283 — body содержит
  `platform_tls_days_left{node="production-node",domain="example.test"} 365` — TLS-секция
  рендерилась из ТЕСТОВОГО файла, но node-label = "production-node" вместо "test-node".
- Root: tests/unit/test_ssl_s3_cache.py::test_get_backup_config_still_works (L408) ставит
  `NODE_NAME="production-node"` в os.environ со snapshot через `os.environ.get(k, "")` и
  finally `if v: os.environ[k] = v` — ключи, НЕ установленные до теста, НЕ удаляются →
  NODE_NAME утекает в env xdist-воркера. Следующий `_setup_app_env` → reload app →
  `NODE_NAME = os.environ.get("NODE_NAME", "test-node")` подхватывает утечку →
  `_handle_metrics` fallback node="production-node" → label-mismatch assert.
- 1-vs-2 фейла объясняется: два tls-теста с dist=load попадали на разные воркеры
  (check_final4: gw2 PASS nan / gw11 FAIL gauges — независимая загрязнённость).
- Репродукция (до фикса, R5): (а) `NODE_NAME=production-node pytest -k tls_gauges` → FAIL;
  (б) polluter-цепочка `pytest test_ssl_s3_cache.py::test_get_backup_config_still_works
  test_status_page.py::TestStatusPageMetrics::test_metrics_renders_tls_gauges` → FAIL.
  Детерминированно в одном процессе.
- Почему «сегодня зелёный»: раскладка dist=load зависит от тайминга; polluter должен
  попасть на воркер ДО tls-теста. 3 контрольных прогона 2026-08-29 — 0 fail (тихая машина).
- Статус: fixing (monkeypatch-конвертация утечных блоков + hermetic-фикс tls-тестов).
