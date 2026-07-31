# 10-Brief — B3: Нода и метрики (greenfield-развёртывание)

<!-- GREP_SUMMARY: node metrics-cron prometheus volumes NODE-detection bootstrap.sh ghcr CONTEXT_IMAGE bind-mounts ci_deploy_key -->
<!-- STRUCTURE: ┌scope┐ → ◇ метрики → ◇ инфра-конфиги → ◇ детекция → ⊕ критерии → ⎋ зависимости -->
# region MODULE_CONTRACT
## @purpose  Волна B3: инфраструктура ноды для greenfield-развёртывания — метрики, конфиги, детекция.
## @scope    U-03, U-38, U-48, U-49, U-52, U-53, U-60, U-67
## @invariants
##   - Сервер переустанавливается: все исправления этой волны применяются к чистому развёртыванию (без миграций).
##   - Документация (docstring/gate-комментарии) не обещает того, чего нет в коде.
# endregion MODULE_CONTRACT

$ARTIFACT_CONTRACT:
  PURPOSE: Обеспечить работающий мониторинг и корректную инфраструктуру на переустановленной ноде.
  DESCRIPTION: Установка metrics cron (systemd timer или cron.d в φ3), устранение дублей prometheus.yml, консолидация volume-деклараций, фикс NODE-детекции, рефакторинг bootstrap.sh, согласование ci_deploy_key, ghcr org/tag политика, удаление мёртвых bind-mounts.
  RATIONALE: U-03: docstring и gate-тест утверждают установку metrics cron, кода нет — новая нода без метрик и мониторинга. U-38: platform-export-metrics выберет scripts/ как NODE_NAME. U-60: push в tronyx161, pull из tronyxlab — несоответствие. Greenfield: разворачиваем правильно с первого дня.
  ACCEPTANCE_CRITERIA: (1) metrics cron устанавливается в φ3 (cron.d/platform-metrics, flock + timeout 50s) — проверяется e2e на переустановленном сервере; (2) prometheus.yml/.tmpl: один файл (шаблон удалён или рендер реальный); (3) volume-декларации: root compose — единственный SoT; macos-оверрайд cadvisor удалён или приведён к реальному сервису; CONTEXT_IMAGE политика определена; (4) NODE-детекция: единая функция (исключает scripts+secrets), 3 реализации → 1; (5) bootstrap.sh ≤ 150 LOC: batch-вызов node_yaml вместо per-field --get; (6) ci_deploy_key: единый источник (node.yaml), setup-node.sh принимает его (env TRAP[BUG] снят); (7) ghcr: единый org (решение tronyx161 vs tronyxlab), единая форма тегов (tag policy: digest-pin для релизов, :latest только для dev); (8) /var/lib/platform/{minio,langfuse}-data удалены из provision (или примонтированы).
  IMPLEMENTS: U-03 (metrics cron), U-38 (NODE-детекция), U-48 (prometheus дубль), U-49 (volumes), U-52 (bootstrap.sh), U-53 (ci_deploy_key), U-60 (ghcr org/tag), U-67 (bind-mounts)
  IMPACTS: core/internal/bootstrap/lifecycle/phases.py (φ3), core/internal/healthcheck/platform-export-metrics.sh, core/modules/monitoring/config/, docker-compose*.yml, core/entrypoints/bootstrap.sh, setup-node.sh, core/platform-infra.yaml, core/platform-env.yaml, .github/workflows/build-hermes.yml, module compose файлы
  REQUIRES: B2 (platform-env паритет), B1 (setup-node/forced-command часть), B8 (dead-code решения)

---

## Scope

| U | Проблема | Ключевые файлы |
|---|----------|----------------|
| U-03 | Metrics cron не устанавливается; docstring:32 и gate:275 врут | phases.py:32,261-317, platform-export-metrics.sh:6, test_gate_status_page.py:275 |
| U-38 | NODE-детекция ×3: metrics-скрипт исключает только secrets | platform-export-metrics.sh:31-33, core-deploy.yml:169-171, node_yaml.resolve |
| U-48 | prometheus.yml ≡ .tmpl (md5 идентичны, 146 строк) | monitoring/config/prometheus.yml(.tmpl) |
| U-49 | Volumes: root driver:local vs модули driver_opts; macos cadvisor; CONTEXT_IMAGE "" | docker-compose.yml:42-51, docker-compose.macos.yml:23, docker-compose.platform-dev.yml:34, postgres/backup-cron base.yml |
| U-52 | bootstrap.sh 178 LOC, per-field --get ×6 | entrypoints/bootstrap.sh:86-124 |
| U-53 | ci_deploy_key: node.yaml vs env (TRAP[BUG] 17.07) | bootstrap.sh:103-114, setup-node.sh:206 |
| U-60 | ghcr: push tronyx161, pull tronyxlab; 3 формы тегов | build-hermes.yml:45-46, platform-infra.yaml:144, smoke.py:116, hermes-agent/base.yml:66 |
| U-67 | Dead bind-mounts minio/langfuse-data | platform-infra.yaml:71,76, provisioner.py:203-247 |

## Ключевые артефакты

1. Metrics: install_cron_metrics() в φ3 (после setup-node); cron.d файл `/etc/cron.d/platform-metrics` (flock + timeout 50s); gate-тест заменяется на проверку bootstrap-кода (не комментария).
2. prometheus: удалить .tmpl-дубль ИЛИ сделать рендер реальным (решение: если рендерера нет — файл один, промаркировать).
3. Volumes: root compose — SoT; модульные driver_opts переносятся в root; macos-оверрайд cadvisor — согласовать с COMPOSE_PROFILES (или удалить); CONTEXT_IMAGE "" — заменить механизмом (без пустой строки).
4. NODE-детекция: единая функция shared (исключает scripts + secrets), 3 потребителя.
5. bootstrap.sh: batch-вызов node_yaml (один python3 -m с --json), фасад ≤ 150 LOC.
6. ci_deploy_key: node.yaml — единственный источник; setup-node.sh читает через node_yaml CLI.
7. ghcr: решение по org (рекомендация: tronyxlab — фактический runtime-pull), tag policy (digest-pin релизы), гейт на форму тегов в platform-infra.yaml.
8. provision: удалить мёртвые host-пути (или перевести на bind-mounts, если нужны).

## Гейт самоверификации волны

- e2e-гейт на переустановленном сервере: после `make bootstrap-node` → metrics файлы появляются (cron установлен, /var/cache/platform/metrics/ наполняется).
- Гейт конфигов: prometheus 1 файл; volume-декларации 1 SoT; тег-форма единая.

## Зависимости

- От: B1 (setup-node), B2 (env-паритет).
- К: B10 (e2e-тесты метрик), B11 (workflow build-hermes).
