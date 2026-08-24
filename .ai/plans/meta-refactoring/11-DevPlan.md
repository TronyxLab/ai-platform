<!-- GREP_SUMMARY: devplan launch-week reliability мета рефакторинг синтез аудиты волны REF rollback healthcheck secrets backup monitoring locks pins drills freeze P0 P1 TOP-20 -->
<!-- STRUCTURE: ▶ вердикт+инварианты(freeze-P3) → ⊕ В0 честные сигналы(0012·0010·0005·0003·0013·0002·0016) → ⚡ В1 аварийные пути(0004·0011·0105·0007·0014) → ⚡ В2 каналы+DR(0006·0001·0008·0009·0015) → ⚡ В3 бюджеты+гигиена(0103·0104·0107·0017·S-пакет) → ∑ В4 drills(reboot·restore·age-key·load·e2e·chaos T1-T12) → ⎋ release-checklist -->
# region MODULE_CONTRACT
## @purpose  Реализация результатов мета-аудита платформы (9 доменов → 10-synthesis): план на 5
##           рабочих дней, закрывающий TOP-20 изменений максимального повышения production
##           reliability (отбор из 17×P0 + 14×P1 + резерв), без архитектурной перестройки
##           (~2.3–3.5k LOC churn).
## @scope    core/internal/deploy/* (orchestrator, receive_flow, audit/history, engine,
##           verify_contracts); core/internal/bootstrap/* (deploy, lifecycle/secrets, converge,
##           security); core/internal/shared/* (file_lock, ssh_cmd_builder, ssl_certs,
##           module_interface, docker_auth, compose_files); core/internal/llm/*;
##           core/internal/healthcheck/* (poller, watchdog, backup_collector);
##           core/modules/* (postgres hooks, backup-cron, nginx, langfuse/redis/minio composes,
##           monitoring configs); core/check-suite.yaml + tests/gates; .github/workflows/* +
##           actions/setup-gitleaks; templates/template-{backend,frontend} deploy.yml +
##           scaffold/project_adopter.py.
## @invariants
##   - Freeze P3-do-not-touch.md ОБЯЗАТЕЛЕН: shared leaf-контракты (deploy_paths/timeouts/
##     node_yaml/ssh_opts/platform_ports), wire-DTO forced-command, имена-контракты (verbs, env —
##     особо AGE_SECRET_KEY, detector names, network names, suite-ID/markers), __init__ ordering,
##     generated-манифесты, docker_orchestrator.py, shell-facades lib/*.sh — только чтение +
##     characterization-тесты.
##   - Только точечные диффы аварийных путей: НИ одного структурного сплита, rename контрактов,
##     миграции facades, wholesale test expansion (churn-бюджет §8 refactoring-map).
##   - Критический путь: REF-0005 → REF-0003 → REF-0004 → drills; restore-drill СТРОГО после
##     REF-0009 (иначе drill добьёт кластер — FAIL-0803); load-test smoke — после удаления дубля
##     locust-call (PERF-080, 1 строка).
##   - Верификация волны: per-task `make check TEST_FILE=<файл>`; фикс-цикл и финал — `make check`
##     (батч, до чистоты); `make agent-check` перед закрытием каждой волны; полный gate — только
##     CI (OOM-политика 0.8; вручную не запускать).
##   - Опасные изменения гейтятся staging-прогоном на test-VPS ДО прод: REF-0007 (транспорт
##     ключей → node-update), REF-0017 (сетевой attach → full-stack), REF-0009 (полный цикл
##     бэкапа), REF-0110 (порядок первого бутстрапа).
##   - Characterization перед правкой: rollback/receive/state-контур покрывается тестами ДО
##     модификации; разрешение сомнений — если изменение нельзя откатить одним revert без риска
##     для receive/polling/state-machine, оно не входит в окно (правило P3).
## @rationale Q: почему волны, а не по доменам? A: дедупликация показала схождение дефектов из
##            3+ аудитов в одни файлы; порядок волн минимизирует конфликт правок и строит
##            «честные сигналы» раньше всего — иначе последующие фиксы верифицируются ложной
##            зеленью (REF-0107 — единственный REF, допустимый к раннему подъёму при ёмкости).
##            Q: почему TOP-20, а не все 31 REF? A: жёсткое окно 5 дней; отбор value/churn из
##            final-summary; остальные — явный резерв и P2-реестр с условиями возврата.
# endregion MODULE_CONTRACT

# DevPlan meta-refactoring/11 — Launch-week reliability hardening (реализация синтеза аудитов)

Дата: 2026-08-24 · Источники: `.ai/plans/meta-refactoring/10-synthesis/` — `final-summary.md`,
`refactoring-map.md` (§5 волны, §6 multi-fix, §8 churn), `P0-launch-blockers.md`,
`P1-before-launch.md`, `P3-do-not-touch.md` · Входы: 9 доменных аудитов (01–09, ~500 raw findings).

$ARTIFACT_CONTRACT
PURPOSE: Сделать запуск платформы безопасным за оставшиеся 5 рабочих дней: восстановить
         работоспособность всех top-level deploy-safety гарантий (rollback, healthcheck-gate,
         самолечение, DR-бэкапы, мониторинг, канал сборки проектов), которые сегодня либо не
         работают, либо ложно-зелёные.
DESCRIPTION: 5 волн точечных фиксов по dependency-графу refactoring-map §5; 20 основных
         изменений (REF из P0/P1-карточек — авторитетный источник деталей каждого) + резерв;
         волна 4 — консолидация `make check` + drills на test-VPS; арбитраж качества —
         `make check`/`make agent-check`.
RATIONALE: Все дефекты confirmed (часть live-reproduced), ни один не требует архитектурной
         перестройки; порядок «честные сигналы → аварийные пути → каналы/DR → бюджеты/гигиена →
         drills» следует критическому пути и правилу freeze P3 (главная угроза недели —
         структурные рефакторинги поверх аварийных путей, а не ненайденные дефекты).
ACCEPTANCE_CRITERIA: (1) все TOP-20 REF закрыты с тестами из своих карточек; (2) `make check`
         чистый, `make agent-check` exit 0 после каждой волны; (3) поведенческие инварианты §7
         воспроизводимы; (4) drills волны 4 зелёные (reboot, restore, age-key-backup, load-smoke,
         e2e scaffold→push→deploy, chaos FULL T1–T12); (5) diff-аудит соответствия freeze P3.
IMPLEMENTS: 10-synthesis final-summary (вердикт, TOP-20, план 4+1 день), refactoring-map §5–§8,
         карточки P0 (REF-0001..0017) и P1 (REF-0101..0114).
IMPACTS: deploy-подсистема, bootstrap/lifecycle secrets, мониторинг, backup/DR, CI-workflows,
         шаблоны проектов, система гейтов; ~2300–3500 LOC; ни одного структурного сплита.
REQUIRES: test-VPS доступен для staging-прогонов и drills; GHCR-права org для build&push (B1);
         hooks установлены (`make pre-commit-install`); ознакомление исполнителей с P3-freeze.
$END_ARTIFACT_CONTRACT

$START_DEVPLAN

## 0. Контекст

- **Вердикт синтеза:** платформа архитектурно зрелая, но все top-level deploy-safety гарантии
  сейчас либо не работают, либо ложно-зелёные: rollback недостижим (ROLLED_BACK unreachable),
  healthcheck-failure = зелёный CI, параллельный деплой не считает неудачи и пропускает
  healthchecks, самолечение (R9/watchdog) сломано, мониторинг слеп в момент инцидента,
  DR-цепочка бэкапов не обеспечивает заявленный RPO, scaffolded проекты физически не могут
  задеплоиться (нет build/push канала).
- **Пять системных паттернов** (корень большинства находок, refactoring-map §2): success-marker
  до доказательства; fail-open swallowing; canon divergence (compose-invocation/healthcheck-
  criterion ×4/таймауты 600vs900/сети SoT≠рантайм); gate закрепляет баг; SoT без enforcement
  tail. План бьёт именно эти паттерны, а не отдельные симптомы.
- **Метод:** дедупликация ~500 raw findings по (файл, механика) → 31 REF (17 P0 + 14 P1),
  только confirmed в P0/P1; гипотезы изолированы (refactoring-map §4, в план не входят).
- **Детали каждого REF** (problem/evidence/files/tests/risk — 13 полей) — в P0/P1-карточках;
  этот DevPlan фиксирует порядок, швы, критерии и запреты, НЕ дублируя карточки.

## 1. Инварианты плана

1. **Freeze-first.** До начала работ каждый исполнитель читает `P3-do-not-touch.md`. Нарушение
   любого пункта freeze (rename контрактов, сплит god-файлов, правка generated-манифестов руками,
   миграция facades, wholesale test expansion, version bump) = остановка задачи.
2. **Порядок волн авторитетен.** Перестановки допустимы только внутри волны; межволновые
   зависимости — см. §2 (единственное исключение: REF-0107 может подниматься раньше при
   высвобождении ёмкости — он делает все остальные проверки честнее).
3. **Честность прежде функциональности.** Волна 0 строит правдивые сигналы (drain-status,
   PARTIAL→FAILED, false-green гейты, monitoring YAML) — на них опирается верификация всех
   последующих фиксов.
4. **Characterization до правки аварийных путей** (Волна 1): TEST-03 rollback-набор, file-lock
   тесты, payload crash-injection — пишутся/фиксируются до модификации кода.
5. **Staging-гейты опасных зон:** REF-0007 → `node-update` на test-VPS; REF-0017 → full-stack
   прогон; REF-0009 → полный цикл бэкапа; REF-0110 → контроль порядка первого бутстрапа.
6. **Имена заморожены:** `AGE_SECRET_KEY` (35+ файлов ×4 языка), verbs, detector names, network
   names, suite-ID/markers — не переименовывать даже там, где правим рядом.
7. **Верификация:** per-task `make check TEST_FILE=<файл>` (один файл на вызов); фикс-цикл —
   `make check` батчем до чистоты; `make agent-check` — обязательный шаг закрытия волны;
   журнал прогонов ведётся автоматически (`.ai/logs/runs.jsonl`, симлинк `latest.log`).
8. **Churn-бюджет:** ~2300–3500 LOC суммарно (refactoring-map §8); превышение бюджета волны —
   сигнал, что задача расползлась в рефакторинг: остановиться и сузить дифф.

## 2. Граф волн и критический путь

```
Волна 0 (день 1, независимые) — «Честные сигналы»:
  REF-0012 pins ─┐
  REF-0010 config(YAML) ─┤
  REF-0005 drain/marker ─┼─→ честные сигналы → всё остальное
  REF-0003 PARTIAL→FAILED ┘
  REF-0013 secrets fail-fast   REF-0016 XS access   REF-0002 hook register (старт)

Волна 1 (день 1–2) — «Аварийные пути»:
  REF-0004 rollback contour ←─ REF-0003 (ветка вызова)
  REF-0011 locks ←→ REF-0105 payload tx (flock-before-copy)
  REF-0007 exposure sweep   REF-0014 R9/watchdog ←─ REF-0010 (notify)

Волна 2 (день 2–3) — «Каналы и DR»:
  REF-0006 L1 gate ─→ negative-тесты TEST-05
  REF-0001 build channel ─→ e2e drill ─→ требует рабочий REF-0002 (hook)
  REF-0008 cert bundle (6 подпунктов независимы)
  REF-0009 backup truth + подготовка drills      [+ REF-0015 ingress/receive guards]

Волна 3 (день 3–4) — «Бюджеты и гигиена»:
  REF-0103 таймауты (после решения start_period из REF-0003)
  REF-0104 LLM store   REF-0107 false-green gates
  S-пакет: REF-0110 (← REF-0005) · REF-0111 · REF-0112   [+ REF-0017 network truth]

Волна 4 (день 5) — «Консолидация и drills»: make check до чистоты +
  reboot test-VPS · restore drill (ПОСЛЕ REF-0009!) · age-key-backup ·
  load-test smoke (после PERF-080 1-line) · e2e scaffold→push→deploy · chaos FULL T1–T12
```

**Критический путь:** REF-0005 → REF-0003 → REF-0004 → drills. Блокёр drills: REF-0009 restore
hardening ДО restore-drill (FAIL-0803).

**Арбитраж расхождений источников** (это решение архитектора, отклонений от final-summary
day-grid ровно два):
1. REF-0015 (ingress/receive guards) и REF-0017 (network truth) присутствуют в TOP-20, но
   пропущены в day-grid финальной сводки. Размещены сознательно: REF-0015 → Волна 2 (его правки
   receive_flow идут ПОСЛЕ кластера Волны 1, чтобы не пересекать диффы одного файла);
   REF-0017 → Волна 3 (зависит от REF-0010 langfuse-exporter достижимости; требует full-stack
   staging-прогон, который естественнее встаёт перед drills).
2. «S-пакеты REF-0109..0114» day-grid'а vs «резерв REF-0109/0114» TOP-20: в Волнах исполняются
   REF-0110/0111/0112 (+REF-0113 при ёмкости); REF-0109/0114 остаются резервом вместе с
   REF-0102. REF-0106 (state-machine honesty) и основной объём REF-0108 — вне окна (см. §9).

### Матрица трассировки TOP-20 → волны

| # | REF | Изменение | Волна | Цена |
|---|-----|-----------|-------|------|
| 1 | REF-0001 | Build&push job в шаблоны + fix adopter `image_tag` | В2 | M |
| 2 | REF-0002 | Postgres hook register + ensure-convergence + REVOKE PUBLIC | В0→В1 | M |
| 3 | REF-0003 | Unhealthy → FAILED/critical, exit≠0 | В0 | S |
| 4 | REF-0004 | Rollback contour repair | В1 | M |
| 5 | REF-0005 | drain_all_count status + all_names + run-scoped hc_done | В0 | S |
| 6 | REF-0006 | L1 deny volumes/socket/host-modes + gate в DeployOrchestrator.deploy | В2 | M |
| 7 | REF-0007 | Секреты вне argv/логов + atomic_writer sweep 0600/0640 | В1 | M |
| 8 | REF-0008 | TLS-бандл (privkey/pair-match/scan/backoff/FQDN/self-signed) | В2 | M-L |
| 9 | REF-0009 | Backup truth (sentinel/stamp/encrypt/restore/drill) | В2 | M |
| 10 | REF-0010 | Мониторинг-минимум (noeviction/rules/noDataState/render-dir/…) | В0 | M |
| 11 | REF-0011 | Конкурентность деплоя (fail-closed lock/flock-perimeter/CI group) | В1 | M |
| 12 | REF-0012 | SHA-pin 22 actions + gitleaks checksum + workflow-гигиена | В0 | S |
| 13 | REF-0013 | Secrets fail-fast (empty-parse/merge-guard/postcondition/NODE) | В0 | M |
| 14 | REF-0014 | Самолечение: R9 build_compose_args + label-детекция + watchdog stamp | В1 | S-M |
| 15 | REF-0015 | Ingress/receive resource guards | В2 | S-M |
| 16 | REF-0016 | XS access hardening (sshd kbd-interactive + sudoers arg-spec) | В0 | XS |
| 17 | REF-0017 | Network truth (hermes-agent-net attach + LANGFUSE_URL :3000 + alias) | В3 | M |
| 18 | REF-0103 | Таймаут-бандл (monotonic deadline/invoke 60s/GIT_SSH/900/120s) | В3 | M |
| 19 | REF-0104 | LLM key store (atomic+lock/fail-loud/404≠transport/pagination) | В3 | M |
| 20 | REF-0107 | False-green гейты (--only exit 2/floors/honesty/fingerprint/oracle) | В3 | M |

Резерв при высвобождении ёмкости: **REF-0102** (dead FQDN preflight repoint, <10 LOC),
**REF-0114** (org-secrets partial-success guard), **REF-0109** (node.yaml RMW локи),
**REF-0113** (SoT-константы — после объявления freeze-зон).

## 3. Черновой код-граф (структурные якоря)

```xml
<code_graph>
  <entity id="deploy_orchestrator_py" TYPE="module"
          keywords="DeployOrchestrator_CLASS,create_snapshot_FUNC,latest_snapshot_FUNC,perform_rollback_FUNC">
    Snapshot создаётся БЕЗ compose_state (REF-0004); вызовы verify_contracts отсутствуют
    (REF-0006); rollback/remove без лока (REF-0011). CrossLinks: deploy_receive_flow_py,
    deploy_history_py, deploy_engine_py, verify_contracts_py, shared_file_lock_py.
  </entity>
  <entity id="deploy_receive_flow_py" TYPE="module"
          keywords="receive_VERB,payload_swap,payload_backup,hc_done_marker">
    Exit 0 при PARTIAL (REF-0003); copy-before-lock (REF-0011); finally rmtree backup
    (REF-0105); extract-ceiling отсутствует (REF-0015). CrossLinks: deploy_orchestrator_py,
    shared_compose_files_py, shared_file_lock_py.
  </entity>
  <entity id="deploy_history_py" TYPE="module" keywords="snapshot_schema,previous_image,require_healthy">
    Схема снапшота дополняется полем previous_image (additive-only — wire-freeze сохранён).
    CrossLinks: deploy_orchestrator_py.
  </entity>
  <entity id="deploy_engine_py" TYPE="module"
          keywords="Engine_CLASS,handle_first_deploy,wait_health_FUNC,previous-rollback-pull">
    Pull локального тега из GHCR при rollback (~135s ×5) → skip при rollback_performed
    (REF-0004); re-verify health после отката; BUG-0100 rider: pull-failure ≠ first-deploy FATAL.
    CrossLinks: engine_lifecycle_py, deploy_orchestrator_py.
  </entity>
  <entity id="verify_contracts_py" TYPE="module"
          keywords="_check_dangerous_volumes_FUNC,L1_deny_set,l1_only_MODE,compose_config_valid">
    Расширение deny-set (volumes/socket/host-modes/pid/userns/sysctls) + новый вход l1_only
    из DeployOrchestrator.deploy (REF-0006). CrossLinks: deploy_receive_flow_py.
  </entity>
  <entity id="bootstrap_parallel_runner_py" TYPE="module"
          keywords="drain_all_count_FUNC,drain_completed_count_FUNC,WEXITSTATUS,pid_to_name">
    Зеркалирование exit-status детей; all_names до drain; run-scoped hc_done (REF-0005).
    CrossLinks: bootstrap_deploy_orchestrator_py, lifecycle_phases_docker_py.
  </entity>
  <entity id="healthcheck_poller_py" TYPE="module" keywords="wait_health,monotonic_deadline,start_period,PARTIAL">
    Success-предикат сужается (PARTIAL ∉ success — REF-0003); единый deadline (REF-0103).
    CrossLinks: deploy_engine_py, shared_timeouts_py.
  </entity>
  <entity id="shared_file_lock_py" TYPE="module" keywords="FileLock_CLASS,_REENTRANT,EACCES,PermissionError">
    Fail-closed на существующем файле; depth → instance attr; база для локов receive/rollback
    (REF-0011). CrossLinks: deploy_receive_flow_py, llm_key_provisioner_py.
  </entity>
  <entity id="secrets_cluster" TYPE="cluster"
          keywords="secrets_manager_py,decrypt_secrets_py,helpers_secrets_py,platform_config_py,merge_guard">
    Empty-parse fatal, Step 3.5 merge-guard, postcondition required∧sops, NODE-dispatch,
    latch-фикс, signal-хендлеры в main() (REF-0013). CrossLinks: shared_crypto_py,
    bootstrap_core_deliverer_py.
  </entity>
  <entity id="key_transport" TYPE="cluster" keywords="ssh_cmd_builder_py,bootstrap_sh,core_deliverer_py,stdin_transport">
    AGE/SSH-ключи вне argv (stdin → bash -s / SCP 0600 + unset); redact логов; atomic_writer
    mode=0600 sweep (REF-0007). Имя AGE_SECRET_KEY заморожено. CrossLinks: secrets_cluster.
  </entity>
  <entity id="selfheal_cluster" TYPE="cluster" keywords="converge_runtime_py,watchdog_py,R9,stamp_after_success">
    R9 через build_compose_args + label=com.docker.compose.project; watchdog last_restart после
    успешного restart; TG на skip (REF-0014). CrossLinks: monitoring_cluster (notify).
  </entity>
  <entity id="cert_cluster" TYPE="cluster"
          keywords="s3_ssl_cache_py,cert_orchestrator_py,issue_cert_py,cert_expiry_check_py,shared_ssl_certs_py">
    privkey обязателен, pair-match, expiry-scan покрывает letsencrypt-live, ACME backoff,
    FQDN validation entry (REF-0008). CrossLinks: node_yaml_projects_py.
  </entity>
  <entity id="backup_cluster" TYPE="cluster"
          keywords="backup_cleanup_sh,upload_py,backup_collector_py,uploaded_sentinel,freshness_stamp,ON_ERROR_STOP">
    Sentinel-gated cleanup, stamp после gzip -t, age-encrypt дампов, restore-рецепт с
    pre-snapshot (REF-0009). CrossLinks: postgres_module_yaml, reboot_policy_py.
  </entity>
  <entity id="monitoring_cluster" TYPE="cluster"
          keywords="config_renderer_py,prometheus_yml_tmpl,alert_rules_yml,noeviction,noDataState,render_dir">
    Конфиг-часть в В0; heartbeat/warning-push после стабильности status-page (REF-0108-зависимость)
    — не позже В3 (REF-0010). CrossLinks: selfheal_cluster, langfuse_composes.
  </entity>
  <entity id="llm_store" TYPE="cluster" keywords="llm_key_provisioner_py,llm_admin_client_py,atomic_write_json">
    Atomic+lock+fail-loud corrupt; 404≠transport-error; pagination verify; phase failure-count
    ≠ skipped (REF-0104). Сначала 5-мин верификация PERF-082. CrossLinks: shared_file_lock_py.
  </entity>
  <entity id="gate_honesty_cluster" TYPE="cluster"
          keywords="static_registry_py,check_suite_yaml,fingerprint_py,honesty_py,collection_floors,manifest_oracle">
    --only validation → exit 2; discovery roots; floors; honesty deny-by-default; fingerprint
    salt; независимый manifest oracle (REF-0107). Поднимается при первой ёмкости. CrossLinks:
    check_suite_init_py (freeze ordering — константы выше re-export).
  </entity>
  <entity id="ci_workflows" TYPE="cluster" keywords="sha_pin,gitleaks_checksum,concurrency_group,permissions,ssh_opts_shell">
    22 actions → @<sha>; setup-gitleaks sha256-verify; concurrency deploy-group; SSH_OPTS из
    ssh_opts --shell (REF-0012, REF-0011). CrossLinks: key_transport.
  </entity>
</code_graph>
```

## 4. Сквозной поток данных деплоя после фиксов

```
git push → CI проекта: build&push ghcr.io/<org>/<proj>:<sha> [REF-0001]
        → reusable deploy-project.yml (SHA-pinned actions, concurrency-group) [REF-0012/0011]
        → SSH forced-command receive:
            ① flock per-project (reentrant, fail-closed)              [REF-0011]
            ② payload staging: stream-extract с uncompressed ceiling  [REF-0015]
               backup_dir ВНЕ target_dir                              [REF-0105]
            ③ verify_project_contracts(l1_only=True)                  [REF-0006]
            ④ create_snapshot({previous_image})                       [REF-0004]
            ⑤ compose up → wait_health(monotonic deadline)            [REF-0103]
        → healthy  ⇒ DEPLOYED → post_deploy_chain → notify success
        → unhealthy ⇒ FAILED-ветка [REF-0003]:
            engine compose-rollback(previous_image)                   [REF-0004]
            → wait_health re-verify → ROLLED_BACK | Rollback-failed
            → exit≠0, Telegram severity=critical; payload ← backup    [REF-0105]
        → postgres hook ensure-convergence: role/DB/GRANT идемпотентно [REF-0002]

Параллельный путь (DEPLOY_PARALLEL=true): группы → drain_all_count читает
WEXITSTATUS [REF-0005] → failed>0 ⇒ атомарный откат группы, вердикт ≠ success;
hc_done пишется только при failed==[] и скоупится run-id.
```

## 5. Волны

Формат задач: карточка REF в P0/P1 — авторитет; здесь — объём, швы и проверка.

### Волна 0 (день 1) — «Честные сигналы»

Цель: сломать системный паттерн «ложная зелень»; всё дальнейшее верифицируется этими сигналами.

| REF | Объём | Файлы | Проверка |
|-----|-------|-------|----------|
| 0012 | Pin 22 actions на full commit SHA (`@<sha> # vX`); gitleaks sha256-verify; развести PR-job'ы от secrets; disable cache на pull_request_target; `permissions:{}` + quoted interpolation; SSH_OPTS из `python3 -m …ssh_opts --shell` | .github/workflows/*.yml, .github/actions/setup-gitleaks/action.yml | Новый структурный gate: все `uses:` SHA-form; grep raw `${{ }}` в `run:`; `make check-diff` |
| 0010 (config) | langfuse-redis → noeviction (+maxmemory↑); redis main maxmemory 192mb; pgbouncer-exporter+job+rules; второй redis_exporter (langfuse) + evicted_keys alert; minio job; scrape loki/alloy + up-rules; DiskSpace/HighMemory → noDataState=Alerting; warning-push enable + critical repeat 2h; canonicalize render-dir (AI-0004); tsdb retention.size | core/modules/{langfuse,redis}/docker-compose.base.yml, infra-metrics compose, monitoring/{config_renderer.py, prometheus.yml.tmpl, alert-rules*.yml}, shared/deploy_paths.py | Gate: renders land in mounted dir (path-parity); alert-rule presence smoke (yaml-parse); runtime — на test-VPS в В4 |
| 0005 | drain_all_count зеркалит WIFEXITED/WEXITSTATUS (failed++/failed_names); маркер `.hc_done_in_deploy` — только при failed==[], run-id в имени, unlink на старте init/update; all_names собирать ДО drain (~10 строк) | bootstrap/deploy/parallel_runner.py, deploy_orchestrator.py, lifecycle/phases/docker.py | Тест с РЕАЛЬНЫМ drain_all_count + mocked waitpid (сегодня красный): имена из pid_to_name, non-empty all_names; `make check TEST_FILE=test_parallel_runner.py` |
| 0003 | unhealthy/timeout → статус FAILED + exit≠0 + notify severity=critical; PARTIAL — внутренний, не success; согласовать окно со start_period (решение — вход для REF-0103) | deploy/orchestrator.py (:556/:151-153), receive_flow.py (:559-568), hooks/post_deploy_chain.py, orchestrator_cli.py | DI-тест poller=unhealthy → rc≠0; severity-mapping уведомлений (TEST-04) |
| 0013 | Непустой enc + 0 ключей → PlatformFatalError; merge-guard Step 3.5; narrow excepts; postcondition parsed ⊇ {required∧sops}; file-wins после decrypt (override-allowlist); NODE-filter; `_loaded=True` после успешного load; signal/atexit → main(), итерировать `list(_TEMP_FILES)`, +SIGHUP + стартовый sweep /dev/shm | bootstrap/lifecycle/helpers/secrets.py, secrets_manager.py, phases/secrets.py, secrets/decrypt_secrets.py, config/platform_config.py, makefiles/ci.mk | TEST-07 (stderr-redaction), TEST-08 (signal-contract), empty-parse→fatal unit, merge-guard unit, NODE-dispatch unit |
| 0002 (старт) | Зарегистрировать `hooks.on_project_deploy` в postgres/module.yaml + переписать hook-gate; начало ensure-convergence (role_exists+no-creds → ALTER PASSWORD + creds + GRANT + реген) | core/modules/postgres/module.yaml, hooks/on_project_deploy.py, tests/gates/test_gate_module_hooks.py | Обновлённый hook-gate; unit ensure-convergence; завершение — в В1 (GRANT-checks, psql timeout=60, REVOKE PUBLIC rider SEC-0008) |
| 0016 | +KbdInteractiveAuthentication no +ChallengeResponseAuthentication no (+MaxAuthTries 3) в drop-in и _SSHD_EXTRA_DIRECTIVES; нейтрализовать *cloud* sshd_config.d; apply-failure → blocking; sudoers arg-spec (--mode pin / launcher-whitelist, игнор --*-key/--state-file) | bootstrap/security/sshd_policy.py, lifecycle/phases/system.py, bootstrap/setup_node.py | Gate: парсинг итогового `sshd -T` на fixture; sudoers line-format gate (прецедент sudoers_generator) |

Готовность волны: `make check` чистый · `make agent-check` exit 0 · демонстрационно: unhealthy
деплой красит CI, drain с failed-ребёнком даёт failed>0.

### Волна 1 (день 1–2) — «Аварийные пути»

Цель: сделать страховочную сетку реальной. Перед правкой — characterization (инвариант 4).

| REF | Объём | Файлы | Проверка |
|-----|-------|-------|----------|
| 0004 | Persist `{"previous_image": <id>}` в снапшот до compose-up; skip snapshot-rollback при `rollback_performed=True`; `latest_snapshot(require_healthy=True)` + WARN-fallback; после perform_rollback — один wait_health + поле rollback_verified; payload восстанавливать только после успешного compose-rollback; BUG-0100 rider (pull-failure при существующем деплое ≠ first-deploy FATAL) | deploy/orchestrator.py, deploy/audit/history.py, deploy/engine/{engine,lifecycle}.py, receive_flow.py | TEST-03 набор ДО правки (characterization), затем: compose_rollback=True→DEPLOYED+audit-row; False→FAILED+"Rollback failed"; unhealthy→ROLLED_BACK сквозной |
| 0011 | PermissionError на существующем файле → FileLockError (degrade — только dir/dev-кейс); locks 0664/chown ci-deploy (паттерн history.py:188); flock в начале ReceiveFlow (reentrant depth); rollback()/remove() под тем же локом; `concurrency: {group: deploy-${{ inputs.project_name }}, cancel-in-progress: false}`; retryable = not success AND exit_code != 124; `_REENTRANT` depth → instance attr + try/finally | shared/file_lock.py, deploy/receive_flow.py, deploy/orchestrator.py, .github/workflows/deploy-project.yml (+template), channels/base.py | Новый test_file_lock.py (nested acquire/release, EACCES-existing→raise, timeout-poll — TEST-32); interleave-тест copy-vs-lock |
| 0105 | backup_dir вне target_dir + restore-from-backup в except ДО rmtree; replace без pre-remove; удалять canonical PROJECT_COMPOSE_FILENAMES, отсутствующие в staging; EOFError в except-кортеж; prefix-sweep orphan tmpdir; единая константа payload file-list (генерируется для CI, потребляется обеими сторонами) | deploy/receive_flow.py, shared/compose_files.py, payload_deliverer.py, deploy-project.yml | Crash-injection unit (исключение между replace → восстановление); stale-compose deletion unit; triple-sync whitelist structural test |
| 0007 | Доставка AGE/SSH ключей вне argv (stdin → `bash -s`, либо SCP 0600 root-файл + unset); redact в логах deliver_fallback; WARN при env-over-file; atomic_writer(mode=0600) для secrets.env.tmp и litellm-keys; umask 077 в lifecycle entrypoints; atomic_write_text(0640)+chown ci-deploy для регена .env.platform; chmod после copy в receive; sops/openssl значения через stdin | shared/ssh_cmd_builder.py, entrypoints/bootstrap.sh, bootstrap/core_deliverer.py, lifecycle/secrets_manager.py, scaffold/gen_env_platform.py, llm/key_provisioner.py, shared/crypto.py | Redaction-тест stderr (TEST-07); тест mode=0600 от создания; ⚠️ ОБЯЗАТЕЛЬНЫЙ staging `node-update` на test-VPS до прод; имя AGE_SECRET_KEY не менять |
| 0014 | R9 → `build_compose_args(module_name, module_dir=...)`; детекция по `label=com.docker.compose.project=<module>` вместо substring; watchdog: last_restart per-action ПОСЛЕ успешного restart + re-save; TG «crash-loop detected, не рестарчу» в skip-path; scheduled converge — минимально задокументировать ручной `make converge` в runbook | bootstrap/converge/runtime.py, healthcheck/watchdog.py | Unit: R9 argv содержит root-first/profile/env-file (fixture на build_compose_args); watchdog stamp-after-success sequence test |

Завершение REF-0002 (перетёк из В0): проверка результата каждого GRANT (failed → CRITICAL-счётчик),
timeout=60 во всех ветках `_psql`, rider `REVOKE CONNECT ON DATABASE <db> FROM PUBLIC`;
port тестов shared-db seam в ci-docker gate (TEST-18).

Готовность волны: `make check` + `make agent-check` чистые; staging node-update прошёл
(REF-0007); characterization-наборы аварийных путей зелёные до и после.

### Волна 2 (день 2–3) — «Каналы и DR»

Цель: рабочие каналы доставки (проектный образ, L1-периметр, TLS, бэкапы).

| REF | Объём | Файлы | Проверка |
|-----|-------|-------|----------|
| 0006 | `_check_dangerous_volumes`: deny socket-mounts + абсолютные host-binds вне минимального allowlist + требование named volumes; deny-keys: network_mode:host / pid / userns_mode / cgroup / sysctls; вызов `verify_project_contracts(dir, l1_only=True)` внутри DeployOrchestrator.deploy перед _apply_deploy; compose-config-valid → блокирующий в l1_only | deploy/verify_contracts.py, receive_flow.py, orchestrator.py | R5-негативы с точным C1-input (socket-mount, `/`-bind); параметризованные traversal-негативы receive/remove через _dispatch (TEST-05); residual SEC-0013 зафиксировать в доке |
| 0001 | Build&push job в оба шаблона (копия блока project_adopter.py:194-222); удалить строку `image_tag:` из генератора adopter; подготовить e2e scaffold→push→deploy | templates/template-{backend,frontend}/.github/workflows/deploy.yml, core/internal/scaffold/project_adopter.py | e2e на test-VPS (release-checklist; требует рабочий REF-0002); lint шаблонов (templates-check) |
| 0008 | 6 независимых подпунктов: (1) privkey обязателен в download_cert + openssl pubkey-match; (2) cert_is_valid проверяет пару; (3) expiry-unit `--cert-dir /etc/letsencrypt/live` + fullchain.pem в CERT_FILENAMES; (4) TG-alert source=self_signed + отказ self-signed overwrite LE-сертификата; (5) ACME sleep/backoff между attempts (shared/retry); (6) validate_vhost_identifiers на register_project И orchestrate_certs entry (fail-fast) + reloadcmd shlex.quote + install-cert через tmp+rename | bootstrap/{s3_ssl_cache,cert_orchestrator,issue_cert,cert_expiry_check,cron_installer}.py, shared/ssl_certs.py, node_yaml/projects.py, project_registry.py | Pair-match unit (valid/mismatch/missing); scan-coverage тест на tmp-каталоге; validator-negative `../`-домен (R5); сверка S3↔live — строка в DR-drill REF-0009 |
| 0009 | `.uploaded` sentinel (или S3 HEAD-confirm) → cleanup удаляет только подтверждённое; ежедневный spool-rescan retry; touch `.last_verified` только после gzip -t OK, collector читает маркер; age-encrypt перед upload (+decrypt шаг в restore-runbook); Makefile restore: down → psql `-v ON_ERROR_STOP=1` → up + mandatory pre-restore pg_dumpall; reboot OnCalendar → 05:45 (или lock-проверка); flock -n ×4 cron-строки; убрать двойную установку crontab (Dockerfile:97/101); doc-fix PostgreSQL 18.4; выполнить `make age-key-backup` | core/modules/backup-cron/scripts/*, healthcheck/metrics/backup_collector.py, core/modules/postgres/Makefile, reboot_policy.py, core/modules/postgres/module.yaml(+docs) | Unit: cleanup не трогает unsentinel; collector читает stamp; restore-recipe dry-структурный тест; ⚠️ полный цикл бэкапа на test-VPS — precondition для restore-drill В4 |
| 0015 | nginx: limit_conn_zone + limit_conn perip 20; client_header/body_timeout 10s, send_timeout 30s, keepalive_timeout 15s; SSE read_timeout ≤300s; receive: stream-extract с running uncompressed ceiling ~200MB + entry-count cap; default payload cap ↓ 64MiB; statvfs guard перед extract | core/modules/nginx/config/nginx.conf, vhost-шаблоны (template-контракт таймаутов), deploy/receive_flow.py | Unit на stream-extract ceiling (маленькая tar-бомба fixture); nginx `-t` structural gate дополнить проверкой директив; потолок выбрать ×3 от текущих легитимных |

Готовность волны: e2e scaffold→push→deploy зелёный на test-VPS; полный цикл бэкапа прошёл;
L1-негативы красят сборку без фикса (проверка R5-семантики).

### Волна 3 (день 3–4) — «Бюджеты, хранилища, гигиена»

| REF | Объём | Файлы | Проверка |
|-----|-------|-------|----------|
| 0103 | Единый monotonic deadline (start+max_retries×interval) прокидывается вниз; single-shot cold-skip gate; HEALTHCHECK_CMD_TIMEOUT=60 для liveness-invokes; GIT_SSH_COMMAND из ssh_opts + DEPLOY_TIMEOUT на mirror; DOCKER_AUTH_TIMEOUT; lib/ssh.sh default ← 900 + fix комментария/TRAP; killpg через subprocess_io canon; SubprocessError в 5 except-кортежей; TimeoutExpired → незавершённые = failed (не (0,[])); litellm request_timeout 120s | deploy/healthcheck_poller.py, bootstrap/deploy/{context_deployer,deploy_orchestrator}.py, shared/{module_interface,docker_auth}.py, core/lib/ssh.sh, monitoring config, llm pipeline excepts | Wall-time budget тест poller (≤ бюджет); argv-тест mirror (GIT_SSH_COMMAND присутствует); except-таблица тест SubprocessError |
| 0104 | Шаг 0: 5-минутная верификация PERF-082 (HYPOTHESIS) — фикс по результату. atomic_write_json + FileLock(store.lock) + corrupt → fail-fast (никогда overwrite-all) + mkstemp-mode 0600; различать 404(None) vs transport-error(raise/skip-WARN); fetch key-list once + filter; pagination loop; long-lived httpx.Client; фазовый summary failure-count ≠ skipped + WARN→ERROR | llm/key_provisioner.py, llm/admin_client.py, lifecycle/phases/docker.py | Corruption-chain unit (truncate → следующий load fails loud); lookup-semantics unit (timeout ≠ 404); pagination integration (mocked transport) |
| 0107 | `--only` против реестра, unknown → exit 2; discovery roots core/modules+node-configs; collection floors (--collect-only ≥1) для исторически наполненных suites; honesty deny-by-default glob по всем workflow с pytest; fingerprint salt = toolchain-digest + 3 env-vars + unique tmp via atomic_write_json; независимый semantic-validator manifests (без импорта генератора); constants → constants.py (атомарно, с прогоном make check — freeze ordering учтён); lifecycle импортирует engine.flow прямым module-path; lint-правило единого import-пути PlatformFatalError | core/internal/static/registry.py, core/check-suite.yaml, check_suite/__init__.py(+constants.py), validate_orchestrator.py, tests/_conftest/honesty.py + gates, check_suite/fingerprint.py, scripts/manifest_driver.py, shared/node_yaml/__init__.py | Сами изменения — тесты (floors gate, honesty glob gate, fingerprint-differs, oracle-parity); всплытие накопленных нарушений — ПРИНЯТЬ (это цель), буфер — В4 |
| 0017 | ОДНО решение размещения, отражённое везде: аддитивно добавить hermes-agent-net членство litellm/langfuse/minio (НЕ убирая текущие сети), синхронизировать platform-infra.yaml; PLATFORM_LANGFUSE_URL → :3000; regen platform-env/templates; nginx alias в generated smoke-host; manifest-parity gate: provides.*.networks ⊆ фактических attach | platform-infra.yaml (SoT), langfuse/litellm/minio composes, gen_env_platform pipeline, templates/.env.example | Новый маленький parity-gate сетей; smoke_env_generated host-resolve тест; ⚠️ full-stack прогон на test-VPS до прод; согласовать с REF-0010 (достижимость langfuse-exporter) |
| S-пакет | REF-0110: kahn-линеаризация для sequential + topo-failure → ConfigValidationError + abort remaining после critical-failure (использует честный failed-учёт REF-0005). REF-0111: параметры docker-smoke (xdist/timeout/pre-cleanup) владеет check-suite.yaml; parity-gate. REF-0112: CI вызывает `python3 -m …core_deliverer` (один owner exclude-set). PERF-080: удалить дубль locust-call (1 строка — обязательно до load-smoke) | bootstrap/deploy/deploy_orchestrator.py; check-suite.yaml + conftest-compose + workflow; core-deploy.yml + core_deliverer.py; load-test скрипт | Order-тест build_dag+kahn на 2-level DAG (TEST-29 rewrite); parity-gate сам тест; grep-gate вызова deliverer в workflow |

Готовность волны: `make check` чистый; wall-time ≤ заявленного бюджета; `--only`-обманщики
закрыты (exit 2 подтверждён).

### Волна 4 (день 5) — «Консолидация и drills»

1. `make check` до чистоты (батч-фикс-циклы); `make agent-check` exit 0.
2. Drills на test-VPS (строго в этом порядке зависимостей):
   - **Reboot** test-VPS → проверить самолечение: converge R9 поднимает модули, watchdog
     штампует после успеха (валидация REF-0014; политика auto-reboot включена — сценарий
     регулярный).
   - **Restore drill** — ТОЛЬКО после REF-0009 (FAIL-0803): down → pre-restore pg_dumpall →
     gunzip|age-decrypt|psql ON_ERROR_STOP → up → healthcheck; сверка S3↔live сертификатов
     (FAIL-0309 leg).
   - **age-key-backup drill**: `make age-key-backup` + проверка восстановления (FAIL-0600/B5).
   - **Load-test smoke** — после PERF-080-фикса; capacity-verdicts пригодны
     (release-checklist требование).
   - **E2E scaffold→push→deploy**: `make new-project` → push → деплой на test-VPS
     (валидация REF-0001+0002 связки).
   - **Chaos FULL T1–T12** (после bootstrap).
3. `make test-node NODE=<test>` зелёный (0 failed); `make check NODE=<test>` — нода согласована;
   `make check MARKER=check-manifests` чистый.
4. Diff-аудит freeze: пройти по чеклисту P3 по всем волновым диффам (§7.5).
5. Release-checklist шаги 1–3 закрыты — платформа готова к `make deploy`/`make context-promote`.

## 6. Манифест файлов (сводный, по областям)

- **CI/workflows:** `.github/workflows/{core-deploy,security-scan,platform-test,deploy-project}.yml`,
  `.github/actions/setup-gitleaks/action.yml` (pins, checksum, permissions, concurrency, SSH_OPTS).
- **Deploy-ядро:** `core/internal/deploy/{orchestrator,receive_flow,orchestrator_cli,
  verify_contracts}.py`, `deploy/audit/history.py`, `deploy/engine/{engine,lifecycle}.py`.
- **Bootstrap deploy:** `bootstrap/deploy/{parallel_runner,deploy_orchestrator,context_deployer}.py`,
  `bootstrap/lifecycle/phases/{docker,system,secrets}.py`, `bootstrap/converge/runtime.py`.
- **Secrets:** `bootstrap/lifecycle/{secrets_manager.py,helpers/secrets.py}`,
  `secrets/decrypt_secrets.py`, `config/platform_config.py`, `shared/crypto.py`,
  `shared/ssh_cmd_builder.py`, `entrypoints/bootstrap.sh`, `bootstrap/core_deliverer.py`.
- **Health/self-heal:** `deploy/healthcheck_poller.py`, `healthcheck/watchdog.py`,
  `healthcheck/metrics/backup_collector.py`.
- **TLS:** `bootstrap/{s3_ssl_cache,cert_orchestrator,issue_cert,cert_expiry_check,cron_installer}.py`,
  `shared/ssl_certs.py`, `shared/node_yaml/projects.py`.
- **Backup/DR:** `core/modules/backup-cron/scripts/*`, `core/modules/postgres/{Makefile,module.yaml}`,
  `core/modules/postgres/hooks/on_project_deploy.py`, `reboot_policy.py`.
- **Monitoring:** `monitoring/{config_renderer.py,prometheus.yml.tmpl,alert-rules*.yml}`,
  `shared/deploy_paths.py`, `core/modules/{langfuse,redis,infra-metrics}/docker-compose.base.yml`.
- **Ingress/network:** `core/modules/nginx/config/nginx.conf` + vhost-шаблоны,
  `platform-infra.yaml`, `core/modules/minio/docker-compose.base.yml`, `shared/compose_files.py`.
- **LLM:** `core/internal/llm/{key_provisioner,admin_client}.py`.
- **Templates/scaffold:** `templates/template-{backend,frontend}/.github/workflows/deploy.yml`,
  `core/internal/scaffold/project_adopter.py`.
- **Shared:** `shared/{file_lock,module_interface,docker_auth,ssl_certs}.py`, `core/lib/ssh.sh`.
- **Gates/тесты:** `tests/gates/` (новые: uses-SHA-form, raw-interpolation, renders-mount-parity,
  networks-parity, L1-negatives R5, floors/honesty/fingerprint), `tests/unit/test_file_lock.py`,
  TEST-03 rollback-suite, TEST-07/08 secrets, corruption-chain/pagination units,
  wall-time budget test, `core/check-suite.yaml` (аккуратно — schema v1 не расширять).
- **Docs-in-code:** runbook restore (+age-decrypt шаг), runbook converge-ручной режим,
  doc-fix PostgreSQL 18.4, residual-заметки SEC-0013/SEC-0008 — только в коде/TRAP/docstrings
  (docs/ каталог запрещён).

## 7. Acceptance criteria (итоговые, verifiable)

7.1. Каждый закрытый REF имеет тесты из колонки «Tests required» своей карточки P0/P1 — зелёные.
7.2. `make check` чистый на финале; `make agent-check` exit 0 после каждой волны; журнал
`.ai/logs/runs.jsonl` содержит прогоны всех волн (goal/exit_code/duration).
7.3. Поведенческие инварианты (воспроизводимы на test-VPS):
   - unhealthy-деплой → rc≠0 + Telegram critical; при готовом REF-0004 — ROLLED_BACK с
     re-verified health; «deployed» при больном стеке невозможен.
   - drain_all_count с failed-потомком → failed>0 → групповой откат; φ11 healthcheck
     исполняется в каждом run (нет маркера чужого запуска).
   - compose с `volumes: [/var/run/docker.sock:…]` блокирован И в receive, И в
     DeployOrchestrator.deploy.
   - AGE/SSH-ключи отсутствуют в `/proc/*/argv` во время node-update и в логах.
   - backup-cleanup не удаляет файл без sentinel; freshness-метрика читает post-gzip stamp.
   - `--only несуществующее` → exit 2; пустая коллекция исторически наполненного suite → FAIL.
   - `PLATFORM_LANGFUSE_URL` указывает на слушающий порт; tenant-контейнеры достижимы по
     hermes-agent-net (аддитивно).
   - poller укладывается в задокументированный бюджет (wall-time тест).
7.4. Drills В4 зелёные: reboot-самолечение, restore (ON_ERROR_STOP + pre-snapshot),
     age-key-backup, load-smoke, e2e scaffold→push→deploy, chaos FULL T1–T12.
7.5. Freeze-аудит: в диффах нет rename контрактов/сплитов/миграций facades/ручных правок
     generated-манифестов; wire-DTO изменены только additive-only.
7.6. Churn ≤ ~3.5k LOC суммарно; ни одна волна не превысила свой бюджет §8 более чем на 20%
     без явного решения о сужении диффа.

## 8. Риски и смягчения

| # | Риск | Смягчение |
|---|------|-----------|
| 1 | REF-0007 транспорт ключей ломает bootstrap/core_deliverer | Обязательный staging `node-update` на test-VPS до прод; имя AGE_SECRET_KEY заморожено; откат — revert одного коммита |
| 2 | REF-0017 сетевые изменения на живой ноде | Только аддитивный attach; full-stack прогон на test-VPS до прод-деплоя; parity-gate фиксирует декларацию |
| 3 | Строжащие фиксы (REF-0107, REF-0003) вскроют накопленные нарушения | Это цель; буфер — Волна 4; новые RED'ы триажируются батчем `make check`, не точечно |
| 4 | Restore-drill до REF-0009 опасен (FAIL-0803) | Жёсткий порядок: REF-0009 → полный цикл бэкапа на test-VPS → только затем drill |
| 5 | Structural creep поверх аварийных путей | Freeze P3 + churn-бюджет + правило «один revert»; сомнение трактуется против изменения |
| 6 | Легитимные slow-start деплои начнут падать после REF-0003/0103 | Согласовать start_period и окна в В0/В3 (решение фиксируется TRAP[DECISION] у poller) |
| 7 | OOM/флакины полного gate на dev-машине | Полный gate НЕ запускать вручную (OOM-политика 0.8); арбитры: `make check`/`agent-check` локально, fast-gate — CI |

## 9. Явно вне окна (post-launch кандидаты)

- **REF-0106** state-machine honesty (M, 1 день) — первый кандидат следующего спринта: CI гоняет
  node-update на каждый push, канал должен стать честным сразу после окна.
- **REF-0108** основной объём status-page/metrics (thread-leak/tail-read/cache) — heartbeat
  (REF-0010 tail) ставится после его стабилизации; исключение — 1-строчный PERF-080 фикс (В3).
- **REF-0101** credential↔project binding — phased (enforce при наличии binding), ротация ключей
  всех repo-CI — отдельная операционная задача.
- P2-реестр целиком: структурные сплиты, multi-tenant security, perf-бэнд, test hygiene,
  gate-amplification архитектура (DEP-0054..59), constant-platform (DEP-0020).

## 10. Коммиты и журнал

- Коммит плана: `docs(meta-refactoring): 11 DevPlan — launch-week reliability waves (синтез 9 аудитов)`.
- Реализация: отдельный feat-коммит на волну — `feat(refactor): волна N — <тема> (REF-…)`
  (раздельные по волнам — норма per commit-policy; big-bang запрещён).
- Перед каждым коммитом: `make fix-gate && git add -u` (pre-commit не отключать); push — по
  одному за раз, таймаут bash-тула ≥300s; отказ hook'а читать в stderr hook-лога.
- Журнал прогонов каждой команды — автоматом в `.ai/logs/runs.jsonl`; следующий агент начинает
  с `python3 -m core.internal.shared.test_journal latest`.

$END_DEVPLAN
