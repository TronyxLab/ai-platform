<!-- GREP_SUMMARY: statusreport ref-ledger леджер чекбоксы REF статусы волны drills сессии прогресс план11 launch-week -->
<!-- STRUCTURE: ▶ старт сессии: леджер + test_journal latest → ⚡ работа по волнам → ⊕ апдейт статусов/сползаний в конце сессии → ⎋ STOP -->
# region MODULE_CONTRACT
## @purpose  REF-леджер плана meta-refactoring/11: кросс-сессионное состояние «какие REF закрыты».
##           runs.jsonl фиксирует прогоны; этот файл — семантический прогресс работы.
## @scope    TOP-20 + резерв + drills В4 из 11-DevPlan.md; детали каждого REF (problem/files/
##           tests/risk) — в P0/P1-карточках синтеза, здесь только статус и заметки.
## @invariants
##   - Обновляется в конце КАЖДОЙ рабочей сессии; новая сессия стартует отсюда +
##     `python3 -m core.internal.shared.test_journal latest`, НЕ с перечитывания DevPlan.
##   - Сползания единиц между волнами (инв. 2b DevPlan: подпункты REF-0010) фиксируются здесь —
##     тихий дрейф запрещён.
##   - done = тесты из колонки «Проверка» карточки зелёные, а не «код написан»;
##     волна закрывается только при чистых `make agent-check` + `make check MARKER=check-manifests`.
# endregion MODULE_CONTRACT

# StatusReport — REF-леджер (план 11, launch-week reliability hardening)

Инициализирован: 2026-08-24 (создание DevPlan 11) · Исполнитель: —

Легенда: `[ ]` pending · `[~]` in-progress · `[x]` done · `⛔` блокер (описать в заметке)

## Волна 0 — «Честные сигналы» (must-set дня 1: REF-0003, REF-0005, REF-0013)

| Статус | REF | Единица | Заметка |
|--------|-----|---------|---------|
| [x] | REF-0003 | PARTIAL→FAILED, exit≠0 | must-set · is_success={DEPLOYED,SKIPPED}; critical-notify; start_period=60s (TRAP[DECISION] у poller, вход REF-0103); DI-тесты rc≠0 + severity-mapping |
| [x] | REF-0005 | drain/marker/all_names | must-set · drain_all_count зеркалит exit-status; run-scoped hc_done+unlink-on-init; тесты с реальным drain (red→green) |
| [x] | REF-0013 | secrets fail-fast | must-set · empty-parse→fatal, merge-guard 3.5, narrow excepts, postcondition DATA-1006, NODE-dispatch, file-wins, signal/atexit в main(); TEST-07/08 зелёные |
| [x] | REF-0012 | pins платформенных workflows + gitleaks checksum | включая SHA-pin шаблонных workflows (TRAP[DECISION] §5/В0); gate test_gate_workflow_sha_pins 8/8; 11 SHA резолвлены gh api; SSH_OPTS inline-набор (TRAP[DECISION] deferred — org-agnostic) |
| [x] | REF-0010 | конфиг-ядро noeviction/maxmemory/noDataState | langfuse-redis noeviction@96mb; main redis 192mb; DiskSpace/HighMemory→Alerting; warning-push on; repeat 2h; tsdb retention.size |
| [x] | REF-0010 | exporters/rules/render-dir | закрыто в В0 — сползания НЕТ: pgbouncer-exporter+job+rule, второй redis_exporter+evicted_keys, minio job, alloy scrape+up-rules, render-dir AI-0004 (additive в deploy_paths) |
| [x] | REF-0016 | XS access hardening | kbd-interactive/challenge no + MaxAuthTries 3 (drop-in+S4); *cloud* glob-нейтрализация; φ1 5.6 blocking; sudoers --mode pin ×2 |
| [x] | REF-0002 | postgres hook register (старт) | wrapper.sh + interfaces:deploy-hook + hooks.on_project_deploy; hook-gate red→green; ensure-convergence orphan-role ветка (5 unit) |

Волна 0 closed 2026-08-24: commits `d331e01` (T9 ops trio) + `aaa209d` (волна) · `make check`
чистый (4756+ passed), `make agent-check` blocking=0, `make check MARKER=check-manifests` GREEN.
Хвосты интеграции волны: test-оверлеи langfuse/service-exporters/minio, service-exporters
module.yaml 288M→448M, healthcheck interval exporter 15s, ZAI_API_KEY в secret-definitions +
.env.example (след T9-zai), LLM-гейты deepseek-only → мультипровайдер (SoT policy#providers),
фикстуры state-machine получили secrets-manifest.yaml (след REF-0013 fail-fast),
R1-ассерты в test_secrets_postcondition.

## Волна 1 — «Аварийные пути»

| Статус | REF | Единица | Заметка |
|--------|-----|---------|---------|
| [ ] | REF-0004 | rollback contour | characterization ДО правки (инв. 4) |
| [ ] | REF-0011 | locks fail-closed/flock-perimeter/CI group | |
| [ ] | REF-0105 | payload tx (backup вне target) | |
| [ ] | REF-0007 | ключи вне argv + atomic sweep 0600 | ⛔ без staging node-update на test-VPS не закрывать |
| [ ] | REF-0014 | R9 build_compose_args + watchdog stamp | |
| [ ] | REF-0002 | финал: GRANT-checks, psql timeout=60, REVOKE PUBLIC | перетёк из В0 |

## Волна 2 — «Каналы и DR»

| Статус | REF | Единица | Заметка |
|--------|-----|---------|---------|
| [ ] | REF-0006 | L1 deny-set + gate в DeployOrchestrator.deploy | negative-тесты TEST-05 |
| [ ] | REF-0001 | build&push job в шаблоны + fix adopter image_tag | блок наследует SHA-pins REF-0012 |
| [ ] | REF-0008 | TLS-бандл (6 независимых подпунктов) | отмечать подпункты здесь по мере закрытия |
| [ ] | REF-0009 | backup truth (sentinel/stamp/encrypt/restore) | precondition restore-drill В4 |
| [ ] | REF-0015 | ingress/receive resource guards | |

## Волна 3 — «Бюджеты, хранилища, гигиена»

| Статус | REF | Единица | Заметка |
|--------|-----|---------|---------|
| [ ] | REF-0103 | таймаут-бандл | после start_period-решения REF-0003 (TRAP[DECISION] у poller) |
| [ ] | REF-0104 | LLM key store | шаг 0: 5-мин верификация PERF-082 |
| [ ] | REF-0107 | false-green гейты | допустим ранний подъём (инв. 2a) |
| [ ] | REF-0017 | network truth (hermes-agent-net/:3000/alias) | full-stack staging прогон обязателен |
| [ ] | S-пакет | REF-0110 kahn / REF-0111 smoke-parity / REF-0112 deliverer | PERF-080 (1 строка) — до load-smoke |

## Резерв (при высвобождении ёмкости)

| Статус | REF | Единица | Заметка |
|--------|-----|---------|---------|
| [ ] | REF-0102 | dead FQDN preflight repoint (<10 LOC) | |
| [ ] | REF-0114 | org-secrets partial-success guard | |
| [ ] | REF-0109 | node.yaml RMW локи | |
| [ ] | REF-0113 | SoT-константы | после объявления freeze-зон |

## Drills В4 — день 5 (порядок зависимостей авторитетен)

| Статус | Drill | Precondition |
|--------|-------|--------------|
| [ ] | Reboot test-VPS → самолечение | REF-0014 |
| [ ] | Restore drill | ⛔ строго ПОСЛЕ REF-0009 + полный цикл бэкапа (FAIL-0803) |
| [ ] | age-key-backup drill | `make age-key-backup` + проверка восстановления |
| [ ] | Load-test smoke | после PERF-080-фикса |
| [ ] | E2E scaffold→push→deploy (+ remove-project cleanup throwaway) | REF-0001+0002 зелёные |
| [ ] | Chaos FULL T1–T12 | после bootstrap |
| [ ] | Финал: make check до чистоты · agent-check · check-manifests · test-node · freeze-аудит | все волны closed |

## Журнал сессий

| Дата | Волны/REF в работе | Статус | Заметки (блокеры, сползания инв. 2b) |
|------|--------------------|--------|--------------------------------------|
| 2026-08-24 | — | планирование | DevPlan 11 создан; леджер инициализирован |
| 2026-08-24 | Волна 0 (все 7 единиц) | closed | 7 параллельных Agent Manager-сессий на Ox Alpha; commits d331e01+aaa209d; make check/agent-check/check-manifests чистые; drills В4 ждут test-VPS; REF-0007 staging node-update — перед продом |
