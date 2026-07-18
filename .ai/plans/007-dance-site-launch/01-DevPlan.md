# GREP_SUMMARY: DevPlan, dance-site, first-deploy, reusable-workflow, ci-deploy-ssh, vhost, ssl, payload-delivery, TronyxLab
# $STATUS: ARCHIVED
# STRUCTURE: ▶ ┌root causes (6)┐ → ◇ decisions → ⊕ $TASKS (T1-T10) → ⊕ $PARALLEL_GROUPS (4 waves) → ⟦acceptance⟧ → ⎋ next steps
# $STATUS: ARCHIVED

$START_DEVPLAN

## $ARTIFACT_CONTRACT
- **PURPOSE:** Запустить первый полный деплой проекта dance-site: git push → CI (reusable workflow) → SSH forced-command → контейнер на VPS → сайт на https://sexydancerostov.ru.
- **DESCRIPTION:** Устранение 6 блокеров: (1) casing + access_level зеркала reusable workflow, (2) отсутствующий authorized_keys ci-deploy, (3) недоставленный vhost + отсутствующий SSL-серт, (4) `context: personal` в ai-platform.yaml, (5) published ports в compose (нарушение контракта платформы), (6) отсутствующий project payload в /opt/projects/ на VPS.
- **RATIONALE:** Q: почему не «поправить uses: в проекте»? A: потому что инвариант платформы и context-promote.sh ожидают строчное `<org>/ai-platform`; rename зеркала чинит все будущие проекты, uses: в deploy.yml уже корректен. Подтверждено пользователем.
- **ACCEPTANCE_CRITERIA:** CI run зелёный; `ssh ci-deploy@VPS` аутентифицируется (forced-command); `curl https://sexydancerostov.ru` возвращает лендинг dance-site (не «Platform Node»); контейнер dance-site healthy.
- **IMPLEMENTS:** Диагностический отчёт пользователя (5 ошибок) + 2 дополнительно найденных блокера (access_level=none, /opt/projects отсутствует).
- **IMPACTS:** GitHub org TronyxLab (rename + Actions access), dance-site (2 файла), VPS tronyx-vps (ci-deploy key, node-configs, SSL, /opt/projects/dance-site).
- **REQUIRES:** root SSH на tronyx-vps (работает: `ssh tronyx-vps`), gh CLI с admin-правами на TronyxLab, локальные ключи `~/.ssh/platform_personal_cicd`, WEBNAMES_API_KEY в SOPS-секретах ноды (уже есть — tronyx.ru выпущен).

---

## Requirements Analysis — критерии успеха

1. CI проекта резолвит reusable workflow `TronyxLab/ai-platform/.github/workflows/deploy-project.yml@main` (сейчас: 422 workflow not found).
2. `ci-deploy@103.88.243.151` аутентифицируется по ключу `platform_personal_cicd` с forced-command `deploy-project.sh tronyx-vps` (сейчас: `/home/ci-deploy/.ssh/` не существует).
3. VPS имеет: vhost `sexydancerostov.ru.conf` в nginx overlay, серт `/etc/letsencrypt/live/sexydancerostov.ru/`, payload в `/opt/projects/dance-site/` (сейчас: всё отсутствует).
4. `git push` в main dance-site → образ в ghcr.io → атомарный деплой с healthcheck → сайт отвечает.
5. Зона ответственности: проект + настройки GitHub-репо зеркала + операции на VPS. Код CI платформы НЕ меняется.

## Верифицированные root causes (не из отчёта — проверено в этой сессии)

| # | Факт | Доказательство |
|---|------|----------------|
| RC1 | Зеркало называется `TronyxLab/AI-platform`; Actions `access_level: none` — private-репо не расшаривает reusable workflows | `gh api repos/TronyxLab/AI-platform/actions/permissions/access` → `none`; `deploy-project.yml` в зеркале на main ЕСТЬ |
| RC2 | `/home/ci-deploy/.ssh/` не существует; юзер ci-deploy есть (uid 995, docker group); GHCR auth настроен (`/home/ci-deploy/.docker/config.json` есть) | ssh root инспекция |
| RC3 | root SSH работает (`ssh tronyx-vps`, ключ id_ed25519); закрыты только ci-deploy/tronyx | `ssh tronyx-vps 'echo ROOT_OK'` → OK |
| RC4 | Причина RC2: `bootstrap.sh` извлекает из node.yaml только `owner_key`; `ci_deploy_key` объявлен в node.schema.json, но НИКЕМ не читается — нужен env `PLATFORM_CI_DEPLOY_KEY` | grep по core/; см. §Debt D1 |
| RC5 | Серт только для tronyx.ru; vhost-файл есть локально в node-configs, на VPS не доставлен (доставка — только SCP-фаза bootstrap-node) | ssh root: `ls /etc/letsencrypt/live/` → только tronyx.ru |
| RC6 | `/opt/projects/` на VPS НЕ существует; `deploy-project.sh` требует `/opt/projects/<name>/docker-compose.yml` → деплой упадёт даже с рабочим CI и SSH | ssh root: `ls /opt/projects` → No such file; см. §Debt D2 |
| RC7 | ssl-provision выпустит серт автоматически: `_issue_project_certs` читает `projects[].domain` из node.yaml (sexydancerostov.ru зарегистрирован); NS обоих доменов — nameself.com (webnames) | grep ssl-provision.sh; `dig NS` |

## Data Flow (целевой)

```
git push main (dance-site)
  → deploy.yml: build-and-push → ghcr.io/tronyxlab/dance-site:<sha>
  → deploy: uses TronyxLab/ai-platform/.github/workflows/deploy-project.yml@main   [чинится T1+T2]
     → resolve NODE_HOST_MAP (org var, есть) → ssh ci-deploy@103.88.243.151       [чинится T5]
     → forced-command: /opt/platform/core/internal/deploy/deploy-project.sh tronyx-vps
        → cd /opt/projects/dance-site → pull → atomic up → healthcheck ≤60s        [чинится T6]
  → nginx overlay vhost sexydancerostov.ru.conf → proxy_pass http://dance-site:80  [чинится T5]
     → серт /etc/letsencrypt/live/sexydancerostov.ru/                              [чинится T5, RC7]
```

---

## $TASKS

### T1 — Rename зеркала (Sysadmin, complexity 1)
```
gh repo rename ai-platform --repo TronyxLab/AI-platform --yes
```
- **Acceptance:** `gh repo view TronyxLab/ai-platform --json name` → `ai-platform`.
- **Deps:** нет. **Риск:** нет — GitHub сохраняет redirect; context-promote.sh пушит на точное новое имя `<org>/ai-platform.git`.

### T2 — Actions access для reusable workflows (Sysadmin, complexity 1)
```
gh api -X PUT repos/TronyxLab/ai-platform/actions/permissions/access -f access_level=organization
```
- **Acceptance:** GET того же endpoint → `"access_level": "organization"`.
- **Deps:** T1 (использует новое имя; со старым тоже сработает через redirect).

### T3 — Фикс context в ai-platform.yaml (Coder, complexity 1)
Файл `~/projects/tronyx-lab/dance-site/ai-platform.yaml`: `context: personal` → `context: tronyx-lab`.
- **Acceptance:** grep `context: tronyx-lab`; совпадает с node.yaml (`context: tronyx-lab`).
- **Deps:** нет.

### T4 — Убрать published ports из docker-compose.yml (Coder, complexity 1)
Файл `~/projects/tronyx-lab/dance-site/docker-compose.yml`: удалить блок `ports: - "127.0.0.1:8082:80"`.
- **@rationale:** Q: зачем? A: контракт платформы (projects-root AGENTS.md §3): «НЕ публикуй порты — ingress делает nginx». Vhost проксирует через proxy-net на `dance-site:80`; host-порт 8082 не используется и попадёт под host_port-uniqueness-валидатор.
- **Acceptance:** в compose нет секции `ports:`; секции networks (proxy-net external) и healthcheck не тронуты.
- **Deps:** нет.

### T5 — Bootstrap-node: ci-deploy ключ + node-configs + SSL + модули (Sysadmin, complexity 5)
Из `~/projects/ai-platform` (перед запуском: `git status` чистый — SCP доставляет локальный core на прод-ноду):
```
PLATFORM_CI_DEPLOY_KEY="$(cat ~/.ssh/platform_personal_cicd.pub)" make bootstrap-node NODE=tronyx-vps
```
Что произойдёт: SCP core + node-configs (включая новый `sexydancerostov.ru.conf`) → init-шаги (идемпотентно) → step 6 допишет authorized_keys ci-deploy с forced-command → step 14 → update mode: ssl-provision (`_issue_project_certs` выпустит серт sexydancerostov.ru через DNS-01 webnames) → deploy-modules (overlay → nginx) → healthcheck.
- **Известный риск (checkpoint skip):** step `user-ci-deploy` может быть заскипан по `.done`-маркеру. Если после прогона `/home/ci-deploy/.ssh/authorized_keys` не появился → на VPS `rm /var/lib/platform/.bootstrap/user-ci-deploy*.done` (или соответствующий checkpoint-файл) и повторить bootstrap. НЕ писать authorized_keys вручную, пока канонический путь не исчерпан.
- **Acceptance (все через `ssh tronyx-vps`):**
  1. `cat /home/ci-deploy/.ssh/authorized_keys` → строка `command="/opt/platform/core/internal/deploy/deploy-project.sh tronyx-vps",restrict ssh-ed25519 ...platform_personal_cicd`;
  2. `ls /etc/letsencrypt/live/sexydancerostov.ru/fullchain.pem`;
  3. vhost присутствует в overlay, который смонтирован в nginx (`docker exec nginx ls /etc/nginx/conf.d/overlay/` или итоговый include-путь); `docker exec nginx nginx -t` → OK;
  4. все контейнеры платформы healthy (bootstrap healthcheck зелёный).
- **Deps:** нет (параллельно с T1-T4).

### T6 — Доставка project payload в /opt/projects/dance-site (Sysadmin, complexity 2)
```
ssh tronyx-vps 'mkdir -p /opt/projects/dance-site'
scp ~/projects/tronyx-lab/dance-site/docker-compose.yml ~/projects/tronyx-lab/dance-site/ai-platform.yaml tronyx-vps:/opt/projects/dance-site/
ssh tronyx-vps 'chown -R ci-deploy:ci-deploy /opt/projects/dance-site'
```
- **@rationale:** Q: почему SCP root'ом, а не git clone на VPS? A: платформа не имеет механизма первичной доставки project payload (Debt D2); SCP — канонический push-канал доставки без секретов/токенов на ноде; git-клон private-репо потребовал бы deploy-token на VPS.
- **Acceptance:** `ssh tronyx-vps 'ls /opt/projects/dance-site/'` → docker-compose.yml, ai-platform.yaml (версии ПОСЛЕ T3/T4).
- **Deps:** T3, T4 (payload должен содержать исправленные файлы).

### T7 — Верификация SSH-канала ci-deploy (QA/Sysadmin, complexity 1)
```
ssh -i ~/.ssh/platform_personal_cicd -o IdentitiesOnly=yes ci-deploy@103.88.243.151
```
- **Acceptance:** аутентификация проходит; forced-command запускает deploy-project.sh, который без `<project> <ref>` завершается `FATAL: invalid invocation` — это ОЖИДАЕМО и подтверждает работу канала. `Permission denied` = FAIL → вернуться к T5.
- **Deps:** T5.

### T8 — Push и прогон CI (Coder/владелец, complexity 2)
В `~/projects/tronyx-lab/dance-site`: закоммитить T3/T4 (сообщение в стиле репо), `git push origin main`.
```
gh run watch --repo TronyxLab/dance-site
```
- **Acceptance:** jobs `build-and-push` и `deploy` зелёные; образ `ghcr.io/tronyxlab/dance-site:<sha>` опубликован.
- **Deps:** T1, T2, T5, T6, T7.

### T9 — E2E-верификация сайта (QA, complexity 2)
1. `ssh tronyx-vps 'docker ps --filter name=dance-site --format "{{.Names}} {{.Status}}"'` → healthy;
2. `curl -sS https://sexydancerostov.ru/ | grep -i -m1 "<title>"` → title лендинга, НЕ «Platform Node»;
3. `curl -s https://sexydancerostov.ru/health` → 200;
4. из ai-platform: `make project-status NAME=dance-site` — больше не падает по SSH.
- **Deps:** T8.

### T10 — Фиксация Debt-наблюдений (Architect, complexity 1)
Записи уже в `02-Debt.md` (см. рядом). Код платформы НЕ трогать (вне зоны задачи).
- **Deps:** нет.

## $PARALLEL_GROUPS

### Wave 1 (независимые)
- Tasks: T1→T2 (последовательно, GitHub), T3, T4, T5, T10
### Wave 2
- Tasks: T6 (после T3+T4), T7 (после T5)
### Wave 3
- Tasks: T8 (после T1,T2,T5,T6,T7)
### Wave 4
- Tasks: T9 (после T8)

**Критический путь:** T5 → T7 → T8 → T9.

## Acceptance Criteria (сводная)

| # | Критерий | Проверка |
|---|----------|----------|
| A1 | Reusable workflow резолвится | CI run: job deploy стартует (не 422) |
| A2 | ci-deploy SSH работает | T7: forced-command отвечает |
| A3 | Серт + vhost активны | `curl -v https://sexydancerostov.ru` → серт CN=sexydancerostov.ru |
| A4 | Сайт задеплоен | title лендинга, контейнер healthy |
| A5 | Конфиг-консистентность | context=tronyx-lab везде; ports отсутствуют |

## File Manifest

| Файл | Изменение |
|------|-----------|
| `~/projects/tronyx-lab/dance-site/ai-platform.yaml` | context: personal → tronyx-lab (T3) |
| `~/projects/tronyx-lab/dance-site/docker-compose.yml` | удалить ports (T4) |
| GitHub: TronyxLab/AI-platform | rename → ai-platform + access_level=organization (T1, T2) |
| VPS: /home/ci-deploy/.ssh/authorized_keys | через bootstrap-node (T5) |
| VPS: /opt/projects/dance-site/ | payload SCP (T6) |
| VPS: /etc/letsencrypt/live/sexydancerostov.ru/, nginx overlay | через bootstrap-node (T5) |

## $TEST_SPEC
`$TEST_SPEC: NONE — @rationale:` конфигурационно-операционная задача без нового бизнес-кода; верификация — операционные acceptance-проверки T7/T9 (E2E). Существующие статические тесты платформы (`test_project_ci_contract.py`) не затрагиваются — код CI не меняется.

## Design Decisions

### DD1 — Rename зеркала vs правка uses: (подтверждено пользователем)
`## @rationale` Q: почему rename? A: инвариант платформы и context-promote.sh хардкодят `<org>/ai-platform` (строчными); зеркало создано с отклонением. Rename + `access_level=organization` чинит текущий и все будущие проекты; uses: в deploy.yml проекта уже корректен; GitHub держит redirect. Rejected: правка uses: на `AI-platform` — оставляет drift, каждый новый проект ломался бы.

### DD2 — Канонический bootstrap-node vs ручная правка authorized_keys
`## @rationale` Q: почему не записать ключ руками за 10 секунд? A: `make bootstrap-node` — канонический идемпотентный путь (инвариант №6), он же доставляет node-configs (vhost), выпускает серт и прогоняет healthcheck — 4 проблемы одной операцией. Ручная правка — только fallback при checkpoint-skip.

### DD3 — SCP payload vs git-канал (см. T6 @rationale)

## Debt Registry (наблюдения — БЕЗ фиксов, вне зоны задачи)
См. `02-Debt.md`: D1 (ci_deploy_key из node.yaml никем не читается), D2 (нет механизма доставки project payload в /opt/projects; tronyx-site живёт в /opt/tronyx-lab/tronyx-site — его деплой через reusable workflow тоже сломан), D3 (adopt-project.sh записал context: personal и repo с неверным регистром).

## Next Steps

### Wave 1
`Use sysadmin role, execute T1, T2, T5 from .ai/plans/007-dance-site-launch/01-DevPlan.md` · `Use coder role, execute T3, T4 from .ai/plans/007-dance-site-launch/01-DevPlan.md`
### Wave 2
`Use sysadmin role, execute T6, T7 from .ai/plans/007-dance-site-launch/01-DevPlan.md`
### Wave 3-4
`Execute T8 (push + gh run watch), then QA verify T9 from .ai/plans/007-dance-site-launch/01-DevPlan.md`

$END_DEVPLAN
