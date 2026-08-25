<!-- GREP_SUMMARY: pre-release QA audit devplans 009 010 meta-refactoring findings verdict NOT_READY docker-user placement delivery role-regex -->
<!-- STRUCTURE: ▶ контекст → ⚡ состояние дерева → ⊕ P0-находки → ∑ P1/P2 → ⎋ покрытие + next steps -->

# 15-VerificationReport — предрелизный QA-аудит (009 / 010 / meta-refactoring)

Дата: 2026-08-25 · Метод: 10 параллельных субагентов-аудиторов + локальная верификация лидом ·
База: HEAD `42679a0` + незакоммиченная Волна 1 (T1.1–T1.6 из 14-DevPlan-fixes) ·
Исключено из поиска: всё, уже зафиксированное в 14-DevPlan-fixes.md (C1–C6, R1–R15, L/G-хвосты, B1–B4).

## Вердикт

# **NOT_READY**

Полный `make check` 16:59 — RED (9 fail / 5031 pass); к моменту синтеза (~17:35) все 9 закрыты
параллельным имплементатором, но: agent-check RED (blocking=5), basedpyright/ruff хвост,
~30 файлов Волны 1 не закоммичены. Новые HIGH-находки ниже — вне плана фиксов.

⚠️ Аудит шёл по живому дереву: параллельная сессия имплементации меняла файлы во время аудита
(17:01–17:21: TRAP-фикс ssh_cmd_builder, loc-allowlist 700→750, восстановление усечённого тела
теста, revert ручной правки .env.example, регенерация манифестов). Перед финальным гейтом — freeze.

---

## P0 — блокеры релиза (новые, вне 14-DevPlan-fixes)

| # | Находка | Локация | Суть |
|---|---------|---------|------|
| P0-1 | **DOCKER-USER не знает о peer'ах — multi-node data-plane мёртв, firewall «зелёный» ложно** | `core/internal/bootstrap/docker_user_policy.py:94-101`, `firewall.py`, `deploy_orchestrator.py:442` | Кросс-нодовые порты публикуются через DNAT → трафик идёт PREROUTING→FORWARD→DOCKER-USER, где политика = established+80/443+bridge-nets+catch-all DROP. ufw peer-ALLOW живёт ниже и для DNAT'ed трафика не выполняется никогда. Итог: (а) весь data-plane (6432/9000/8123/19000/3100/9100/…) молча DROPается при зелёном `verify_firewall`; (б) реальная изоляция = catch-all без семантики пиров — первая же «починка связности» ACCEPT RFC1918 открывает порты всему интернету. Дополнительно DOCKER-USER видит post-DNAT порт (19000→dport 9000), ufw это выразить не может. Фикс: peer-source ACCEPT'ы из placement.yaml в `desired_docker_user_rules()` по post-DNAT портам PEER_PUBLISH_PORTS; verify против `iptables-save`. *Подтверждено лидом чтением кода.* |
| P0-2 | **placement.yaml физически не доставляется на ноды** | `core_deliverer.py:508-549`, `context_overlay.py:111`; потребители ждут `/opt/<ctx>/placement.yaml` | Phase 2 rsync кладёт только `node-configs/<node>/`; context-overlay кладёт overlay-репо. Все потребители (`deploy_orchestrator.py:375`, `placement.py:767`, `modules_healthcheck.py:322`) деривируют путь, куда никто не пишет → `load_placement→None` → деплой резолвит из node.yaml (placement НЕ авторитетен), peer-firewall `[]`, healthcheck проверяет чужие singleton'ы. Весь multi-node контур на реальном VPS молча деградирует в legacy. Фикс: доставка context-level файла в core_deliverer либо деривация от доставляемого пути. |
| P0-3 | **Postgres role-regex отклоняет канонические имена проектов + silent success** | `core/modules/postgres/hooks/on_project_deploy.py:73,237-240` | `_ROLE_NAME_RE = ^[a-zA-Z0-9_]+$`: роль `my-app_user` для ЛЮБОГО kebab-case проекта (канон root AGENTS.md) не матчится → лог FATAL и **`return 0`** → деплой зелёный, роли/GRANT/credentials НЕТ. Фича DevPlan 133 W2 мертва на канонических именах. Фикс: `[a-z0-9_-]` в regex (SQL quoted) + ненулевой rc на skip-by-invalid-name или валидация на adopt/sync-env. *Подтверждено лидом.* |
| P0-4 | **Freshness-гейт sha-pins упадёт в CI на первом же пуше** | `tests/gates/test_gate_workflow_sha_pins.py:496-531` + `push-gate.yml:75`, `platform-gate-fast.yml:70` | Checkout depth=1 → `git log -- <path>` резолвит last-touch в граничный коммит → на следующем пуше `merge-base --is-ancestor` ломается → ложный stale-pin RED. Локально зелёный, CI — красный. Фикс: `fetch-depth: 0` либо fallback через дату пина при shallow. |
| P0-5 | **Профильные метаданные затирают lookup-ключ provisioner'а → бесконечный generate дублей ключей** | `core/internal/llm/key_provisioner.py:689-694` | `key_metadata.update(profile_metadata)` перезаписывает зарезервированный `project` → совпадение никогда не находится → GENERATE на каждом прогоне (budget-bearing дубли, класс DATA). Фикс: merge с запретом перезаписи `project`. |
| P0-6 | **Unmanaged-проект проходит pre-deploy L1 гейт** | `core/internal/deploy/verify_contracts.py:356,401-409` | При l1_only (receive/orchestrator) drift-practices скипается → 0 findings → exit 0. Заявленный контракт `[PRACTICES:UNMANAGED] … L1-контракты блокируют деплой` (:234) не реализован. Фикс: L1-finding `drift-practices-unmanaged` в l1_only-режиме. |
| P0-7 | **AGE мастер-ключ в argv/core-deploy CI** | `.github/workflows/core-deploy.yml:230-233` | `export AGE_SECRET_KEY="${{ secrets… }}"` → интерполяция в remote `bash -c` → ключ в `/proc/*/cmdline` на всё время node-update. Тот же класс, что C5, канал не мигрирован на готовый `remote_executor.execute_update(secret_prelude=…)`/stdin-prelude. |

## P1 — важно до/сразу после релиза

| # | Находка | Локация |
|---|---------|---------|
| P1-1 | `validate_topology` в проде вызывается без `projects_scan` → инвариант exposed target_node/FQDN-уникальность не проверяется нигде кроме тестов | `deploy_orchestrator.py:392-396` vs `placement.py:671` |
| P1-2 | `generate_node_targets` — 0 production-вызовов: нодовые file_sd не рендерятся → RemoteNodeDown/LokiCollectorStale неспособны сработать | `prometheus_targets.py:274`, config_renderer без NodeInfo |
| P1-3 | REF-0010 honesty-jobs разорвали peer-матрицу: 9127 (pgbouncer-exporter) и 9122 (langfuse redis-exporter) эмитятся в targets, отсутствуют в PEER_PUBLISH_PORTS и deny-листах | `prometheus_targets.py:166-179` vs `firewall.py:129-147,194-209` |
| P1-4 | Многострочный секрет (AGE-ключ с `\n` из env) десинхронизирует stdin-транспорт → silent-коррупция prelude, rc=0 | `ssh_cmd_builder.py:484-502` + `build-ssh-cmd.sh:52`; env-источник вербатим (`node_detect.py:121`) |
| P1-5 | `failed>0 → PlatformError` задекларирован, но не реализован: partial dict → exit 0 → φ11 фиксирует llm-keys done при проваленных ключах | `key_provisioner.py:793-805` vs инвариант :27-29 |
| P1-6 | TOCTOU provision: FileLock охраняет запись стора, но list→find→generate вне лока → конкурентные прогоны создают дубли | `key_provisioner.py:475` vs `:647-783` |
| P1-7 | Недетерминированный победитель коллизии metadata.project (first-match) → второй ключ навсегда орфан с бюджетом, без WARN | `admin_client.py:643-651` |
| P1-8 | Пустой `token` из листинга персистится поверх рабочего ключа стора → mass-401 | `key_provisioner.py:701,709-710` |
| P1-9 | `security_opt` dict-форма `{seccomp: unconfined}` не детектируется (подтверждено двумя агентами независимо) | `verify_contracts.py:990-1002` |
| P1-10 | GPU/device-reservation (`deploy.resources.reservations.devices`, `gpus:`) вне deny-set — device-доступ мимо закрытого `devices` | `verify_contracts.py` |
| P1-11 | Сканируется ровно один compose-файл: override/include слепая зона статического гейта | `verify_contracts.py:324` |
| P1-12 | Stale peer-правила не реконсилятся (collect_stale пропускает peer-порты, build_peer_rules аддитивен) → потом `verify_firewall` FAIL перманентно без self-heal | `firewall.py:506-507,645-653` |
| P1-13 | minio scrape-target на ноде без nginx/langfuse не получает peer-правило → job minio молча down в топологии с выделенной obs-нодой | `prometheus_targets.py:180-186` vs CONSUMER_OF |
| P1-14 | Смешанный регистр db_name: `CREATE DATABASE MyApp` создаёт `myapp`, quoted GRANT падает (non-fatal), `.platform-db.env` пишет `MyApp` → приложение не подключается | `on_project_deploy.py:148` vs `:309-313,436` |
| P1-15 | `ssh_exec_stdin` — прямой ssh БЕЗ timeout-обёртки (инвариант lib/ssh.sh, класс P02 CI-hang); гейт no-direct-binary сканирует только `core/entrypoints/*.sh`, internal-обход не ловится; `root@` захардкожен | `build-ssh-cmd.sh:86-91` |
| P1-16 | `input()` без TTY в adopt-project: EOFError traceback после частичной адопции (ai-platform.yaml уже создан) | `project_adopter.py:181-185` |
| P1-17 | Опциональный ci_root-слот глотает позиционные флаги (`--force` съеден молча); 4-я строка stdin отбрасывается; пустое обязательное значение даёт prelude без слота — всё rc=0, отказ проявляется далеко на ноде | `ssh_cmd_builder.py:444-448,497-502` |
| P1-18 | Одиночное ПУСТОЕ значение секрета невозможно через stdin (`printf '\n'` → FATAL got 0) вопреки TRAP-клейму; argv-путь принимал | `_read_secret_stdin` — частично отфикшен 17:01 (ровно один хвостовой `\n`), кейс count=1 перепроверить |
| P1-19 | deploy-project.yml: inputs интерполируются сырыми в run-блоки и строки remote-команд (defense-in-depth разрыв; эксплуатация требует write-доступа) | `deploy-project.yml:147,161,186,431,441` |
| P1-20 | DRY_RUN core-deliver теперь роняется FATAL до preview на машине без AGE-ключа (раньше: WARN + полный WOULD-план) | `core_deliverer.py:780-788` |

## P2 — низкий приоритет

- Рыхлая эвристика «already simplified» в adopter (`:171`) — legacy workflow с любым `uses:` считается упрощённым, mutable-канал не переписывается; строгий regex нужен.
- Невалидные `# noqa: EXC` директивы (несуществующий код — ничего не глушат) + несуществующий синтаксис `# ruff: ignore[BLE001]`: `on_project_deploy.py:493`, `key_provisioner.py:942/944`, `project_adopter.py:580`.
- `verify` verb: токен `<node>` не валидируется форматом (только project) — `orchestrator_cli.py:381`.
- Таймаут-литералы `lib/ssh.sh:111,171` (900/60) без parity-гейта к `shared/timeouts.py` — silent drift (TRAP[DECISION] есть, enforcement нет).
- `redis/healthcheck.sh:33`: литерал `-p 6379` без `${REDIS_PORT:-…}` и ссылки на platform_ports.py (sibling-healthchecks параметризованы).
- Дефис в `needs.database` = FATAL всего деплоя с невнятным сообщением (`on_project_deploy.py:128`).
- bare `off` YAML-ловушка защищена schema enum, но без R5-негатива (ослабление схемы ни один тест не поймает).
- PyYAML `on:`→True: любой платформенный валидатор generated workflows не увидит триггеры.
- Литералы старого SHA `4425ce0` в prose `@changes`-комментариях шаблонов/channel_pin (AC T1.1 буквально не выполнен); AC T1.4 «grep age-secret-key пуст» невыполним по дизайну (--age-secret-key-file легитимен).
- `docker-compose.macos.yml` DATABASE_URL="": отклонение задокументировано @changes, но TRAP[DEBT]/DECISION-тег отсутствует (B3 требует явного решения владельца).
- `secrets-manifest.yaml`: consumers: [] у S3_ACCESS_KEY/S3_SECRET_KEY при живых потребителях (AWS_* алиасы backup-cron) — pre-existing.
- zai glm-4.5-flash: санкция владельца записана в secret-definitions/policy.yaml ✓ — формальная запись решения в B3 остаётся за владельцем.

## Процессуальные системические замечания

1. **Два арбитра манифестов отвечают на разные вопросы под одним названием**: `make check MARKER=check-manifests` = «диск == генераторы» (GREEN честный), pytest `test_manifests_up_to_date` = «дерево == HEAD» (RED до коммита by design). Repair-сообщение «Run: make generate-manifests» вводит в заблуждение, когда нужен commit. Гейт ловит и рукописную прозу core/AGENTS.md вне GENERATED-регионов.
2. **Гейт-слепая зона shell-политики**: no-direct-binary/thin-wrapper сканируют только `core/entrypoints/*.sh`; новые internal `.sh` (build-ssh-cmd.sh) вне скоупа — обход P1-15 стал возможен.
3. **Параллельная реализация во время аудита**: см. шапку. Все 9 RED 16:59 закрыты к ~17:30 (57/57 targeted rerun GREEN: env-drift ×3, honesty-R1, loc-allowlist, bootstrap_auto ×3, ssh_cmd_builder).

## Покрытие аудита (честность метода)

| Зона | Агент | Статус |
|------|-------|--------|
| Diff Волны 1 vs спек T1.x | субагент | ✅ полный; T1.2/T1.3/T1.6 соответствуют спеку полностью |
| 9 RED + линтеры | субагент + лид | ✅ полные первопричины, фиксы верифицированы |
| 010 multi-node | субагент | ✅ глубокий; 66 targeted тестов зелёные; claims VerificationReport: 4 не подтверждены (метрика 5 «Наблюдаемость», инварианты 7/4/1-2) |
| Манифесты/env-drift | субагент | ✅ первопричина всех 4 падений = один ключ AGE_RECIPIENT; противоречия арбитров объяснены |
| Security-свип | субагент + кросс-валидация лидом | ✅ инъекции/секреты/docker/sshd чисты; P0-1/P0-7/P1-19 найдены |
| Shell-политика | субагент | ✅ inline python3 = 0, фасады ≤150 LOC, executable bits ок; P1-15 |
| Логика новых подсистем | субагент | ✅ 17 находок, pure-probe верификация |
| Test-honesty R1-R5 | частично (агент BLOCKED ×2 по балансу) | R1/R4 закрыты лидом (R1 был регрессией усечения теста — восстановлен; R4 чист), R5-негативы sha-pins подтверждены diff-агентом; R3-полный свип не выполнен |
| 009 Brief vs код | частично (агент BLOCKED ×2) | Лидом проверено: maturity.py/escalator.py есть, K5 hook генерируется (practices/generators.py:217), quality.level default auto ✓, remove-project data-safe ✓ (O7/DD10), глоссарий↔манифест 64/64 точное совпадение, таргеты резолвятся в makefiles/*.mk. Полный матричный прогон Brief не выполнялся |
| Docs/TRAP-аннотации | частично (агент BLOCKED ×2) | Заголовки новых модулей ✓ (channel_pin/placement/maturity/escalator), TRAP fail-closed AGE_RECIPIENT ✓, TRAP validate_topology off/deps ✓; macos-override без тега (P2) |

## Next steps

1. Имплементатору Волны 1: добить остаточный RED (agent-check blocking=5: EXE001 channel_pin chmod +x, ARG005 test_core_deliverer:944, EM101 test_llm_provision:257; basedpyright key_provisioner:780 cast), затем freeze → `make fix-gate && git add -A && commit` (закроет и test_manifests_up_to_date).
2. Открыть план фиксов P0-1…P0-7 отдельной волной до staging-драфтов (все семь — silent-failure класс на канонических входах).
3. Track O (staging/drills) выполнять ТОЛЬКО после P0-1/P0-2 — иначе drills multi-node прогонят неработающий data-plane и placement-доставку.
