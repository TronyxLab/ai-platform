# Findings 002 — Bootstrap lifecycle

Scope: `core/internal/bootstrap/lifecycle/` · Agent wave 1 · 2026-08-22

### PERF-010 | HIGH | conf=High
- Category: fixed-sleep polling + per-item sequential subprocess waits
- Hot path: yes — каждый `make node-update` (φ11 registry_update → `_registry_step_healthcheck`; маркер `.hc_done_in_deploy` не может существовать — deploy идёт позже в φ12) + standalone пост-деплой healthcheck
- File/symbol: `core/internal/bootstrap/lifecycle/helpers/reporting.py::run_healthchecks`
- Trigger: любой модуль, проваливший liveness в φ11; ретраи сериальные per-module с фиксированным 10s sleep
- Complexity/cost: O(M × R × T); на упавший модуль worst case R=10 × timeout=30s + 9 × sleep=10s ≈ ~390s; M ≈ 8–14 платформенных модулей последовательно
- Expected impact: один unhealthy модуль добавляет до ~6.5 мин к ранy с бюджетом ~5 мин; 3+ быстро падающих модуля = ≥5 мин чистого сна даже при детерминированном фейле (контейнер отсутствует)
- Evidence: `helpers/reporting.py:90-91`, `:130-139`, `:161-162`
  ```python
  hc_max_retries = 10
  hc_retry_interval = 10
  ...
  for attempt in range(1, hc_max_retries + 1):
      hc_result = subprocess.run(["bash", "-c", hc_cmd], ..., timeout=30, check=False)
  if attempt < hc_max_retries:
      time.sleep(hc_retry_interval)
  ```
  Caller: `phases/docker.py:616` внутри последовательной `phase_registry_update`
- Minimal fix: общий deadline + exponential backoff вместо 10s×10 serial; fast-skip детерминированных фейлов; либо параллельный поллинг всех модулей против одного дедлайна
- Measurement: wall time φ11 + счётчик "attempt %d/%d" в логах
- Phase: Pre-launch

### PERF-011 | LOW | conf=High
- Category: chatty round-trips — per-item sops subprocess
- Hot path: no — только первый bootstrap / первая автогенерация секретов (дальше idempotent skip)
- File/symbol: `core/internal/bootstrap/lifecycle/secrets_manager.py::ensure_secrets` → `_persist_to_sops`
- Trigger: ≥1 tier=generated секрета отсутствует в φ4; каждый секрет персистится отдельным `sops --set`
- Complexity/cost: N последовательных sops процессов; каждый decrypt+re-encrypt всего `{node}.enc.yaml`; N≈7–10 → ~5–15s однократно
- Expected impact: секунды на первом bootstrap (<1% от ~30 мин init); нулевая steady-state цена
- Evidence: `secrets_manager.py:557-560`, `:402-403`
- Minimal fix: батч всех пар в один `sops --set '["k1"] "v1" ["k2"] "v2" …' enc_file`
- Measurement: число sops spawn и длительность Step 4 первого bootstrap
- Phase: Post-launch
