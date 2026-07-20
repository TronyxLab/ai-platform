# Brief: Консолидация секретов и защита от дрейфа

<!--
$ARTIFACT_CONTRACT
  PURPOSE:      Упорядочить управление секретами/токенами платформы:
                (1) устранить дрейф GitHub-токенов — сократить до 1 авто + 1 PAT,
                (2) ввести единый secrets-manifest.yaml с tier-моделью,
                (3) персистентную автогенерацию секретов,
                (4) anti-drift гейт, запрещающий нелегализованные секреты.
  DESCRIPTION:  Дрейф-аудит 2026-07-20 выявил: GHCR_TOKEN мёртв но документирован,
                GIT_MIRROR_TOKEN fail-fast required хотя уже сделан SSH-primary,
                autogen-секреты эфемерны (новые ключи при каждом bootstrap),
                валидация секретов размазана по 4 несогласованным механизмам,
                нет единого SSoT по секретам — агенты дрейфуют.
  RATIONALE:    Дрейф не остановится сам — документация, генерирующая запросы на
                новые токены, остаётся в коде. Нужен системный anti-drift механизм:
                манифест как SSoT → гейт консистентности → блокировка молчаливого
                добавления секретов. Option A выбрана по обоим вопросам суперпозиции.
  ACCEPTANCE_CRITERIA:
    AC1: GHCR_TOKEN удалён из .env.example (мёртвый), GIT_MIRROR_TOKEN не fail-fast
         в context-promote.sh (уже SSH primary, HTTPS fallback опциональный — done T3.4)
    AC2: secrets-manifest.yaml — единый SSoT: tier=required|generated|optional,
         feature=*, consumers=[модули]. Обязательность вычисляется из состава модулей ноды.
    AC3: autogen-секреты (LITELLM_MASTER_KEY, LANGFUSE_*, SALT, NEXTAUTH_SECRET)
         персистятся обратно в encrypted-файл при генерации — `sops --set` или аналогично.
    AC4: Гейт test_gate_secrets_manifest.py: каждый secrets.X в .github/workflows/*.yml
         обязан быть зарегистрирован в манифесте; новый секрет без регистрации = RED.
    AC5: SSH_KEY и CI_DEPLOY_KEY проверены на дублирование и унифицированы.
    AC6: .env.example CI-секция генерится/валидируется из манифеста (автоматически).
    AC7: Все 4 механизма валидации (required_vars, env_requires, _ensure_secret, validate_env)
         консолидированы на манифест как единый проверяемый источник.
  IMPLEMENTS:   Plan 018 — secrets-consolidation
  IMPACTS:      files: .env.example, core/secrets-manifest.yaml (NEW), core/lib/secrets.sh,
                core/internal/bootstrap/node-lifecycle.sh, core/internal/bootstrap/deploy-modules.sh,
                .github/workflows/mirror.yml, context-promote.sh (минимально),
                core/internal/secrets/decrypt-secrets.sh, tests/gates/ (новый гейт).
                modules: все module.yaml (env_requires синхронизация с манифестом),
                platform-secrets (опционально).
  REQUIRES:     Plan 015 T3.4 завершён (SSH primary в context-promote.sh — done 2026-07-18).
                Plan 010 T3 частично: .env.example CI-секция документирована.
                AGE_SECRET_KEY доступен локально для тестов sops --set.
-->

# Brief: 018-secrets-consolidation

## 1. Проблема

Аудит 2026-07-20 выявил системный дрейф в управлении секретами и токенами платформы:

### 1.1 GitHub-токены

| Токен | Статус на 2026-07-20 | Проблема |
|-------|----------------------|----------|
| `GITHUB_TOKEN` | ✅ авто, живой | — |
| `GHCR_TOKEN` | ⛔ **мёртв** — 0 использований в коде | Документирован в `.env.example:218`, агенты просят его создать |
| `GHCR_PULL_TOKEN` | ✅ живой, единственный настоящий PAT | Pull образов на VPS — законный, не заменим на GITHUB_TOKEN |
| `GIT_MIRROR_TOKEN` | ⚠️ fail-fast required в context-promote.sh (было), используется в mirror.yml | Plan 015 T3.4 уже перевёл promote на SSH primary — токен стал опциональным; но `.env.example:225` и документация не обновлены |
| `SSH_KEY` vs `CI_DEPLOY_KEY` | ⚠️ вероятный дубль одного SSH-ключа ci-deploy | 2 разных repo secrets с одним назначением |
| `NODE_CONFIGS_TOKEN` | ✅ устранён (Plan 001) | Охраняется гейтом `test_project_ci_contract.py` |

**Итоговая цель**: `GITHUB_TOKEN` (авто) + `GHCR_PULL_TOKEN` (единственный PAT, read:packages) + 2 SSH-ключа с разными ролями (`VPS_SSH_KEY` для rsync, `CI_DEPLOY_KEY` для forced-command). Всё остальное — удаляется.

### 1.2 Autogen-секреты эфемерны

`step_12b_ensure_secrets` (core/lib/secrets.sh:225-231) генерит 7 секретов при отсутствии, но **не персистит** их обратно в encrypted-файл → при каждом bootstrap новые ключи → рассинхрон клиентов с LiteLLM/Langfuse.

### 1.3 Валидация размазана

Обязательность секретов проверяется через 4 несогласованных механизма:
- `required_vars` inline-массив (node-lifecycle.sh:146) — только инфраструктурные
- `env_requires` в module.yaml (12 модулей) — per-module
- `_ensure_secret()` (secrets.sh:225) — autogen с WARN
- `validate_env()` (decrypt-secrets.sh:67) — только AGE_SECRET_KEY + путь

Единого SSoT нет. `${VAR:?}` не используется нигде.

### 1.4 Мёртвая документация генерирует дрейф

`.env.example:218` документирует мёртвый `GHCR_TOKEN` → агенты читают и просят создать. Нет гейта, запрещающего `secrets.X` в workflows без регистрации в манифесте.

## 2. Решение (коллапс суперпозиции)

### 2.1 GitHub-токены: «1 авто + 1 PAT»

```
GITHUB_TOKEN (авто, Actions)      ── CI: ghcr push, GH API
GHCR_PULL_TOKEN (PAT, read:pkgs)  ── VPS: docker pull ghcr.io
VPS_SSH_KEY (SSH)                 ── CI: rsync core на VPS
CI_DEPLOY_KEY (SSH, forced-cmd)   ── CI: deploy-project на VPS

УДАЛЯЮТСЯ:
  GHCR_TOKEN         — мёртв (заменён на GITHUB_TOKEN + packages:write)
  GIT_MIRROR_TOKEN   — mirror.yml переводится на SSH deploy key (как context-promote.sh);
                        HTTPS+токен остаётся опциональным fallback
  SSH_KEY            — дубль CI_DEPLOY_KEY (верифицировать и слить)
```

### 2.2 secrets-manifest.yaml с tier-моделью

Единый SSoT: `core/secrets-manifest.yaml`.

```yaml
# core/secrets-manifest.yaml
secrets:
  - name: POSTGRES_PASSWORD
    tier: required           # identity — не генерится, должен быть в enc-файле
    consumers: [postgres, litellm, backup-cron, infra-metrics, langfuse]
    source: sops             # sops | env | ci-secret | autogen | alias
    note: "Генерировать: openssl rand -base64 32"

  - name: LITELLM_MASTER_KEY
    tier: generated          # машина умеет генерить; персистится в enc-файл
    consumers: [litellm, hermes-agent]
    source: autogen
    gen_command: "sk-$(openssl rand -hex 32)"

  - name: TELEGRAM_BOT_TOKEN
    tier: optional           # feature-gated, отсутствие = skip
    feature: telegram
    consumers: [hermes-agent, monitoring]
    source: sops

  - name: GHCR_PULL_TOKEN
    tier: required           # единственный PAT
    consumers: [docker-login]
    source: sops
    note: "Fine-grained PAT: read:packages на tronyx161 + все контекстные orgs"

  # ... (полный список — 25+ секретов)
```

Обязательность **вычисляется** из состава модулей ноды: если модуль не в `node.yaml modules` — его `env_requires` не проверяются. Это позволяет одну и ту же платформу разворачивать с разным составом без ложных fail.

### 2.3 Персистентная автогенерация

`step_12b_ensure_secrets` дорабатывается: после `openssl rand` для каждого generated-секрета → `sops --set` в encrypted-файл. При повторном bootstrap секреты читаются из enc-файла, а не генерируются заново.

### 2.4 Anti-drift гейт

Новый гейт `test_gate_secrets_manifest.py` (регистрируется в `entrypoint-manifest.yaml` секция gates):

1. **Manifest ↔ module.yaml**: каждое `env_requires` имя обязано быть в манифесте (tier=required или generated). Неизвестное имя → RED.
2. **Manifest ↔ workflows**: каждый `secrets.X` в `.github/workflows/*.yml` обязан быть в манифесте (source=ci-secret). Неизвестное имя → RED.
3. **Manifest ↔ .env.example**: каждая CI-secret запись в .env.example обязана быть в манифесте. Расхождение → AMBER (warning).
4. **No hardcoded secrets**: существующий `test_no_hardcoded_ci_secrets` расширяется на `core/**/*.sh` (был только `.github/**`).

## 3. Scope и границы

**Входит в план:**
- Создание `core/secrets-manifest.yaml` (25+ секретов)
- Модификация `core/lib/secrets.sh`: персистенция autogen через sops
- Модификация `core/internal/bootstrap/deploy-modules.sh`: `_check_env_requires()` → манифест-driven
- Модификация `.env.example`: удаление GHCR_TOKEN, синхронизация CI-секции
- Гейт `test_gate_secrets_manifest.py` (3 проверки + расширение credential scan)
- Регистрация гейта в `core/entrypoint-manifest.yaml`
- Верификация SSH_KEY/CI_DEPLOY_KEY дублирования
- Обновление `mirror.yml`: документирование flow и переход на SSH deploy key

**НЕ входит в план (out of scope):**
- Хранилище секретов (vault) — в беклоге, без сроков
- Миграция существующих секретов на VPS (новый манифест не меняет значения)
- Ротация токенов (механизм, не политика)
- `make project-sync-secrets` (DISABLED — T3.6 conditional)

## 4. Ключевые риски

| Риск | Mitigation |
|------|-----------|
| `sops --set` может сломать encrypted-файл | Тест на dry-run: зашифровать → расшифровать → сравнить значения до/после |
| SSH deploy key для mirror.yml требует отдельного GitHub user или machine user | Можно использовать тот же ключ что CI_DEPLOY_KEY, если у него write на TronyxLab |
| Манифест рассинхронизируется с кодом | Гейты CI (make gate) блокируют merge при расхождении |
| GIT_MIRROR_TOKEN ещё нужен в mirror.yml пока SSH deploy key не настроен | Двухшаговая миграция: сначала SSH deploy key → потом удаление токена |

## 5. Метрики успеха

- Количество ручных Actions secrets для нового контекста: **0** (против 12 документированных сейчас)
- Количество GitHub PAT: **1** (GHCR_PULL_TOKEN, против 3 сейчас)
- Autogen-секреты: **персистентны** (повторный bootstrap не меняет ключи)
- Anti-drift гейт: **RED на CI** при добавлении secrets.X без регистрации в манифесте

## 6. Связанные планы

| План | Статус | Связь |
|------|--------|-------|
| 001-project-connection-model | ✅ done | Устранил NODE_CONFIGS_TOKEN, ввёл vars.NODE_HOST_MAP |
| 006-ci-optimization | ✅ done | Ввёл GHCR_TOKEN → заменён на GITHUB_TOKEN (TRAP[BUG]) |
| 010-drift-simple-fixes | ✅ done | Документировал 12 CI-секретов в .env.example (включая мёртвый GHCR_TOKEN) |
| 015-legalize-vps-mutations | 🔄 T3.4 done | SSH primary в context-promote.sh (GIT_MIRROR_TOKEN → optional) |
