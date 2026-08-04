# POSTGRES_PASSWORD — процедура ротации (runbook)
# GREP_SUMMARY: postgres rotation runbook POSTGRES_PASSWORD ALTER-USER pgbouncer litellm backup-cron secrets.env sops charset scram quarterly
# STRUCTURE: ▶ preflight (окно + доступ) → ▶ generate (charset-safe) → ▶ ALTER USER (живой БД) → ▶ secrets (sops → secrets.env) → ▶ restart потребителей → ▶ верификация → ⎋ rollback

# region MODULE_CONTRACT
## @purpose  Пошаговая процедура ротации POSTGRES_PASSWORD на provisioned-ноде.
##           P3-4/D11 (реестр 001, DevPlan 130 W3): пароль postgres ротировался вручную →
##           риск рассинхрона потребителей (litellm, langfuse, infra-metrics, backup-cron,
##           pgbouncer auth_query). Закрывает TRAP[DEBT] 2026-07-17 в docker-compose.base.yml.
## @scope    Provisioned-нода с модулем postgres (docker compose, shared-db-net).
##           НЕ для dev-локали (там ci_default «test-pg-pwd» из .env).
## @invariants
##   - POSTGRES_PASSWORD потребляется при initdb и хранится в data dir (pg_authid) —
##     смена env-переменной НЕ ротирует пароль в работающей БД (TRAP[DEBT] 2026-07-17).
##     Ротация = ALTER USER (DB-сторона) + обновление секретов (config-сторона).
##   - Charset-ограничение: ^[A-Za-z0-9._-]+$ (secret-definitions.yaml + secrets_validator) —
##     спецсимволы ломают pgbouncer/scram (crash-loop, DevPlan 116 B3 T5).
##   - Порядок критичен: сначала ALTER USER (живой БД), затем секреты, затем потребители.
##     Обратный порядок = состояние рассинхрона (новый пароль в secrets.env, старый в БД).
##   - pgbouncer auth_user=postgres читает pg_shadow через auth_query — после ALTER USER
##     БД-аутентификация обновляется автоматически, но пулер требует restart (кэш соединения).
## @rationale Решение 130 W3: runbook вместо автоматизации в secrets_manager — полная ротация
##            (ALTER USER + sops re-encrypt + restart 5 потребителей) >30 LOC и НЕ unit-тестируема
##            без живого postgres/sops/age (native pytest, §TESTING). Runbook — минимальное
##            решение DevPlan; Rev-условие: ротация ≥1 раз в квартал ИЛИ автоматизация.
# endregion MODULE_CONTRACT

## Rev-условие (обновлено 130 W3)

Ротация **обязательна ≥1 раз в квартал** (следующая плановая: ≤2026-11-04). Rev-условие
снято, если появится автоматизация ротации через `secrets_manager` (с unit-тестами) —
тогда runbook становится справкой по ручному фолбэку.

## Потребители POSTGRES_PASSWORD (инвентарь, 130 W3)

| Потребитель | Механизм | Эффект ротации |
|-------------|----------|----------------|
| `postgres` (initdb) | `POSTGRES_PASSWORD` env в compose | Только initdb-время; смена env после init НЕ ротирует (TRAP[DEBT]) |
| `pgbouncer` | `DATABASE_URLS` (wildcard) + `DB_PASSWORD` | auth_query→pg_shadow обновляется сам; пулер требует restart (кэш auth-соединения) |
| `litellm` | `DATABASE_URL` → pgbouncer:6432/litellm | Пересоздание контейнера с новым env |
| `langfuse` | `DATABASE_URL` → pgbouncer:6432/langfuse | Пересоздание контейнера с новым env |
| `infra-metrics` | `DATA_SOURCE_NAME` → postgres:5432 | Пересоздание контейнера с новым env |
| `backup-cron` | `POSTGRES_PASSWORD` env | Пересоздание контейнера с новым env |
| `hooks/on_project_deploy.py` | `PGPASSWORD`/`POSTGRES_PASSWORD` (env) | Подхватывает новый пароль при следующем деплое проекта |

## Процедура

### 0. Preflight

- [ ] План-окно: ротация обрывает активные соединения потребителей (~секунды).
- [ ] Доступ к ноде по SSH + AGE-ключ для secrets (`make secrets-unlock NODE=<node>` работает).
- [ ] Старый пароль известен и работает (иначе сначала восстановление доступа — вне runbook).
- [ ] Зафиксировать старый пароль в безопасном месте (rollback-материал, НЕ в git).

### 1. Сгенерировать новый пароль (charset-safe)

```bash
NEW_PG_PASSWORD="$(openssl rand -base64 32 | tr -dc 'A-Za-z0-9._-' | head -c 32)"
# валидация: [[ "$NEW_PG_PASSWORD" =~ ^[A-Za-z0-9._-]+$ ]] || exit 1
```

### 2. ALTER USER в работающей БД (ротация на DB-стороне)

```bash
docker exec postgres psql -U postgres -c "ALTER USER postgres WITH PASSWORD '${NEW_PG_PASSWORD}';"
# Проверка немедленно (новый пароль):
docker exec postgres psql "postgresql://postgres:${NEW_PG_PASSWORD}@127.0.0.1:5432/platform" -c "SELECT 1;"
```

### 3. Обновить секреты (config-сторона)

3.1. **sops-файл** — единый источник для прод (`${NODE_CONFIGS_DIR:-/opt/node-configs}/secrets/<node>.enc.yaml`):

```bash
# на операторской машине (локально), где есть AGE-ключ:
sops set "${NODE_CONFIGS_DIR}/secrets/<node>.enc.yaml" '["POSTGRES_PASSWORD"] "'"${NEW_PG_PASSWORD}"'"'
# или интерактивно: sops edit <файл> → изменить POSTGRES_PASSWORD → сохранить (sops пере-шифрует)
```

3.2. **secrets.env** — пере-декрипт (φ9 secrets_update / `make node-update NODE=<node>`
или `make secrets-unlock NODE=<node>` → `step_10_decrypt_secrets` пересоздаёт
`/run/platform/secrets.env` из sops-файла). Проверка:

```bash
grep -c '^POSTGRES_PASSWORD=' /run/platform/secrets.env   # → 1
grep '^POSTGRES_PASSWORD=' /run/platform/secrets.env | tail -c 1 | grep -q '=' || true
```

### 4. Перезапустить потребителей (новый env)

Порядок: postgres НЕ пересоздавать (env не ротирует, restart ничего не даст) →
pgbouncer (auth-кэш) → остальные потребители:

```bash
docker compose -f /opt/platform/core/docker-compose.yml \
  up -d --force-recreate pgbouncer litellm langfuse infra-metrics backup-cron
# или через модульные таргеты: make up MODULES=pgbouncer,litellm,langfuse,infra-metrics,backup-cron
```

### 5. Верификация

```bash
# 5.1 Liveness postgres + pgbouncer (docker health):
docker inspect --format '{{.State.Health.Status}}' postgres pgbouncer   # → healthy ×2
# 5.2 Аутентификация новым паролем через pgbouncer (wildcard-роутинг):
docker exec postgres psql "postgresql://postgres:${NEW_PG_PASSWORD}@pgbouncer:6432/platform" -c "SELECT 1;"
# 5.3 Потребители (модульные healthcheck'и):
make healthcheck NODE=<node>
# 5.4 Старый пароль ОБЯЗАН перестать работать:
docker exec postgres psql "postgresql://postgres:${OLD_PG_PASSWORD}@127.0.0.1:5432/platform" -c "SELECT 1;" \
  && { echo "FAIL: old password still valid"; exit 1; } || echo "OK: old password rejected"
```

### 6. Rollback (при сбое потребителя)

1. Вернуть пароль в БД: `ALTER USER postgres WITH PASSWORD '<старый>'`.
2. Вернуть sops-файл (git history / сохранённый rollback-материал) → пере-декрипт secrets.env.
3. `docker compose up -d --force-recreate pgbouncer litellm langfuse infra-metrics backup-cron`.
4. Повторить §5-верификацию.

## Примечания

- **НЕ менять** `POSTGRES_PASSWORD` только в `.env`/compose на проде — это создаёт состояние
  рассинхрона (новый env, старый пароль в БД), ровно тот риск, что фиксирует P3-4.
- Для проектных ролей (не `postgres`-суперпользователь) действует `on_project_deploy.py`
  (role provisioning) — он использует `POSTGRES_PASSWORD` из env; после ротации пересоздание
  потребителей (§4) достаточно, спец-действий не требуется.
- Ежеквартальная ротация логируется в audit (audit_logger) и status-отчёте Sysadmin.
