# $ARTIFACT_CONTRACT
## @PURPOSE Создание debt registry — единого реестра архитектурного долга после завершения Strangler-Fig
## @DESCRIPTION
Директория `.ai/debt/` пуста. DevPlan 096 упоминает `096-Residual-Debt.md`
(16.5 KB, 5 разделов: OPEN, COSMETIC, SHELL-RESIDUAL, 085-RC3-BLOCKERS, LESSON_LEARNED),
но файл отсутствует на диске.

После завершения финальной волны Strangler-Fig (Briefs 099-110) необходимо:
1. Создать актуальный debt-реестр со всеми известными долгами
2. Задокументировать оставшиеся >200 LOC shell-скрипты и обоснование их исключения из миграции
3. Зафиксировать P2/P3 задачи на будущие волны
4. Добавить TRAP[DECISION] аннотации для ключевых архитектурных решений, принятых в ходе миграции

**Содержание реестра:**
- **SHELL-RESIDUAL:** скрипты >200 LOC, исключённые из миграции, с обоснованием:
  - `issue-cert.sh` (704) — acme.sh executor, TRAP 080
  - `healthcheck.sh` (388) — STABLE, исключён политикой
  - `module-interface.sh` (206) — STABLE, исключён политикой
  - `install-tor-proxy.sh` (422) — одноразовый bootstrap
- **P2-BACKLOG:** задачи на следующую волну:
  - `validate/validate.sh` (251 → Python) — Brief 107
  - `scp-deliver.sh` (251 → Python) — Brief 108
  - `check-dead-code.sh` (86 → Python) — Brief 109
  - `lint.sh` + `check-doc-headers.sh` консолидация — Brief 106
- **P3-BACKLOG:** долгосрочные кандидаты:
  - `install-docker.sh` (218) — bootstrap, кандидат при росте
  - `setup-node.sh` (215) — bootstrap, кандидат при росте
  - `platform-secrets/install.sh` (223) — bootstrap
- **TEST-DEBT:** зарегистрированные тестовые проблемы
- **ARCH-DECISIONS:** ключевые TRAP[DECISION] с датами пересмотра
## @RATIONALE
- Отсутствие debt-реестра — риск потери контекста между волнами
- После финальной волны Strangler-Fig нужен formal close-out документ
- AGENTS.md ссылается на `.ai/debt/` как на source of truth для долгов
## @ACCEPTANCE_CRITERIA
- AC1: `.ai/debt/001-Strangler-Fig-Closeout.md` создан и force-added в git
- AC2: Все секции заполнены (SHELL-RESIDUAL, P2-BACKLOG, P3-BACKLOG, TEST-DEBT, ARCH-DECISIONS)
- AC3: Каждая запись содержит: файл, LOC, обоснование исключения/отсрочки, rev-дату
- AC4: Все TRAP[DECISION] из AGENTS.md с будущими rev-датами продублированы в реестре
- AC5: `make check-file-lines` пропускает `.ai/debt/` (бинарный/игнорируемый)
- AC6: Файл закоммичен с `git add -f` (`.ai/debt/` в `.gitignore`)
## @IMPLEMENTS Brief 111
## @IMPACTS .ai/debt/001-Strangler-Fig-Closeout.md (NEW), .gitignore (проверить)
## @REQUIRES Результаты всех миграционных брифов (099-110) для актуальных данных
