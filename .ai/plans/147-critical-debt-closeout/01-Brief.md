# 147-critical-debt-closeout — 01-Brief.md

$START_BRIEF

$ARTIFACT_CONTRACT
PURPOSE:               Зафиксировать и описать проблему незакрытых критичных пунктов технического долга платформы ai-platform после волны 145 (реестр v2.0): операторские блокеры и дедлайны, требующие решения. Бриф НЕ содержит решений — только факты, доказательства, влияния и дедлайны.
DESCRIPTION:           Реестр 145 (v2.0, 2026-08-11) насчитывает 10 OPEN + 3 partially-done пунктов. Из них 5 — критичные для эксплуатации: (1) утраченная приватная пара ci-deploy (D-142-R15-B29, HI) — оба задокументированных пути восстановления фактически невозможны (проверено 2026-08-11: локальной пары ~/.ssh/platform_personal_cicd нет, gh-секрета CI_DEPLOY_KEY нет); (2) отсутствующие DOCKER_HUB_USERNAME/DOCKER_HUB_TOKEN в gh-секретах (D-142-R17, MED) — механизм docker/login-action в platform-test.yml есть, секретов нет, CI-джобы platform-test/Build Platform Agent красные; (3) невыполненный DR-drill AGE мастер-ключа (D-136-W10-S-13, HI, дедлайн 2026-08-31); (4) невыполненное chaos-окно (D-126-D5, D-126-T9-T11, D-142-Chaos-T6-T10, D-136-B7 — дедлайн 2026-09-15, T6-T10 формально RED, T9-T11 не запускались); (5) непроверенный/неудалённый legacy forced-command deploy.sh (D-139-T1, HI, дедлайн 2026-11-01).
RATIONALE:             Кодовая часть долга закрыта волной 145 W2/W3/W4 (20 пунктов, make check GREEN). Оставшиеся пункты — операторские/оконные: требуют действий оператора (секреты, SSH, окна обслуживания, реальные LE-домены), не код-фиксов. Без их закрытия: недоступен штатный канал `make deploy-project`/e2e MODE=remote, CI-джобы остаются красными (rate-limit), отсутствует проверенный сценарий восстановления AGE-ключа (риск полной потери доступа к секретам при потере ноды), chaos-покрытие T6-T11 остаётся формально неверифицированным, а legacy-канал deploy.sh остаётся недокументированной точкой входа.
ACCEPTANCE_CRITERIA:   (1) Проблема воспроизводимо зафиксирована фактами и доказательствами (состояние секретов, ключей, CI, дедлайнов) на дату 2026-08-11. (2) Все заинтересованные стороны и зоны влияния перечислены. (3) Дедлайны и severity каждого пункта зафиксированы в одном месте. (4) Бриф не содержит предписанных решений — выбор способа закрытия остаётся за оператором/DevPlan.
IMPLEMENTS:            Наблюдения проверки остатков реестра 145 (2026-08-11): gh secret list, отсутствие ~/.ssh/platform_personal_cicd, статусы CI, состояние chaos-прогонов 141/142.
IMPACTS:               tronyx-vps (production), CI-джобы (platform-test, Build Platform Agent), каналы деплоя (`make deploy-project`, e2e MODE=remote), процесс восстановления секретов, chaos-покрытие T6-T11.
REQUIRES:              — (фактологический бриф; решение — последующим DevPlan/решениями оператора).
$END_ARTIFACT_CONTRACT

## 1. Контекст

**Источник проблемы:** единый реестр технического долга `.ai/plans/145-repo-cleanup-debt-registry/00-TECHNICAL-DEBT-REGISTRY.md` (v2.0, synced волной 145 W2, 2026-08-11). Метрики v2.0: 67 пунктов, 41 [CLOSED], 10 OPEN, 3 partially-done, 3 deferred, 10 monitoring. Код-часть закрыта W2/W3/W4; остаток — операторские/оконные пункты.

**Дата верификации фактов:** 2026-08-11 (live-проверки: `gh secret list -R tronyx161/ai-platform`, `ls ~/.ssh/platform_personal_cicd*`, `ssh -i ~/.ssh/platform_personal_cicd`, `gh run list`, `make check`).

## 2. Проблема (факты, без решений)

### 2.1 Блокер деплоя: утраченная пара ci-deploy (D-142-R15-B29, HI, External/RED)

| Факт | Доказательство (2026-08-11) |
|------|------------------------------|
| Приватная пара `platform_personal_cicd` утрачена при чистке ключей оператором (зафиксировано VR 142 §4.4 R15) | — |
| Локальная пара `~/.ssh/platform_personal_cicd(.pub)` НЕ существует на dev-машине | `ls -la` — no matches; `ssh -i ~/.ssh/platform_personal_cicd tronyx@tronyx-vps` → `Permission denied (publickey,password)` |
| gh-секрет `CI_DEPLOY_KEY` НЕ существует (14 секретов в репозитории, CI_DEPLOY_KEY отсутствует) | `gh secret list -R tronyx161/ai-platform` |
| Следствие: `make deploy-project` и e2e MODE=remote недоступны; частично блокирует закрытие D-136-B6 (project-деплой через CI workflow) | VR 142 §4.4; StatusReport 142:47 |
| Реестр v2.0 ссылается на путь восстановления «из локальной пары ~/.ssh/platform_personal_cicd» (145 W1 TRAP[DECISION]) — путь фактически невозможен | реестр 145 v2.0, категория D |

### 2.2 Docker Hub rate-limit в CI (D-142-R17, MED, partially-done)

| Факт | Доказательство |
|------|----------------|
| Механизм аутентификации есть: `platform-test.yml:171-174` docker/login-action + DOCKER_HUB_AUTH-гейт | код platform-test.yml |
| Секреты `DOCKER_HUB_USERNAME` / `DOCKER_HUB_TOKEN` ОТСУТСТВУЮТ в gh (проверено 2026-08-11) → DOCKER_HUB_AUTH='false' → анонимные пуллы | `gh secret list`; реестр 145 v2.0 |
| CI-джобы `platform-test` и `Build Platform Agent` красные (apk add rsync → Docker Hub rate-limit) | `gh run list` (последние прогоны 2026-08-09/10) |

### 2.3 DR-drill AGE мастер-ключа не выполнен (D-136-W10-S-13, HI, дедлайн 2026-08-31)

| Факт | Доказательство |
|------|----------------|
| Off-node encrypted backup + restore-first на пересозданной ноде НЕ выполнен (T12.12) | реестр 145 v2.0, категория C; Debt 136 W10-S-13 |
| Требует операторского окна + sops/KMS; дедлайн 2026-08-31 (ближайший в реестре) | реестр 145 v2.0, §СВОДКА |
| Риск: без проверенного restore-сценария потеря ноды = потенциальная потеря доступа ко всем секретам платформы | 136 Debt; threat-model docs/age-master-key-dr.md |

### 2.4 Chaos-покрытие T6-T11 не закрыто (D-126-D5, D-126-T9-T11, D-142-Chaos-T6-T10, D-136-B7; дедлайн 2026-09-15)

| Факт | Доказательство |
|------|----------------|
| Прогон 142 (2026-08-07): 5/11 PASSED (T1-T5); T6-T10 RED с диагностическими причинами (маркеры postgres, OOM-жертва, ENOSPC-критерий, age rc=0, restore-канал) | VR 142 §6 |
| T9-T11 (cert/secrets corruption, restore-drill, reboot + real ACME) НЕ выполнялись на пересозданной ноде с реальными LE-сертификатами | Debt 126 T9-T11; Debt 136 T9-T11 |
| OOM-инъекция clickhouse не верифицирована (T7 жертвой стал bash-аллокатор) | Debt 126 D-5 |
| Реальные LE fresh-выпуски (ACME DNS-01) не тестированы (0 вызовов acme.sh --issue; только restore из S3-кеша) | реестр 145 v2.0, D-136-B7 |
| Единое chaos-окно на provisioned-ноде требуется до 2026-09-15 | реестр 145 v2.0, §СВОДКА топ-3 |

### 2.5 Legacy forced-command deploy.sh (D-139-T1, HI, дедлайн 2026-11-01)

| Факт | Доказательство |
|------|----------------|
| `core/entrypoints/deploy.sh` (175 LOC) — переходный SSH forced-command entrypoint; канонический канал — orchestrator_cli dispatch | keep-таблица AGENTS.md (119 D8) |
| Верификация «0 вызовов deploy.sh в audit-логах» НЕ проведена после деплоя | реестр 145 v2.0, категория B |
| При 0 вызовах — удаление + обновление manifest; при вызовах — иная обработка. Оба исхода требуют операторского наблюдения за аудит-логами | Debt 139 T-1 |

## 3. Зоны влияния

| Зона | Влияние |
|------|---------|
| Канал деплоя проектов | `make deploy-project` / e2e MODE=remote недоступны (R15) |
| CI | platform-test, Build Platform Agent — красные (R17); security-scan/hermes-nightly (W4) не имеют секретов для полного цикла (DOCKER_HUB, CI_DEPLOY_KEY, GHCR_PUSH_TOKEN) |
| Безопасность/восстановление | Нет проверенного restore-сценария AGE-ключа (S-13); риск потери доступа к секретам |
| Chaos-верификация | T6-T10 формально RED; T9-T11 не запускались; OOM-clickhouse (D-5) не верифицирован |
| Поверхность атаки | legacy forced-command deploy.sh — недокументированная точка входа до 2026-11-01 |

## 4. Дедлайны

| Дедлайн | Пункты | Severity |
|---------|--------|----------|
| немедленно (блокер) | D-142-R15-B29 (deploy-канал), D-142-R17 (CI red) | HI / MED |
| 2026-08-31 | D-136-W10-S-13 (DR-drill AGE) | HI |
| 2026-09-15 | D-126-D5, D-126-T9-T11, D-142-Chaos-T6-T10, D-136-B7 (chaos-окно) | MED |
| 2026-11-01 | D-139-T1 (deploy.sh) | HI |

## 5. Ограничения брифа

- Бриф фиксирует ТОЛЬКО проблему. Решения (регенерация ключей, выбор канала деплоя, планирование окон, критерии chaos-прогона, судьба deploy.sh) — предмет последующего DevPlan и/или решений оператора.
- Факты проверены 2026-08-11; состояние секретов/CI может измениться — перед планированием решения требуется повторная сверка.
- Некритичные OPEN-пункты (D-134-L5 LOW, D-135-hermes-500 LOW, D-J4 LOW, deferred D-143-memory-limits/logrotate, D-127-issue-cert) вне скоупа брифа — фиксируются, но не требуют срочных решений.

$END_BRIEF
