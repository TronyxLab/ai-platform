# 019-Brief: Pipeline gaps after bare-metal deploy

$START_BRIEF

$ARTIFACT_CONTRACT
PURPOSE:               Закрыть 5 системных проблем в deploy pipeline, обнаруженных при бутстрапе голого сервера tronyx-vps (20.07.2026), чтобы будущий деплой выполнялся одной командой без ручной подкрутки агентом.
DESCRIPTION:           Анализ сессии «Оркестратор деплоя: фазы и инварианты» (ses_0815eda13ffelKjTvw5g65fWpf) выявил 6 коммит-фиксов (уже в main) и 5 незакрытых системных проблем. Бриф фокусируется на последних: S1 (NODE_YAML_PATH не экспортится в subshell), S2 (vhost-файлы не доставляются на сервер), S3 (PLATFORM_DOMAIN не гарантированно попадает в Docker-окружение nginx), S4 (nginx падает без upstream-контейнеров проектов), S5 (нет YAML-валидации node.yaml перед парсингом).
RATIONALE:             Каждая из этих проблем была «обойдена» ручными действиями агента во время деплоя (export, scp, docker compose env, ручная правка nginx config). Без фиксов в репозитории следующий бутстрап голого сервера потребует тех же ручных вмешательств. Текущий pipeline: bootstrap → ручной SCP vhosts → ручная установка PLATFORM_DOMAIN → ручная правка nginx → node-update. Целевой pipeline: bootstrap → node-update → deploy проектов.
ACCEPTANCE_CRITERIA:   1. `make render-vhosts NODE=<n>` + `make node-update NODE=<n>` — vhost-файлы доставлены на сервер без ручного scp. 2. После `make node-update` — nginx healthy, `PLATFORM_DOMAIN` подставлен во все platform-vhosts (prometheus, hermes, langfuse, loki). 3. Nginx стартует даже при отсутствии project upstream-контейнеров (до деплоя проектов). 4. `make bootstrap-node NODE=<n>` — fail-fast при синтаксически невалидном node.yaml (понятное сообщение, а не «owner_key not found»). 5. `make gate MODE=fast` — зелёный.
IMPLEMENTS:            Требования из отчёта деплоя (попутные фиксы S1–S5), инварианты 1 (Makefile — единый фасад), 6 (bootstrap-node идемпотентный), 9 (тестовый сервер может быть пересоздан заново).
IMPACTS:               core/internal/bootstrap/node-lifecycle.sh (новый шаг update_step_2.5), core/internal/scaffold/add-vhost.sh (export NODE_YAML_PATH), core/internal/bootstrap/deploy-modules.sh (PLATFORM_DOMAIN env), core/modules/nginx/nginx.conf (глобальный resolver), core/internal/provision-environment.sh (PLATFORM_DOMAIN provisioning).
REQUIRES:              Ветка от origin/main, make gate MODE=fast зелёный до начала, working tree чистый.
$END_ARTIFACT_CONTRACT

---

## 1. Контекст

20.07.2026 проведён полный цикл деплоя на голый сервер tronyx-vps: gate → CI → bootstrap → context-promote → node-update → healthcheck → верификация. Сессия: `ses_0815eda13ffelKjTvw5g65fWpf`.

Из 11 проблем, обнаруженных в процессе, 6 закрыты коммитами в main (`203ea71`, `ffaaa8a`). Оставшиеся 5 — системные gaps, которые были обойдены ручными действиями агента (export, scp, docker compose env, правка nginx config) и **повторятся при следующем бутстрапе голого сервера**.

### Хронология проблем в сессии

| Фаза | Проблема | Решение агента | Коммит? |
|------|----------|----------------|---------|
| 1 | CI: 7 smoke failures — hermes-agent network isolation | Добавлены test-shared-db-net, test-shared-cache-net | ✅ `ffaaa8a` |
| 2 | `FATAL: owner_key not found` — node.yaml syntax error | Ручная правка node.yaml | ✅ (в tronyx-lab) |
| 2 | `compute_content_hash` → undefined function | Переименован в `compute_step_hash` | ✅ `203ea71` |
| 3 | `render-vhosts` — harness nginx.conf collision | Исправлена логика harness | ✅ `203ea71` |
| 3 | `context-promote` — pipefail + SSH exit 1 | Исправлен SSH check | ✅ `203ea71` |
| 3 | Makefile `render-vhosts` без --node-configs-dir | Добавлен флаг | ✅ `203ea71` |
| 3 | `NODE_YAML_PATH` не экспортится в subshell | Ручной `export NODE_YAML_PATH` | ❌ **S1** |
| 3 | Vhost-файлы не доставлены на сервер | Ручной `scp` | ❌ **S2** |
| 3 | `PLATFORM_DOMAIN` пустой в nginx-контейнере | Ручная установка env | ❌ **S3** |
| 3 | Nginx рестарт-луп из-за project upstream | Ручное комментирование vhost | ❌ **S4** |
| 2 | `owner_key not found` без диагностики YAML | Ручной поиск ошибки | ❌ **S5** |

---

## 2. Проблемы (S1–S5)

### S1. `NODE_YAML_PATH` не экспортируется в command substitution

**Файл**: `core/internal/scaffold/add-vhost.sh:725`

```bash
# Текущий код (БАГ)
NODE_YAML_PATH="$node_yaml" projects_json="$(read_node_yaml_projects "$node_yaml")"
```

Переменная `NODE_YAML_PATH` присваивается в том же simple command, что и command substitution, но **не экспортируется** в subshell. Python-heredoc внутри `read_node_yaml_projects` (строка 262) читает её через `os.environ.get('NODE_YAML_PATH', '')` — получает пустую строку, не может открыть node.yaml, падает с `sys.exit(1)`.

**Симптомы**: `render_all` не находит проекты с доменами, рендерит 0 vhost-файлов.

**Текущий workaround (не в репо)**: ручной `export NODE_YAML_PATH` перед вызовом.

**Предлагаемый фикс** (вариант A — минимальный): заменить на две строки с явным export:
```bash
export NODE_YAML_PATH="$node_yaml"
projects_json="$(read_node_yaml_projects "$node_yaml")"
```

**Предлагаемый фикс** (вариант B — правильный): переделать Python-heredoc на приём пути через `sys.argv[1]`, как это уже делает bash-ветка `read_node_yaml_projects` через `$1`. Убрать зависимость от environ.

---

### S2. Vhost-файлы не доставляются на сервер при node-update

**Где gap**: `make render-vhosts` генерирует `*.conf` в `node-configs/<node>/overlays/nginx/` **локально**. `make node-update` → `node-lifecycle.sh --mode update` не содержит шага доставки этих файлов на сервер.

**Текущие шаги update-режима**:
```
verify-core → provision → ssl-provision → deploy-docker → deploy-system → healthcheck
```

Context-overlay клонируется через git (`ensure_context_repo()` → `git clone/pull`), но GENERATED vhost-файлы (c заголовком `# GENERATED by add-vhost.sh — DO NOT EDIT`) не должны коммититься в git-репозиторий. Они — build artifact, не source code.

**Симптомы**: после `node-update` nginx не видит project vhosts — файлы отсутствуют в `/opt/node-configs/<node>/overlays/nginx/`, nginx использует только platform-vhosts (prometheus, hermes и т.д.).

**Текущий workaround (не в репо)**: ручной `scp` vhost-файлов на сервер.

**Предлагаемый фикс**: добавить в `node-lifecycle.sh --mode update` новый шаг `update_step_2.5_deliver_overlays` между provision и ssl-provision:
- Резолвит локальный `node-configs/<node>/overlays/nginx/` (из NODE_YAML)
- Rsync'ит `*.conf` на сервер в `/opt/node-configs/<node>/overlays/nginx/`
- Идемпотентный (rsync сам по себе идемпотентный — копирует только изменившиеся файлы)
- Использует существующую инфраструктуру `scp-deliver.sh` / `remote-cmd.sh`

---

### S3. `PLATFORM_DOMAIN` не гарантированно попадает в Docker-окружение nginx

**Файл**: `core/modules/nginx/docker-compose.base.yml:47`

```yaml
environment:
  PLATFORM_DOMAIN: ${PLATFORM_DOMAIN:-}
```

`PLATFORM_DOMAIN` экспортится в `node-lifecycle.sh` шаге `ssl-provision` (строка 769), но к моменту `deploy-docker` (шаг 4) он может не дойти по цепочке:
```
node-lifecycle.sh (export PLATFORM_DOMAIN)
  → bash deploy-modules.sh (наследует export? ДА)
    → docker compose up (читает из process environment? ДА, но только если export)
```

Проблема в том, что `export PLATFORM_DOMAIN` происходит **внутри** `update_step_3_ssl_provision`, которая вызывается из `main()` в `node-lifecycle.sh`. Если шаг 3 пропущен по checkpoint (уже выполнен ранее), `export` не происходит. Между запусками `make node-update` переменная теряется.

**Симптомы**: nginx-контейнер получает пустой `PLATFORM_DOMAIN`, `envsubst-templates` не подставляет домен в platform-vhosts (prometheus, hermes, langfuse, loki), nginx падает с ошибкой конфигурации.

**Текущий workaround (не в репо)**: ручная установка `PLATFORM_DOMAIN=tronyx.ru` в docker-compose окружении.

**Предлагаемый фикс**: гарантировать `PLATFORM_DOMAIN` (и связанные переменные: `PLATFORM_EMAIL`, `PLATFORM_ACME_DNS_PLUGIN`, `PLATFORM_PROJECT_DOMAINS`) через `.env`-файл, который **всегда** подхватывается `deploy-modules.sh`:

Вариант A: в `update_step_2_provision` (или новом шаге) записывать `PLATFORM_DOMAIN=...` в `/run/platform/secrets.env` (уже используется `--env-file` в `deploy-modules.sh:412`).

Вариант B: в `deploy-modules.sh` перед `docker compose up` принудительно экспортировать `PLATFORM_DOMAIN` из node.yaml (как это уже делается для `ssl-provision`).

Рекомендация: **вариант A** — единый source of truth в `.env`-файле, не зависит от checkpoint'ов и порядка вызова шагов.

---

### S4. Nginx падает при отсутствии upstream-контейнеров проектов

**Файл**: `core/internal/scaffold/add-vhost.sh:365-437` (`generate_vhost_body`)

Шаблон vhost использует resolver 127.0.0.11 + variable proxy_pass:
```nginx
resolver 127.0.0.11 valid=30s ipv6=off;
set $upstream_tronyx_site http://tronyx-site:80;
proxy_pass $upstream_tronyx_site;
```

Это **должно** позволять nginx стартовать без upstream (lazy DNS resolution). На практике — nginx ушёл в рестарт-луп. Две вероятные причины:

1. **Синтаксические ошибки в сгенерированных vhost**: в сессии упоминались пропущенный `include proxy_params;` и неправильный `server_name` для `sexydancerostov.ru.conf`. Эти проблемы могли быть следствием ошибок рендеринга, а не самого шаблона.

2. **Nginx всё равно не стартует с variable proxy_pass**: если Docker DNS (127.0.0.11) недоступен на этапе `nginx -t` (resolver проверяется при старте), nginx отказывается запускаться даже с переменными.

**Симптомы**: nginx в рестарт-лупе после добавления project vhosts, пока проекты не задеплоены.

**Текущий workaround (не в репо)**: ручное комментирование project vhosts до деплоя проектов.

**Предлагаемый фикс** (комбинированный):
- (a) Добавить глобальный `resolver 127.0.0.11 valid=30s ipv6=off;` в основной `nginx.conf` (секция `http`), чтобы resolver был доступен на уровне `nginx -t`
- (b) Усилить `nginx_t_harness` в `add-vhost.sh` — сейчас он заменяет только SSL-пути, но не проверяет, что `proxy_pass $upstream_<name>` не вызывает `host not found` при запуске. Добавить в harness запуск `nginx -t` с реальным Docker DNS (запускать harness-контейнер с `--dns 127.0.0.11` или аналогично)
- (c) В шаблоне vhost: использовать `set $upstream_<name> ""` как fallback, чтобы при недоступном DNS nginx использовал пустой upstream и отдавал 502 (graceful degradation вместо отказа стартовать)

---

### S5. Отсутствует YAML-валидация `node.yaml` перед парсингом

**Файл**: `core/internal/bootstrap/node-lifecycle.sh` (шаг `read-node-yaml` и `ssl-provision`)

Bootstrap упал с `FATAL: owner_key not found` потому что `node.yaml` содержал дубликат ключа `context:` — YAML был синтаксически невалиден. Ошибка обнаружилась только при попытке извлечь конкретное поле, без указания на корневую причину (невалидный YAML).

**Симптомы**: `FATAL: owner_key not found` с неочевидной диагностикой, ручной поиск ошибки в YAML.

**Текущий workaround (не в репо)**: ручная проверка YAML глазами.

**Предлагаемый фикс**: в `node-lifecycle.sh` (шаг `step_11_read_node_yaml` или в начале любого шага, читающего node.yaml) добавить pre-flight валидацию:
```bash
if ! python3 -c "import yaml; yaml.safe_load(open('$NODE_YAML'))" 2>/dev/null; then
    echo "[IMP:10][node-lifecycle] FATAL: node.yaml is not valid YAML — check syntax at ${NODE_YAML}" >&2
    exit 1
fi
```
Fail-fast до попытки извлечения отдельных полей, с указанием точного пути к файлу.

---

## 3. Приоритеты

| Приоритет | Проблема | Блокирует | Сложность |
|-----------|----------|-----------|-----------|
| **P0** | S3 — PLATFORM_DOMAIN provisioning | nginx не стартует после bootstrap | Низкая (1 файл, ~10 строк) |
| **P0** | S2 — Vhost delivery в node-update | Проекты не видны nginx после node-update | Средняя (новый шаг в lifecycle, ~30 строк) |
| **P1** | S5 — YAML-валидация node.yaml | Неочевидная диагностика при порченом node.yaml | Низкая (1 файл, ~5 строк) |
| **P1** | S1 — NODE_YAML_PATH export | render-vhosts рендерит 0 файлов без явного export | Низкая (1 файл, 1 строка) |
| **P2** | S4 — Nginx без upstream | Nginx падает до деплоя проектов | Средняя (шаблон vhost + nginx.conf + harness, ~20 строк) |

---

## 4. Файлы, затрагиваемые фиксами

| Файл | S1 | S2 | S3 | S4 | S5 |
|------|:--:|:--:|:--:|:--:|:--:|
| `core/internal/scaffold/add-vhost.sh` | ✅ | — | — | ✅ | — |
| `core/internal/bootstrap/node-lifecycle.sh` | — | ✅ | ✅ | — | ✅ |
| `core/internal/bootstrap/deploy-modules.sh` | — | — | ✅ | — | — |
| `core/internal/provision-environment.sh` | — | — | ✅ | — | — |
| `core/modules/nginx/config/nginx.conf` | — | — | — | ✅ | — |
| `core/internal/bootstrap/remote-cmd.sh` | — | ✅ | — | — | — |

---

## 5. Acceptance Criteria

1. **AC-S2**: `make render-vhosts NODE=tronyx-vps && make node-update NODE=tronyx-vps` — vhost-файлы доставлены в `/opt/node-configs/tronyx-vps/overlays/nginx/`, nginx видит project vhosts
2. **AC-S3**: После `make node-update` nginx healthy, `PLATFORM_DOMAIN=tronyx.ru` подставлен в `prometheus.tronyx.ru`, `hermes.tronyx.ru`, `langfuse.tronyx.ru`, `loki.tronyx.ru` (валидируется через `docker compose exec nginx cat /etc/nginx/conf.d/prometheus-vhost.conf`)
3. **AC-S4**: Nginx healthy даже при отсутствии project-контейнеров (до `make deploy PROJECT=<dir>`). Project vhosts отдают 502 (graceful degradation), но nginx не падает
4. **AC-S5**: `make bootstrap-node NODE=<n>` с синтаксически невалидным node.yaml → exit 1 с сообщением `FATAL: node.yaml is not valid YAML` (не `owner_key not found`)
5. **AC-S1**: `make render-vhosts NODE=<n>` без ручного `export NODE_YAML_PATH` — рендерит все project vhosts из node.yaml
6. **AC-GATE**: `make gate MODE=fast` зелёный до и после всех изменений

$END_BRIEF
