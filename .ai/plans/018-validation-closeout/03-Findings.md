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

### F-21a · 2026-08-29 08:55 · W2 · P1 · watchdog-инъекция — GREEN
- Root 1: docker 29 удалил --health-* из `docker update` — сломаны оба канала F6 (inject/restore).
- Root 2 (эксперимент на ноде): requirepass-инъекция непригодна — redis-cli при WRONGPASS/NOAUTH
  отвечает error-reply с exit code 0 → Docker healthcheck (exit-code based) проходит, unhealthy
  не наступает (NOAUTH → RC=0; «AUTH failed» тоже RC=0). Connection refused — канал с rc=1.
- Root 3 (эксперимент): `docker restart` НЕ инкрементирует RestartCount (растёт только от
  restart-policy) — прежнее доказательство F6 валидно никогда не было (тест падал раньше — на
  inject). Новое доказательство: stamp last_restart[redis] в state-file (stamp-after-success
  REF-0014; prep чистит redis-записи) + StartedAt в proof-строке.
- Фикс: инъекция = `CONFIG SET port 0` (runtime, не persisted; Config.Healthcheck не тронут) →
  probe ping получает Connection refused (rc=1) → unhealthy 30s×3; watchdog-restart сбрасывает
  runtime-CONFIG; finally-restore = docker restart + PONG verify.
- Инцидент (self-inflicted, закрыт за ~10s): развед-команда выполнила реальный
  `CONFIG SET requirepass ""` на живой ноде до написания теста — восстановлено немедленно
  (CONFIG SET requirepass $REDIS_PASSWORD), health=healthy failing=0 не успел деградировать.
  Урок: CONFIG SET на живой ноде НЕ read-only — только в составе теста с restore.
- Верификация: `NODE=tronyx-vps pytest -k test_watchdog_heals_unhealthy` → PASS 188.28s
  (logs/w2-f6-watchdog-r3-*.log); пост-стейт: healthy/PONG/канонический Test/rc=0.
- Статус: fixed

### F-21b · 2026-08-29 09:20 · W3 · P1 · oom_clickhouse сайзинг — GREEN
- Root-гипотеза ПОДТВЕРЖДЕНА: bomb 400×8MB≈2.98GiB был захардкожен под старый лимит 1GiB;
  SoT-лимит 3G (docker inspect HostConfig.Memory=3221225472) → 2.98 < 3 → cgroup-OOM не наступал.
- Фикс: (1) лимит из docker inspect на ноде; (2) bomb = 1.3×лимит динамически, 64MiB-чанки
  (63 шт — меньше O(n²)-копирования bash); (3) pre-check assert (R4, не skip):
  MemAvailable > лимит (3.81GiB > 3GiB ✅) И SwapTotal == 0 (MemorySwap=2×6GiB без swap-девайса —
  иначе bomb ушёл бы в swap вместо OOM); (4) TTR пере-семантизирован: от OOM-доказательства,
  не от старта теста (3GiB-инъекция занимает минуты — окно восстановления только про healthy).
- Попутный факт: clickhouse State.OOMKilled=true при health=healthy/started 2026-08-27 — stale
  флаг после старого OOM (docker не сбрасывает OOMKilled через policy-restart) — тест не
  полагается на флаг (жертва — bomb-процесс, не init), не влияет.
- Верификация: pytest -k test_oom_clickhouse_kernel_kill → PASS 76.10s
  (logs/w3-f7-oom-*.log); пост-стейт healthy.
- Статус: fixed
