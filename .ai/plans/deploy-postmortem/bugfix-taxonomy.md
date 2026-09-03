# Bugfix Taxonomy — каталог классов исправлений (2026-08-25 → 09-02)

Охват: 164 коммита, из них 73 `fix()` + 14 структурированных находок F-01..F-14.
Критичность: P0 = блокирует clean one-command · P1 = серьёзно · P2 = вторично.

## Каталог (сортировка: рецидив × критичность)

| Класс | Встр. | Компоненты | Попыток | Повторялся | Критичность |
|---|---|---|---|---|---|
| **Fail-silent → honest error/exit** | 13 | `restore_psql.sh`, `context_deployer.py`, `core_deliverer.py`, `rollback.py`, `domains.py`, `s3_ssl_cache.py`, `cli.py` | ~13 | **Да** (014/017/020/027) | P0 |
| **Secret/config/credential propagation** | 12 | `node_detect.py`, `lifecycle/cli.py`, `node-lifecycle.sh`, `build-ssh-cmd.sh`, `decrypt_secrets.py`, `platform-secrets.service`, `deploy-project.yml` | 6+ на ОДНОМ корне (phi4) | **Да** (сага 6 коммитов) | P0 |
| **CI-specific (env/depth/disk/pins/parse)** | 9 | `deploy-project.yml`, `platform-test.yml`, `orchestrator_cli.py` | ~9 | **Да** (disk-класс: 007→F-12) | P0 (канал) / P2 (диск) |
| **Readiness vs healthcheck / startup order** | 8 | `langfuse/litellm compose`, `docker_orchestrator.py`, smoke-тесты | 3 раунда (depends_on→revert→smoke) | **Да** (`64fe57d`→`86987a9`→F-13) | P0/P1 |
| **Python-скрипты (shadow/argv/re-exec/parity)** | 5 | `converge/ssl.py`, `orchestrator_cli.py`, `lifecycle/cli.py`, `gen_project_platform_md.py` | 5 | argv-класс ×2 (`379fd01`+`e0d0e09`) | P0 |
| **Idempotency violations (2nd run ≠ 1st)** | 5 | `helpers/domains.py`, `converge/ssl_certs.py`, `project_payload_delivery.py` | 5 | **Да** (`6094933`→F-10) | P1 |
| **Missing dependency (флаг/пакет/модуль/образ)** | 5 | `config.alloy`, `docker_orchestrator.py`, `context_deployer.py` | 5 | Нет | P1 |
| **Hardcoded assumptions (paths/scope/списки)** | 4 | `test_chaos_resilience.py`, `verify_sweep/collection.py` | 4 | Нет | P2 |
| **State leakage / env contamination** | 4 | тесты (ssl_s3_cache, add_vhost, no_empty_dirs, compose_preflight) | 4 | **Да** (017/018/027) | P2 |
| **Missing drift detection / validation** | 3 | `vhost_renderer.py`, `converge/runtime.py`, `converge/ssl_certs.py` | 3 | Нет | P0 |
| **Volume / permissions** | 3 | `backup-cron/entrypoint.py`, `nginx_reload_hook.sh` | 3 | Нет | P1 |
| **DB restore / migration (postgres)** | 3 | `restore_psql.sh`, `restore_self_role_filter.awk`, `restore_db_check.py` | 3 | Нет | P1 |
| **Monitoring / metrics contract** | 3 | `status-page/app.py`, `platform_export_metrics.py` | 3 | Нет | P2 |
| **Docker networking (ports/loopback)** | 2 | `test_smoke_redis.py` | 2 | Нет | P1 (hardening) |

## Топ по (рецидив × критичность)

1. **Fail-silent → honest handling** — 13, рецидив, P0 (наибольший объём, блокирует clean deploy).
2. **Secret/credential propagation** — 12, рецидив, P0 (самая длинная single-root сага — phi4).
3. **CI-specific** — 9, рецидив, P0 (канал).
4. **Readiness vs healthcheck** — 8, рецидив, P0.
5. **Python-скрипты** — 5, рецидив-argv, P0.
6. **Idempotency** — 5, рецидив, P1.
7. **Missing dependency** — 5, P1.
8. **Missing drift detection** — 3, P0.

## Рецидив-заголовок

Три класса дают почти весь re-breakage после «фикса»:
- **secret propagation** (phi4 AGE-цепь: `3358f98→9852633→081ffe6→fc515c1→41ddd6c→d1337ab`, затем свежий SSH_OPTS в F-06);
- **fail-silent masking** (рецидив через планы 014/017/020/027);
- **readiness-vs-healthcheck** (фикс `64fe57d` → revert `86987a9` → re-broken T2.0a в F-13).

Остальное — одноточечные фиксы без наблюдаемого рецидива.

## Агрегация: сколько проблем реально?

- ~73 fix-коммита → **~14 классов** → **7 корневых причин** → **2 системные первопричины**:
  1. **Неправильный предикат успеха** («healthy/phase-done» вместо «serving/desired-state-verified») — покрывает fail-silent, readiness, drift-detection, idempotency, и часть CI.
  2. **Нет обязательного clean-server гейта** — энэйблер, который делает детект отложенным (2.5 недели красного CI) и позволяет дрейфу контрактов доезжать до production.
