# GREP_SUMMARY: ai-instructions.mk, ai-instructions-sync, convention-compiler, .kilo, canon-pin, project-mode, ai-sync
# STRUCTURE: ┌ai-instructions-sync target┐ → ○ $(PYTHON) -m ai_instructions sync --config pins.yaml → ○ PROJECT? --project-dir · TEMPLATE? --template · CANON_PATH? --canon-path → ⎋ exit code
# region MODULE_CONTRACT
## @purpose  Make-фасад конвенционного компилятора инструкций (DevPlan 001 R16/T4.2):
##           ai-instructions-sync — пересборка .kilo/ (правила/агенты/скиллы) из канона
##           + проектных дополнений .ai/ + hermes-профиля platform
## @scope    Local dev + проекты (PROJECT=<dir> — проектный режим с фильтром TEMPLATE);
##           тонкий фасад — вся логика в ai_instructions.runtime.cli (установлен в venv платформы)
## @invariants
##   - Конфиг — единый SoT: core/internal/ai-instructions/ai-instructions-pins.yaml
##   - Повторный прогон на неизменённом дереве — no-op (детерминизм, hash-сверка lock)
##   - CANON_PATH=<dir> — dev-оверрайд резолва канона (локальное дерево вместо pin-cache/clone)
##   - PROJECT=<dir> — проектный режим: эмиссия в <dir>/.kilo, hermes выключен,
##     TEMPLATE=all|backend|frontend|ai-project — фильтр наследования по @language/@stack
## @rationale Регистрация глагола в entrypoint-manifest (namelint/allowed_verbs) —
##   единый фасад make (инвариант 1); тонкий фасад над Python CLI (языковая политика)
# endregion MODULE_CONTRACT

# ─── ai-instructions-sync ────────────────────────────────────────────────────
# Пересборка инструкций: канон + .ai/ потребителя → .kilo/ + hermes platform-профиль
# + ai-instructions.lock. Параметры:
#   PROJECT=<dir>   проектный режим (эмиссия в <dir>/.kilo/, hermes off)
#   TEMPLATE=...    all|backend|frontend|ai-project — фильтр наследования (с PROJECT)
#   CANON_PATH=<dir> dev-оверрайд локального дерева канона
.PHONY: ai-instructions-sync
ai-instructions-sync:
	@echo "[IMP:7][make][ai-instructions-sync] compiling instructions (canon + .ai -> .kilo)..." 1>&2
	@PYTHONPATH="$(_platform_root)/vendor:$${PYTHONPATH:-}" $(PYTHON) -m ai_instructions sync --config core/internal/ai-instructions/ai-instructions-pins.yaml \
		$(if $(PROJECT),--project-dir '$(PROJECT)',) \
		$(if $(TEMPLATE),--template '$(TEMPLATE)',) \
		$(if $(CANON_PATH),--canon-path '$(CANON_PATH)',); _rc=$$?; \
	$(PYTHON) -m core.internal.shared.test_journal record --goal ai-instructions-sync --exit-code $$_rc || true; \
	exit $$_rc
