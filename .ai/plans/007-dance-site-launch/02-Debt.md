# GREP_SUMMARY: Debt, dance-site, ci_deploy_key, project-payload-delivery, adopt-project-drift, platform-observations
# STRUCTURE: ▶ 3 TRAP[DEBT] наблюдения из сессии dance-site launch — БЕЗ фиксов, вне зоны задачи

$START_DEBT

## $ARTIFACT_CONTRACT
- **PURPOSE:** Зафиксировать 3 латентных дефекта платформы, обнаруженных при запуске dance-site, вне зоны текущей задачи (зона = проект).
- **DESCRIPTION:** ci_deploy_key не читается из node.yaml; нет механизма первичной доставки project payload; adopt-project.sh породил конфиг-drift.
- **RATIONALE:** Наблюдения дороги в повторном обнаружении; сохраняем гипотезы для будущей сессии по core платформы.
- **ACCEPTANCE_CRITERIA:** N/A (реестр наблюдений).
- **IMPLEMENTS:** Побочные находки диагностики dance-site.
- **IMPACTS:** core платформы (будущая сессия) — НЕ трогается в задаче dance-site.
- **REQUIRES:** N/A.

---

## D1 — ci_deploy_key из node.yaml никем не читается

```
# 📝 TRAP[DEBT] · 2026-07-17 · HI · node.yaml.node.ci_deploy_key объявлен в схеме, но не потребляется bootstrap-каналом
# · Observed: /home/ci-deploy/.ssh/ отсутствовал после исходного bootstrap. node.schema.json
#   определяет node.ci_deploy_key; node.yaml его содержит; но core/entrypoints/bootstrap.sh
#   извлекает ТОЛЬКО owner_key (строка ~114). step_6_create_ci_deploy_user пишет ключ лишь
#   при заданном env PLATFORM_CI_DEPLOY_KEY, который bootstrap.sh из node.yaml не заполняет.
# · Suspected: пропущен парсинг node.ci_deploy_key → export PLATFORM_CI_DEPLOY_KEY в bootstrap.sh
#   (по аналогии с owner_key → --owner-key). Один источник истины (node.yaml) не доходит до потребителя.
# · Impact: любой первый bootstrap ноды оставляет ci-deploy без ключа → весь CI-деплой мёртв,
#   пока оператор вручную не передаст PLATFORM_CI_DEPLOY_KEY. Молчаливая деградация.
# · When: during dance-site first-deploy — обходится передачей env в T5, фикс core отложен (вне зоны).
```

## D2 — Нет механизма первичной доставки project payload в /opt/projects/

```
# 📝 TRAP[DEBT] · 2026-07-17 · HI · deploy-project.sh требует /opt/projects/<name>/, но платформа его не создаёт
# · Observed: /opt/projects/ на VPS не существует. deploy-project.sh (forced-command) падает
#   "project directory not found", если payload (docker-compose.yml + ai-platform.yaml) не доставлен
#   заранее. Ни bootstrap, ни deploy-project.sh, ни adopt-project.sh не доставляют его на ноду.
#   Реально задеплоенный tronyx-site лежит в /opt/tronyx-lab/tronyx-site (context-overlay git-канал),
#   а НЕ в /opt/projects — то есть его CI через reusable workflow тоже сломан/не использовался.
# · Suspected: пропущен шаг доставки/синхронизации project payload (SCP push либо git-overlay clone
#   в /opt/projects/<name>) как часть adopt/deploy. Расхождение PROJECTS_BASE=/opt/projects vs
#   фактического /opt/<context>/<project>.
# · Impact: ни один adopted-проект не деплоится через штатный forced-command без ручного SCP payload.
# · When: during dance-site first-deploy — обходится ручным SCP в T6, фикс core отложен (вне зоны).
```

## D3 — adopt-project.sh породил конфиг-drift (context + repo casing)

```
# 📝 TRAP[DEBT] · 2026-07-17 · MED · adopt-project записал context: personal и repo с неверным регистром
# · Observed: dance-site/ai-platform.yaml имел context: personal, хотя org = tronyx-lab (node.yaml:
#   context: tronyx-lab). node.yaml.projects[dance-site].repo = "tronyx-lab/dance-site" (строчными),
#   а фактический GitHub-репо TronyxLab/dance-site. deploy.yml генерировался с IMAGE_NAME
#   ghcr.io/tronyxlab/... (ok, т.к. Docker требует lowercase) но uses: TronyxLab/ai-platform.
# · Suspected: adopt-project.sh дефолтит context в "personal" при отсутствии явного --context и не
#   валидирует его против node.yaml; не нормализует/не сверяет casing org с фактическим GitHub-репо.
# · Impact: при перегенерации .env.platform/повторном adopt drift воспроизводится → ломает uses:,
#   ghcr-пути, cross-project валидацию.
# · When: during dance-site first-deploy — исправлено в проекте (T3), фикс генератора отложен (вне зоны).
```

$END_DEBT
