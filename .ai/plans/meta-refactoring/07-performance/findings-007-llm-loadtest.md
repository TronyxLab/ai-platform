# Findings 007 — LLM provisioning + Monitoring + Loadtest

Scope: `core/internal/llm/`, `core/internal/monitoring/`, `core/loadtest/`, `core/internal/loadtest/` · Agent wave 1 · 2026-08-22

### PERF-080 | HIGH | conf=High
- Category: duplicated work — двойная генерация нагрузки в remote mode
- Hot path: yes — каждый LOAD_RUNNER=node load-test step (smoke/regression/capacity)
- File/symbol: `core/internal/loadtest/runner_cli.py::_run_one_step`
- Trigger: remote-mode ран исполняет remote locust И затем полный local locust run
- Complexity/cost: 2× full-duration runs на шаг (remote + local); local run пишет CSV в несуществующий локальный `/lt/results/...` → rc≠0 → шаг репортится как error
- Expected impact: двойной wall-time и двойной трафик на ноду; remote mode заканчивается false FAIL после полной второй нагрузки (capacity verdicts непригодны)
- Evidence: `runner_cli.py:584-618` — `:606` внутри `if remote:` (12-space indent, verified) дублирует вызов из `else:` ветки `:618`; TRAP-комментарий `:611-616` говорит "восстановлена else-ветка", но вызов был продублирован, а не перемещён
- Minimal fix: удалить дублирующий `_run_locust_process` на `:606-609` внутри `if remote:` ветки
- Measurement: remote step wall-time (~duration, не ~2×duration); rc==0 для LOAD_RUNNER=node
- Phase: Pre-launch

### PERF-081 | HIGH | conf=High
- Category: N+1 network round-trips (no batch API use)
- Hot path: no — `make provision-llm`, node-update фаза «llm-keys», converge
- File/symbol: `core/internal/llm/key_provisioner.py::provision_all` → `admin_client.py::get_key_by_metadata`
- Trigger: полное скачивание key-list на каждого consumer, последовательно
- Complexity/cost: O(C×N) bytes + C последовательных HTTP calls; C≈N с ростом проектов → O(N²)
- Expected impact: при ~100 LLM-enabled проектах ≈100 вызовов, перекачивающих ~100 key objects каждый (~MBs, десятки секунд к каждому node-update); растёт квадратично
- Evidence: `key_provisioner.py:549,589`; `admin_client.py:372-373` (list-all + client-side filter)
- Minimal fix: fetch `/key/info` один раз перед циклом, фильтровать кэшированный список per consumer
- Measurement: provision-llm wall time и HTTP call count vs project count (C calls → 1)
- Phase: Pre-launch

### PERF-082 | HIGH | conf=Med [HYPOTHESIS]
- Category: missing pagination handling → duplicate resource creation
- Hot path: no — provision-llm/node-update, но production-cost bearing
- File/symbol: `core/internal/llm/admin_client.py::get_key_by_metadata`
- Trigger: LiteLLM пагинирует /key/info listings; код читает только page 1
- Complexity/cost: ключи за первой страницей невидимы → get_key_by_metadata вернёт None → generate_key создаёт НОВЫЙ ключ на каждом прогоне для этих проектов
- Expected impact: unbounded дубликаты virtual keys (budget-bearing credentials) в LiteLLM DB; также раздувает N×payload цену PERF-081
- Evidence: docstring `admin_client.py:353-355` заявляет "pagination awareness", но `:373` — одиночный GET без page/page_size и без loop
- Minimal fix: итерировать страницы до исчерпания
- Measurement: key count в LiteLLM до/после двух подряд `make provision-llm` на ноде с >1 страницей ключей
- Phase: Pre-launch

### PERF-083 | MED | conf=High
- Category: connection churn — новый httpx.Client на каждый запрос
- Hot path: no — тот же триггер что PERF-081
- File/symbol: `core/internal/llm/admin_client.py::_sync_client/_async_client`
- Trigger: каждый method call строит свежий httpx.Client и teardown'ит его
- Complexity/cost: 1 TCP connect+teardown на API call × C calls × retries
- Expected impact: RTT×C connection-setup overhead (~секунды при C≈100); keep-alive reuse невозможен полностью
- Evidence: `admin_client.py:118-122`; per-call usage `:160,:237,:291,:325,:372`
- Minimal fix: один long-lived Client per instance (close в context manager)
- Measurement: HTTP calls vs TCP connections (ss/netstat) во время provision-llm
- Phase: Post-launch

### PERF-084 | MED | conf=Med
- Category: unrealistic spawn profile искажает percentile verdicts
- Hot path: yes — capacity mode (все steps) и старты smoke/regression
- File/symbol: `core/internal/loadtest/runner_cli.py::_build_locust_args` (+ `_run_capacity_mode._step`)
- Trigger: spawn rate = total user count → весь пул подключается за ~1s на каждом шаге
- Complexity/cost: users = rps×2 → top steps открывают сотни соединений одновременно; warmup spike попадает в то же 60s окно, чей p95/p99 решает safety-stop
- Expected impact: завышенные p95/p99 на старте шага → преждевременный safety-stop → заниженный max_rps; плюс idle-pool memory/CPU на --cpus 2 генераторе
- Evidence: `runner_cli.py:495-508` (`-u users -r users`), `:851`
- Minimal fix: `-r` = доли от users (users/duration или фикс 5–10/s)
- Measurement: per-step p95 first-30s vs last-30s split (stats_history.csv)
- Phase: Pre-launch

### PERF-085 | MED | conf=Med
- Category: excessive logging в самом измерительном пути
- Hot path: yes — db-scenario load generation (per row, per query, per connect)
- File/symbol: `core/loadtest/scenarios/pgwire.py::parse_data_row` / `PGSocket.query` / `.authenticate`
- Trigger: INFO-log на каждый parsed DataRow и каждый завершённый query во время генерации нагрузки
- Complexity/cost: ~2R log lines/s на step rate R — format + stderr write на gevent event loop, который гоняет все greenlets
- Expected impact: self-inflicted CPU/IO noise раздувает измеряемый db p95/p99 (искажает capacity/regression baselines); линейно с target RPS
- Evidence: `pgwire.py:378`, `:685`, plus 17 logger calls module-wide (`:589,:602,:653,:766`)
- Minimal fix: пер-row/per-query логи → DEBUG (IMP:9 только на ошибки)
- Measurement: db-scenario p99 at fixed RPS: INFO vs WARNING logging
- Phase: Post-launch
