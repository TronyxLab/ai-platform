<!-- GREP_SUMMARY: multi-node placement размещение модулей контекст ноды шаримые all-nodes nodes off multi-ingress canary критичные канарейки vpn_enforced peer-firewall scram redis-acl loki-tenant адресация наблюдаемость сценарии placement.yaml -->
<!-- STRUCTURE: ▶ контекст+инварианты(r2) → ◇ placement.yaml (node|all-nodes|nodes[]|off) → 🔒 W2-s security-префикс → ⊕ W0 контракт → ⚡ W1 резолв → ⚡ W2 адресация/firewall → ∑ W3 наблюдаемость → ⎋ сценарии S2/S2b-canary/S3 + верификация -->
# region MODULE_CONTRACT
## @purpose  Реализация post-release фазы беклога .ai/backlog/multi-node-hermes-split.md и Brief-009 T6:
##           размещение модулей платформы ПО МОДУЛЯМ (без групп ролей) на 1-N серверах одного
##           контекста. Ревизия 2 (2026-08-22, после критического 6-агентного анализа): удалены
##           абстракции без потребителей (follow, public_host, secrets-consistency валидатор, S4),
##           исправлена порт-матрица (−5432, +clickhouse-native 19000), добавлен security-префикс
##           Волны 2 (redis auth, Loki tenant, pg_hba scram, vpn_enforced), S2b переосмыслен как
##           «критичные проекты / канарейки» (решение владельца).
## @scope    core/schemas/placement.schema.json; core/internal/shared/placement.py;
##           node_yaml validation (1 нода = 1 контекст); deploy_orchestrator (резолв модулей);
##           gen_env_platform (кросс-нодовые хосты); bootstrap/firewall.py (peer-allow + fix
##           stale-reconcile delete-формы); secret-definitions.yaml + redis/loki конфиги +
##           postgres pg_hba (security-префикс W2-s); shared/platform_ports.py (+9100/8080-cadvisor/
##           9187/9121/9113/3100/19000); compose-DSN модулей (langfuse/litellm → ${POSTGRES_HOST};
##           langfuse CH-migration → ${CLICKHOUSE_HOST}:${CLICKHOUSE_NATIVE_PORT});
##           platform-vhost upstream'ы nginx (envsubst из placement);
##           modules: logging→logging+log-collector, infra-metrics→node-metrics+service-exporters;
##           monitoring render (file_sd remote targets); Makefile/glossary;
##           AGENTS.md (root/core) — модель деплоя.
## @invariants
##   - БЕЗ групп ролей: решение R3 Brief-009 + владелец 2026-08-22. Только приписка модуль→нода.
##   - Single-node — канон и байт-совместимое поведение: нет placement.yaml → легаси-резолв no-op.
##   - Размещение настраивается в ОДНОМ месте (placement.yaml); placement АВТОРИТЕТЕН — node.yaml#modules
##     при наличии placement не читается для деплоя, дрейф = lint-WARNING (не RED).
##   - Закрытый словарь форм: singleton {node} / all-nodes / nodes[] (только nginx) / off.
##     follow и public_host УДАЛЕНЫ в r2 (0 потребителей; возврат additive, 2-4 ч, §11).
##   - Security-префикс: НИ ОДИН кросс-нодовый порт не публикуется до выполнения W2-s
##     (redis auth, Loki tenant, pg_hba scram) — публикация неаутентифицированных сервисов запрещена.
##   - Кросс-нодовые порты открываются ТОЛЬКО для IP нод-пиров (ufw allow from <peer>), loopback-bind —
##     дефолт; peer-ALLOW вставляется ДО module-deny (ufw first-match); overlay-сети в v1 не вводятся.
##   - Приватная сеть обязательна + аттестация: nodes[].host — только RFC1918/100.64; multi-node
##     контекст обязан нести vpn_enforced: true (RFC1918 ≠ крипто — оператор подтверждает шифрованный
##     канал). Платформа VPN не строит (sslmode=disable, redis/minio без TLS — шифрует VPN-канал).
##   - Порт-матрица канонична: кросс-нодово публикуются ТОЛЬКО 6432 (pgbouncer), 6379 (redis),
##     9000 (minio API), 8123 (clickhouse HTTP), 19000 (clickhouse native peer; коллизия с minio:9000,
##     прецедент host≠container: langfuse 3001/3000), 3100 (loki push), 9100+8080 (node-metrics),
##     9187/9121/9113 (exporters). Прямой 5432 НЕ публикуется: все его потребители (backup-cron,
##     postgres-exporter, pgbouncer) переезжают на data-ноду вместе с postgres.
##   - Multi-ingress: nginx в форме {nodes:[...]} (экземпляр на каждой ноде); exposed-проект обязан
##     размещаться на ноде из списка; DNS-steering (per-FQDN A-записи) — prerequisite оператора (§8).
##   - mode:off — явное выключение модуля контекста; полнота записей: каждый модуль инвентаря обязан
##     иметь запись (включая off) — fail-fast против опечаток.
##   - Граница бэкапа (честная, r2): backup-cron покрывает ТОЛЬКО postgres (pg_dumpall+WAL → внешний S3);
##     minio/clickhouse/loki/grafana/project volumes не бэкапятся и в single-node (app-data-скрипт —
##     мёртвый стаб phase-07, долг §11). Multi-node экспозицию не ухудшает — фиксирует существующую.
##   - Выполнять ПОСЛЕ завершения плана .kilo/plans/1787342045763-simplify-refactor-waves.md;
##    зоны исключений того плана (Brief-009 T1/T3/T7 payload/schema/tri-write) не трогаются.
## @rationale Q: почему по модулям, а не ролям? A: роли — жёсткие группы, не покрывают реальные
##            топологии; приписка модуль→нода выражает любую топологию 2-4 серверов без нового DSL.
##            Q: зачем security-префикс W2-s? A: план открывает порты сервисов с нулевой/слабой
##            аутентификацией (redis без auth вообще, loki auth_enabled:false, pg_hba md5 для
##            RFC1918-диапазонов) — публикация без префикса УВЕЛИЧИЛА бы attack surface; порядок
##            «сначала auth, потом порт» делает multi-node безопаснее single-node (сегментация).
# endregion MODULE_CONTRACT

# DevPlan 010 — Multi-node: размещение модулей по серверам контекста

Дата: 2026-08-22 · **Ревизия 2** (вечер — после критического анализа, 6 субагентов верифицировали
план против кода; интеграционные швы подтверждены, найденные ошибки исправлены) · Источники:
`.ai/backlog/multi-node-hermes-split.md` · Brief-009 T6/R3 · решения владельца 2026-08-22
(утро: R3-канон по модулям, VPN-only host, nginx multi-ingress `{nodes:[...]}`, `mode:off`,
бэкап v1 = data-нода; вечер: **S2 «вынос БД» подтверждён как реальный ожидаемый сценарий**;
**S2b = разделение проектов: критичные на отдельной ноде / остальные «канарейки»**;
решение по постгресу принято архитектом — §8 S2).

## 0. Контекст

- Беклог требует разнести стек одного контекста на несколько серверов с максимальным шарингом
  общих сервисов и единой наблюдаемостью. Исходная формулировка беклога («три роли серверов»)
  **скорректирована владельцем**: ролей-групп нет — активируется размещение каждого модуля.
- **Ревизия 2 основана на критическом анализе против кода** (6 субагентов: deploy-механика,
  security, ingress, наблюдаемость, stateful/бэкап, упрощение). Интеграционные швы подтверждены:
  `_parse_modules` (`deploy_orchestrator.py:314`), `parse_modules_from_node_yaml`
  (`secrets_validator.py:327`), emission `gen_env_platform.py:313-334`, hermes уже параметризован
  (`${POSTGRES_HOST:-pgbouncer}`), file_sd в Prometheus существует (`platform-projects` job),
  nginx envsubst-механизм уже работает. Исправления r2: порт-матрица (5432 не нужен, CH native
  нужен), reconcile delete-форма firewall, честная формулировка бэкапа, drift-WARNING вместо RED,
  удалены follow/public_host/S4/secrets-consistency-валидатор, добавлен security-префикс W2-s.
- **Решения владельца (вечер 2026-08-22):**
  1. Вынос БД на отдельный сервер — реальный ожидаемый сценарий → S2 первичен (§8).
  2. Разделение проектов по двум серверам: критически важные — на выделенной ноде (максимальный
     аптайм), остальные — «канарейки» → S2b (§8), multi-ingress остаётся в v1 с DNS-steering
     prerequisite.
- Решение по постгресу (принято архитектом, §8 S2): pgbouncer — единственный кросс-нодовый фасад;
  прямой 5432 не публикуется (все его потребители едут на data-ноду вместе с postgres); pg_hba
  md5→scram до публикации; backup-cron переезжает вместе с postgres (restore-ко-локация
  сохраняется); RTT VPN 20-50 мс безопасен (TCP-probe healthcheck_deps 3s ≫ RTT); риск =
  availability, не латентность: healthcheck langfuse проверяет зависимости → рестарт-циклы при
  морганиях VPN (мониторится RemoteNodeDown, healthcheck v1 не меняем).
- Открытые вопросы Brief-009 закрываются этим планом: §8 Q3 (формат слоя →
  `node-configs/<context>/placement.yaml`), §8 Q6 (перечень шаримых по умолчанию → §3).
- Сейчас: все модули ноды разворачиваются локально (node.yaml `modules[].enabled`), provides-хосты —
  Docker DNS алиасы внутри локальных external-сетей, всё слушает 127.0.0.1, ufw default-deny.
  Платформа не умеет: (а) резолвить адрес сервиса НА ДРУГОЙ ноде, (б) открывать порт только пирам,
  (в) собирать логи/метрики всех нод в центральный узел, (г) описывать топологию в одном файле.
- Связь с планом `1787342045763-simplify-refactor-waves.md`: исполняется СЕЙЧАС; этот план —
  следующий. Его W2.17 (NO_PROXY генерация) и T1.4 (CADVISOR_IMAGE параметризация)
  переиспользуются в Волнах 2-3 (пути подтверждены: `.kilo/plans/…:92` и `:55`).

## 1. Инварианты плана

1. **Обратная совместимость обязательна**: отсутствие `placement.yaml` у контекста = текущее поведение
   (все модули из node.yaml, loopback-bind, локальные сети). Ни один существующий конфиг не мигрирует принудительно.
2. **Одна точка настройки, placement авторитетен**: топология описывается ТОЛЬКО в placement.yaml;
   node.yaml ноды при наличии placement для деплоя НЕ читается; дрейф node.yaml.modules ↔ placement —
   lint-WARNING с repair-подсказкой (не RED — гейт не охраняет файл, объявленный мёртвым).
3. **Fail-fast валидация**: неизвестный модуль/нода, размещение без ноды, модуль без записи в multi-node
   контексте — ConfigValidationError(4) до любого деплоя.
4. **Порты наружу — только пирам**: peer-ALLOW вставляется ДО module-deny (ufw first-match);
   stale-reconcile удаляет peer-правила командой С source IP (сегодняшняя форма `ufw delete allow
   <port>/tcp` неоднозначна при ≥2 пирах на порту); Anywhere-публикация запрещена гейтом.
5. **Security-префикс Волны 2**: ни один кросс-нодовый порт не публикуется до выполнения T2.0.*
   (redis auth, Loki tenant, pg_hba scram) — публикация неаутентифицированных сервисов запрещена.
6. Верификация волны: `make check` до чистоты + `make agent-check`; per-task `make check TEST_FILE=<файл>`;
   полный `make gate MODE=fast` в dev-цикле не запускается.
7. **Приватная сеть + аттестация**: `nodes[].host` — только приватный/VPN адрес (RFC1918, 100.64/10),
   публичный адрес → ConfigValidationError; multi-node контекст обязан нести `vpn_enforced: true`
   (оператор подтверждает шифрованный канал; RFC1918 ≠ крипто). Обоснование: кросс-нодовый трафик не
   шифруется платформой — шифрование даёт VPN-канал, ufw задаёт только периметр.
8. **Multi-ingress**: nginx — единственный модуль в форме `{nodes:[...]}` (v1); каждый exposed-проект
   (`expose:true`+domain) обязан иметь target_node из этого списка; FQDN-уникальность проверяется
   кросс-нодово статическим сканом node-configs; DNS-steering (per-FQDN A-записи) — prerequisite
   оператора, документируется в runbook (§8).
9. **Граница бэкапа честная**: backup-cron покрывает ТОЛЬКО postgres (pg_dumpall+WAL → внешний S3);
   minio/clickhouse/loki/grafana/project volumes не бэкапятся и в single-node (app-data-стаб — долг,
   §11). Формулировка «покрывает volumes data-ноды» запрещена как вводящая в заблуждение.

## 2. Канонический конфиг размещения — `node-configs/<context>/placement.yaml`

Единственный файл топологии контекста. Живёт в node-configs-репозитории контекста рядом
с директориями нод (context = org, инвариант 3 root AGENTS.md). Схема — `core/schemas/placement.schema.json`
(draft-07, валидация через `shared/schema_validator.py` — единственная Draft7Validator-точка).

```yaml
# node-configs/tronyx-lab/placement.yaml — пример (сценарий S3)
# GREP_SUMMARY: placement context vpn_enforced nodes modules node all-nodes off nodes-list

context: tronyx-lab            # = contexts[0].name КАЖДОЙ ноды контекста (drift-гейт)
vpn_enforced: true             # аттестация оператора: кросс-нодовый трафик идёт по шифрованному каналу
                               # (обязательна в multi-node; RFC1918 ≠ крипто — инвариант 7)

nodes:                         # члены контекста (>=1); host — адрес для cross-node трафика
  - name: data-1
    host: 10.8.0.11            # приватный/VPN адрес (RFC1918 или 100.64/10)
  - name: agent-1
    host: 10.8.0.12
  - name: apps-1
    host: 10.8.0.13

modules:                       # приписка «модуль → нода»; ключ = имя из core/modules/<name>/
  postgres:      { node: data-1 }
  redis:         { node: data-1 }
  minio:         { node: data-1 }
  clickhouse:    { node: data-1 }
  backup-cron:   { node: data-1 }
  service-exporters: { node: data-1 }        # postgres/redis/nginx-exporter'ы
  platform-secrets: { node: data-1 }         # system-модуль — на ноде данных
  hermes-agent:  { node: agent-1 }
  litellm:       { node: agent-1 }
  langfuse:      { node: agent-1 }
  nginx:         { node: apps-1 }            # или { nodes: [apps-a, apps-b] } — multi-ingress (S2b)
  status-page:   { node: apps-1 }
  monitoring:    { node: apps-1 }
  logging:       { node: apps-1 }            # Loki-хранилище (singleton)
  log-collector: { mode: all-nodes }         # Alloy — на КАЖДОЙ ноде
  node-metrics:  { mode: all-nodes }         # node-exporter + cadvisor — на КАЖДОЙ ноде
  # clickhouse:    { mode: off }             # пример явного выключения модуля контекста
```

### 2.1 Виды размещения (закрытый словарь)

| Вид | Форма | Семантика |
|-----|-------|-----------|
| **singleton** | `{ node: <name> }` | Ровно один экземпляр в контексте, на указанной ноде |
| **all-nodes** | `{ mode: all-nodes }` | Экземпляр на КАЖДОЙ ноде контекста (коллекторы, node-метрики) |
| **nodes-list** | `{ nodes: [a, b] }` | Экземпляр на КАЖДОЙ из перечисленных нод; v1 — только nginx (multi-ingress, S2b) |
| **off** | `{ mode: off }` | Модуль не разворачивается ни на одной ноде контекста (явное выключение) |

`additionalProperties: false` на уровне записи размещения — новые виды только версией схемы.
Полнота: в multi-node контексте КАЖДЫЙ модуль инвентаря обязан иметь запись размещения
(включая `off`) — отсутствие записи = ошибка валидации (fail-fast против опечаток в имени модуля).

🧐 TRAP[DECISION] · 2026-08-22 · r2 · Формы `{follow}` и поле `public_host` УДАЛЕНЫ из схемы v1 ·
Rejected: зарезервировать «на будущее» · Reason: 0 потребителей; мёртвая ветка схемы тянет
resolver-ветку/parity-гейт; непроверенная лексика хуже отсутствующей · Rev: возврат additive
(форма + ветка резолва ≈ 2-4 ч) при первом кейсе «exporter не с сервисом» / SSH-over-public-channel.

### 2.2 Правила резолва

1. **Single-node контекст** (placement.yaml отсутствует): enabled-модули = `node.yaml#modules[enabled=true]`
   как сегодня; все provides-хосты — Docker DNS алиасы; bind 127.0.0.1. Резолвер — no-op.
2. **Multi-node контекст** (placement.yaml существует): эффективные модули ноды =
   `placement.modules`, отфильтрованный по «эта нода» (singleton-совпадение / all-nodes / член
   nodes[]-списка; `off` — исключён). `node.yaml#modules` для деплоя НЕ читается — только
   `config_overlay`. Противоречие node.yaml.modules ↔ placement = lint-WARNING с repair-подсказкой.
3. Нода обязана быть членом контекста: `node.yaml#contexts[0].name == placement.context`;
   каждая нода из placement.nodes имеет директорию `node-configs/<node>/node.yaml` (обратная связь — тоже гейт).
4. **1 нода = 1 контекст** — гейт в `node_yaml/validation.py`: `len(contexts) > 1` → ConfigValidationError
   (сейчас schema допускает массив без maxItems, читается только contexts[0] — закрываем жёстко).
5. **Порядок деплоя контекста** (данные раньше потребителей): оператор бутстрапит ноды в порядке
   «data → agent → apps» (runbook §8); платформа v1 НЕ строит распределённый оркестратор.
   Проверяемость — per-node `make status NODE=<n>` (глагол context-status отложен, §11).
6. **VPN-only host + аттестация**: `nodes[].host` — приватный адрес (RFC1918: 10/8, 172.16/12,
   192.168/16) или CGNAT 100.64/10; публичный адрес → ConfigValidationError; отсутствие
   `vpn_enforced: true` в multi-node → ConfigValidationError (инвариант 7).
7. **Multi-ingress co-location**: exposed-проект (`ai-platform.yaml#expose:true` + domain) обязан
   иметь target_node ∈ nodes[]-списка nginx; дубликат домена на другой ноде → ошибка (кросс-нодовый
   статический скан всех node-configs — они в одном репозитории). Headless-проекты — на любую ноду.
8. **off-зависимости**: `mode: off` запрещён модулю, на которого ссылается data-plane зависимость
   размещённого модуля из `module.yaml#depends_on` (langfuse/litellm требуют postgres;
   log-collector требует logging — иначе оба off). Инфра-упорядочивающие зависимости (nginx и пр.)
   ИСКЛЮЧЕНЫ из кросс-нодовых ограничений — иначе легитимные топологии false-RED.

## 3. Классификация модулей и разделение составных

Инвентарь: 13 docker-модулей COMPOSE_PROFILES + platform-secrets (system).

| Модуль | Вид размещения | Обоснование |
|--------|----------------|-------------|
| postgres, redis, minio, clickhouse, backup-cron | singleton (по умолчанию одна «data»-нода) | общие данные контекста, инвариант 1 беклога |
| hermes-agent, litellm, langfuse | singleton («agent»-нода) | агент + LLM-фасад; langfuse требует postgres → cross-node DSN (T2.7) |
| nginx | singleton ИЛИ `{nodes:[...]}` (multi-ingress — web-проекты на 2+ серверах; единственный модуль с nodes-формой в v1) | точка публикации 80/443; по экземпляру на каждой apps-ноде |
| status-page | singleton («apps»-нода) | сводка своей ноды в v1 (T3.6) |
| monitoring (prometheus+grafana) | singleton («obs», часто совпадает с apps) | центральный сбор метрик всех нод |
| logging (Loki) | singleton | ЕДИНОЕ хранилище логов контекста |
| **log-collector** (Alloy, НОВЫЙ модуль — выделен из logging) | all-nodes | «логирование на всех серверах»: коллектор шипит в центральный Loki по `LOKI_URL` |
| **node-metrics** (node-exporter+cadvisor, НОВЫЙ — выделен из infra-metrics) | all-nodes | метрики ХОСТА по природе per-node |
| **service-exporters** (postgres/redis/nginx-exporter, НОВЫЙ — выделен из infra-metrics) | singleton у сервисов | exporter'ы ходят Docker DNS к своим сервисам — живут с ними на одной ноде; скрейпинг центральным Prometheus — кросс-нодовый (публикация портов + peer-firewall T2.2/T2.4, file_sd T3.3) |
| platform-secrets | singleton (data) | system-модуль secrets.env |

🧐 TRAP[DECISION] · 2026-08-22 · — · Составные модули делятся ФИЗИЧЕСКИ (новые директории modules/), не service-фильтром compose · Rejected: `docker compose up <svc>`-подмножества внутри одного модуля · Reason: контракт healthcheck/gates/профилей модуль-уровневый; один модуль = одно решение размещения = предсказуемый резолв · Rev: если число модулей удвоится из-за новых split-кейсов — ввести placement_units в module.yaml
@rationale: split минимален (3 новых модуля из 2 составных); COMPOSE_PROFILES регенерируется генератором автоматически; single-node получает те же модули (стек функционально идентичен).

🧐 TRAP[DECISION] · 2026-08-22 · r2 · Порт-матрица: прямой 5432 НЕ публикуется, clickhouse native
публикуется как host-порт 19000 · Rejected: открыть 5432 «на всякий случай»; публиковать CH native
как 9000 · Reason: все потребители 5432 (backup-cron `POSTGRES_HOST=postgres`, postgres-exporter DSN,
сам pgbouncer) переезжают на data-ноду вместе с postgres — кросс-нодового потребителя нет; CH native
9000 коллидирует с minio API 9000 на общей data-ноде · Rev: minio уходит с data-ноды или CH получает
выделенную ноду → вернуть host-порт 9000.
@rationale: прецедент host≠container уже существует (langfuse 3001 host / 3000 container,
platform_ports.py:18-21); константа CLICKHOUSE_NATIVE_PEER=19000 добавляется в platform_ports.py,
langfuse CLICKHOUSE_MIGRATION_URL параметризуется `${CLICKHOUSE_NATIVE_PORT:-9000}` (локально 9000,
кросс-нодово 19000 через provision).

## 4. ВОЛНА 0 — Контракт: схема, загрузчик, валидация (ничего не меняет поведение)

| ID | Задача | Файлы | Действие |
|----|--------|-------|----------|
| T0.1 | placement.schema.json | `core/schemas/placement.schema.json` | draft-07: context (pattern kebab), `vpn_enforced` (boolean, required в multi-node), nodes[] {name, host}, modules {} со строгими формами node / mode:all-nodes / mode:off / nodes[] (только nginx), additionalProperties:false; БЕЗ follow/public_host (r2); семантика host (приватный диапазон) — в загрузчике, не в regex |
| T0.2 | Загрузчик+резолвер | `core/internal/shared/placement.py` | `load_placement(path)` (+VPN-host проверка +vpn_enforced), `resolve_node_modules(placement, node_name)` (singleton/all-nodes/nodes[]/off; БЕЗ follow-циклов — форма удалена), `service_host(placement, module, consumer_node)` → адрес для .env.platform. DI-seam'ы как NodeYaml; unit-тесты tmp_path |
| T0.3 | Гейт «1 нода = 1 контекст» | `core/internal/shared/node_yaml/validation.py`, schema note | len(contexts)>1 → ConfigValidationError(4); негативный тест |
| T0.4 | Валидатор топологии | `placement.py::validate_topology()` | модули существуют в core/modules/, ноды имеют node.yaml, context match, ПОЛНОТА записей (включая off), VPN-only host, vpn_enforced:true, exposed-проект ↔ nodes[] nginx + кросс-нодовая FQDN-уникальность (статический скан всех node-configs — один репозиторий), off без data-plane зависимых (по module.yaml#depends_on, инфра-deps исключены); БЕЗ secrets-consistency матрицы (r2: удалена); вызов из `node_config_validate` при наличии файла |
| T0.5 | Фикстуры сценариев | `tests/fixtures/placement/{s2,s2b,s3}.yaml` | S2 «данные отдельно», S2b «критичные/канарейки» (multi-ingress), S3 «data/agent/apps» — как входы тестов. S4 удалён (r2: не добавляет резолвер-паттернов сверх S3) |
| T0.6 | Гейт-тесты | `tests/gates/test_gate_placement.py` | схема↔загрузчик parity, single-node no-op, fail-fast матрица: неизвестный модуль/нода, неполнота записей, публичный host, отсутствие vpn_enforced, off с data-plane зависимыми, exposed вне nginx-нод |

**Acceptance W0:** `make check TEST_FILE=tests/unit/test_shared_placement.py` зелёный; отсутствие
placement.yaml → резолв no-op (все существующие тесты зелёные без правок); гейты ловят каждую ошибку
из §1.3 и матрицы T0.6 (публичный host, нет vpn_enforced, off-зависимости data-plane, exposed вне
nginx-нод); схема НЕ содержит follow/public_host.

## 5. ВОЛНА 1 — Резолв модулей ноды

| ID | Задача | Файлы | Действие |
|----|--------|-------|----------|
| T1.1 | Интеграция резолва в деплой | `bootstrap/deploy/deploy_orchestrator.py::_parse_modules` (+secrets_validator.parse_modules_from_node_yaml обёртка) | если placement есть → enabled_names из resolve_node_modules; оверлеи остаются из node.yaml; single-node путь байт-идентичен |
| T1.2 | Drift-сигнал node.yaml ↔ placement | `tests/gates/test_gate_placement.py` | при наличии placement.yaml: модуль enabled в node.yaml, но не размещён на этой ноде → lint-WARNING с repair-подсказкой («удали из node.yaml или перенеси в placement»), НЕ RED (r2: placement авторитетен по §2.2; гейт не охраняет устаревший источник). Повышение до RED — по факту первого инцидента дрейфа |
| T1.3 | Runbook порядок бутстрапа | раздел §8 этого плана → `AGENTS.md` (root, секция deploy-model) | data → agent → apps; повторный bootstrap идемпотентен; проверка готовности — per-node `make status NODE=<n>` (context-status отложен, §11) |

**Acceptance W1:** на fixture S3 резолв даёт: data-1={postgres,redis,minio,clickhouse,backup-cron,
service-exporters,platform-secrets,node-metrics,log-collector}, agent-1={hermes-agent,litellm,langfuse,
node-metrics,log-collector}, apps-1={nginx,status-page,monitoring,logging,node-metrics,log-collector};
drift node.yaml↔placement даёт WARNING (не RED) с repair-подсказкой.

## 6. ВОЛНА 2 — Security-префикс, кросс-нодовая адресация, публикация портов, firewall

### 6.0 Security-префикс (T2.0.* — ВЫПОЛНЯЕТСЯ ПЕРВЫМ, до любой публикации портов)

| ID | Задача | Файлы | Действие |
|----|--------|-------|----------|
| T2.0a | Redis auth (сегодня аутентификации НЕТ вообще) | `secret-definitions.yaml` (+REDIS_PASSWORD autogen), `core/modules/redis/docker-compose.base.yml` (requirepass/ACL), потребители: hermes-agent REDIS env (`base.yml:160`), redis-exporter DSN, healthcheck_deps | credentialed URL `redis://:${REDIS_PASSWORD}@…`; без этого публикация 6379 пирам = владение инстансом любым процессом пира |
| T2.0b | Loki tenant auth (сегодня `auth_enabled:false`, push+read на одном порту) | `logging/config/loki-config.yml`, config.alloy (header X-Scope-OrgID), grafana datasource, loki-vhost proxy header | tenant = имя контекста; alloy и grafana получают header через env; без этого любой пир читает И пишет логи контекста |
| T2.0c | pg_hba md5 → scram-sha-256 | `postgres/config/pg_hba.conf:31-33` (RFC1918-диапазоны сейчас md5) | password_encryption=scram (PG16 default), pgbouncer auth_query scram-совместим; md5 challenge-response replayable — активируется в момент публикации порта |
| T2.0d | vpn_enforced enforcement + control plane hygiene | placement.py валидатор (отказ multi-node без флага), AGENTS.md deploy-model | аттестация оператора «канал шифрован» обязательна; node-configs репозиторий — protected branch: git-write = способность перенаправить `nodes[].host` на свой RFC1918-IP и собирать plaintext-креды |

**Acceptance W2-s:** unit-тесты auth-пропагации (redis URL с паролем у hermes/exporter; loki header
в alloy+grafana+nginx); pg_hba не содержит md5 для RFC1918; ufw dry-run S3 ДО T2.0.* не содержит
ни одного peer-ALLOW.

### 6.1 Адресация, порты, firewall

| ID | Задача | Файлы | Действие |
|----|--------|-------|----------|
| T2.1 | provides-хосты по размещению | `core/internal/scaffold/gen_env_platform.py` (emit PLATFORM_<SVC>_* :313-334) | вход placement+consumer_node (consumer_node ← `ai-platform.yaml#target_node`, поле уже существует): сервис на чужой ноде → HOST = `<node>.host`; своя нода → Docker DNS алиас как сейчас. Хосты читаются из platform-infra.yaml SoT — правка emission, не литералов |
| T2.2 | Порт-матрица (КАНОН, r2) | platform_ports.py (+константы 9100/9187/9121/9113/3100/CLICKHOUSE_NATIVE_PEER=19000), compose base.yml затронутых модулей | bind параметр `SERVICE_BIND_HOST:-127.0.0.1` (env из provision): multi-node → host ноды, single-node → 127.0.0.1 без изменений. Публикуются ТОЛЬКО: 6432 (pgbouncer), 6379 (redis), 9000 (minio API), 8123 (CH HTTP), 19000 (CH native peer; локальный native остаётся непубликованным), 3100 (loki push), 9100+8080 (node-exporter/cadvisor), 9187/9121/9113 (exporters — сегодня host-портов НЕ имеют, добавить). **5432 НЕ публикуется** (потребители едут с postgres); CH-native 19000 вместо 9000 — коллизия с minio (TRAP §3). Порт-литералы только из shared/platform_ports.py |
| T2.3 | Peer-scoped firewall + reconcile fix | `core/internal/bootstrap/firewall.py` (+φ1 вызов) | источник правил «platform-peer»: `ufw allow from <peer_host> to any port <p>/tcp comment platform-peer-*`; вставка ПЕРЕД module-deny (ufw first-match); **stale-reconcile: delete-команда ОБЯЗАНА нести source IP** (сегодняшняя `["ufw","delete","allow",f"{port}/tcp"]` неоднозначна при ≥2 пирах на порту — `firewall.py:268`); verify_firewall: peer-ALLOW от известного пира = PASS, Anywhere на этих портах = FAIL; MODULE_PORTS_DENY сохраняется |
| T2.4 | Scrape-порты метрик | покрытие T2.2/T2.3 | 9100/8080 каждой ноды открыты ТОЛЬКО IP ноды monitoring; 9187/9121/9113 — только monitoring-ноде их data/apps-ноды |
| T2.5 | NO_PROXY/no_proxy_internal | `generate_platform_env.py` + platform-infra.yaml SoT + fallback-списки hermes-agent (`base.yml:151`) и monitoring (`base.yml:219-220`) | при multi-node дополнить списки адресами нод (базируется на W2.17 плана 1787342045763). ⚠️ Многофайловое: расширение SoT ломает superset-гейты (test_gate_env_shared_consistency / env_example_drift), пока не обновлены compose fallback'и |
| T2.6 | Секреты кросс-нодовые (doc-note) | `secret-definitions.yaml` примечание + AGENTS.md | значения общих кредов (LITELLM_MASTER_KEY и т.п.) сознательно дублируются в secrets.env нужных нод (sops per-node). r2: консистентность-валидатор удалён (матрица secret×node не существует; ошибка всплывает на deploy-healthcheck за минуты); per-node sops recipients — Rev §11 |
| T2.7 | DSN модулей кросс-нодовые | `core/modules/{langfuse,litellm}/docker-compose.base.yml` (DATABASE_URL `@${POSTGRES_HOST:-pgbouncer}:6432`) + langfuse `CLICKHOUSE_MIGRATION_URL` → `${CLICKHOUSE_HOST:-clickhouse}:${CLICKHOUSE_NATIVE_PORT:-9000}` | langfuse-worker наследует тот же якорь x-langfuse-env — правка одного места; hermes-agent уже параметризован. Кросс-нодово provision подставляет CH native 19000; локально дефолт 9000 |
| T2.8 | Platform-vhost upstream'ы | `core/modules/nginx/config/*.conf` (grafana/prometheus/loki/langfuse/hermes-dashboard/status-page) | upstream через envsubst `${UPSTREAM_*}`; ⚠️ entrypoint envsubst подставляет ТОЛЬКО переменные, объявленные в env контейнера — дефолты резолвить на compose-уровне (`environment: UPSTREAM_GRAFANA: ${UPSTREAM_GRAFANA:-grafana:3000}` из placement), шаблон использует bare `${UPSTREAM_*}`; механизм уже существует (templates/*.conf.template, `base.yml:54-65`) — третьего механизма не создаёт |

**Acceptance W2:** fixture S3 → `.env.platform` проекта на apps-1 содержит
`PLATFORM_POSTGRES_HOST=10.8.0.11`; langfuse на agent-1 получает DATABASE_URL с host 10.8.0.11 и
CLICKHOUSE_MIGRATION_URL с host 10.8.0.11 порт 19000; hermes-dashboard.conf на apps-1 резолвит
upstream agent-1; ufw dry-run S3 показывает allow from 10.8.0.13 to port 6432 и НЕ показывает
Anywhere и НЕ содержит 5432; peer-матрица включает 9187/9121/9113 и 19000; delete-команды
stale-reconcile содержат `from <ip>`; single-node diff пуст; `make check MARKER=contract` зелёный.

## 7. ВОЛНА 3 — Единая наблюдаемость контекста

| ID | Задача | Файлы | Действие |
|----|--------|-------|----------|
| T3.1 | Выделить log-collector | `core/modules/log-collector/` (compose+config.alloy+module.yaml+healthcheck.sh) из logging | Alloy уходит из logging; endpoint сегодня ЗАХАРКОЖЕН (`config.alloy:31` → `http://loki:3100`) → параметризовать `LOKI_URL:-http://loki:3100` + run-flag `--config.expand-env`; ⚠️ УДАЛИТЬ alloy→loki depends_on (WAL буферизует, self-heal); healthcheck без локального loki `/ready`. Labels-контракт сохранён |
| T3.2 | Split infra-metrics | `core/modules/node-metrics/`, `core/modules/service-exporters/`; удаление infra-metrics | состав §3; exporter'ы ходят Docker DNS к своим сервисам. ⚠️ Blast radius: строка infra-metrics в ~25 файлах (гейты test_gate_module_profiles/compose_base_contract/make_contract/healthcheck_*, unit-тесты, volumes SoT platform-infra.yaml:69, GENERATED) — объём механический, не сложность |
| T3.3 | Remote targets Prometheus | `monitoring/config_renderer.py` + prometheus_targets.py рендерер | job `platform-projects` УЖЕ file_sd (`/prometheus-targets/*.json`, refresh 30s, mount :112) — добавить job nodes: `<node>:9100`, `<node>:8080` + service-exporters размещённых нод (`<node>:9187/9121/9113`); рендер идемпотентен. ⚠️ ЛОВУШКА: при миграции static→file_sd сохранить job_name 1:1 (`node-exporter`, `cadvisor`) — иначе дашборды/алерты молча ломаются |
| T3.4 | Алерты мульти-ноды | `monitoring/config/platform-alerts.yml` (нативный формат, glob rule_files) | RemoteNodeDown (`up{job="nodes"}==0 for:5m`) — тривиально в существующем формате; LokiCollectorStale — **Prometheus-based** (scrape freshness коллектора), НЕ Loki-log-based (Grafana chain format — лишняя сложность; метрики-источника для log-based сегодня нет) |
| T3.5 | Grafana/Loki datasource | без изменений | Loki один на контекст (+tenant header T2.0b); дашборды работают как есть |
| T3.6 | status-page multi-node | отложено (§11) | v1 показывает свою ноду; cross-node сводка — после первого реального контекста |

**Acceptance W3:** COMPOSE_PROFILES регенерирован (`make generate-manifests`, check-manifests чистый);
fixture S3: alloy на agent-1 имеет LOKI_URL=http://<apps-1>:3100 и header tenant; prometheus targets
содержат 3 ноды c сохранёнными job_name; на single-node стек функционально эквивалентен прежнему
(те же сервисы, новые имена модулей); `make check MARKER=gates` зелёный.

## 8. Сценарии декомпозиции (2-4 сервера, один контекст)

Целевые раскладки; каждая валидируется фикстурами T0.5 и выражается ТОЛЬКО placement.yaml.
Порядок бутстрапа: data → agent → apps/obs (данные раньше потребителей).
Приоритет сценариев по владельцу (вечер 2026-08-22): **S2 первичен** (реальный ожидаемый кейс),
**S2b — второй** (критичные/канарейки), S3 — референсная комбинация.

### S2 — «данные отдельно» (2 ноды) — ПЕРВИЧНЫЙ СЦЕНАРИЙ
| Нода | Модули |
|------|--------|
| data-1 | postgres, redis, minio, clickhouse, backup-cron, service-exporters + node-metrics, log-collector |
| main-1 | nginx, hermes-agent, litellm, langfuse, monitoring, logging, status-page + node-metrics, log-collector |

Открытия: data-1 → 6432/6379/9000-minio/8123/19000-CH-native для IP main-1; 9100/8080/9187/9121
для IP main-1; main-1 → 3100 для IP data-1 (collector push); scrape-порты не нужны (monitoring
локален). Прямой 5432 НЕ открывается.

**Решение по постгресу (принято архитектом, подтверждено владельцем как первичный кейс):**
- pgbouncer — единственный кросс-нодовый фасад (6432, scram через auth_query); прямой 5432 не
  публикуется: все его потребители (backup-cron `POSTGRES_HOST=postgres`, postgres-exporter DSN,
  сам pgbouncer) переезжают на data-ноду вместе с postgres — restore-ко-локация сохраняется.
- Латентность не риск: TCP-probe healthcheck_deps 3s и healthcheck-интервалы langfuse 15s/10s
  дают запас ≫ RTT VPN 20-50 мс; write-heavy ingest langfuse асинхронный (worker+langfuse-redis).
- Реальный риск — availability: `/api/public/health` langfuse проверяет зависимости → при морганиях
  VPN контейнеры agent-ноды уходят в рестарт-циклы. Mitigation v1: RemoteNodeDown алерт +
  restart policy; healthcheck-контракт не меняем (Rev §11 при первом инциденте flap'а).

### S2b — «критичные проекты / канарейки» (data + 2 apps-ноды, multi-ingress)
💼 TRAP[BUSINESS] · 2026-08-22 · HI · Критически важные проекты изолируются на выделенной ноде для максимального аптайма; остальные — «канарейки» · Source: owner · Risk: деплой канарейки не должен влиять на критичные сервисы; изоляция blast-radius деплоев важнее плотности упаковки
| Нода | Модули |
|------|--------|
| data-1 | как S2 |
| apps-critical | nginx, monitoring, logging, status-page + node-metrics, log-collector (+критичные web-проекты) |
| apps-canary | nginx + node-metrics, log-collector (+проекты-канарейки) |

nginx в форме `{nodes: [apps-critical, apps-canary]}`; каждая apps-нода публикует свои домены,
FQDN-уникальность кросс-нодовая (статический скан). TLS: wildcard DNS-01 выдаётся на любой ноде
независимо (S3-cache restore существует) — cert-стоимость multi-ingress ≈ ноль.
**PREREQUISITE оператора — DNS-steering:** wildcard-запись указывает на одну ноду; каждый FQDN
критичного/канареечного проекта получает A-запись на IP своей ноды. Платформа DNS не управляет —
шаг фиксируется в runbook (T1.3) как обязательный перед первым exposed-проектом на второй ноде.
Кейс: рост числа web-проектов без переезда существующих; деплой канареек не затрагивает critical-ноду.

### S3 — «data / agent / apps+obs» (3 ноды, референсный пример §2)
| Нода | Модули |
|------|--------|
| data-1 | postgres, redis, minio, clickhouse, backup-cron, service-exporters |
| agent-1 | hermes-agent, litellm, langfuse |
| apps-1 | nginx, monitoring, logging, status-page |

Плюс node-metrics+log-collector на всех трёх. Открытия: data-1 → 6432 (agent-1, apps-1),
6379/9000-minio/8123/19000 (apps-1); agent-1 → 4000/3001 для IP apps-1 (проекты ходят в LLM/langfuse);
все ноды → 9100/8080 только для apps-1; data-1 → 9187/9121 для IP apps-1; 3100 только для IP
agent-1+data-1. Кейс: агент и проекты на разных серверах при общем хранилище — прямой кейс беклога.
(S4 «наблюдаемость отдельно» выводится из S3 переносом monitoring/logging на четвёртую ноду —
новых резолвер-паттернов не добавляет, фикстура удалена в r2.)

### Границы сценариев
- Проекты привязаны `ai-platform.yaml#target_node` (required) — размещение проектов вне placement.yaml
  (они не модули платформы). Multi-ingress: exposed-проекты распределяются по нодам из nodes[] nginx;
  headless-проекты (боты long-polling) — на любую ноду: vhost не создаётся, `.env.platform` получает
  кросс-нодовые хосты после Волны 2. Webhook-боты с доменом = exposed → нода из nodes[] nginx.
- **VPN — prerequisite + аттестация:** все `nodes[].host` — приватные адреса, `vpn_enforced: true`
  обязателен (инвариант 7); платформа VPN не строит.
- **DNS-steering — prerequisite S2b** (см. выше); для S2/S3 не требуется (одна ingress-нода).
- Бэкап: backup-cron покрывает ТОЛЬКО postgres (pg_dumpall+WAL → внешний Timeweb S3); project/minio/
  clickhouse/loki volumes не бэкапятся и сегодня на single-node (app-data-стаб — долг §11).
  Потеря main/apps-ноды в S2 = файлы проектов + Loki-логи + CH-аналитика невосстановимы — граница
  зафиксирована честно, Rev §11.
- SPOF фиксируется честно: одна копия каждого singleton — RTO/RPO секция root core/AGENTS.md остаётся
  каноном отказоустойчивости; multi-node v1 НЕ про HA, а про разделение нагрузки и ролей данных.

## 9. Верификация

| Проверка | Способ |
|----------|--------|
| Резолв/валидация топологии | unit: test_shared_placement.py (нативные импорты, tmp_path, LDD IMP:9) |
| Гейты контракта | tests/gates/test_gate_placement.py: схема-parity, drift-WARNING, 1-контекст-гейт, peer-firewall матрица (включая delete-форму с source IP), VPN-host + vpn_enforced, off-зависимости data-plane, exposed↔nginx co-location |
| Security-префикс | redis auth пропагирован потребителям (hermes/exporter), loki tenant header в alloy/grafana/vhost, pg_hba без md5 для RFC1918, ufw-план пуст ДО T2.0.* |
| Адресация .env.platform | unit gen_env_platform: local vs remote host emission (fixture S3) |
| Порт-матрица | гейт: peer-план S2/S3 содержит {6432,6379,9000,8123,19000,3100,9100,8080,9187,9121,9113}, НЕ содержит 5432, НЕ содержит Anywhere |
| DSN модулей | unit: langfuse/litellm DATABASE_URL host = `${POSTGRES_HOST}`; langfuse CH-migration host/port = `${CLICKHOUSE_HOST}:${CLICKHOUSE_NATIVE_PORT}` (S3 → 10.8.0.11:19000; дефолт clickhouse:9000) |
| Platform-vhost upstream'ов | unit: hermes-dashboard/langfuse vhost upstream = node.host из placement; single-node — Docker-DNS дефолт |
| Split модулей | существующие module-гейты по новым директориям + check-manifests после generate-manifests; job_name сохранены |
| E2E мульти-нода | requires_node вручную: 2 ноды на тестовых VPS (S2) — `make test-node` расширенная сцена; НЕ блокирующий CI-гейт (канон requires_node) |
| Регрессия single-node | полный `make check` до чистоты; отсутствие placement.yaml во всех существующих fixture/тестах |

$TEST_SPEC
| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| tests/unit/test_shared_placement.py | test_load_placement_s3_fixture | Валидная S3-топология загружается, резолв даёт ожидаемые наборы модулей нод | shared/placement.py |
| tests/unit/test_shared_placement.py | test_single_node_noop | Нет файла → resolve_node_modules = None / легаси-путь | shared/placement.py |
| tests/unit/test_shared_placement.py | test_service_host_remote_vs_local | consumer на чужой ноде → node.host; своей → Docker alias | shared/placement.py |
| tests/unit/test_shared_placement.py | test_vpn_enforced_required | multi-node без vpn_enforced:true → ConfigValidationError | shared/placement.py |
| tests/unit/test_gen_env_platform_multinode.py | test_remote_postgres_host_emitted | S3: PLATFORM_POSTGRES_HOST=10.8.0.11 в .env.platform проекта apps-ноды | scaffold/gen_env_platform.py |
| tests/gates/test_gate_placement.py | test_multi_context_rejected | contexts[] > 1 → ConfigValidationError | shared/node_yaml/validation.py |
| tests/gates/test_gate_placement.py | test_unknown_module_or_node_red | placement ссылается на несуществующий модуль/ноду → RED | shared/placement.py |
| tests/gates/test_gate_placement.py | test_peer_firewall_matrix_canonical | build_rules(S2) содержит allow from <ip> на {6432,6379,9000,8123,19000,3100,9100,8080,9187,9121}; НЕ содержит 5432 и Anywhere | bootstrap/firewall.py |
| tests/gates/test_gate_placement.py | test_stale_reconcile_delete_carries_source | при ≥2 пирах на одном порту delete-команда содержит `from <ip>` (не голый `delete allow <port>/tcp`) | bootstrap/firewall.py |
| tests/gates/test_gate_placement.py | test_verify_firewall_accepts_peer_allow | ALLOW на 6432 от известного пира → PASS; Anywhere → FAIL | bootstrap/firewall.py |
| tests/gates/test_gate_placement.py | test_public_host_rejected | nodes[].host = публичный IP → ConfigValidationError (инвариант 7) | shared/placement.py |
| tests/gates/test_gate_placement.py | test_off_module_with_data_plane_dependent_red | mode:off у postgres при размещённом langfuse → RED; mode:off у nginx при живом hermes → GREEN (инфра-deps исключены) | shared/placement.py |
| tests/gates/test_gate_placement.py | test_exposed_project_requires_nginx_node | exposed-проект с target_node вне nodes[] nginx → RED; S2b (обе ноды с nginx) → зелёный | shared/placement.py + валидатор топологии |
| tests/gates/test_gate_placement.py | test_module_inventory_completeness | в multi-node placement без записи для модуля инвентаря → RED | shared/placement.py |
| tests/unit/test_security_prefix.py | test_redis_password_propagated | REDIS_PASSWORD в secret-definitions; hermes compose и redis-exporter DSN используют credentialed URL | modules/redis + secret-definitions |
| tests/unit/test_security_prefix.py | test_loki_tenant_header | alloy config и grafana datasource содержат X-Scope-OrgID; pg_hba не содержит md5 для RFC1918 | modules/logging + modules/postgres |
| tests/unit/test_module_dsn_multinode.py | test_langfuse_litellm_db_host_remote | S3: DATABASE_URL host = data-node host; single-node: pgbouncer дефолт | modules/{langfuse,litellm} compose env |
| tests/unit/test_module_dsn_multinode.py | test_clickhouse_migration_port | S3: CLICKHOUSE_MIGRATION_URL = 10.8.0.11:19000; single-node: clickhouse:9000 | modules/langfuse compose env |
| tests/unit/test_platform_vhost_upstream.py | test_hermes_vhost_upstream_remote | S3: hermes-dashboard upstream = agent-1 host; single-node: hermes-agent Docker-DNS | nginx vhost envsubst |
| tests/unit/test_node_metrics_module.py | test_module_contract_healthcheck | node-metrics/service-exporters/log-collector проходят канонические module-гейты; job_name scrape-целей сохранены | core/modules/node-metrics |

## 10. Бриф 009 — какие паттерны реально портируем (вердикт)

| Brief-009 | Вердикт | Куда |
|-----------|---------|------|
| T6 multi-node контракт (R3) | **ПОРТИРУЕМ — это и есть данный план** | Волны 0-3 (post-release фаза T6); закрывает §8 Q3/Q6 брифа |
| T7 реестр single-writer | **ЧАСТИЧНО, сейчас** — принцип «один писатель на поле» распространяется на топологию: placement.yaml = единственный SoT размещения/адресов нод; drift-WARNING вместо tri-write | Волна 0 (T0.4), Волна 1 (T1.2) |
| T8 словарь/резервирование терминов | **ДА, дёшево** — зарезервировать: «размещение/placement», «шаримый модуль (singleton)», «per-node модуль (all-nodes)», «нода-пир», «критичная нода / канарейка» | Волна 1 (T1.3) — секция root AGENTS.md |
| T3 гибридная схема ai-platform.yaml | НЕ сейчас — placement платформенный конфиг, проектный манифест не трогает; зона исключена планом 1787342045763 | после T3-плана |
| T1 шаблонное дерево, T2 layout-convention, T4 TS-практики, T5 GHCR npm, T9 аналитика | НЕ связаны с multi-node — вне скоупа | их собственные волны брифа 009 |
| §4 «не портируем» (HITL, invoke-spine, …) | Подтверждаю — обоснования брифа остаются в силе | — |

## 11. Отложено и удалено (осознанно)

| Что | Почему | Rev-условие |
|-----|--------|-------------|
| Управляемая платформой приватная сеть (wireguard/swarm overlay) | VPN-канал — ответственность оператора (prerequisite + аттестация `vpn_enforced`); платформа не строит overlay | >3 ноды или первый инцидент перехвата внутреннего трафика |
| Бэкап не-PG состояния (project/minio/clickhouse/loki volumes) | Долг существует И в single-node: app-data-скрипт — мёртвый стаб phase-02 («No app-data volumes to back up»), minio/CH/loki тома не бэкапились никогда; multi-node экспозицию не ухудшает | первый stateful-проект ИЛИ первый инцидент потери не-PG данных → реализовать phase-07 |
| status-page cross-node сводка | v1 = своя нода; `make status NODE=` покрывает оператора | первый реальный multi-node контекст |
| Распределённый оркестратор порядка старта | runbook + идемпотентный bootstrap достаточны для 2-4 нод | >4 нод или частые пересоздания нод |
| Глагол context-status (перенесён из Волны 1 в r2) | `for n in …; make status NODE=$n` достаточен для ≤4 нод; verb-registration overhead (glossary/namelint/manifest) до реальной потребности | >4 нод или операционная боль при sweep |
| follow-размещение — **УДАЛЕНО** (не отложено) | 0 потребителей; мёртвая ветка схемы хуже отсутствующей; возврат additive | первый кейс «exporter не с сервисом» → вернуть форму (≈2-4 ч: schema+resolver) |
| HA/репликация postgres, multi-instance singleton | multi-node v1 про размещение, не про HA | RTO/RPO секция core/AGENTS.md — канон отказоустойчивости |
| Per-node sops recipients (сейчас общий AGE master key дешифрует все ноды) | один recipient-компромисс = дешифровка контекста; для доверенных нод оператора приемлемо | первый контекст с недоверенными/арендованными нодами |
| CH native host-порт 19000 → обратно 9000 | коллизия с minio API на общей data-ноде | minio уходит с data-ноды или CH получает выделенную ноду |
| Drift node.yaml↔placement WARNING → RED | placement авторитетен; RED охранял бы мёртвый источник | первый инцидент реального дрейфа конфигурации |
| Healthcheck langfuse при VPN-морганиях (рестарт-циклы) | v1: RemoteNodeDown алерт + restart policy достаточно | первый flap-инцидент → рассмотреть debounce healthcheck |

## 12. Порядок исполнения и метрики успеха

**Порядок:** строго после завершения `.kilo/plans/1787342045763-simplify-refactor-waves.md`.
Волна 0 → 1 → 2 (**T2.0.* security-префикс строго первым**) → 3 последовательно
(волна = feat-коммит; ≤2 коммитов на волну по канону U-83).
Перед стартом: `make pre-commit-install` (новый клон), журнал прогонов — `.ai/plans/010-multi-node-module-placement/logs/`.
Зоны Brief-009 T1/T3/T7 (payload шаблонов, enum type, tri-write реестра) НЕ трогаются.
E2E на 2 тестовых VPS (S2) — ручной прогон `make test-node` перед финализацией (release-checklist канон).

**Метрики успеха:**
1. `make check` зелёный после каждой волны; `make agent-check` exit 0 в конце.
2. Single-node регрессия = 0: все существующие тесты зелёные БЕЗ правок fixture (нет placement.yaml → no-op).
3. S3-фикстура: резолв даёт раскладку §5; `.env.platform` проекта apps-ноды указывает на 10.8.0.11;
   langfuse/litellm DSN указывают на data-ноду; CH-migration = 10.8.0.11:19000; hermes-dashboard
   upstream = agent-1; ufw-план не содержит ни одного Anywhere и НЕ содержит 5432; peer-матрица
   включает {6432, 6379, 9000, 8123, 19000, 3100, 9100, 8080, 9187, 9121, 9113}.
4. Security-префикс: redis требует пароль (потребители пропагированы), Loki tenant-header сквозной,
   pg_hba без md5 для RFC1918; ufw-план пуст ДО выполнения T2.0.*.
5. Наблюдаемость: prometheus targets включают все ноды S3 (node-metrics + service-exporters,
   job_name сохранены); алерт RemoteNodeDown срабатывает на остановленной ноде в fixture-тесте правил;
   Loki получает логи с трёх нод (E2E S2).
6. Multi-ingress: S2b-фикстура — exposed-проекты на обеих нодах зелёные в валидаторе топологии;
   DNS-steering зафиксирован как prerequisite в runbook; exposed вне nginx-нод → RED.
7. Словарь: схема НЕ содержит follow/public_host; drift даёт WARNING (не RED); глагол context-status
   отсутствует (отложен). Доки: root AGENTS.md deploy-model описывает multi-node (VPN-prerequisite +
   аттестация, границы бэкапа, DNS-steering); backlog-файл закрыт ссылкой на этот план.

## 13. Связи

- Беклог: `.ai/backlog/multi-node-hermes-split.md` (статус обновляется при старте волны 0)
- Brief-009 T6/§8 Q3+Q6: `.ai/plans/009-ai-project-platform-enablement/01-Brief.md`
- Текущий план (предшественник): `.kilo/plans/1787342045763-simplify-refactor-waves.md` (W2.17 :92, T1.4 :55 переиспользуются)
- Ревизия 2 основана на критическом анализе 2026-08-22 (6 субагентов верифицировали план против кода;
  все ссылки file:line в тексте — из этой верификации)
- SoT-точки интеграции: platform-infra.yaml (provides), gen_env_platform.py, firewall.py,
  deploy_orchestrator.py::_parse_modules, shared/schema_validator.py, shared/platform_ports.py
