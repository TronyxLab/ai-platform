$START_DEVPLAN

# DevPlan 031 — Оценка ночного прогона 029/030: первопричина wipe, silent-skip F2, honesty-хвосты

## $ARTIFACT_CONTRACT

| Поле | Значение |
|------|----------|
| PURPOSE | Закрыть остаточную первопричину ночного прогона 2026-09-03: (1) незакоммиченные fix-файлы — consistency hazard (ноды несут исправленный код, source-репозиторий — старый; чистый checkout → `node-update` откатит фиксы); (2) amplifier-баг F2 — `install_overlay_deploy_key_node_side` молча WARN-skip'ает (exit 0) при отсутствии dev-ключа, из-за чего «ключ вне канона» разросся до production-outage; (3) первопричина F8 — нода дважды теряет docker-состояние за ночь — не раскрыта; (4) honesty-хвосты F5/F6/F7/F9. |
| DESCRIPTION | (1) **Commit+CI:** закоммитить и запushить 3 fix + 3 test-файла ночи (`context_initializer.py`, `lifecycle/cli.py`, `converge/networks.py` + тесты) — прецедент 91ed43c; (2) **F2 hardening:** fail-loud когда `repos.core` = `git@github.com-overlay:` но dev-ключа нет в `~/projects/<ctx>/.secrets/`; (3) **F8 форензика:** read-only диагностика docker-слоя ноды (journalctl, docker events, `docker system df`) + provider-консоль (owner-gated) → документировать причину + prevention-мониторинг; (4) **honesty-хвосты:** F5 wildcard-coverage в cert-unit (жжёт ACME quota), F6 reload-reorder, F9 `make status NODE=` fail-loud, F7 probe-семантика. |
| RATIONALE | Ночной прогон довёл обе ноды до зелёного честно, НО: прямая причина F1/F2 решена, amplifier (silent-skip) и глубинная причина (node wipe) — нет. Оставленный незакоммиченным код создаёт divergence «ноды исправлены / репо сломан» — следующий `core-deliver`/`node-update` с чистого дерева откатит фиксы. Повторяющийся wipe (вчера + сегодня) без root-cause = повторное пожарное тушение. |
| ACCEPTANCE_CRITERIA | (1) `git status` чист; фиксы ночи закоммичены и запushены, CI green; (2) unit-тест: `repos.core` SSH-алиас + отсутствие dev-ключа → installer exit ≠ 0 с читаемой ошибкой (не молча WARN); (3) F8: причина wipe документирована (TRAP[INCIDENT]/Debt) ИЛИ явно эскалирована владельцу с provider-консолью; prevention-метрика/алерт заведён; (4) F5: converge cert-unit пропускает домен, покрытый wildcard (reuse `cert_covers_domain`) — `make converge` на asi не ре-выпускает self-signed roadmap.asiteam.ru; (5) F9: `make status NODE=<remote>` → fail-loud (как healthcheck); (6) `make check` зелёный + `make agent-check` exit 0. |
| IMPLEMENTS | Закрытие остаточных пунктов 029-deploy-integrity/06-overnight-report.md (F5–F9) + amplifier-первопричины F2 + первопричины F8. |
| IMPACTS | `core/internal/scaffold/context_initializer.py` (F2 fail-loud), `core/internal/bootstrap/cert_orchestrator.py` (F5), `core/internal/bootstrap/converge/*.py` (F6), `makefiles/modules.mk` + `core/internal/scaffold/status-*` (F9), тесты `tests/unit/*`, возможный новый monitoring-модуль/alert (F8 prevention). |
| REQUIRES | Commit/push-авторизация владельца (фиксы ночи отложены на владельца); SSH read-only к `tronyx-vps`/`asi-team-vps` для F8-форензики; консоль провайдера ноды (owner-only, вне агентского доступа). |

---

## 1. Requirements Analysis

### 1.1 Верифицированные факты (сверено с кодом/git, 2026-09-03)

| # | Факт | Источник |
|---|------|----------|
| F1 | В working tree 3 незакоммиченных fix-файла (`context_initializer.py`, `lifecycle/cli.py`, `converge/networks.py`) + 3 тест-файла. Отчёт сам фиксирует: «в git ждут коммита владельца». | `git status` |
| F2 | Ноды несут исправленный код (доставлен `node-update`/rsync core), source-репо — старый. Divergence: чистый checkout → `make core-deliver`/`node-update` откатит F1/F3/F4 на нодах. | 06-overnight-report.md L87-90 |
| F3 | Клон overlay исполняется как **root**: `deploy-modules.sh:29` (`must run as root`) → `exec python3 deploy_orchestrator.py` → `_preflight` → `context_overlay.ensure_context_repo` → `git clone`. Инсталлер ставит ключ/алиас/TOFU на `root@<node>` (`ssh root@<host> bash -s`, `$HOME`=/root). | deploy-modules.sh, deploy_orchestrator.py:338, context_initializer.py:563 |
| F4 | Утверждение журнала «deploy-modules.sh (clone runner) executes as ci-deploy» (строка 130) — **ложный след**: `ci-deploy` исполняет `receive` (project payload), НЕ overlay-clone. `context_deployer.py` chown'ит только `.deploy-snapshots` для ci-deploy. Первопричина F1/F2 решена верно. | execution-journal.md:130, context_deployer.py:582-597 |
| F5 | F2 amplifier: `install_overlay_deploy_key_node_side` при отсутствии dev-ключа → WARN + exit 0 («ретро-контекст без dev-ключа → WARN»). Именно этот silent-skip дал «алиас не установлен» → production-outage. | context_initializer.py, bootstrap/AGENTS.md §VPS-доступ |
| F6 | F8 (wipe) рекуррентен: «как F8/F16 вчера». tronyx потерял 12 named volumes + 4 проектных payload + `/opt/<ctx>/platform` + `known_hosts` за ночь при живых bind-dirs. | 06-overnight-report.md L59, execution-journal.md:75,116 |
| F7 | Кандидат причины wipe: ежемесячный `docker system prune -af --filter until=720h` cron (`/etc/cron.d/platform-prune`, день 1, 04:00). НО `-af` БЕЗ `--volumes` НЕ удаляет named volumes — cron объясняет максимум остановленные контейнеры/неиспользуемые образы, НЕ потерю volumes. Вероятен provider-level wipe / docker daemon reset. | system.py:831-836 |
| F8 | F5: converge cert-unit каждый прогон ре-выпускает unused self-signed `roadmap.asiteam.ru` (acme-попытка + TG-алерт) — wildcard `*.asiteam.ru` уже покрывает. Helper `cert_covers_domain` уже есть (plan 030). | execution-journal.md:275,293, ssl_certs.py |
| F9 | F9: `make status NODE=<remote>` — local-only, NODE молча игнорируется (пустая таблица ≠ нода). `make healthcheck NODE=` уже fail-loud'ит — прецедент для честности. | execution-journal.md:66-69,96-98 |

### 1.2 Корневая причина (двухуровневая)

1. **Уровень прямого отказа (решён):** F1 — wiped `known_hosts` (TOFU-pin root) + F2 — ключ вне канона (перенос в канон). Верифицировано против кода: клон идёт как root, инсталлер ставит на root. ✅
2. **Уровень amplifier (НЕ решён):** silent WARN-skip в `install_overlay_deploy_key_node_side` — отсутствие ключа трактуется как «наверное уже установлено вручную», exit 0. Превращает «забыли положить ключ» в «production outage без сигнала».
3. **Уровень глубинной причины (НЕ решён):** нода спонтанно теряет docker-состояние (volumes + payload + clone + known_hosts) при живых bind-dirs — рекуррентно. Восстановление (redeploy) лечит симптом, причина не установлена. Кандидат-крон объясняет только часть.

### 1.3 Решения (superposition, auto-collapsed)

**D1. Незакоммиченные фиксы — Option A: commit + push (прецедент 91ed43c).**
- ✅ Выбрано: `fix(029/030): overlay known_hosts TOFU + report stale-records + R4 container warn` — коммит фиксов ночи + push + CI-gate. Устраняет divergence «ноды/репо».
- Rejected: оставить — риск отката фиксов на нодах при следующем core-deliver с чистого дерева; commit без push — source зафиксирован, но CI не валидирует.

**D2. F2 amplifier — Option A: fail-loud.**
- ✅ Выбрано: если `repos.core` содержит `git@github.com-overlay:` и dev-ключ отсутствует в `~/projects/<ctx>/.secrets/` → installer exit ≠ 0 с читаемой ошибкой (не молча WARN). Ретро-контекст БЕЗ SSH-алиасного repos.core остаётся exit 0 skip.
- Rejected: оставить WARN-skip — F2 повторится молча; автоматическая попытка keygen (выдал бы ключ без GH-регистрации — бессмысленно).

**D3. F8 wipe — Option A: read-only форензика + prevention, provider-консоль owner-gated.**
- ✅ Выбрано: SSH read-only (`journalctl -u docker`, `docker system df -v`, `docker events --since`, `ls -la /var/lib/docker/volumes`, проверка `/etc/cron.d/platform-prune` last-run) на обеих нодах → документировать. Provider-консоль (биллинг/события) — эскалация владельцу. Prevention: метрика/алерт на падение числа docker-объектов (сравнение `docker system df` / списка volumes между прогонами) ИЛИ watch-cron.
- Rejected: только prevention (лечит симптом, не причину); игнорировать (третий wipe = третий ночной пожар).

**D4. F5 cert-churn — Option A: wildcard-coverage skip в cert-unit.**
- ✅ Выбрано: в converge cert-unit применить `cert_covers_domain` (уже есть) — домен, покрытый wildcard родителя, НЕ идёт в issue-path и не генерит self-signed. Убирает ACME-quota burn + TG-алерт.
- Rejected: чистить S3-запись вручную (одноразово, рецидив останется).

**D5. F9 `make status NODE=` — Option A: fail-loud.**
- ✅ Выбрано: `NODE` задан ≠ local → exit ≠ 0 + сообщение «для удалённой ноды используйте e2e-verify / project-status» (зеркало healthcheck-контракта).
- Rejected: remote-режим status (дороже, отложить); оставить (honesty-дыра).

**D6. F6/F7 — F6 reorder (reload после всех R-units) берём; F7 (probe rc=1) — диагностика, решение после форензики.**

---

## 2. Draft Code Graph

```
TASK-1 (commit)      git add/commit/push фиксов ночи (owner-gated) → CI green
TASK-2 (F2)          install_overlay_deploy_key_node_side:
                       read repos.core → if "git@github.com-overlay:" AND dev-key missing
                       → fail-loud (exit 10) с remediation; else WARN-skip (repos.core НЕ alias)
TASK-3 (F8)          read-only node forensics (journalctl/docker events/volumes/cron last-run)
                       → TRAP[INCIDENT] + prevention-метрика/алерт
TASK-4 (F5)          cert_orchestrator converge path: cert_covers_domain(domain, parent_wildcard)
                       → skip issue-path (не self-signed, не acme, не TG)
TASK-5 (F6)          converge: defer nginx reload до завершения R-units (post-reconcile)
TASK-6 (F9)          makefiles/modules.mk + status entrypoint: NODE≠local → fail-loud
TASK-7 (F7)          диагностика liveness probe rc=1 → решение (documented or code)
```

## 3. Tasks

| # | Задача | Выход | Severity |
|---|--------|-------|----------|
| T1 | Commit+push фиксов ночи (3+3 файла) | чистый git, CI green | HIGH |
| T2 | F2 fail-loud (silent-skip → exit 10 при SSH-алиас + отсутствии ключа) | +unit-тест, `make check` | HIGH |
| T3 | F8 форензика + prevention | TRAP[INCIDENT]/Debt + алерт | HIGH |
| T4 | F5 wildcard-coverage skip в cert-unit | +unit-тест, converge asi без self-signed churn | MED |
| T5 | F6 reload-reorder | +unit-тест, downtime window закрыт | MED |
| T6 | F9 status NODE= fail-loud | +unit-тест | LOW |
| T7 | F7 probe rc=1 диагностика | решение (code/doc) | LOW |

## 4. Verification

`make check` (батч) → `make agent-check` → на нодах: `make converge NODE=asi-team-vps` (F5: без self-signed churn), `make status NODE=asi-team-vps` (F9: fail-loud). F8 — отчёт с причиной или эскалацией.

$END_DEVPLAN
