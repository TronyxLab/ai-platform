# GREP_SUMMARY: dev.mk, dev-hosts, etc-hosts, hosts-manager, dry-run, apply, macos, dev-infra, age-key-backup, DR, fix-pyright, agent-check, basedpyright, static-analysis, L1-signal, DevPlan-163
# STRUCTURE: ┌dev-hosts target┐ → ◇ APPLY=1? → ⊕ --apply (sudo, атомарно) · → ⊕ --dry-run (default, exit 1 на diff) → ⎋ python3 core/internal/dev_hosts.py ── ┌age-key-backup target┐ → ◇ AGE_RECIPIENT → ⎋ python3 -m core.internal.deploy.age_key_backup ── ┌fix-pyright target┐ → ⎋ .venv/bin/basedpyright --level error (информативный прогон) ── ┌agent-check target┐ → ⎋ $(PYTHON) -m core.internal.agent_check (L1-сигнал <5 s)
# region MODULE_CONTRACT
## @purpose  Dev-infrastructure targets for the local machine (macOS) — dev-hosts:
##           idempotent /etc/hosts management for the dev FQDN scheme (DevPlan 136 W4, T4.2);
##           age-key-backup: off-node encrypted backup of the AGE master key (DevPlan 147 W2,
##           секция «DR мастер-ключа AGE» core/AGENTS.md — DR-drill W3.1); fix-pyright: информативный локальный
##           прогон basedpyright (DevPlan 163 W-A A5, recommended-режим --level error);
##           agent-check: L1-статический сигнал агента на изменённых файлах <5 s (DevPlan 163
##           W-E E2, T1.3) — ruff + basedpyright + static + bespoke на diff (JSON-режим: JSON=1).
## @scope    Included from root Makefile; local-dev targets only (no deployment/CI logic).
##           The business logic lives in core/internal/dev_hosts.py,
##           core/internal/deploy/age_key_backup.py and core/internal/agent_check/ (package,
##           170 W10-C: agent_check.py → agent_check/__init__.py) — this file is a thin facade.
## @invariants
##   - Default mode is --dry-run: exit 1 when the managed block differs from /etc/hosts
##     (blocking signal), exit 0 when in sync
##   - APPLY=1 switches to --apply: writes the managed block (sudo for /etc/hosts), idempotent
##   - Env chain mirrors the dev-certs canon (TRAP[BUG] 2026-07-16): env > .env > Python default;
##     `make` does not read .env — recipe-level extraction via core.internal.shared.env_reader
##     (DevPlan 172 W2.3, 0 inline grep/cut)
##   - Never runs with APPLY=1 implicitly — operator must opt in explicitly
##   - age-key-backup: реципиент — AGE_RECIPIENT env или --recipient флаг; ключ читается
##     локально по env-цепочке node_detect (AGE_SECRET_KEY → … → /etc/age/key.txt);
##     --dry-run по умолчанию БЕЗОПАСЕН (0 мутаций) — реальная выгрузка только без --dry-run
##   - fix-pyright: exit code basedpyright прокидывается как есть (0 чисто / 1+ ошибки);
##     НЕ auto-fix таргет (информативный — показывает, что фиксить); runs from .venv/bin
##   - agent-check: exit code модуля прокидывается (0 чисто / 1 blocking-нарушения);
##     JSON=1 → --json (машиночитаемый stdout); $(PYTHON) (.venv/bin/python) — тулы ruff/
##     basedpyright резолвятся рядом с sys.executable (ядро agent_check._venv_tool)
##   - fix-pyright и agent-check регистрируются в entrypoint-manifest.yaml allowed_verbs —
##     handoff W-G (handoff_wg_verb_fix_pyright.md / handoff_wg_verb_agent_check.md); до
##     регистрации name-linter/verb-register падает (координационная зависимость фаз 1-2,
##     НЕ обходится правкой манифеста)
## @rationale Extracted as makefiles/dev.mk per DevPlan 136 §8 file manifest (makefiles/dev.mk —
##            dev-hosts). Separate file keeps helpers.mk (shared dev-utils: venv/templates/
##            dev-certs/dev-metrics) free of the hosts-manager concern and matches the plan's
##            include-split convention (one thematic .mk per concern).
##            Alternative rejected: adding the target to helpers.mk (existing dev-* canon) —
##            helpers.mk would grow a second responsibility (hosts management vs generic
##            helpers) and the plan explicitly prescribes dev.mk. Both options feed
##            generate_entrypoint_manifest.py identically (it globs makefiles/*.mk).
##            age-key-backup добавлен в dev.mk (DevPlan 147 W2): операторская DR-операция
##            локальной машины, канон dev-* (как dev-hosts/dev-certs).
##            fix-pyright — в dev.mk (DevPlan 163 W-A A5): локальный dev-инструмент статики,
##            канон dev-* (аналогично fix-ruff в repair.mk — но fix-pyright информативный,
##            не repair/auto-fix; repair.mk = только REPAIR_TARGETS, контракт не расширяется).
##            agent-check — в dev.mk (DevPlan 163 W-E E2): L1-сигнал agent-loop — dev-цикл
##            агента, канон dev-* (L0 редактор → L1 agent-check → L2 pre-commit).
## @changes 2026-08-05 | DevPlan 136 W4 (T4.2) — Created
## @changes 2026-08-11 | DevPlan 147 W2 — +age-key-backup (DR off-node backup, секция «DR мастер-ключа AGE» core/AGENTS.md)
## @changes 2026-08-13 | DevPlan 163 W-A A5 — +fix-pyright (информативный basedpyright-прогон)
## @changes 2026-08-13 | DevPlan 163 W-E E2 — +agent-check (L1-статический сигнал агента)
## @changes 2026-08-15 | DevPlan 172 W2.3 — dev-hosts: grep/cut → env_reader (0 inline .env-логики)
# endregion MODULE_CONTRACT

.PHONY: dev-hosts age-key-backup agent-check

## dev-hosts: Управление /etc/hosts dev-блоком (default dry-run exit 1 на diff; APPLY=1 → apply)
##   Держит локальный /etc/hosts синхронным с dev-FQDN схемой (DevPlan 136 W4, T4.1-T4.2):
##   server_names vhost_renderer (dev-mode <project>.<suffix>) + base-домены из SAN dev-сертификатов.
##   Маркер-блок `# BEGIN ai-platform dev-hosts` / `# END ai-platform dev-hosts`; чужие строки
##   /etc/hosts НЕ трогаются (идемпотентность: повторный APPLY=1 = no-op).
##   Env-цепочка (canon dev-certs): env > .env > Python default (NODE_NAME/NODE_CONFIGS_DIR/
##   PLATFORM_DOMAIN/DEV_CERTS_DIR). Default --dry-run блокирует make при diff (exit 1).
dev-hosts:
	@echo "[IMP:7][make][dev-hosts] Checking /etc/hosts dev block (APPLY=$${APPLY:-0})..."
	@_env_file="$(_platform_root)/.env"; \
	export NODE_NAME="$${NODE_NAME:-$$($(PYTHON) -m core.internal.shared.env_reader get NODE_NAME --file "$$_env_file")}"; \
	export NODE_CONFIGS_DIR="$${NODE_CONFIGS_DIR:-$$($(PYTHON) -m core.internal.shared.env_reader get NODE_CONFIGS_DIR --file "$$_env_file")}"; \
	export PLATFORM_DOMAIN="$${PLATFORM_DOMAIN:-$$($(PYTHON) -m core.internal.shared.env_reader get PLATFORM_DOMAIN --file "$$_env_file")}"; \
	export DEV_DOMAIN_SUFFIX="$${DEV_DOMAIN_SUFFIX:-$$($(PYTHON) -m core.internal.shared.env_reader get DEV_DOMAIN_SUFFIX --file "$$_env_file")}"; \
	export DEV_CERTS_DIR="$${DEV_CERTS_DIR:-$(_platform_root)/core/modules/nginx/dev-certs}"; \
	ARGS="--dry-run"; \
	if [ "$${APPLY:-}" = "1" ]; then ARGS="--apply"; fi; \
	python3 $(_platform_root)/core/internal/dev_hosts.py $$ARGS
	@echo "[IMP:9][make][dev-hosts] Done (exit code propagated)"

## age-key-backup: Off-node encrypted backup of the AGE master key (секция «DR мастер-ключа AGE» core/AGENTS.md, DevPlan 147 W2)
##   Usage: make age-key-backup [AGE_RECIPIENT=<pubkey>] [ARGS="--dry-run"]
##   Реципиент: AGE_RECIPIENT env или --recipient флаг (python3 -m core.internal.deploy.age_key_backup).
##   Ключ читается ЛОКАЛЬНО по env-цепочке node_detect; backup — ТОЛЬКО зашифрованный (sops).
##   Доп. флаги пробрасываются через ARGS (--dry-run/--no-upload/--output-enc/--s3-key).
##   Флаги CLI переопределяют ARGS (приоритет: AGE_RECIPIENT/DRY_RUN/NO_UPLOAD/OUTPUT_ENC/S3_KEY > ARGS).
age-key-backup:
	@echo "[IMP:7][make][age-key-backup] Off-node AGE master key backup (секция «DR мастер-ключа AGE» core/AGENTS.md)..."
	@python3 -m core.internal.deploy.age_key_backup \
		$(if $(AGE_RECIPIENT),--recipient '$(AGE_RECIPIENT)',) \
		$(if $(filter 1,$(DRY_RUN)),--dry-run,) \
		$(if $(filter 1,$(NO_UPLOAD)),--no-upload,) \
		$(if $(OUTPUT_ENC),--output-enc '$(OUTPUT_ENC)',) \
		$(if $(S3_KEY),--s3-key '$(S3_KEY)',) \
		$(ARGS)
	@echo "[IMP:9][make][age-key-backup] Done (exit code propagated)"

## fix-pyright: УДАЛЁН (План 175 W2.4) — дубль суита 'pyright' check-suite.yaml
##   (core/entrypoints/pyright-hook.sh: basedpyright --level=error, grep ' - error: ').
##   Тот же инструмент/режим; суит покрывает (включая blocking-канал pre-commit).

## agent-check: L1-статический сигнал агента на изменённых файлах (<5 s, DevPlan 163 W-E)
##   Обязательный шаг агента перед объявлением готовности (W-G впишет в root AGENTS.md
##   §Языковая политика): прогон ruff (blocking select) + advisory (SLF/FBT/ARG/C90 по
##   fp_registry.yaml) + basedpyright (файловый режим) + static check --changed + bespoke
##   doc-headers по git diff HEAD + untracked. exit 0 = чисто; exit 1 = blocking-нарушения;
##   advisory-сигналы не блокируют. JSON=1 → --json. Использует $(PYTHON) (.venv/bin/python) —
##   ruff/basedpyright резолвятся рядом с sys.executable. ⚠️ Чистый машиночитаемый stdout
##   (JSON без примесей) гарантирован при ПРЯМОМ вызове модуля `$(PYTHON) -m
##   core.internal.agent_check --json` — через make stdout-поток объединяется со stderr
##   платформенным make-log-shell.sh (свойство ВСЕХ make-таргетов, см. Makefile §make log).
##   Глагол agent-check регистрируется в entrypoint-manifest.yaml — handoff W-G
##   (handoff_wg_verb_agent_check.md); до регистрации verb-register падает (координационная
##   зависимость фазы 2).
agent-check:
	@echo "[IMP:7][make][agent-check] L1 static signal on changed files..." 1>&2
	@$(PYTHON) -m core.internal.agent_check $(if $(filter 1,$(JSON)),--json,); _rc=$$?; \
	$(PYTHON) -m core.internal.shared.test_journal record --goal agent-check --exit-code $$_rc || true; \
	exit $$_rc
