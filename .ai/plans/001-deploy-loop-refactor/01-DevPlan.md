$START_DEVPLAN

## $ARTIFACT_CONTRACT

| Поле | Значение |
|------|----------|
| **PURPOSE** | Устранить системный класс ручных правок в деплой-контуре: детерминированный hostname upstream без `container_name`, конвергенция генератора vhost на ленивый DNS-резолвинг, nginx-reload hook, project-compose gate, авто-skip macOS-специфичных smoke-тестов, `make verify`, CI-замыкание доставки через `platform-deliver`. |
| **DESCRIPTION** | Тактический рефакторинг деплой-контура (Option A+D из суперпозиции). 8 атомарных задач: network alias в шаблонах, variable-based proxy_pass в add-vhost.sh, nginx reload hook в deploy, валидационный gate на project docker-compose.yml, авто-skip env-specific smoke на macOS, make verify NODE= для пост-деплойных HTTPS-проверок, CI доставка через platform-deliver. Option C (Traefik) выносится в TRAP[DEBT]. |
| **RATIONALE** | 4 ручные правки в сессии деплоя tronyx-vps — симптомы единого дефекта: рассинхронизация proxy/compose/deploy слоёв. Каждая правка должна либо детектиться локальным gate, либо применяться автоматически. |
| **ACCEPTANCE_CRITERIA** | (1) `make new-project` + deploy → nginx стартует и резолвит upstream без ручного `container_name`. (2) nginx не падает при старте, если upstream ещё не поднят (ленивый DNS). (3) nginx перезагружается автоматически после project-deploy. (4) `make gate MODE=fast` FAIL'ит если в project-compose есть `ports:` или отсутствует `proxy-net` alias. (5) `make test MARKER=smoke` на macOS пропускает 2 env-specific теста с явной диагностикой. (6) `make verify NODE=tronyx-vps` выдаёт HTTP-статусы всех expose:true доменов. |
| **IMPLEMENTS** | — |
| **IMPACTS** | `templates/template-*/docker-compose.yml`, `core/internal/scaffold/add-vhost.sh`, `core/modules/nginx/module.yaml`, `core/internal/deploy/deploy-project.sh`, `tests/gates/`, `tests/test_smoke_nginx.py`, `tests/_conftest/smoke.py`, `Makefile`, `core/entrypoints/verify.sh`, CI workflow |
| **REQUIRES** | Docker daemon, Python 3.10+, bash 4+, curl |

---

## Requirements Analysis — 5 Key Success Criteria

1. **Zero manual DNS fixes.** После `git push` и deploy контейнеры резолвятся nginx без настройки `container_name` руками.
2. **nginx never crash-loops on deploy.** Ленивый DNS-резолвинг (как уже в платформенных vhost после TRAP[BUG] 2026-07-16) распространён на проектные vhostы.
3. **Project compose errors caught locally.** `make gate MODE=fast` детектит `ports:`, отсутствие `proxy-net`, отсутствие `env_file` до деплоя.
4. **macOS smoke gives clear signal, not blocker.** 2 env-specific smoke-теста авто-skip'аются с диагностикой, Linux CI сохраняет полное покрытие.
5. **Post-deploy verification is one command.** `make verify NODE=` — единая точка пост-деплойной верификации.

---

## Architecture Overview — Draft Code Graph

```
make new-project PROJECT=foo ───────── generator ───▶ template-*/docker-compose.yml
                                                        ├── networks.proxy-net: name: proxy-net (external)
                                                        │     aliases: ["foo"]   ◀── NEW: детерминированный hostname
                                                        ├── env_file: .env.platform
                                                        └── NO ports: mapping

                          ── add-vhost.sh ──────────▶ overlays/nginx/foo.conf
                                                        ├── set $upstream_foo foo:80;     ◀── CHANGED: переменная
                                                        └── proxy_pass http://$upstream_foo;  ◀── CHANGED: ленивый DNS

tests/gates/test_gate_project_compose.py ◀── NEW:
  ▶ validate compose-yml ┌ports┐ → FAIL if found
  ◇ check proxy-net attach + alias → FAIL if missing
  ⊕ check env_file: .env.platform → FAIL if missing

make verify NODE=<node> ◀── NEW:
  ▶ read node.yaml → collect domains: expose:true
  ◇ foreach domain: curl -sS -o /dev/null -w '%{http_code}\n' https://${domain}
  ⊕ sum results → exit 0 if all 200, exit 1 otherwise

CI workflow (context):
  ▶ build → push ghcr → platform-deliver tar → deploy-project.sh
    └─ _trigger_deploy_hooks → nginx -t && nginx -s reload  ◀── NEW hook
    └─ make verify NODE=<node>  ◀── NEW CI step
```

---

## Step-by-Step Data Flow (Post-Refactor Target State)

```
1. Developer: make new-project PROJECT=foo DOMAIN=foo.example.com
   └─▶ scaffold.sh generates compose with proxy-net alias "foo"
   └─▶ add-vhost.sh generates nginx conf with set $upstream_foo foo:80 + resolver

2. Developer: git push (CONTEXT CI)
   └─▶ CI gate MODE=fast (includes test_gate_project_compose → catches any drift)
   └─▶ build docker image → push ghcr.io/<org>/foo:latest
   └─▶ CI step: ssh ci-deploy@<node> 'platform-deliver foo' < foo-payload.tar.gz
       └─▶ deploy-project.sh handle_deliver() → extract → validate → atomic mv
       └─▶ deploy-project.sh handle_deploy() → docker compose up -d
           └─▶ healthcheck poll (≤60s)
           └─▶ _trigger_deploy_hooks("on_project_deploy") → nginx reload

3. CI: make verify NODE=<node>
   └─▶ curl https://foo.example.com/ → 200 ✓
   └─▶ exit 0 → deploy success
```

---

## §TASKS

| ID | Task | Role | Output | Dependencies | Complexity |
|----|------|------|--------|-------------|------------|
| **TASK-1** | Добавить network alias в шаблоны проектов | Coder | `templates/template-{frontend,backend,fullstack}/docker-compose.yml` — секция `networks.proxy-net.aliases: ["__PROJECT_NAME__"]` | None | 2 |
| **TASK-2** | Конвергенция add-vhost.sh на ленивый DNS-резолвинг | Coder | `core/internal/scaffold/add-vhost.sh` — замена `proxy_pass http://${project_name}:80` на `set $upstream_<N> ...; proxy_pass http://$upstream_<N>;` | None | 3 |
| **TASK-3** | nginx reload hook в модуль + deploy | Coder | `core/modules/nginx/module.yaml` + `core/internal/deploy/deploy-project.sh` — объявление `hooks.on_project_deploy`, вызов `docker compose exec nginx nginx -s reload` | TASK-1 (alias needed for resolution) | 3 |
| **TASK-4** | Валидационный gate на project docker-compose.yml | Coder | `tests/gates/test_gate_project_compose.py` — 3 проверки: ports запрещены, proxy-net с alias обязателен, env_file обязателен | None | 4 |
| **TASK-5** | Авто-skip env-specific smoke на macOS | Coder | `tests/test_smoke_nginx.py` + `tests/_conftest/smoke.py` — `@pytest.mark.skipif(Darwin)` с явным rationale + ссылкой на CI Linux-parity | None | 2 |
| **TASK-6** | `make verify NODE=` пост-деплойная верификация | Coder | `Makefile` (новый target `verify`) + `core/entrypoints/verify.sh` — чтение node.yaml → curl всех `expose: true` доменов | None | 3 |
| **TASK-7** | CI-замыкание: platform-deliver + verify | Coder | `.github/workflows/deploy-project.yml` (reusable) — шаг deliver over forced-command + verify | TASK-6 | 2 |
| **TASK-8** | TRAP[DEBT] на Option C (Traefik/label-proxy) | Architect | `02-Debt.md` — документирование north-star архитектуры с label-based dynamic proxy | None | 1 |

### Critical Path

```
TASK-2 (add-vhost) ────┐
TASK-1 (alias) ────────┤
TASK-4 (gate) ─────────┤──▶ TASK-3 (hook) ──▶ TASK-6 (verify) ──▶ TASK-7 (CI)
TASK-5 (macOS skip) ───┘
TASK-8 (DEBT) — independent
```

---

## $PARALLEL_GROUPS

### Wave 1 (independent, no shared files)
- Tasks: TASK-1, TASK-2, TASK-4, TASK-5
- Command: `coder Read DevPlan.md, implement Wave 1: TASK-1, TASK-2, TASK-4, TASK-5`

### Wave 2 (depends on Wave 1)
- Tasks: TASK-3 (depends on TASK-1 alias + TASK-2 variable-based proxy)
- Command: `coder Read DevPlan.md, implement Wave 2: TASK-3`

### Wave 3 (independent of Wave 2, can run parallel with Wave 2)
- Tasks: TASK-6 (verify), TASK-8 (DEBT)
- Command: `coder Read DevPlan.md, implement Wave 3: TASK-6 && architect implement TASK-8`

### Wave 4 (depends on TASK-6 verify script)
- Tasks: TASK-7 (CI integration)
- Command: `coder Read DevPlan.md, implement Wave 4: TASK-7`

---

## Acceptance Criteria (Summary Table)

| # | Критерий | Проверка |
|---|----------|----------|
| AC-1 | Новый проект из шаблона имеет `proxy-net` alias = project name | `grep aliases: templates/template-*/docker-compose.yml` |
| AC-2 | add-vhost.sh генерирует variable-based proxy_pass | `grep 'set \$upstream' overlays/nginx/*.conf` после `make new-project` |
| AC-3 | nginx reload вызывается автоматически после project-deploy | LLD-логи в `deploy-project.sh` или `docker compose logs nginx` |
| AC-4 | `make gate MODE=fast` FAIL если project-compose содержит `ports:` | тест `test_gate_project_compose_ports_forbidden` RED на нарушителе |
| AC-5 | `make test MARKER=smoke` на macOS SKIP 2 env-specific теста, не FAIL | pytest output: `SKIPPED [2] macOS: Linux-parity in CI` |
| AC-6 | `make verify NODE=tronyx-vps` возвращает HTTP 200 для всех expose-доменов | exit code 0 + output per domain |
| AC-7 | CI пайплайн доставляет payload через `platform-deliver` без ручного SCP | CI logs: `platform-deliver <project>` |
| AC-8 | TRAP[DEBT] на Option C записан в `.ai/plans/001-deploy-loop-refactor/02-Debt.md` | Файл существует, содержит rationale и north-star спецификацию |

---

## File Manifest

| # | File | Change | Task |
|---|------|--------|------|
| 1 | `templates/template-frontend/docker-compose.yml` | +`aliases: ["__PROJECT_NAME__"]` в `networks.proxy-net` | TASK-1 |
| 2 | `templates/template-backend/docker-compose.yml` | +`aliases: ["__PROJECT_NAME__"]` в `networks.proxy-net` | TASK-1 |
| 3 | `templates/template-fullstack/docker-compose.yml` | +`aliases: ["__PROJECT_NAME__"]` в `networks.proxy-net` | TASK-1 |
| 4 | `core/internal/scaffold/add-vhost.sh` | `proxy_pass http://${project_name}:80` → `set $upstream_<N> ...; proxy_pass http://$upstream_<N>;` | TASK-2 |
| 5 | `core/modules/nginx/module.yaml` | +`hooks.on_project_deploy: nginx_reload` | TASK-3 |
| 6 | `core/internal/deploy/deploy-project.sh` | Вызов `_trigger_deploy_hooks("on_project_deploy")` после успешного deploy (уже существует, только hook объявить) | TASK-3 |
| 7 | `tests/gates/test_gate_project_compose.py` | **NEW** — 3 теста: no ports, proxy-net alias, env_file | TASK-4 |
| 8 | `tests/test_smoke_nginx.py` | +`@pytest.mark.skipif(sys.platform == 'darwin', reason=...)` на cert-стадию | TASK-5 |
| 9 | `tests/_conftest/smoke.py` | Возможно +helper `is_macos()` если ещё нет | TASK-5 |
| 10 | `Makefile` | +target `verify` | TASK-6 |
| 11 | `core/entrypoints/verify.sh` | **NEW** — curl всех expose:true доменов из node.yaml | TASK-6 |
| 12 | `.github/workflows/deploy-project.yml` (reusable) | +шаг `platform-deliver` + `verify` | TASK-7 |
| 13 | `.ai/plans/001-deploy-loop-refactor/02-Debt.md` | **NEW** — TRAP[DEBT] Option C north-star | TASK-8 |

---

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/gates/test_gate_project_compose.py` | `test_no_ports_published` | Project compose содержит `ports:` → FAIL | Docker Compose project validator |
| `tests/gates/test_gate_project_compose.py` | `test_proxy_net_with_alias` | Project compose не имеет `proxy-net` с alias → FAIL | Docker Compose project validator |
| `tests/gates/test_gate_project_compose.py` | `test_env_file_platform_present` | Project compose не имеет `env_file: .env.platform` → FAIL | Docker Compose project validator |
| `tests/gates/test_gate_project_compose.py` | `test_valid_project_passes` | Корректный шаблонный compose → PASS | Docker Compose project validator |
| `tests/test_smoke_nginx.py` | `existing test_nginx_responds` | macOS: cert generation skipped with reason | Smoketest isolation |
| `tests/gates/test_gate_add_vhost_pattern.py` | `test_variable_based_proxy_pass` | Сгенерированный vhost содержит `set $upstream` + `proxy_pass http://$upstream` | add-vhost.sh generator |

---

## Design Decisions

### ## @rationale: Network alias вместо container_name

**Q:** Почему не `container_name` (как патчили в сессии)?
**A:** Потому что `container_name` — глобальное имя Docker-демона, и оно создаёт конфликт имён между разными compose-проектами. Если два проекта захотят `container_name: myapp`, второй упадёт. Network alias — детерминированный hostname **внутри сети** без глобальной коллизии.

### ## @rationale: Variable-based proxy_pass с resolver, а не статический upstream-блок

**Q:** Почему не `upstream tronyx-site { server tronyx-site:80; }`?
**A:** Статический `upstream`-блок резолвится nginx на этапе загрузки конфига — если контейнер не существует, nginx падает (restart-loop из сессии). Variable-based `proxy_pass` с `resolver 127.0.0.11` резолвит имя на каждый запрос — nginx стартует даже если upstream временно недоступен. Это уже доказано в platform-vhosts (TRAP[BUG] 2026-07-16).

### ## @rationale: Project-compose gate, а не runtime-валидация

**Q:** Почему gate (статический анализ), а не проверка на VPS перед deploy?
**A:** Fail-fast на уровне разработчика (локально, `make gate MODE=fast`) быстрее и дешевле, чем на VPS. Аналог: существующий `check-compose-spec` в pre-commit. Расширяем ту же парадигму на бизнес-контракты.

### ## @rationale: macOS smoke skip вместо исправления

**Q:** Почему не починить тесты для macOS?
**A:** Корневая причина (mkcert, Docker Desktop bind-mount) — в ограничениях платформы, не в коде. CI уже гоняет те же smoke-тесты на Linux (ubuntu-latest runner, `platform-test.yml`). Auto-skip на macOS с явной диагностикой «Linux-parity in CI» убирает ручное решение «acknowledge and proceed», не теряя покрытие.

---

## Next Steps

### Wave 1
Use coder role and read `.ai/plans/001-deploy-loop-refactor/01-DevPlan.md`, implement Wave 1: TASK-1, TASK-2, TASK-4, TASK-5

### Wave 2
Use coder role and read `.ai/plans/001-deploy-loop-refactor/01-DevPlan.md`, implement Wave 2: TASK-3

### Wave 3
Use coder role and read `.ai/plans/001-deploy-loop-refactor/01-DevPlan.md`, implement Wave 3: TASK-6 && architect implement TASK-8

### Wave 4
Use coder role and read `.ai/plans/001-deploy-loop-refactor/01-DevPlan.md`, implement Wave 4: TASK-7

---

## TRAP[DEBT] North-Star

Option C (label-based dynamic proxy: Traefik или nginx-proxy с Docker labels) — архитектурно убивает весь класс проблем upstream/resolver/reload/ordering. Выносится в отдельный `02-Debt.md` в этой же папке для будущего рассмотрения.

$END_DEVPLAN
