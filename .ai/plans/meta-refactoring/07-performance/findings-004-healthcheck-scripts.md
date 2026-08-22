# Findings 004 — Scripts + Healthcheck (перманентный polling hot path)

Scope: `core/internal/scripts/`, `core/internal/healthcheck/` · Agent wave 1 · 2026-08-22
Контекст: metrics export — cron каждую минуту (1440×/день), healthcheck — операторский/CI гейт.

### PERF-050 | HIGH | conf=High
- Category: blocking I/O / отсутствие timeout на вызове
- Hot path: yes — `make healthcheck` (оператор, CI-гейты, release checklist)
- File/symbol: `core/internal/healthcheck/modules_healthcheck.py::check_module` / `run_healthchecks`
- Trigger: один зависший module `healthcheck.sh` (wedged container/docker daemon) в сериальном sweep
- Complexity/cost: serial O(M); `invoke_module_interface(module, "healthcheck", ...)` вызывается БЕЗ timeout kwarg → наследует `timeout=COMPOSE_UP_TIMEOUT` = **180s на модуль** (`shared/timeouts.py:49`); у entrypoint нет глобального таймаута (`core/entrypoints/healthcheck.sh:23`)
- Expected impact: один stuck модуль блокирует sweep на 180s; K stuck модулей → K×180s (13 модулей → до ~39 мин worst case); CI healthcheck-гейты висят
- Evidence: `modules_healthcheck.py:243-249`; `module_interface.py:63-67` (`def invoke(..., timeout=COMPOSE_UP_TIMEOUT)`); serial loop `:323-328`
- Minimal fix: передать `timeout=HEALTHCHECK_POLL_TIMEOUT` (60s, существующая SoT-константа) для liveness/deep invokes
- Measurement: p100 `time make healthcheck` → ≤60s/модуль; дисперсия длительности гейта
- Phase: Pre-launch

### PERF-051 | MED | conf=High
- Category: N+1 subprocess + repeated computation
- Hot path: yes — каждый запуск `make healthcheck`
- File/symbol: `modules_healthcheck.py::check_restart_loop` / `check_module`
- Trigger: docker-модули итерируют контейнеры, по одному `docker inspect` на контейнер; дуплицирует inspect, который module `healthcheck.sh` только что сделал сам
- Complexity/cost: O(C) subprocesses, C ≈ 15–25 контейнеров → ~1–4s/ран
- Expected impact: 1–4s впустую за прогон; прецедент батч-фикса уже есть (`watchdog.py` T2.7)
- Evidence: `modules_healthcheck.py:252-254`; batched fix precedent `watchdog.py:291-293` (`docker inspect *container_ids`)
- Minimal fix: собрать все container names, один `docker_inspect_many()`, переиспользовать результат
- Measurement: subprocess count per `make healthcheck`: ~M+C → ~M+1
- Phase: Pre-launch

### PERF-052 | MED | conf=High
- Category: дорогостоящий recurring external call / silent degradation
- Hot path: yes — metrics export cron ежеминутно
- File/symbol: `core/internal/healthcheck/metrics/docker_collector.py::get_containers`
- Trigger: `docker stats --no-stream` форсирует полный CPU-sampling window всех контейнеров каждый экспорт
- Complexity/cost: ~2s минимум wall per invocation by docker design; под нагрузкой daemon'а приближается к cap 15s — на таймауте батч-вызов вернёт пустоту → **CPU%/mem ВСЕХ контейнеров молча обнуляются** в status-metrics.json на эту минуту
- Expected impact: постоянные ~2–15s/мин нагрузки docker-daemon; деградация status-page ровно когда нода занята больше всего (момент, когда метрики важнее всего)
- Evidence: `docker_collector.py:120-121`; cadence `platform-export-metrics.sh:6` ("cron every minute via flock -n ... timeout 50s")
- Minimal fix: сэмплировать stats раз в N прогонов (TTL 60–300s через CacheManager) или снизить cadence; отдельная ошибка в логе при stats timeout вместо нулей
- Measurement: число zero-CPU% минут/день в status-metrics.json; export wall time
- Phase: Pre-launch

### PERF-053 | MED | conf=High
- Category: cache contract violation — «TTL cache», реально uncached + wildcard search O(D×L)
- Hot path: yes — ежеминутный экспорт
- File/symbol: `platform_export_metrics.py::main` (step 4) + `metrics/cert_collector.py::get_certs`
- Trigger: комментарий координатора "Certificates (TTL cache)", но `get_certs()` зовётся без cache_mgr каждый ран; wildcard-default домены всегда промахиваются мимо direct-пути → `_search_wildcard_cert`
- Complexity/cost: O(D×L) x509 PEM parse per run (D домены × L live certs): каждый домен без direct-cert перечитывает и перепарсивает КАЖДЫЙ cert-файл; 20×5 = 100 parses/min = 144k/day идентичных входов
- Expected impact: постоянный wasted CPU/file-I/O растущий линейно с числом проектов; противоречит контракту модуля ("TTL cache reduces x509 parsing to once per hour", `metrics/cache.py:15`)
- Evidence: `platform_export_metrics.py:194-198` (нет аргумента cache_mgr); `cert_collector.py:240-242`, `:302-311` (loop `os.listdir(_LETSENCRYPT_LIVE)` + `_load_cert` per entry)
- Minimal fix: обернуть cert collection в CacheManager (`cache_mgr.get("certs", ttl_seconds=3600)`) как image_sizes/project_size уже делают
- Measurement: x509 parses/min: D×L → ~0 на cache hit; export duration delta
- Phase: Pre-launch

### PERF-054 | MED | conf=Med [HYPOTHESIS]
- Category: blocking I/O burst vs cron budget (синхронное TTL-expiry wave)
- Hot path: yes — часовая волна истечения TTL в ежеминутном экспорте
- File/symbol: `metrics/project_collector.py::_get_code_size_cached`
- Trigger: одновременное истечение `project_size_*` ключей (записаны одним раном с одним timestamp → истекают вместе) → сериальные `du -sb` per project
- Complexity/cost: P последовательных `du -sb`, каждый с timeout 30s; весь экспорт имеет бюджет 50s (`flock -n ... timeout 50s`), AC "<15s"
- Expected impact: при мног сотнях MB project dirs P×du может превысить 50s → SIGTERM убивает экспорт mid-run → status-metrics.json stale до следующего успешного раза [HYPOTHESIS — зависит от размера payload]
- Evidence: `project_collector.py:172-178`
- Minimal fix: параллелить du (ThreadPool) или jitter/stagger TTL; пропускать du при превышении бюджета прошлого рана
- Measurement: max export wall time вокруг TTL-границы (>50s/killed → <15s)
- Phase: Pre-launch

### PERF-055 | MED | conf=High
- Category: expensive recurring startup в CI loop (7 интерпретаторов на проверку)
- Hot path: yes — каждый `make check` / `check-manifests` (многократно за fix-cycle агента и CI job)
- File/symbol: `core/internal/scripts/manifest_driver.py::main` / `_run_check`
- Trigger: freshness check spawn'ит 7 свежих Python-интерпретаторов последовательно, каждый реимпортирует и перепарсивает всё SoT-дерево
- Complexity/cost: 7 × (interpreter startup ~0.3–0.5s + tree parse 1–5s) ≈ 10–35s добавки к каждому диагностическому циклу
- Expected impact: десятки секунд × тысячи прогонов `make check` — измеримый агентский/CI wall-time налог; чистый overhead (генераторы детерминированы)
- Evidence: `manifest_driver.py:187-193`; `_run_check` = `subprocess.run([_PY, script, *args, "--check"], ...)` (`:140-141`)
- Minimal fix: single-process driver, импортирующий generator main(argv) in-process (subprocess оставить только для failure isolation)
- Measurement: `time make check MARKER=check-manifests` before/after (expect −70–90% сегмента)
- Phase: Pre-launch

### PERF-056 | LOW | conf=High
- Category: repeated computation — node.yaml парсится 3× за ран
- Hot path: yes — ежеминутный экспорт
- File/symbol: `platform_export_metrics.py::main` + collectors
- Trigger: `_load_node_yaml` (`:165`, результат отброшен), затем снова внутри `get_certs` (`cert_collector.py:209`) и `get_projects` (`project_collector.py:81`)
- Complexity/cost: 3× YAML parse одного файла за ран × 1440/день; ms-scale каждый
- Expected impact: <0.5s/min абсолютно — пренебрежимо, но бесплатно убрать; плюс drift-risk — коллекторы могут видеть разное состояние файла mid-run
- Evidence: см. выше
- Minimal fix: пробросить загруженный NodeYaml объект в get_certs/get_projects (backward-compatible optional param)
- Measurement: node.yaml parse count per export 3 → 1
- Phase: Post-launch

### PERF-057 | LOW | conf=High
- Category: excessive logging на hot path (IMP:9 за routine телеметрию)
- Hot path: yes — ежеминутный экспорт + watchdog/tor cron каждые 5 мин
- File/symbol: `metrics/*` collectors + `platform_export_metrics.py`
- Trigger: IMP:9 (business-critical уровень) используется для routine per-item строк ("Cache HIT", "Collected N projects") при stderr basicConfig INFO
- Complexity/cost: ~40–80 stderr строк на минутный ран → journald churn (десятки MB/день), конкуренция с настоящими IMP:9 сигналами
- Expected impact: dilution операционного signal-to-noise; disk/journald I/O overhead на VPS
- Evidence: `cache.py:126` (`[IMP:9][cache][get] Cache HIT`), `platform_export_metrics.py:172,189,199,209`
- Minimal fix: понизить routine строки до IMP:7-8; IMP:9 только run summary + failures (по LDD-канону)
- Measurement: journald bytes/day юнита; IMP:9 строк на ран (~25 → ~2)
- Phase: Post-launch
