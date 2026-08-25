<!-- GREP_SUMMARY: verification report qa drift placement multinode validate-topology firewall peer redis-url nginx-exporter -->
# region MODULE_CONTRACT
## @purpose  Semantic QA верификация реализации DevPlan 010 (multi-node placement) — волновой аудит W0-W3,
##           кросс-файловый дрейф, инварианты, качество тестов, рантайм
## @scope    Реализация eb97ef6 → d220501 (follow-ups включены); 10 параллельных субагентов + перекрёстная
##           перепроверка ключевых находок родителем против финального HEAD
## @invariants
##   - @protected  true
##   - Все находки с file:line evidence; спорные перепроверены на HEAD d220501
##   - Вердикт семантический: DRIFTED (CRITICAL) — см. последний раздел
## @rationale План LARGE (>20 файлов, контрактные изменения): полный набор фаз 1-6 по QA-контракту
# endregion MODULE_CONTRACT

$ARTIFACT_CONTRACT
- PURPOSE: Зафиксировать семантический вердикт реализации DevPlan 010 и делегируемые дефекты
- DESCRIPTION: Мультиагентный аудит (10 субагентов) волн W0-W3 + дрейф/инварианты/тесты/рантайм
- RATIONALE: Механический pass тестов insufficient — дрейф сосредоточен на стыках (dead wiring, SoT-metadata vs compose)
- ACCEPTANCE_CRITERIA: Каждый AC плана §12 проверен с evidence; вердикт вынесен по scale STABLE..BLOCKED
- IMPLEMENTS: Верификация DevPlan 010 (01-DevPlan.md, rev 2)
- IMPACTS: Делегирование фиксов Coder/Architect (раздел «Делегирование»)
- REQUIRES: git d220501; журналы прогонов .ai/logs/runs.jsonl; /tmp/kilo логи рантайма

🔒 SHA anchors: аудит начат на 81873d02 (eb97ef6 feat(010) + delta); во время аудита репозиторий
продвинулся до **d220501** (54598a6 feat(010): multi-node follow-ups + 38699a9 + 3cc6b4d).
Ключевые находки перепроверены родителем против d220501; рабочее дерево чистое (кроме docker-compose.macos.yml).

# $START_VERIFICATION_REPORT

## Раздел 1 — Статический аудит (сводка)

Контракты файлов соблюдены (MODULE_CONTRACT/GREP_SUMMARY/STRUCTURE/TRAP-разметка присутствуют в новых
модулях и shared/placement.py; ruff на ключевых файлах — clean). Полная матрица file×check не ведётся —
структурные контракты покрыты субагентами волн; ниже только отклонения.

| Проверка | Результат |
|---|---|
| placement.schema.json draft-07, closed forms, no follow/public_host | ✅ (LOW: nodes-form «nginx only» — prose-only, не enforce) |
| node_yaml validation: len(contexts)>1 → ConfigValidationError | ✅ (+unit+gate негативы; schema maxItems осознанно без изменения — TRAP) |
| Фикстуры s2/s2b/s3 = плану §2/§8 | ✅ байт-соответствие s3 примеру §2 |
| Gate trinity test_gate_placement.py (13 тестов, manifest :1350) | ✅ |
| Модульные контракты log-collector/node-metrics/service-exporters (control-sample parity с redis/minio) | ✅ |
| COMPOSE_PROFILES регенерация: 15 docker profiles ↔ 16 dirs (platform-secrets system) | ✅ |

## Раздел 2 — Дрейф-реестр (Phase 2)

| ID | Sev | Находка | Evidence |
|----|-----|---------|----------|
| DR-C1 | **CRITICAL** | `validate_topology()` — zero production call sites; задокументированный entrypoint `node_config_validate` физически отсутствует (план T0.4: «вызов из node_config_validate»). Топологические ошибки (неполнота, чужой context, exposed вне nginx, off-deps) ловятся ТОЛЬКО тестами. Док-дрейф: placement.py:12 и core/internal/bootstrap/AGENTS.md:211 ссылаются на несуществующий модуль | grep core/**: def только placement.py:571; glob node_config_validate* пуст; перепроверено на d220501 |
| DR-H1 | HIGH | φ1/φ11 bootstrap вызывают firewall.sh БЕЗ `--placement` → peer-rules никогда не применяются при реальном бутстрапе multi-node (fail-closed: сервисы недостижимы для пиров). План T2.3 явно требовал «+φ1 вызов» | lifecycle/phases/system.py:458-469, phases/docker.py:509-515; grep --placement по phases — пусто; CLI-поддержка есть (firewall.py:785,820) |
| DR-H2 | HIGH | **nginx-exporter: трёхфайловое противоречие** module-granularity placement ↔ service-granularity мониторинг. Фикстуры: `service-exporters {node: data-1}` (весь модуль на data-1); рендерер эмитит target `<node-nginx>:9113`; firewall открывает 9113 только data-1→monitoring. Итог: nginx-метрики отсутствуют во ВСЕХ multi-node топологиях + ложный peer-open | prometheus_targets.py:152-156 (`required_module="nginx"`) vs fixtures/s3.yaml:31 vs firewall.py:190,213 |
| DR-H3 | HIGH | `PLATFORM_REDIS_URL` эмитится credential-free (`redis://redis:6379/0`) при безусловно обязательном requirepass (`${REDIS_PASSWORD:?}`) — любой проект по канону получает NOAUTH. Credential-injection существует только для dsn_template, не url_template | platform-infra.yaml:113 vs redis/base.yml:80,84 vs gen_env_platform.py:483; AGENTS.md:208 контракт |
| DR-H4 | HIGH | Healthcheck-пайплайн игнорирует placement: modules_healthcheck читает node.yaml#modules безусловно → multi-node `make healthcheck` проверяет чужие модули / пропускает локальные | core/internal/healthcheck/modules_healthcheck.py:271-299 |
| DR-M1 | MEDIUM | Ссылки форм на ноды ({node: X}/{nodes:[...]}) не валидируются против placement.nodes: опечатка `{node: data-9}` молча выпадает из резолва любой ноды (частичный lazy-guard только в service_host) | placement.py:525-564 (проверок нет), :286-304, :373-385 |
| DR-M2 | MEDIUM | MODULE_PORTS_DENY: 13 raw-литералов вопреки SoT-канону + deny-лист не расширен новыми портами (6432/19000/9187/9121 отсутствуют; 8080 исключён по устаревшему rationale «loopback») | firewall.py:124-138 vs :183-195 vs node-metrics/base.yml:57 |
| DR-M3 | MEDIUM | spool_dir фикция: log-collector объявляет `/var/lib/platform/alloy-data`, но alloy-data — docker-managed volume (U-67 канон: тогда spool_dir: none); путь не провижинится и не монтируется | log-collector/module.yaml:39-40 vs minio/module.yaml:28-32 vs root compose:90 |
| DR-M4 | MEDIUM | platform-infra.yaml provides.networks расходится с фактическими attach'ами (minio/clickhouse/langfuse) — gen_env_platform ведёт проекты в сеть, где DNS-алиаса нет; работает лишь через случайные co-attachments. Root AGENTS.md при этом описывает все 5 сервисов на hermes-agent-net — дрейф doc↔SoT↔compose | platform-infra.yaml:128-146 vs composes (субагент-верификация) vs AGENTS.md «Сети платформы» |
| DR-L1 | LOW | AGENTS.md security-prefix перечень (11 портов) ≠ PEER_PUBLISH_PORTS (+4000 litellm, +3001 langfuse, +9119 hermes-dashboard). Отклонение признано TRAP[DECISION] в коде, но канон-док не обновлён | AGENTS.md:314-319 vs firewall.py:183-195,174-182 |
| DR-L2 | LOW | SPOF-honesty («multi-node ≠ HA», RTO/RPO ссылка) отсутствует в root-секции multi-node (план §8:396 требовал честной фиксации) | rg SPOF/HA по AGENTS.md — пусто |
| DR-L3 | LOW | U-68 computed-count stale: root compose «вычисляемое: 14» vs 15 includes после сплита | docker-compose.yml:10 |
| DR-L4 | LOW | KeyError в `{nodes:[...]}`-ветке service_host (:385) вместо ConfigValidationError (нарушение exit-code контракта 4) | placement.py:379-387 |
| DR-L5 | LOW | hermes healthcheck_deps redis-probe без auth → сигнал зависимости перманентно false-negative (warn-only, /ready зелёный) | healthcheck_deps.py:148-149 vs requirepass |
| DR-L6 | LOW | port_scanner `_PORT_NAME_MAP` без 19000; POSTGRES_PORT двойной смысл (5432 client vs 6432 publish) в соседних модулях | port_scanner.py:31-52; backup-cron/base.yml:71 vs postgres/base.yml:109 |

Опровержено (проверено, НЕ дрейф): подозрение на raw-литералы портов в PEER_PUBLISH_PORTS — все 14 значений именованные константы из platform_ports.py (firewall.py:90-105); 5432 нигде не публикуется; infra-metrics остатки — 24 хита, все исторические.

**Сводка дрейфа:** 1 CRITICAL · 4 HIGH · 4 MEDIUM · 6 LOW

## Раздел 3 — Инварианты плана (Phase 3)

| # | Инвариант | Статус | Evidence |
|---|-----------|--------|----------|
| 1 | Backward compat single-node | HELD | load_placement→None (placement.py:230-233); node-configs/** placement.yaml = 0; binds `${SERVICE_BIND_HOST:-127.0.0.1}` |
| 2 | Placement авторитетен, drift=WARNING | HELD | deploy_orchestrator.py:512-520; lint_drift non-raising (:661-676); gate :339-364; WARNING с repair-подсказкой |
| 3 | Fail-fast валидация ДО деплоя | **AT_RISK** | Loader-часть wired (schema/VPN/host); validate_topology(a)-(e) — мёртвый код (DR-C1) |
| 4 | Peer-only firewall | **AT_RISK** | Механика полная и протестированная; production-wiring отсутствует (DR-H1) |
| 5 | Security-префикс T2.0.* | HELD | redis requirepass fail-fast; loki auth_enabled+X-Scope-OrgID сквозной; pg_hba scram-sha-256 RFC1918 (0 md5); vpn_enforced enforced. Остатки: DR-H3, DR-L5 |
| 6 | VPN-only host + аттестация | HELD | ipaddress-membership (placement.py:74-79,134-148); vpn_enforced raise (:256-262); негатив-гейты. INFO: строже плана — требуется даже для 1-нодового placement.yaml (fail-safe) |
| 7 | Multi-ingress | **AT_RISK** | target_node∈nodes[] + FQDN-uniq реализованы; но (a) nodes-form для не-nginx schema пропускает (prose-only), (b) проверки живут в невыполнимом validate_topology (DR-C1) |
| 8 | Honest backup boundary | HELD | AGENTS.md:326-328; запрещённая формулировка repo-wide отсутствует |
| 9 | Closed vocabulary | HELD | Schema oneOf ровно 4 формы; follow/public_host отвергаются parity-гейтом |

**Сводка:** HELD=6 · VIOLATED=0 · AT_RISK=3 (все три — варианты одной корневой причины DR-C1/DR-H1: wiring-слой)

## Раздел 4 — Качество тестов (Phase 4)

**Health score: 86/100** (−5 отсутствующий сценарий, −3 частичный, −6 хрупкие кластеры; R1-R4 чистые)

Покрытие $TEST_SPEC: 19/20 строк закрыты (84 unit+gate теста, 12 файлов). Пробелы:
- Row 16 loki_tenant_header — выделенного теста нет; alloy-header покрыт (test_alloy_config.py:52), pg_hba (test_postgres.py:482), **grafana datasource httpHeaderName1 не тестируется нигде**
- Row 15 частично: redis-exporter credentialed URL не ассертится (grep `redis://:` по tests/ = 0)
- Нет негативного ассерта scoping'а (не-monitoring пир НЕ получает 9100/8080/9187 правило) — покрытие через код-путь
- Нет ассерта содержимого EXTRA_NO_PROXY (что там именно IP нод)

Хрупкое: private-API `_NODE_TARGET_JOBS` + exact-count freeze; тест на наличие комментария `"S3: 10.8.0.11:19000"` (близок к pass-test); conditional-guards в gate (:177,:191) могут молча терять RED-leg; security_prefix exact-format substrings ×3; фиксированный список 6 vhost-шаблонов без discovery.

LDD: test_shared_placement/test_firewall_peer/test_module_dsn — образцовые (assert_ldd_imp9); test_platform_vhost_upstream/test_node_metrics — trajectory отсутствует; gates-файл — без IMP:9 enforcement.

## Раздел 5 — Рантайм-валидация (Phase 5)

- **Placement-батч: 84 passed / 0 failed / 0 skipped, 0.82s** (12 файлов: shared_placement 9, deploy_placement_resolve 4, gen_env_platform_multinode 4, security_prefix 3, module_dsn 4, platform_vhost_upstream 5, node_metrics 3, firewall_peer 19, alloy_config 7, log_collector 8, monitoring_multinode 5, gate_placement 13)
- **Single-node smoke: 24 passed** (compose_contract + secrets_validation) — no-op совместимость подтверждена
- **Anti-Illusion: PASS** — IMP:9 ×331, IMP:8 ×670, IMP:10 ×6 в траекториях (-rP прогон)
- **ruff: clean** на 5 ключевых файлах
- Журнал: make check exit 0 + agent-check exit 0 @2026-08-24T20:10 (.ai/logs/runs.jsonl — свидетельство инвариант-агента; собственный полный make check не гонялся по OOM-политике)

### Acceptance criteria (§12 метрики успеха)

| # | Метрика | Статус |
|---|---------|--------|
| 1 | make check + agent-check зелёные | ✅ (журнал; таргет-батчи подтверждают) |
| 2 | Single-node regression 0 | ✅ (таргет-smoke; полный батч — журнал предыдущего агента) |
| 3 | S3-фикстура: резолв/env/DSN/upstream/firewall-матрица | ✅ тестами; ⚠️ nginx-exporter scrape сломан (DR-H2) |
| 4 | Security-префикс | ✅ с остатками DR-H3/DR-L5 |
| 5 | Наблюдаемость: targets/job_name/alerts | ✅ RemoteNodeDown (job="node-exporter", обоснованное отступление от плейсхолдера плана) + LokiCollectorStale Prometheus-based; ⚠️ DR-H2 |
| 6 | Multi-ingress S2b + DNS-steering runbook | ✅ валидатор+тесты; wiring-оговорка DR-C1 |
| 7 | Словарь/доки: no context-status, backlog закрыт, AGENTS.md multi-node | ✅; ⚠️ DR-L1/DR-L2 док-дрейф |
| E2E | requires_node S2 на 2 VPS | ❌ НЕ ВЫПОЛНЕН — release-checklist пункт остаётся открытым (ручной шаг оператора) |

## Раздел 6 — Config Sync (Phase 6)

- REDIS_PASSWORD цепочка: secret-definitions:214 → redis:84 → service-exporters:105 → sync_env_defaults:389 → .env.example:101 — ✅ полная (обрыв на потребителе PLATFORM_REDIS_URL — DR-H3)
- LOKI_TENANT: единый emitter deploy_orchestrator:416 (placement.context), потребители alloy/nginx-vhost/grafana — ✅ консистентный дефолт `platform`
- SERVICE_BIND_HOST: 10 compose-потребителей, единственный provider multinode_runtime_env — ✅
- EXTRA_NO_PROXY (T2.5): deploy-time инъекция IP нод + compose-passthrough hermes/monitoring, superset-гейты толерантны (test_module_domains_static:1232) — PARTIAL: план-буква SoT-расширения не выполнялась (функциональный эквивалент), content-test отсутствует
- **Точка схлопывания:** `core/platform-infra.yaml` — 4 измерения дрейфа сходятся в одном SoT (env_defaults пробелы 9187/9121/19000, provides.networks DR-M4, url_template DR-H3) — чинить системно, не точечно

# ⟦CHECKPOINT 2⟧ — Семантический вердикт

# ВЕРДИКТ: DRIFTED (CRITICAL)

Ядро плана реализано добротно и протестировано: W0-контракт полный, резолв авторитетен,
single-node no-op байт-совместим (регрессия 0), security-префикс выполнен до публикации,
наблюдаемость собрана, 108 таргет-тестов зелёные с обильным IMP:9. НО мульти-нодовый контур
имеет два мёртвых сегмента wiring (validate_topology без единой production-точки вызова;
firewall peer-rules без bootstrap-вызова) и один сквозной функциональный разрыв
(nginx-exporter scrape во всех multi-node топологиях) — плюс контрактный обрыв PLATFORM_REDIS_URL,
который затронет проекты уже на single-node при первом requirepass-деплое.

Health score: **60/100** (band 40-69 — significant drift, action needed).

Блокирует декларацию «план 010 завершён»; single-node продакшн не затронут (placement.yaml
нигде не существует — все CRITICAL/HIGH латентны до первого реального multi-node контекста,
кроме DR-H3).

# $END_VERIFICATION_REPORT

## Делегирование (предложения, не исполняется)

| → | Задача | Закрывает |
|---|--------|-----------|
| Coder | Wired validate_topology: вызвать из deploy-контура (или создать entrypoint node_config_validate + зацепить в preflight/converge); удалить/исправить док-ссылки placement.py:12, bootstrap/AGENTS.md:211 | DR-C1, инвариант 3/7 |
| Coder | φ1/φ11 phases → передавать --placement в firewall run (путь уже поддержан CLI) | DR-H1, инвариант 4 |
| Architect | Решить granularity-коллизию exporters: либо split nginx-exporter в отдельную форму размещения, либо renderer/firewall синхронизировать с module-granularity | DR-H2 |
| Coder | platform-infra.yaml: credentialed url_template для redis (или _apply_credentials_to_url) + правка provides.networks под фактические attach'ы + env_defaults дополнить 9187/9121/19000 | DR-H3, DR-M4 |
| Coder | modules_healthcheck: placement-awareness (резолв через resolve_node_modules как deploy) | DR-H4 |
| Coder | Мелочи пачкой: form→node refs validation (DR-M1), MODULE_PORTS_DENY из констант + новые порты (DR-M2), spool_dir:none для log-collector (DR-M3), KeyError→ConfigValidationError :385 (DR-L4), AGENTS.md порт-перечень + SPOF-абзац + count U-68 (DR-L1/L2/L3) |
| QA (повтор) | После фиксов: re-run placement-батча + добить тестовые пробелы row15/row16/negative-scoping/EXTRA_NO_PROXY-content |
