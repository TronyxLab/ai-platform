<!-- GREP_SUMMARY: devplan-post-audit-fixes P0-1..P0-7 P1 docker-user-peer placement-delivery postgres-role-regex sha-pin-shallow provisioner-metadata unmanaged-l1 age-stdin-prelude stdin-transport waves -->
<!-- STRUCTURE: ▶ Wave0 freeze/sync → ⚡ Волна 1 P0 ×7 (параллель, файлы ∩=∅) → ⚡ Волна 2 P1 ×5 → ∑ Track O строго после P0-1/P0-2 → ⎋ READY-for-drills -->

$START_DEVPLAN

## $ARTIFACT_CONTRACT

| Поле | Значение |
|------|----------|
| PURPOSE | Закрыть 7 P0-блокеров и 20 P1-находок предрелизного аудита (`15-VerificationReport.md`) ДО staging-drills; устранить два процессуальных системических дефекта (двойной арбитр манифестов, слепая зона shell-гейта) |
| DESCRIPTION | Fix-forward план по второму независимому аудиту. Волна 0 — freeze/sync с параллельной сессией Волны 1 плана 14. Волна 1 — P0-1…P0-7 (7 параллельных задач, silent-failure класс на канонических входах). Волна 2 — P1-хвост, сгруппированный по владельцам файлов (5 задач). P2 + coverage-gap'ы аудита — backlog. Каждый таск = верифицированная находка с якорями файл:строка@HEAD, дизайном фикса, тест-спецификацией и AC |
| RATIONALE | Q: почему отдельный план, а не продолжение 14-DevPlan-fixes? A: 14 закрывает дефекты первого QA (C/R/L/G/B-нумерация) и находится в финале имплементации; аудит 15 — новый внешний вход с собственной нумерацией P0/P1/P2 и явной рекомендацией «отдельной волной до staging-drills». Отдельный план сохраняет аудит-трейл «аудит → фикс» и не переоткрывает закрытые волны |
| ACCEPTANCE_CRITERIA | SC1: multi-node data-plane жив при «зелёном» verify — DNAT'ed peer-трафик проходит DOCKER-USER по peer-source правилам из placement; изоляция peer-scoped (никаких RFC1918-catch-all открытий). SC2: placement.yaml доставляется каналом core и читается всеми потребителями по единому пути-резолверу. SC3: канонические kebab-case проекты получают роль/БД/GRANT; любой skip — loud (ненулевой rc), не молчаливый успех. SC4: гейты не лгут — freshness sha-pins переживает shallow checkout, unmanaged-проект блокируется на L1, слепые зоны (security_opt dict, GPU, override-compose, internal .sh) закрыты. SC5: секреты вне командных строк — core-deploy CI через stdin-prelude, stdin-транспорт устойчив к многострочным значениям; провижинер ключей не создаёт дублей и не глотает неудачи |
| IMPLEMENTS | Аудит 15: P0-1…P0-7 (полностью), P1-1…P1-20 (группировкой в T1/T2), процесcуальные замечания №1 (арбитры манифестов) и №2 (гейт-слепая зона shell-политики) |
| IMPACTS | ~27 файлов: core/internal/bootstrap/{docker_user_policy.py, firewall.py, core_deliverer.py, remote_dispatch.py (проверка), build-ssh-cmd.sh}; core/internal/shared/{ssh_cmd_builder.py, placement.py, deploy_paths.py (резолвер)}; core/modules/postgres/hooks/on_project_deploy.py; core/internal/llm/{key_provisioner.py, admin_client.py}; core/internal/deploy/verify_contracts.py; core/internal/bootstrap/deploy/deploy_orchestrator.py; core/internal/monitoring/{prometheus_targets.py, config_renderer (wiring)}; core/internal/scaffold/project_adopter.py; .github/workflows/{core-deploy.yml, push-gate.yml, platform-gate-fast.yml, deploy-project.yml}; core/internal/check-suite (repair-сообщения); tests/unit/* + tests/gates/* (~12 файлов) |
| REQUIRES | Коммит Волны 1 плана 14 (freeze живого дерева — T0.1); `make check` + `make agent-check` зелёные на стартовом HEAD; во время исполнения плана параллельные сессии НЕ мутируют рабочее дерево; канон: Python-only new code, generated-файлы только через `make generate-manifests`, тестовая команда агента `make check` |

---

## §Source

Пользовательская постановка: «Составь девплан фиксов после аудита. Учти, что сейчас работает параллельная сессия по коду, тесты не запускай они могут давать ошибки». Вход: итог предрелизного QA-аудита — `.ai/plans/meta-refactoring/15-VerificationReport.md` (вердикт **NOT_READY**; 7 P0 / 20 P1 / 13 P2, все вне скоупа 14-DevPlan-fixes; рекомендация: P0-1…P0-7 отдельной волной **до** staging-drills).

## §Debt Intake (Step 0)

Существующие DEBT-реестры: glob `.ai/plans/**/*-Debt.md` — пусто (наследуется §Debt Intake плана 14). Находки аудита 15, не попавшие в таски:

| Находка | Решение |
|---------|---------|
| P2-хвост (13 позиций: adopter-heuristics, noqa/ruff-ignore директивы, `<node>`-валидация, lib/ssh.sh таймаут-литералы, redis healthcheck литерал, needs.database дефис, bare-off R5-негатив, PyYAML `on:`→True, SHA-литералы в prose @changes, macos-override TRAP-тег, secrets-manifest S3 consumers) | DEFER → backlog BP-1..BP-9 (триаж-таблица ниже) |
| Coverage gaps аудита: полный R3-stale-skip свип + матричный аудит 009 Brief (агенты BLOCKED по балансу провайдера) | DEFER → BP-10/BP-11; ключевые каналы K1–K5 и глоссарий 64/64 уже подтверждены лидом |
| Claims VerificationReport 010: 4 клейма не подтверждены кодом («Наблюдаемость ✅» = DI-seam без wiring — закрывается T2.A п.2) | Документационная поправка отчёта 010 → BP-12 |
| TRAP[BUG]/TRAP[DECISION]-аннотации по фактам фиксов | Добавляют имплементаторы в момент фикса (обычный протокол); планировщик сознательно НЕ правит код сейчас: дерево мутирует параллельная сессия — точечные правки планировщика создали бы конфликт слияния |

## §Requirements Analysis — критерии успеха

1. **Data-plane не врёт**: «зелёный» `verify_firewall` означает фактическую достижимость кросс-нодовых портов пирами (DOCKER-USER peer-правила) и одновременно — фактическую изоляцию остальных источников.
2. **Placement авторитетен на ноде**: файл физически доставлен, все потребители резолвят один путь; single-node поведение байт-идентично легаси.
3. **Канонические входы не падают молча**: kebab-case проекты — рабочий provisioning; любое отклонение — ненулевой rc/FATAL, видимый оркестратору деплоя.
4. **Гейты честные**: CI-гейт freshness-pins зелёный на первом же пуше; unmanaged-проект не проходит pre-deploy L1; известные векторы (dict-form, GPU, override-файлы, internal .sh) детектируются.
5. **Секреты и бюджет**: мастер-ключ отсутствует в argv/cmdline на любом участке core-deploy; повторные прогоны provision-llm идемпотентны; провал ключа никогда не фиксирует фазу как done.

## §Size Classification

LARGE по счётчику файлов (~27), но артефакт — одиночный DevPlan по конвенции родительской папки (flat-file: 11/14-DevPlan). CONFIRM_BRIEF пропущен: Brief заменяет внешний аудит-отчёт 15 с уже зафиксированным скоупом (все находки пронумерованы, локали и дизайн-направления указаны, приоритет подтверждён рекомендацией аудита). ## @rationale: повторная CONFIRM-итерация добавила бы цикл без нового входа; scope жёстко ограничен находками аудита, ничего сверх.

---

# $TASKS

Оценки: XS ≤20 строк, S ≤100, M ≤300. Одна волна = один feat-коммит. Все задачи стартуют от HEAD после T0.1; имплементатор обязан перечитать целевые файлы перед правкой (Волна 1 плана 14 меняла часть из них).

## Волна 0 — freeze/синхронизация (сериально, блокирует всё)

### T0.1 · Freeze дерева + добивка остаточного линт-RED [S] · closes аудит 15 §Next steps п.1
Координация с параллельной сессией: она завершает Волны 1 плана 14. Остаточный RED на момент аудита: agent-check blocking=5 (EXE001 `channel_pin` не executable → chmod +x; ARG005×2 `test_core_deliverer:944`; EM101 `test_llm_provision:257`), basedpyright `key_provisioner.py:780` (cast). Добить, затем `make fix-gate && git add -A && git commit` (закроет и `test_manifests_up_to_date`). На новом HEAD: `make check` (до чистоты) + `make agent-check`.
**AC:** свежий коммит Волны 1; оба арбитра зелёные; запись в `.ai/logs/runs.jsonl`; все волны настоящего плана стартуют исключительно от этого HEAD; до T0.1 ни одна задача Волны 1 не начинается.

## Волна 1 — P0-блокеры (7 параллельных задач, файлы не пересекаются)

### T1.A [P0-1 + P1-12] · DOCKER-USER peer-семантика + реконсиляция стейл-правил [M]
Факты (верифицировано чтением): `desired_docker_user_rules()` (`docker_user_policy.py:94-102`) = established + 80/443 + bridge-nets + **catch-all DROP**, без понятия пиров; peer-ALLOW живут в ufw ниже и для DNAT'ed трафика (PREROUTING→FORWARD→DOCKER-USER) не выполняются никогда → весь data-plane (6432/9000/8123/19000/3100/9100/…) молча DROPается при зелёном verify. DOCKER-USER видит post-DNAT dport (хост 19000 → контейнер 9000). Изоляция фактически держится на catch-all — первая «починка связности» ACCEPT RFC1918 открывает DNAT'ed порты всему интернету. Дополнительно P1-12: `collect_stale_platform_rules` пропускает peer-порты (`firewall.py:506-507`), `build_peer_rules` аддитивен (`:645-653`) → стейл peer-правила не реконсилятся, `verify_firewall` FAIL перманентно без self-heal.

Дизайн:
1. `firewall.py`: расширить SoT-матрицу публикаций парами (host_port, container_post_dnat_port) — новые константы рядом с `PEER_PUBLISH_PORTS`; 0 новых порт-литералов (значения только из `shared/platform_ports.py` — гейт port-parity).
2. `desired_docker_user_rules(peer_rules: list[list[str]] | None = None)` — аддитивный kwarg (обратная совместимость: None → текущий список байт-в-байт). Peer-ACCEPT (`-s <peer_ip> -p tcp --dport <container_port> -j ACCEPT`, comment `platform-du-peer-<port>-<peer>`) вставляются после bridge-ACCEPT, **строго до DROP**. Импорт матрицы — через параметр вызывающего (leaf-контракт, прецедент TRAP dport: цикл docker_user_policy↔firewall недопустим).
3. Применение: `apply_docker_user_policy` принимает peer_rules; вызовы — systemd ExecStartPost (placement недоступен на раннем буте → базовая политика, fallback-семантика) и пост-деплой хук оркестратора (placement доставлен после T1.B → полная политика с пирами). Идемпотентность -C guard сохраняется; peer-правило помечено комментарием → реконсиляция удаляет стейл (пир исчез из placement) и перевыполняет набор.
4. Verify против факта: новая проверка `iptables-save`-вывода DOCKER-USER против желаемого множества (порядок: ACCEPT-набор, DROP последний; каждый (peer, port) присутствует) — встраивается в `verify_firewall`/companion-check, чтобы «зелёный» статус перестал лгать о data-plane.
5. P1-12: `collect_stale_platform_rules` включает peer-порты; reconcile-путь удаляет стейл ufw peer-правила (self-heal до того, как verify начнёт FAIL).

Тесты: unit `test_docker_user_policy.py` — peer-правила до DROP; peers=None → список идентичен текущему (регрессия); стейл-удаление. Unit `test_firewall_reconcile*` — collect_stale видит peer-порты; self-heal сценарий. Негатив (R5): simulated ruleset без peer-ACCEPT → verify FAIL (red→green). Реальный iptables/DNAT — requires_node, ручной прогон на test-VPS после волны.

**AC:** для каждой пары (peer, post-dnat port) из placement в DOCKER-USER существует ACCEPT с source=peer; DROP всегда последний; verify детектирует отсутствие/стейл peer-правил и чинится реконсиляцией; single-node путь байт-идентичен; никакое правило не открывает peer-порт не-пирам.

### T1.B [P0-2 + P1-20] · Доставка placement.yaml + DRY_RUN preview [S]
Факты: Phase 2 `deliver_node_configs` (`core_deliverer.py:508-549`) rsync'ит только `node-configs/<node>/`; context-overlay кладёт overlay-репо; потребители деривируют `Path(node_yaml).parent.parent / context / "placement.yaml"` (`deploy_orchestrator.py:375`; также `shared/placement.py:767`, `modules_healthcheck.py:322`) — файл по этому пути никто не создаёт → `load_placement→None` → деплой резолвит из node.yaml, peer-firewall `[]`, healthcheck проверяет чужие singleton'ы. P1-20: DRY_RUN core-deliver роняется FATAL до preview на машине без AGE-ключа (`:780-788`).

Дизайн:
1. Единый резолвер пути: `placement_remote_path(context) -> str` в `shared/placement.py` (или `shared/deploy_paths.py` — по месту обитания прочих remote-path констант); ВСЕ потребители (orchestrator, placement loader, modules_healthcheck) переходят на него — убивает три независимые деривации (knowledge dedup).
2. Новая sub-phase `deliver_placement` в core_deliverer (после Phase 2): source `node-configs/<context>/placement.yaml` → remote `{node_configs_base}/<context>/placement.yaml` (тот же base, что у node-configs). Файл отсутствует → skip `[IMP:8]` (single-node канон, no-op); dry_run печатает команду. Канон выбора канала: core-push (SCP), НЕ context-overlay-git — placement живёт в node-configs рядом с node.yaml (единый SoT), git-канал породил бы второй источник истины.
3. Порядок гарантий: deliver_placement исполняется ДО фаз-потребителей (φ8 deploy-modules / φ12) — проверить последовательность в `deliver()`; при отсутствии файла на multi-node ноде (placement объявлен локально, но не доставлен) — WARN в сводке.
4. P1-20: в `deliver_fallback`/dry-run пути AGE-детекция выполняется ПОСЛЕ построения WOULD-плана; DRY_RUN без ключа → WARN + полный preview, rc=0. Реальный прогон без ключа — FATAL как сейчас.

Тесты: `test_core_deliverer.py` — placement доставлен по резолверу (fake runner); absent → skip без ошибки; DRY_RUN без ключа → rc=0 + preview содержит все фазы (негатив к P1-20). Unit на резолвер: все три потребителя дают один путь.

**AC:** после deliver на ноде существует файл по пути `placement_remote_path(context)`; `load_placement` в проде находит его; single-node без файла — поведение не изменилось; DRY_RUN на машине без AGE даёт полный WOULD-план.

### T1.C [P0-3 + P1-14] · Postgres hook: kebab-case роли, честный rc, регистр db_name [S]
Факты: `_ROLE_NAME_RE = ^[a-zA-Z0-9_]+$` (`on_project_deploy.py:73`); неподошедшее имя → лог FATAL и **`return 0`** (`:238-240`) → деплой зелёный, роли/GRANT/credentials нет. P1-14: смешанный регистр db_name — `CREATE DATABASE MyApp` создаёт `myapp`, quoted GRANT падает (non-fatal), `.platform-db.env` пишет `MyApp` (`:148` vs `:309-313,436`).

Дизайн:
1. Regex → `^[a-z0-9_-]+` на роль (нижний регистр; дефис разрешён — канон kebab-case имён проектов root AGENTS.md); все SQL-идентификаторы ролей/БД — ВСЕГДА double-quoted.
2. Fail-loud: невалидное имя → `return 2` + `[IMP:10]`; контракт вызывающего deploy-hook проверить имплементатором (grep invoke deploy-hook в receive_flow/orchestrator): rc≠0 должен подниматься в blocking-ошибку деплоя, не глотаться best-effort. Ранняя валидация имени проекта (sync-env/adopt) — отдельной проверкой не вводить (вне скоупа), достаточно loud-hook.
3. P1-14: `db_name` нормализуется `lower()` ОДИН раз на входе `auto_create_db`; CREATE/GRANT/`.platform-db.env` пишут одно и то же нормализованное значение (единая точка нормы, 0 рассинхронов).

Тесты: `test_on_project_deploy.py` — kebab-case проект → роль `my-app_user` создана (fake runner, SQL-ассерт с quoted идентификатором); невалидный символ → rc==2; `MyApp` → `myapp` во всех трёх местах (CREATE/GRANT/env-файл).

**AC:** канонический kebab-case проект получает роль/GRANT/credentials при зелёном деплое; невозможно получить зелёный деплой без provisioning (или с тихим skip); регистр БД совпадает с `.platform-db.env`.

### T1.D [P0-5 + P1-5 + P1-6 + P1-7 + P1-8] · Provisioner: lookup-ключ, честный failed, lock-scope, дубли/пустые токены [M]
Факты: `key_metadata.update(profile_metadata)` перезаписывает зарезервированный `"project"` (`key_provisioner.py:689-694`) → `find_key_by_metadata` никогда не матчит → GENERATE бюджетных дублей на каждом прогоне. `failed>0` не поднимает ошибку (`:793-805`) → φ11 фиксирует llm-keys done при проваленных ключах (противоречие инварианту :27-29). List→find→generate вне FileLock (`:475` vs `:647-783`) → конкурентные дубли. Коллизия metadata.project решается first-match без WARN (`admin_client.py:643-651`). Пустой `token` из листинга персистится поверх рабочего ключа стора (`:701,709-710`).

⚠️ Наследование: файлы активно менялись T1.3 плана 14 (fetch-once индекс, fall-through-removal) — дизайн согласован с ним: индекс known_keys строится внутри общего лока.

Дизайн:
1. Merge-guard: `RESERVED_METADATA_KEYS = {"project"}`; профильные метаданные применяются только к ключам вне reserved; попытка затирания → WARN `[IMP:8]` с именем ключа. Инвариант: `key_metadata["project"] == consumer_name` всегда (assert в тестах).
2. Пост-цикл: `if failed: raise PlatformError(...)` — exit-контракт main() платформенный (`exceptions.py`); φ11 получает провал фазы, partial-словарь не пишется как успех.
3. Lock-scope: весь потребительский проход (list_keys → индекс → find → update/generate → persist) под существующим FileLock стора; конкурентный второй прогон ждёт или падает по таймауту лока — дубли невозможны структурно.
4. Детерминизм коллизии: кандидаты сортируются (created_at, id); коллизия >1 → WARN с обоими id (орфан перестаёт быть безвестным).
5. Empty-token guard: `token == ""` из листинга → запись трактуется как not-found; поверх рабочего ключа стора НЕ пишется; WARN.

Тесты: `test_llm_key_provisioner.py` — profile-metadata не затирает `project` (idempotent SKIP на втором прогоне, generate_count==0); failed≠∅ → PlatformError; lock охватывает find→generate (unit на порядок захват/релиз относительно вызовов); collision → стабильный победитель + WARN; empty-token → стор не тронут.

**AC:** повторный прогон с профильными метаданными = 0 generate; любой failed → исключение → φ11 не done; конкурентные прогоны не создают дублей; пустой токен не попадает в стор.

### T1.E [P0-6 + P1-9 + P1-10 + P1-11] · verify_contracts: unmanaged-L1-block + dict-form + GPU + multi-compose [M]
Факты: при `l1_only` drift-practices скипается целиком (`verify_contracts.py:356`) → unmanaged-проект (practices.lock отсутствует) проходит pre-deploy гейт с 0 findings — заявленный контракт `[PRACTICES:UNMANAGED] … L1-контракты блокируют деплой` (:234) не реализован. `security_opt` dict-форма `{seccomp: unconfined}` не детектируется (:990-1002). GPU/device-reservation (`deploy.resources.reservations.devices`, top-level `gpus:`) вне deny-set. Сканируется ровно один compose-файл (:324) — override/include слепая зона.

Дизайн:
1. l1_only + lock отсутствует → L1-finding `drift-practices-unmanaged` (klass=KLASS_L1 → SEVERITY_BLOCK автоматически через `_severity_for`). Наличие lock → прежняя семантика (drift-check в l1_only остаётся skip — латентность docker-L2 не возвращаем).
2. `security_opt`: нормализация list[str] И dict → пары key=value перед unconfined-детектом (case-insensitive).
3. Deny-set дополнить: `deploy.resources.reservations.devices[*]` (любой device, включая GPU), top-level/service `gpus:`, `device_cgroup_rules` → violation (device-доступ мимо закрытого `devices`).
4. Мульти-compose: сканировать все файлы проекта из `shared/compose_files.py` (`PROJECT_COMPOSE_FILENAMES` — SoT уже существует, 6 потребителей); findings агрегируются с указанием конкретного файла в сообщении.

Тесты: `test_verify_contracts.py` — l1_only+no-lock → has_blocking=True с contract_id=`drift-practices-unmanaged`; l1_only+lock → прежнее поведение (регрессия); dict-form seccomp:unconfined → violation (точный вход из аудита); reservations.devices → violation; violation в override-файле → найдена с именем файла.

**AC:** unmanaged-проект блокируется на pre-deploy L1; каждый вектор (dict-form, GPU, override) имеет red→green негатив; латентность l1_only не выросла (без docker-subprocess).

### T1.F [P0-7] · core-deploy CI: AGE-ключ через stdin-prelude [S]
Факты: `core-deploy.yml:230-233` — `export AGE_SECRET_KEY="${{ secrets… }}"` и интерполяция в строку remote `bash -c` → ключ в `/proc/*/cmdline` remote-shell на всё время node-update. Канал готов: `build_update_secret_prelude()` (`build-ssh-cmd.sh:59-64` — значение идёт через builtin printf → stdin python, argv чист) + композиция prelude+cmd через `bash -s` (`remote_executor._ssh_exec`, контракт `execute_update(secret_prelude=)` `remote_executor.py:276-320`).

Дизайн:
1. Шаг Deploy: `AGE_SECRET_KEY` читается в step-env (НЕ в remote-строку); локально собираются `REMOTE_CMD=$(build_update_ssh_cmd "$NODE" "" "$PASSTHROUGH")` и `PRELUDE=$(build_update_secret_prelude "$AGE_SECRET_KEY")` (facade source из чекаута); отправка `{ printf '%s\n' "$PRELUDE" ; printf '%s\n' "$REMOTE_CMD" ; } | ssh … "bash -s"` — формат композиции сверить байт-в-байт с `remote_executor._ssh_exec` (единый контракт stdin).
2. Guard до отправки: значение из GitHub Secrets, содержащее `\n` → fail-loud (до обобщения транспорта в T2.B); после T2.B — заменить на b64-режим.
3. Комментарии шага (:223-229) переписать под новый канал (REF-0007 ссылка); audit-trail шаг не меняется.

Тесты: статический гейт (расширение существующего workflow-гейта или новый `tests/gates/test_gate_ci_secrets_transport.py`): в core-deploy.yml нет `${{ secrets.* }}` внутри строки remote-команды; присутствует prelude/bash -s паттерн. R5-негатив: откат к export-интерполяции → RED.

**AC:** `${{ secrets.AGE_SECRET_KEY }}` не встречается в remote-командной строке; гейт ловит регрессию; dispatch CI-прогона на test-ноде (оператор, после волны) — decrypt φ9 успешен.

### T1.G [P0-4] · Freshness sha-pins: shallow-proof [XS]
Факты: `push-gate.yml`/`platform-gate-fast.yml` не имеют `fetch-depth` (grep: единственный `fetch-depth: 0` — mirror.yml) → depth=1; `test_gate_workflow_sha_pins.py:496-531` использует `git log -- <path>` + `merge-base --is-ancestor` → на shallow истории last-touch резолвится в граничный коммит → следующий пуш даёт ложный stale-pin RED.

Дизайн: `fetch-depth: 0` в checkout-шагах обоих workflow (+ комментарий почему — гейту нужна полная история для merge-base). Date-fallback в гейте НЕ строить (YAGNI: усложнение oracle при дешёвом upstream-фиксе). TRAP[DECISION] в гейте: «shallow checkout = конфигурационная ошибка, fallback не предусмотрен».

Тесты: статический ассерт в `test_gate_workflow_sha_pins.py` (или соседний gate-тест): оба workflow содержат `fetch-depth: 0`; негатив: удаление строки → RED. Опционально: unit-симуляция shallow-вызова функции гейта с fixture-repo depth=1 → явная ошибка «insufficient history», не ложный stale (fail-loud, R4-совместимо).

**AC:** первый же CI-пуш после волны зелёный по freshness-гейту; симуляция shallow не даёт ложного RED.

## Волна 2 — P1-остаток + процессуальное (5 параллельных задач; стартует после Волны 1)

### T2.A [P1-1 + P1-2 + P1-3 + P1-13] · Multi-node консистентность: topology-scan, targets-wiring, peer-матрица 9127/9122, minio-consumer [M] · deps: T1.A, T1.B
Факты: прод-вызов `validate_topology` без `projects_scan` (`deploy_orchestrator.py:392-396` vs параметр `shared/placement.py:629`) → exposed target_node/FQDN-инварианты не проверяются нигде кроме тестов. `generate_node_targets` (`monitoring/prometheus_targets.py:274`) — 0 production-вызовов → нодовые file_sd не рендерятся, RemoteNodeDown/LokiCollectorStale мертвы (claim 010 «Наблюдаемость ✅» = DI-seam без wiring). Peer-матрица разорвана REF-0010: 9127 (pgbouncer-exporter) и 9122 (redis-exporter langfuse) эмитятся в targets (:166-179), отсутствуют в `PEER_PUBLISH_PORTS`/deny-листах (`firewall.py:194-209,129-147`). Minio-target на obs-ноде без nginx/langfuse не получает peer-правило (:180-186 vs CONSUMER_OF).

Дизайн:
1. `deploy_orchestrator`: передать `projects_scan` (скан ai-platform.yaml проектов контекста — тот же источник, что vhost_renderer/project_registry; DI-callable, ошибки скана → ConfigValidationError fail-fast).
2. Wiring targets: config_renderer при placement≠None строит NodeInfo-list → вызывает `generate_node_targets(nodes, output_dir)`; single-node → не вызывает (байт-совместимость). Это закрывает и claim-поправку 010 (BP-12).
3. Матрица: `PEER_PUBLISH_PORTS["service-exporters"] += PGBOUNCER_EXPORTER (9127)`; 9122 — co-located с langfuse (владелец — модуль langfuse) → добавить туда; синхронно MODULE_PORTS_DENY, CONSUMER_OF и parity-гейт (эмит-set ⊆ матрица) распространить на оба порта.
4. CONSUMER_OF[minio]: нода с monitoring (obs) — потребитель minio-scrape; правило для её IP даже при отсутствии на ней nginx/langfuse (scenario-тест выделенной obs-ноды).

Тесты: wiring — orchestrator вызывает validate_topology с non-None scanner; renderer эмитит nodes/*.json при placement (tmp_path). Parity: emitted-exporter-ports ⊆ PEER_PUBLISH_PORTS (негатив: убрать 9127 → RED). Scenario: топология «data-agent-apps+obs» → minio-peer-правило для obs-IP.

**AC:** прод-деплой ловит exposed/FQDN-нарушения; file_sd нодовых таргетов рендерится в multi-node; parity-гейт покрывает весь эмит-набор; job minio не down в S3-топологии 010.

### T2.B [P1-4 + P1-15 + P1-17 + P1-18 + проц.№2] · Stdin-транспорт секретов: robustness + timeout + слепая зона гейта [M] · deps: T1.F (не ломать контракт single-line)
Факты: line-based `_read_secret_stdin` (`ssh_cmd_builder.py:484-502`) — многострочное значение секрета сдвигает строки → silent-коррупция prelude при rc=0 (env-источник вербатим, `node_detect.py:121`); опциональный ci_root-слот глотает лишние позиционные аргументы (`:446-448`), 4-я строка stdin отбрасывается (`:502` возвращает ровно count первых), пустой одиночный секрет (`printf '\n'`) даёт FATAL got 0 вопреки контракту (частичный фикс 17:01 — снять ровно один `\n`; кейс count=1 перепроверить). `build-ssh-cmd.sh:86-91` — ssh-exec без timeout (класс P02 CI-hang; инвариант lib/ssh.sh). Гейты no-direct-binary/thin-wrapper сканируют только `core/entrypoints/*.sh` → internal `.sh` вне политики (обход P1-15 стал возможен).

Дизайн:
1. Транспорт v2: маркер-первая-строка `v2` → последующие строки — base64(stdlib) каждого значения; декодирование в `_read_secret_stdin`. Legacy (без маркера) — прежняя семантика single-line (обратная совместимость с T1.F/bootstrap.sh). Фасады `build-ssh-cmd.sh` кодируют через `base64` (или python-CLI) — значения могут содержать `\n`.
2. Строгий stdin: лишние НЕпустые строки сверх count → BuildModeError (fail-loud вместо тихого drop); пустой stdin (0 байт) ≠ одна пустая строка (`"\n"` → `[""]` при count=1).
3. `_dispatch_build("init")`: нераспознанные лишние позиционные → BuildModeError; ci_root — только из документированной позиции; проверить всех вызывающих (bootstrap.sh) на предмет скрытой передачи флагов.
4. Timeout: ssh-exec ветка build-ssh-cmd.sh оборачивается в `timeout <N>`; значение N — из SoT `shared/timeouts.py` (emit через CLI ssh_cmd_builder/ssh_opts `--shell`, НЕ литерал — parity-требование); класс P02 закрыт.
5. Гейт: no-direct-binary/thin-wrapper расширяют скан на `core/**/*.sh` (entrypoints + internal + modules); существующие фасады проходят (они тонкие) — при необходимости точечный allowlist с обоснованием.

Тесты: b64-roundtrip многострочного AGE-ключа (значение на выходе байт-в-байт); legacy single-line регрессия; лишний позиционный → error; лишняя stdin-строка → error; одиночный пустой → ok; static: timeout присутствует в exec-ветке; gate-негатив: нарушение политики в internal .sh → RED.

**AC:** многострочный секрет транспортируется без коррупции (rc=0 И корректное значение); ни один лишний вход не глотается молча; внутренние .sh покрыты shell-политикой; CI-hang класс закрыт.

### T2.C [P1-19] · deploy-project.yml: defense-in-depth интерполяции [S]
Факт: inputs интерполируются сырыми в run-блоки и строки remote-команд (`deploy-project.yml:147,161,186,431,441`); эксплуатация требует write-доступа, но канал деплоя — последнее место, где это допустимо.

Дизайн: канонический паттерн GitHub — `env:` шага принимает `${{ inputs.x }}`, run-блок потребляет `"$VAR"` в кавычках; для remote-строк — значения через quoted-env/args, не конкатенацией. Полный проход по пяти точкам.

Тесты: расширение статического гейта workflow: `${{ inputs.* }}` запрещён непосредственно внутри `run:`-строк (разрешён только в `env:`-блоках); негатив-fixture с прямой интерполяцией → RED.

**AC:** гейт GREEN на приведённом файле; инъекционный fixture RED; семантика деплоя не изменилась (dry-run прогона оператора).

### T2.D [P1-16] · adopt-project: неинтерактивная деградация [XS]
Факт: `input()` без TTY (`project_adopter.py:181-185`) → EOFError traceback после частичной адопции (ai-platform.yaml уже создан).

Дизайн: `sys.stdin.isatty()`-guard → читаемое сообщение + rc≠0 БЕЗ traceback; флаг `--yes`/non-interactive для автоматизации (дефолт-ответ явным параметром). Частично созданное состояние — напечатать перечень созданного + hint rollback (remove-project не трогает данные — сослаться).

Тесты: EOF-симуляция (stdin=devnull) → чистое сообщение, rc≠0, без traceback; `--yes` проходит неинтерактивно.

**AC:** piping/CI-запуск adopt не даёт traceback; состояние после отказа описано в выводе.

### T2.E [проц.№1] · Арбитры манифестов: развести вопросы и починить repair-сообщения [S]
Факт: два арбитра под одним именем отвечают на разные вопросы: `make check MARKER=check-manifests` = «диск == генераторы» (GREEN честный), pytest `test_manifests_up_to_date` = «дерево == HEAD» (RED до коммита by design). Repair-сообщение «Run: make generate-manifests» вводит в заблуждение, когда нужен commit; гейт также ловит рукописную прозу core/AGENTS.md вне GENERATED-регионов.

Дизайн: имена арбитров НЕ менять (внешние ссылки/CI), развести сообщения: pytest-арбитр при divergence → «generated files differ from committed HEAD → commit them (git add …), НЕ запускай make generate-manifests»; check-suite-арбитр → «Run: make generate-manifests» (остаётся); оба сообщения упоминают случай рукописной правки вне GENERATED-регионов. Docstrings обоих арбитров дополняются фразой-различителем вопросов.

Тесты: fixture-divergence → сообщения содержат правильное действие (строковые ассерты обоих арбитров); негатив: неверное сообщение → RED.

**AC:** repair-сообщение каждого арбитра ведёт к единственному правильному действию; путаница «commit vs regenerate» устранена.

---

# $PARALLEL_GROUPS

```
Wave 0 (serial, блокирует всё):           T0.1 (freeze + коммит Волны 1 плана 14)
Wave 1 (7 параллельных, файлы ∩ = ∅):
  T1.A docker_user_policy+firewall · T1.B core_deliverer+placement-resolver
  T1.C postgres hook · T1.D llm provisioner+admin_client · T1.E verify_contracts
  T1.F core-deploy.yml · T1.G push-gate/platform-gate-fast
  Command: coder Read .ai/plans/meta-refactoring/16-DevPlan-post-audit-fixes.md, implement Wave 1: T1.A–T1.G
  (по одному субагенту на таск; каждому — только его секция; общий коммит волны после зелёного make check)
Wave 2 (5 параллельных, файлы ∩ = ∅; deps: T2.A←T1.A/T1.B, T2.B←T1.F-контракт):
  T2.A multi-node consistency · T2.B stdin-transport+gates · T2.C deploy-project.yml
  T2.D adopter · T2.E arbiter messages
  Command: coder Read .ai/plans/meta-refactoring/16-DevPlan-post-audit-fixes.md, implement Wave 2: T2.A–T2.E
Track O (оператор/test-VPS): строго ПОСЛЕ Wave 1 (минимум T1.A+T1.B — иначе drills гоняют мёртвый data-plane)
```

Матрица пересечений проверена: в пределах волны ни одна задача не разделяет файл с другой. T2.B и T1.F разделены по файлам (builder-модули ↔ workflow); контракт single-line зафиксирован за T1.F до прихода v2-транспорта T2.B.

# Acceptance Criteria (сводная таблица)

| ID | Критерий | Верификация |
|----|----------|-------------|
| AC-1 | T0.1: HEAD = коммит Волны 1; make check + agent-check зелёные | runs.jsonl |
| AC-2 | T1.A: peer-source ACCEPT в DOCKER-USER для всех (peer, port); DROP последний; verify ловит отсутствие/стейл | unit-негативы + requires_node ручной |
| AC-3 | T1.B: placement.yaml на ноде по резолверу; load_placement находит; DRY_RUN без AGE = preview rc=0 | test_core_deliverer |
| AC-4 | T1.C: kebab-case → роль/GRANT созданы; invalid → rc≠0; регистр db консистентен | test_on_project_deploy |
| AC-5 | T1.D: 0 дублей при повторе; failed→PlatformError; lock покрывает find→generate; empty-token не в сторе | test_llm_key_provisioner |
| AC-6 | T1.E: unmanaged → L1 block; dict-form/GPU/override → violations | test_verify_contracts |
| AC-7 | T1.F: секрет вне remote-cmdline; гейт RED на откат | test_gate_ci_secrets_transport |
| AC-8 | T1.G: fetch-depth: 0 в двух workflow; shallow не даёт ложный stale | gate-тест |
| AC-9 | T2.*: каждая P1 имеет red→green тест | per-task TEST_SPEC |
| AC-10 | READY-for-drills: Волна 0–1 смержены; Track O стартует только после них | release-checklist |

# $TEST_SPEC (ключевые строки; полный состав — в тасках)

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| tests/unit/test_docker_user_policy.py | test_peer_accept_before_drop / test_no_peers_legacy_identical / test_stale_peer_rule_removed | peer-семантика DOCKER-USER | docker_user_policy |
| tests/unit/test_firewall_reconcile.py (или существующий reconcile-файл) | test_collect_stale_includes_peer_ports / test_self_heal_peer_rules | реконсиляция стейл-ufw | firewall |
| tests/unit/test_core_deliverer.py | test_placement_delivered_to_resolver_path / test_placement_absent_skip / test_dry_run_without_age_full_preview | доставка placement + DRY_RUN | core_deliverer |
| tests/unit/test_shared_placement.py | test_consumers_share_resolver_path | единый путь у 3 потребителей | placement resolver |
| tests/unit/test_on_project_deploy.py | test_kebab_case_role_provisioned / test_invalid_role_nonzero_rc / test_mixed_case_db_normalized_everywhere | P0-3/P1-14 | postgres hook |
| tests/unit/test_llm_key_provisioner.py | test_reserved_project_key_not_overwritten / test_failed_raises_platform_error / test_lock_covers_find_generate / test_collision_deterministic_warn / test_empty_token_not_persisted | P0-5+P1-5..8 | key_provisioner/admin_client |
| tests/unit/test_verify_contracts.py | test_l1_unmanaged_blocking / test_l1_with_lock_unchanged / test_security_opt_dict_form / test_gpu_reservation_denied / test_override_file_scanned | P0-6+P1-9..11 | verify_contracts |
| tests/gates/test_gate_ci_secrets_transport.py | test_core_deploy_no_secret_interpolation / test_regression_export_interpolation_red | P0-7 гейт | CI workflow |
| tests/gates/test_gate_workflow_sha_pins.py | test_workflows_fetch_depth_zero / test_shallow_sim_fails_loud | P0-4 | sha-pins gate |
| tests/unit/test_monitoring_config_renderer.py (wiring) | test_node_targets_rendered_when_placement / test_single_node_skips_node_targets | P1-2 wiring | config_renderer/prometheus_targets |
| tests/unit/test_deploy_orchestrator_topology.py | test_validate_topology_receives_projects_scan | P1-1 wiring | deploy_orchestrator |
| tests/unit/test_prometheus_targets_parity.py | test_emitted_exporter_ports_in_peer_matrix | P1-3 | prometheus_targets↔firewall |
| tests/unit/test_shared_ssh_cmd_builder.py | test_b64_multiline_roundtrip / test_legacy_single_line_compat / test_extra_positional_rejected / test_extra_stdin_lines_rejected / test_single_empty_value_ok | P1-4/17/18 | ssh_cmd_builder |
| tests/unit/test_verify_contracts_gate_scope.py (или существующий shell-policy gate) | test_internal_shell_scripts_covered | проц.№2 | shell-policy gates |
| tests/unit/test_project_adopter.py | test_eof_clean_error / test_yes_flag_noninteractive | P1-16 | project_adopter |
| tests/gates/test_gate_workflow_inputs_interp.py | test_inputs_only_in_env_blocks / test_negative_raw_interpolation_red | P1-19 | deploy-project.yml |
| tests/unit/test_manifest_arbiters_messages.py | test_pytest_arbitr_message_commit / test_suite_arbitr_message_regenerate | проц.№1 | arbiters |

# Design Decisions (@rationale)

## @rationale Q: почему peer-ACCEPT в DOCKER-USER, а не смягчение catch-all до RFC1918? A: DNAT выполняется до source-фильтрации — ACCEPT по RFC1918-open-source откроет DNAT'ed порты всему интернету (точка (б) аудита); изоляция сохраняется только явными source=peer правилами, а семантика «пиры» уже канонизирована placement.yaml.
## @rationale Q: почему доставка placement через core-push, а не context-overlay-git? A: placement — часть node-configs (рядом с node.yaml, единый SoT); git-канал создал бы второй источник истины и зависимость bootstrap от доступности remote-репо; SCP-канал уже доставляет sibling-файлы той же директории.
## @rationale Q: почему роль-regex `[a-z0-9_-]+` (lowercase), а не произвольные quoted-идентификаторы? A: канон kebab-case имён проектов; lowercase исключает class P1-14 (folding/регистр-рассинхрон между БД и .env.platform); quoted-поддержка дефиса достаточна, произвольные символы — поверхность атак впустую.
## @rationale Q: почему failed>0 → исключение, а не WARN+continue? A: φ11 фиксирует llm-keys done → системный silent-degradation; бюджетные дубли и недопровиженные потребители обнаруживаются только по 401 в проде. Асимметрия риска однозначна.
## @rationale Q: почему fetch-depth: 0, а не date-fallback в гейте? A: дешёвый upstream-фикс против усложнения oracle (вторая кодовая ветка сопоставления дат, своя поверхность ложных срабатываний); fallback — YAGNI до появления evidence.
## @rationale Q: почему b64-v2 с маркер-строкой, а не ломающий апгрейд протокола? A: T1.F уже переводит CI на stdin-канал в Волне 1 — разрыв контракта между волнами сломал бы bootstrap.sh/CI; маркер даёт параллельную жизнь legacy/v2 без флагов совместимости.
## @rationale Q: почему P1-20 объединён с P0-2, а P1-3/P1-12 разнесены по волнам? A: группировка строго по владельцу файла: core_deliverer.py — один хозяин (T1.B); firewall.py принадлежит T1.A в Волне 1, T2.A приходит за ним последовательно — параллельный редактирование одного файла двумя субагентами запрещено.
## @rationale Q: почему CONFIRM_BRIEF пропущен? A: прецедент плана 14: внешний аудит с зафиксированным скоупом заменяет Brief; пользовательская постановка односложна и не содержит альтернатив для суперпозиции.

# Верификационный протокол (канон репо)

- **Координация:** исполнение стартует ТОЛЬКО после T0.1 (freeze). Параллельные сессии не мутируют дерево во время волн. Тесты в момент планирования НЕ запускались (живое дерево параллельной сессии — по требованию владельца).
- Per-task: `make check TEST_FILE=<path>` (один файл = один вызов); мелкие правки — `make check-diff`.
- Фикс-цикл: `make check` батчем до чистоты; финал волны — `make check` + `make agent-check` (обязателен).
- Коммиты: `make fix-gate && git add -u` перед каждым commit; одна волна = один feat-коммит `feat(post-audit): волна N P0/P1 fixes — <состав>` (+ отдельный docs-коммит на этот план; ≤2 на волну).
- `make gate MODE=fast` локально НЕ запускать (OOM-политика 0.8) — арбитры: pre-push hook + CI push-gate.yml.
- requires_node-проверки (реальный DNAT/iptables, CI-dispatch decrypt) — ручной прогон оператора на test-VPS в рамках Track O, не в волнах.

# Next Steps

### Волна 0
Use coder role and read /Users/tronyx/projects/ai-platform/.ai/plans/meta-refactoring/16-DevPlan-post-audit-fixes.md, execute T0.1: координировать freeze с параллельной сессией, добить остаточный линт-RED (agent-check blocking=5, basedpyright key_provisioner:780), `make fix-gate && git add -A && commit`, затем `make check` (до чистоты) + `make agent-check`. Стоп при первом RED — репорт.

### Волна 1
Use coder role and read .ai/plans/meta-refactoring/16-DevPlan-post-audit-fixes.md, implement Wave 1 tasks T1.A–T1.G (параллельные субагенты по одному на таск; каждому — только его секция таска; файлы перечитать перед правкой). Для каждого таска: реализация по дизайну, тесты по $TEST_SPEC, затем `make check TEST_FILE=<его тест>` (до чистоты). После всех семи: `make check` батчем, `make agent-check`, `make fix-gate && git add -u`, commit `feat(post-audit): волна 1 — P0-1..P0-7`.

### Волна 2
Use coder role and read .ai/plans/meta-refactoring/16-DevPlan-post-audit-fixes.md, implement Wave 2 tasks T2.A–T2.E (аналогичная схема; T2.A после T1.A/T1.B). Финал: `make check`, `make agent-check`, commit `feat(post-audit): волна 2 — P1-хвост + арбитры`.

### Track O (оператор/test-VPS, вне кода)
Строго после Волны 1 (минимум T1.A+T1.B): по release-checklist root AGENTS.md — node-update staging (REF-0007 drill через новый stdin-канал T1.F) → проверка DOCKER-USER peer-правил на multi-node стенд-топологии → full-stack REF-0017 → drills В4 → e2e scaffold→push→deploy. Затем — пересмотр вердикта NOT_READY → READY_WITH_WARNINGS.

# Backlog (вне скоупа READY)

| # | Позиция | Условие возврата |
|---|---------|------------------|
| BP-1 | Adopter «already simplified»-эвристика → строгий regex (mutable-канал не переписывается) | ближайший touch project_adopter |
| BP-2 | Невалидные `# noqa: EXC` / `# ruff: ignore[BLE001]` директивы (on_project_deploy:493, key_provisioner:942/944, project_adopter:580) | XS-пачка при touch файлов |
| BP-3 | `verify` verb: `<node>` без формат-валидации (orchestrator_cli:381) | XS при touch |
| BP-4 | Таймаут-литералы lib/ssh.sh:111,171 без parity-гейта к shared/timeouts.py | вместе с T2.B-механикой SoT-emit |
| BP-5 | redis/healthcheck.sh:33 литерал `-p 6379` без параметризации | XS при touch |
| BP-6 | Дефис в needs.database = FATAL всего деплоя с невнятным сообщением (on_project_deploy:128) | UX-проход деплой-ошибок |
| BP-7 | bare `off` schema enum без R5-негатива; PyYAML `on:`→True в валидаторе workflow | гейт-усиление пачкой |
| BP-8 | SHA-литералы `4425ce0` в prose @changes шаблонов; AC T1.4-формулировка (age-secret-key-file легитимен) | docs-проход |
| BP-9 | Решения владельца: macos `DATABASE_URL=""` TRAP-тег (против инварианта 8); secrets-manifest S3 consumers:[] (pre-existing); zai glm-4.5-flash формальная запись в B3 | следующий релиз |
| BP-10 | Полный R3-stale-skip свип (агенты BLOCKED по балансу) | следующий аудит-раунд |
| BP-11 | Полный матричный аудит 009 Brief vs код | следующий аудит-раунд |
| BP-12 | Поправка VerificationReport 010: 4 клейма (Наблюдаемость = wiring из T2.A, инварианты 7/4/1-2) | docs, после T2.A |

$END_DEVPLAN
