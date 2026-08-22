# Findings 003 — Shared libraries

Scope: `core/internal/shared/`, `core/internal/template_engine.py` · Agent wave 1 · 2026-08-22

### PERF-030 | HIGH | conf=High
- Category: whole-file load (unbounded append-only log)
- Hot path: yes — каждую минуту, бесконечно
- File/symbol: `core/internal/shared/audit_logger.py::read_audit_log`
- Trigger: metrics cron (`platform_export_metrics` ежеминутно) → `deploy_collector.get_deploy_status(limit=200)` → full-file load
- Complexity/cost: O(file_size) read + O(lines) list build ради последних 200 записей
- Expected impact: на dev-ноде `/var/log/platform/audit.jsonl` уже 21,136,057 байт / 118,926 строк; ~21MB чтение + ~50MB transient Python-памяти каждую минуту; в production растёт unbounded (ротации не найдено)
- Evidence: `audit_logger.py:270-271`
  ```python
  with pathlib.Path(log_file).open(encoding="utf-8") as f:
      lines = f.readlines()
  ```
  Docstring утверждает обратное (`:249-250` "reverse-line reading from the end"); consumer `metrics/deploy_collector.py:49`
- Minimal fix: настоящий reverse chunked tail-read (seek от EOF, стоп после `limit` валидных записей), как уже описано в docstring
- Measurement: metrics-export step time & RSS: сейчас ~30–80ms + десятки MB @21MB (растёт линейно); после <5ms + <1MB константа
- Phase: Pre-launch

### PERF-031 | MED | conf=High
- Category: N+1 file parsing
- Hot path: no — module validation (`validate_module_yaml --all`, secrets_validator)
- File/symbol: `core/internal/shared/env_requires.py::check_requires_presence` → `env_var_in_dotenv` / `env_var_in_secrets_manifest`
- Trigger: валидационный цикл по env_requires; каждая переменная перечитывает `.env.example`, перекомпилирует regex и перепарсивает весь secrets-manifest.yaml
- Complexity/cost: O(V×(L+S)) вместо O(L+V+S)
- Expected impact: ~20 модулей × 3–5 vars = 60–100 лишних чтений + 60–100 полных PyYAML-parse'ов манифеста за прогон валидации → секунды чистого re-parse в CI-гейтах
- Evidence: `env_requires.py:94,100-101,142,190-223`
- Minimal fix: парсить `.env.example` один раз в dict и грузить манифест один раз на вызов `check_requires_presence`
- Measurement: wall time validate-module-yaml (est. −1–3s на полную валидацию)
- Phase: Post-launch

### PERF-032 | MED | conf=High
- Category: parse-per-call config readers без memoization
- Hot path: yes — каждый project/module deploy и bootstrap φ8
- File/symbol: `core/internal/shared/env_requires.py::check_runtime_env` (+ `shared/secrets_manifest_reader.iter_secrets`)
- Trigger: вызывается раз на включённый модуль в `_deploy_sequential` и в `batch_check_env`; каждый вызов = полный `yaml.safe_load` secrets-manifest.yaml + полный read+parse secrets.env
- Complexity/cost: M×(S+E), M≈14–20 модулей
- Expected impact: тот же манифест парсится 14–20× за один деплой (~50–150ms впустую)
- Evidence: `env_requires.py:268,287`; callers `deploy_orchestrator.py:686-689`, `secrets_validator.py:453-464`
- Minimal fix: mtime-keyed lru_cache на `iter_secrets()` или переиспользование одного env_map в цикле
- Measurement: env-check фаза деплоя: M YAML parse'ов → 1
- Phase: Post-launch

### PERF-033 | MED | conf=High
- Category: fresh SSH connection per check (нет ControlMaster)
- Hot path: yes — каждый `make deploy` pre-flight (makefiles/deploy.mk:37-38) и CI deploy-project.yml
- File/symbol: `core/internal/shared/vps_readiness.py::check_vps_ready` / `default_ssh_runner`
- Trigger: до 4 последовательных проверок, каждая — новый full TCP+SSH handshake; SSH_OPTS без ControlMaster/ControlPersist (`ssh_opts.py:40-51`)
- Complexity/cost: 4× handshake (~0.5–2s каждый) vs 1 соединение или 1 объединённая команда
- Expected impact: ~2–8s к КАЖДОМУ деплою проекта во всех CI-пайплайнах
- Evidence: `vps_readiness.py:136-137`; call sites `:306,317,331,345`
- Minimal fix: объединить 4 remote-команды в один `ssh_read` (или ControlPersist socket reuse в SSH_OPTS)
- Measurement: pre-flight duration: ~4 handshakes → 1; −2–6s/деплой
- Phase: Pre-launch

### PERF-034 | MED | conf=High
- Category: N+1 subprocess (docker inspect per container per poll)
- Hot path: yes — пост-compose healthcheck каждого модуля в φ8
- File/symbol: `core/internal/shared/docker_compose.py::healthcheck_poll`
- Trigger: poll-loop: `docker ps` раз + один `docker inspect` subprocess НА контейнер НА итерацию
- Complexity/cost: P×(1+C) docker spawns (P≤20 polls, C контейнеров); `docker inspect` принимает несколько ID за вызов
- Expected impact: C=3, P=10 → 30 лишних spawn ≈ 1.5–3s на модуль; ×14 модулей ≈ десятки секунд на полный bootstrap/update
- Evidence: `docker_compose.py:590-592` внутри `while time.monotonic() < deadline:` (`:560`)
  ```python
  for cid in cids:
      state, health = dops.inspect_state_health(cid, timeout=DOCKER_CMD_TIMEOUT)
  ```
- Minimal fix: батч `docker inspect cid1 cid2 … --format '{{.State.Status}}|{{.State.Health.Status}}'` раз на полл, split строк
- Measurement: spawn count на healthcheck: P×C → P; −10–40s worst case на полный деплой
- Phase: Post-launch

### PERF-035 | LOW | conf=High
- Category: избыточные openssl spawns
- Hot path: no — cert orchestration, S3 SSL cache sync, e2e-verify TLS sweep
- File/symbol: `core/internal/shared/ssl_certs.py::cert_is_valid` (через _run_openssl)
- Trigger: цепочка 3–5 отдельных `openssl x509` процессов на сертификат
- Complexity/cost: 3–5 spawn + re-parse PEM vs 1 `openssl x509 -text`
- Expected impact: sweep D доменов → 3D–5D процессов; ~0.3–1s на ноду с 20 certs за sweep/renewal
- Evidence: `ssl_certs.py:372-396`
- Minimal fix: один `openssl x509 -text -noout` на cert, вывести issuer/SAN/CN/expiry из одного вывода
- Measurement: openssl spawn count per cert 3–5 → 1; sweep time −~70%
- Phase: Post-launch

### PERF-036 | LOW | conf=High
- Category: O(n²) rescан префикса в regex replacer
- Hot path: no — scaffold renders, templates-render/check, monitoring render
- File/symbol: `core/internal/template_engine.py::render_template._replacer`
- Trigger: на каждый placeholder line number считается ресканом всего префикса
- Complexity/cost: O(N×M) вместо O(N) с бегущим счётчиком
- Expected impact: сегодня пренебрежимо (шаблоны ≤ десятков KB); 100KB шаблон × 1000 placeholders ≈ 30–100ms/render
- Evidence: `template_engine.py:205-209,231`
- Minimal fix: бегущий счётчик переносов от предыдущего match offset
- Measurement: render time vs template size: superlinear → linear
- Phase: Post-launch

### PERF-037 | LOW | conf=Med [HYPOTHESIS]
- Category: curl subprocess там, где есть in-repo stdlib client
- Hot path: no — verify-domains, e2e-verify sweeps
- File/symbol: `core/internal/shared/http_probe.py::curl_http_code`
- Trigger: curl-процесс на каждый probe при существующем urllib-based `shared/http_client.py`
- Complexity/cost: ~20–50ms spawn overhead на probe поверх network time
- Expected impact: E×2 probes → ~1–2s на полный e2e sweep при E≈20–40
- Evidence: `http_probe.py:69,75-77`
- Minimal fix: опциональный urllib backend для простых GET code checks (curl оставить для `--resolve` TLS-SNI)
- Measurement: probe latency delta curl-subprocess vs urllib
- Phase: Post-launch
