# Direction 8 — Initialization / Lifecycle Architecture

Агент: форензик направления «lifecycle/init» · Дата: 2026-08-22

Итог направления: 6 находок — 0 CRITICAL, 3 HIGH (ARCH-071..073), 3 MEDIUM (ARCH-074..076). Подтверждённые ordering-by-side-effect пары: φ12 `--skip-provision` ← provision sub-step φ11 (docker.py:702 ← :276-298, guarded WARN-tracking); φ8 deploy-context ← preflight clone side effect (docker.py:191 ← deploy_orchestrator.py:267, unguarded); φ11 healthcheck ← `.hc_done_in_deploy` маркер (docker.py:600 ← deploy_orchestrator.py:554, unguarded on failure). Import-time вопрос: NEGATIVE для runtime — ни один core/bootstrap модуль не импортирует generated артефакты на module level; единственный import-crash сайт — тестовая инфраструктура (tests/_conftest/env.py:34 импортирует smoke_env_generated; tests/helpers/env_defaults_generated.py fallback :157), огороженная гейтом check-manifests. Resume грубый (whole-phase re-run, без sub-step checkpoints), но когерентный; системная слабость — корректность фаз опирается на filesystem markers, env mutation и list ordering, а не на декларированный DAG, при этом concurrency и deprovisioning вне модели. Lifecycle robustness хороша для happy path и single-operator использования; хрупка под partial failure, retry и concurrent access.

---

### ARCH-071: ensure_context_repo — без locking, единый глобальный pull-timestamp, clone failure молча деградирует overlay resolution
- Severity: HIGH
- Confidence: HIGH
- Files: core/internal/bootstrap/deploy/context_overlay.py:91-127, :64-68, :170-227; core/internal/bootstrap/deploy/deploy_orchestrator.py:266-271, :360-379
- Symbols: `ensure_context_repo()`, `_pull_with_cache()`, `_clone_context_repo()`, `CONTEXT_PULL_TS_PATH`, `DeployOrchestrator._preflight()`, `_resolve_overlay_dirs()`
- Evidence: ensure_context_repo() берётся без lock: `os.path.isdir(context_path)` → `git pull --ff-only`, иначе `git clone`; S9 cooldown — один глобальный timestamp-файл (`CONTEXT_PULL_TS_PATH`, context_overlay.py:67), общий для ВСЕХ конкурентных вызовов независимо от context/repo. Единственный caller в bootstrap-цепочке трактует результат как best-effort: `_preflight()` логирует rc и глотает исключения (deploy_orchestrator.py:266-271 — «non-fatal»). Ниже по потоку `_resolve_overlay_dirs()` (deploy_orchestrator.py:360-379) выставляет overlay ТОЛЬКО если `/opt/<ctx>/platform/modules/<name>` существует на диске — failed clone даёт пустые overlays без распространения ошибки.
- Failure/maintenance scenario: fresh-node φ8 → deploy-modules preflight clone fails (timeout/creds/race с конкурентным CI node-update, тянувшим тот же repo → git index.lock contention или double-clone «destination exists») → модули деплоятся без context-overlay кастомизаций (nginx conf, module overrides), и φ8 всё равно помечается done. Конкурентные make deploy-context + node-update делят один ts-файл — успешный pull одного процесса подавляет pull другого в течение 300s.
- Impact: silent config drift на свежезагруженных нодах; overlay content применяется только на позднем φ11 overlay pass; никакой сигнал не связывает degraded deploy с failed clone.
- Minimal fix: flock вокруг ensure_context_repo (паттерн есть: shared/file_lock.FileLock), per-repo timestamp key, эскалация rc=1 из context step в tracked warning (или громкий fail `_parse_modules` overlay resolution при failed clone).
- Code churn: S
- Phase: Pre-launch

### ARCH-072: `.hc_done_in_deploy` маркер связывает подавление healthcheck φ11 с безусловным side effect параллельного деплоя φ8
- Severity: HIGH
- Confidence: HIGH
- Files: core/internal/bootstrap/deploy/deploy_orchestrator.py:553-554 (:920-943 `_set_hc_marker`); core/internal/bootstrap/lifecycle/phases/docker.py:582-610
- Symbols: `_deploy_parallel()`, `_set_hc_marker()`, `hc_marker_path()`, `_registry_step_healthcheck()`
- Evidence: В режиме DEPLOY_PARALLEL=true `_deploy_parallel()` пишет маркер БЕЗУСЛОВНО на шаге 6 (deploy_orchestrator.py:554) — даже когда `failed` непуст или целый deploy_docker_group упал и был проглочен на :541. Consumer-side φ11 `_registry_step_healthcheck` (docker.py:599-609) проверяет только существование файла: маркер присутствует → standalone healthcheck skipped И маркер unlinked. Это межфазная коммуникация чисто через filesystem side effect (per-context suffix T9.19), не через state.json или данные, передаваемые вперёд.
- Failure/maintenance scenario: bootstrap с parallel deploy частично фейлится (group exception пойман, модули unhealthy) → φ8 помечен done_with_warnings/done → спустя дни make node-update запускает φ11, видит stale маркер времён bootstrap → глубокий standalone healthcheck skipped однократно → unhealthy модули всплывают только через converge R9 restarts, никогда не верифицированные healthy.
- Impact: последний health safety net lifecycle для update mode подавлен stale success-сигналом другого invocation; видимость отказов снижена ровно в partial-failure случае, который маркер должен исключать.
- Minimal fix: писать маркер только при `failed == []` и отсутствии group exception; либо записывать provenance маркера (timestamp/run-id) в state.json и позволить φ11 решать по same-run evidence вместо голого file existence.
- Code churn: S
- Phase: Post-launch

### ARCH-073: Нет run-level mutual exclusion для lifecycle-запусков — flock охраняет только state.json writes, тогда как фазы конкурентно мутируют общее состояние ноды
- Severity: HIGH
- Confidence: MED
- Files: core/internal/bootstrap/node-lifecycle.sh:57-76 (delegation, no lock); core/internal/bootstrap/lifecycle/cli.py:403-478 (main, no lock acquisition); core/internal/bootstrap/lifecycle/state_store.py:315-341 (flock scope)
- Symbols: `main()`, `_delegate()`, `save_state()`, `FileLock`
- Evidence: единственная сериализация pipeline — flock в save_state на state.json.lock (state_store.py:318-320), чей TRAP-комментарий называет «bootstrap + node-update параллельно» реальным сценарием, который пришлось митигировать от tmp-file corruption — т.е. конкурентные запуски anticipated, но сериализованы только writes. node-lifecycle.sh делегирует прямо в cli.py без lockfile; ничто не мешает init и update (или двум updates) исполняться одновременно на одной ноде.
- Failure/maintenance scenario: CI node-update перекрывается с operator bootstrap-node: оба исполняют deploy/converge фазы конкурентно (гонки docker compose up, гонки git pull — усугубляет ARCH-071), state.json становится last-writer-wins: run A помечает deploy_services done, пока run B исполняет ту же фазу, разрушая checkpoint семантику.
- Impact: checkpoint state может разойтись с фактическим состоянием ноды; дублированные/конфликтующие side effects при overlapping runs; recovery требует --force.
- Minimal fix: эксклюзивный FileLock на `/var/lib/platform/.bootstrap/run.lock` на весь run_init_mode/run_update_mode (переиспользовать существующий примитив); второй runner выходит с читаемой ошибкой.
- Code churn: S
- Phase: Post-launch

### ARCH-074: Пробелы dependency-графа — φ7 certificates не требует φ4 secrets, а --run-phase обходит позиционный порядок, маскирующий пробелы
- Severity: MEDIUM
- Confidence: HIGH
- Files: core/internal/bootstrap/lifecycle/state_machine.py:226-246 (graph), :191-209 (positional order); core/internal/bootstrap/lifecycle/cli.py:366-383 (`_run_single_phase`); core/internal/bootstrap/lifecycle/helpers/domains.py:164-177
- Symbols: `_phase_dependency_graph`, `BootstrapPhase.CERTIFICATES`, `_run_single_phase`, `ssl_provision_via_orchestrator`, `SECRETS_ENV_FILE`
- Evidence: граф объявляет CERTIFICATES: {NODE_CONFIGURATION} (state_machine.py:233) — без ребра к SECRETS_PROVISION, хотя cert issuance потребляет provider credentials из secrets.env, произведённого φ4 (domains.py:164: secrets_env передаётся в orchestrate_certs). Корректность полных запусков держится на позиции INIT_PHASE_ORDER (φ4 до φ7), т.е. на порядке исполнения, а не на declared dependencies. Аналогично φ12 требует {φ9, φ11}, но не φ10. `--run-phase certificates` валидирует только граф (cli.py:377) — может исполниться до любого secrets provisioning.
- Failure/maintenance scenario: repair flow на ноде с потерянным/reset состоянием (или targeted --run-phase certificates) → DNS-01 creds отсутствуют → каждый домен фейлится non-fatally → φ7 lands done_with_warnings → downstream φ8 заблокирован запутанной dependency ошибкой вдали от root cause (missing φ4 output).
- Impact: вводящие в заблуждение failure modes ровно в manual-repair сценариях, ради которых существует state machine; ordering контракт имплицитен в порядке списка, невидим валидатору графа/тестам.
- Minimal fix: добавить CERTIFICATES: {SECRETS_PROVISION, NODE_CONFIGURATION} (и DEPLOY_UPDATE += NODE_CONFIG_UPDATE) в _phase_dependency_graph.
- Code churn: S
- Phase: Post-launch

### ARCH-075: Hash invalidation покрывает только 5 deploy/converge фаз — изменения node.yaml для users/keys/certs/cron не ре-применяются на re-bootstrap без --force
- Severity: MEDIUM
- Confidence: HIGH
- Files: core/internal/bootstrap/lifecycle/state_machine.py:262-271 (`_HASH_INVALIDATED_PHASES`), :567-592 (`phase_needs_rerun` возвращает False для остальных), :519-521 (deliberate-skip TRAP); core/internal/bootstrap/lifecycle/cli.py:782-807 (skip logic)
- Symbols: `_HASH_INVALIDATED_PHASES`, `phase_needs_rerun`, `_phase_input_hash`, `_mark_phase_success`
- Evidence: Idempotency = status + input-hash, но hash set = {deploy_services, converge_services, registry_update, deploy_update, converge_update} (state_machine.py:265-271). Для φ1-φ7/φ9-φ10 статус done сохраняется безусловно независимо от изменения входов — комментарий кода фиксирует, что ssh_authorized_keys изменения намеренно не инвалидируют. Между тем эти фазы ПОТРЕБЛЯЮТ node.yaml входы (φ1 timezone через _node_timezone, φ2 keys через PLATFORM_*_KEY env из node.yaml flow, φ3 cron).
- Failure/maintenance scenario: ротация SSH key или timezone в node.yaml → make bootstrap-node → все 9 фаз «already done — skipping» → изменение молча не применено; документированный workaround — --force, очищающий ВСЕ checkpoint'и и перезапускающий disruptive фазы (apt, docker install) на живой ноде.
- Impact: устаревшее security-relevant состояние (authorized_keys) при зелёном bootstrap; операторы толкаются к full re-run как единственному пути обновления.
- Minimal fix: расширить relevance-fields `_phase_input_hash` per phase (hash node.timezone для φ1, keys block для φ2) и добавить эти фазы в _HASH_INVALIDATED_PHASES.
- Code churn: M
- Phase: Post-launch

### ARCH-076: Lifecycle create-only — нет reverse-order teardown, reconcile units (R1-R10) покрывают services, но не users, certs, cron, systemd, firewall, secrets
- Severity: MEDIUM
- Confidence: HIGH
- Files: core/internal/bootstrap/converge/reconciler.py:8-17 (R-unit scope); core/internal/bootstrap/lifecycle/state_machine.py:149-186 (phase enum: no cleanup phase); core/internal/bootstrap/lifecycle/cli.py:312-325 (--force = audit+state reset only)
- Symbols: `_unit_actions` (R1-R10), `BootstrapPhase`, `_reset_state`, `sm.reset()`
- Evidence: resume после mid-phase failure — whole-phase re-run (инвариант 13, state_machine.py:32-33; sub-step checkpoints отсутствуют); half-applied effects полагаются на operation-level idempotency плюс converge R-units. Эти R-units реконсилят perms, audit-log, project dirs, networks, hosts, vhosts, volumes (detect-only), sudoers, container runtime, TSDB — ничто не удаляет/реконсилят users/SSH keys (φ2), cron units /etc/cron.d/platform-* (φ3), acme certs удалённых доменов (φ7), reboot-policy systemd units (φ1/φ12), ufw rules (φ1). --force сбрасывает state.json, но никогда node resources; единственный deprovision verb платформы — project-scoped remove-project.
- Failure/maintenance scenario: модуль/домен удалён из node.yaml → контейнеры со временем реконсилятся, но его cron entries, sudoers fragment, cert renewal cron и ufw allowances персистят бесконечно; failed φ1 оставляет zram/journald/fstab half-configured без rollback — следующий run применяет forward only (fix-forward everywhere by construction).
- Impact: неограниченное накопление orphaned node-level ресурсов за жизнь ноды; DR/reprovisioning — единственная реальная очистка (согласуется с runbook инвариантом 9, но не задокументировано как lifecycle-ограничение).
- Minimal fix: зафиксировать асимметрию как канон (TRAP[DECISION]) и добавить detect-only reconcile units (R11 stale-cron, R12 orphan-certs), чтобы drift хотя бы всплывал в converge --report-only до мутаций.
- Code churn: M
- Phase: Post-launch
