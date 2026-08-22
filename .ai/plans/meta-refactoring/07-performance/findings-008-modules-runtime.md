# Findings 008 — Modules runtime (status-page, backup-cron)

Scope: `core/modules/` · Agent wave 2 · 2026-08-22
Контекст: status-page — единственный долгоживущий HTTP-сервис платформы (ThreadingHTTPServer, лимиты контейнера 128M/256 pids).

### PERF-040 | HIGH | conf=High
- Category: per-request file reads + redundant live probes (нет кэша)
- Hot path: yes — каждый GET `/`, `/health`, `/metrics`, `/status.json`
- File/symbol: `core/modules/status-page/collectors/aggregate.py::get_all_checks`
- Trigger: любой запрос перезапускает полный probe suite: yaml+JSON load + N vhost curls + 6 platform curls + DNS probes
- Complexity/cost: O(N+6) subprocess spawn (~11+ curl) + 2× file parse per request; до 5s wall каждый при деградации
- Expected impact: Prometheus скрейпит `/metrics` 1/мин → постоянные ~11 subprocess/мин фоном; page views умножают; нулевое кэширование, хотя metrics JSON экспортируется cron'ом только раз в минуту (данные не могут меняться быстрее)
- Evidence: `app.py:137-149` + `aggregate.py:154-183`
  ```python
  checks += fan_out_checks(vhost_tasks, total_timeout)
  checks += fan_out_checks(svc_tasks, total_timeout)
  ```
- Minimal fix: кэш OverallData 30–60s (mtime-keyed на status-metrics.json); `/metrics` из кэша
- Measurement: status-page CPU + curl proc count/min; `/metrics` scrape duration p95
- Phase: Pre-launch

### PERF-041 | HIGH | conf=High
- Category: blocking I/O без эффективного timeout (dead timeout)
- Hot path: yes — любой запрос с зависшим probe worker'ом
- File/symbol: `core/modules/status-page/collectors/aggregate.py::fan_out_checks`
- Trigger: worker блокируется дольше `total_timeout` (напр. `socket.gethostbyname` виснет на Docker DNS 127.0.0.11 — не прерывается, без таймаута) → `as_completed(futures)` блокируется навсегда
- Complexity/cost: `TOTAL_TIMEOUT=30` — dead code: `future.result(timeout=...)` зовётся только на уже завершённых futures; `ThreadPoolExecutor.__exit__` shutdown(wait=True) тоже блокирует
- Expected impact: один зависший DNS lookup → request thread + ≤10 pool threads утекают перманентно (daemon threads, silent); повторные инциденты → исчерпание pid limit (PERF-042), 500s, healthcheck flaps
- Evidence: `aggregate.py:78-83` + `platform.py:127`
  ```python
  with ThreadPoolExecutor(max_workers=min(len(tasks), 10)) as executor:
      for future in as_completed(futures):
          results.append(future.result(timeout=total_timeout))
  ```
- Minimal fix: `as_completed(futures, timeout=total_timeout)` + try/TimeoutError; gethostbyname → getaddrinfo в executor с таймаутом
- Measurement: thread count status-page over uptime; запросы >30s
- Phase: Pre-launch

### PERF-042 | MED | conf=Med
- Category: unbounded concurrency vs жёсткие контейнерные лимиты
- Hot path: yes — под конкурентной нагрузкой или latency-спайком инцидента (все probes упираются в 5s timeout)
- File/symbol: `core/modules/status-page/app.py::main` (ThreadingHTTPServer)
- Trigger: >~30–50 конкурентных медленных запросов → unbounded per-request threads + curl subprocess vs `pids: 256` / `memory: 128M`; default `request_queue_size=5` дропает бёрсты
- Complexity/cost: каждый in-flight запрос: полный metrics dict + rendered HTML + ≤10 pool threads + N+6 curl procs (~5–15MB RSS каждый)
- Expected impact: fork/thread-creation failures при умеренном бёрсте → 500s; Docker HEALTHCHECK (30s interval, retries=3) flaps unhealthy → рестарты контейнера во время инцидентов
- Evidence: `app.py:303-305` + `docker-compose.base.yml:70-74`
- Minimal fix: глобальный semaphore на конкурентные probe suite (или single-flight refresh + serve из кэша); поднять request_queue_size
- Measurement: pids/memory контейнера vs лимиты во время load test
- Phase: Pre-launch

### PERF-043 | LOW | conf=High
- Category: per-request duplicate file loads (node.yaml парсится 2× за запрос)
- Hot path: yes — `/`, `/metrics`, `/status.json`
- File/symbol: `core/modules/status-page/app.py::_handle_status_json/_handle_metrics/_render_html`
- Trigger: каждый запрос: 2× yaml.safe_load + 2× resolve_node_yaml_path (включая os.listdir glob fallback)
- Complexity/cost: ~2× parse маленького файла per request (~1–5ms)
- Expected impact: пренебрежимо само по себе; компаундит overhead PERF-040 per-scrape
- Evidence: `app.py:254-258`
- Minimal fix: возвращать node_name из get_all_checks (он уже загрузил node.yaml)
- Measurement: request self-time breakdown (py-spy) на /status.json
- Phase: Post-launch

### PERF-044 | MED | conf=Med
- Category: backup job — однопоточная компрессия + перекрывающиеся окна без lock
- Hot path: no — 03:00 UTC daily cron
- File/symbol: `core/modules/backup-cron/scripts/backup_postgres.py::run_backup`
- Trigger: pg_dumpall всего кластера пайпом в одноядерный gzip (~30–50MB/s); retry upload спит 30min×2 в том же job; flock нигде нет — crontab документирует "collision: parallel start allowed"
- Complexity/cost: dump+gzip растёт линейно с размером кластера; job может перейти окна 04:00/05:00
- Expected impact: при ~5–10GB кластере: 3–6 мин CPU-bound gzip конкурируют с prod DB на малой VPS; перекрывающиеся jobs удваивают I/O
- Evidence: `backup_postgres.py:212-217` + `crontab:12-13`
- Minimal fix: pigz/zstd + flock guard в backup-postgres.sh
- Measurement: wall time backup job vs window; CPU 03:00–03:30
- Phase: Post-launch (monitor с запуска)

### PERF-045 | LOW | conf=Med
- Category: backup job — последовательные S3 HEAD per WAL файл, нет run lock
- Hot path: no — hourly cron
- File/symbol: `core/modules/backup-cron/scripts/wal_sync.py::sync` / `apply_local_retention`
- Trigger: последовательный boto3 HEAD per WAL файл (~100–300ms RTT каждый); N ≈ сотни → минуты за ран; зависший ран перекрывается со следующим (нет lockfile)
- Expected impact: в основном benign (safe-delete guard корректен); дублированный HEAD churn и log noise при overlap
- Evidence: `wal_sync.py:305-308`
- Minimal fix: flock в wal_sync cron wrapper; batch HEADs через list_objects_v2 prefix
- Measurement: wal-sync.log run duration trend
- Phase: Post-launch

### PERF-046 | LOW | conf=High
- Category: unbounded response payload / serialization
- Hot path: yes — `/status.json` на каждый verifier sweep + dashboard poll
- File/symbol: `core/modules/status-page/app.py::_handle_status_json` / `_send_json`
- Trigger: полные containers+certs+projects+checks списки сериализуются `json.dumps(indent=2)` per request, без ETag/cache headers
- Complexity/cost: indent=2 раздувает payload ~30–50% + CPU; payload растёт линейно с числом проектов
- Expected impact: при десятках проектов — десятки KB (незначимо); при сотнях — MB-класс ответов per poll
- Evidence: `app.py:126-129`
- Minimal fix: убрать indent=2 на /status.json; добавить ETag/Cache-Control
- Measurement: payload size + serialize time
- Phase: Post-launch

### PERF-047 | LOW | conf=High
- Category: нет pagination в rendered tables
- Hot path: yes — HTML render на каждый page view
- File/symbol: `templates/status.html` + `renderer/enrich.py`
- Trigger: шаблон рендерит ВСЕ проекты и контейнеры полными строками таблицы; enrichment строит новый dict per item per request
- Expected impact: bounded размером платформы (десятки) — норм на launch; деградация линейная при росте до сотен
- Evidence: `status.html:295,326`
- Minimal fix: cap строк "show N more" или sort+limit в enrich слое
- Measurement: HTML byte size vs project count
- Phase: Post-launch

---
### Скоуп bootstrap-root (spot-check, wave 3)
`issue_cert.py`, `cert_orchestrator.py`, `s3_ssl_cache.py`, `python_deps.py`, `docker_registry_auth.py`, `preflight.py`, `remote_executor.py` — точечная проверка sleep/poll/subprocess паттернов: retry-циклы bounded (`ISSUE_MAX_ATTEMPTS=2`, docker poll 6×5s), idempotence по hash, per-op subprocess без N+1 петель. **Подтверждённых findings нет.** Два агентских прогона вернули пусто — согласуется с spot-check.
