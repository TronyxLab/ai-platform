<!-- GREP_SUMMARY: final-qa verdict NOT_READY conditions fix-forward coverage method staging drills -->
<!-- STRUCTURE: ▶ вердикт → ⊕ основания → ⚡ fix-forward чеклист → ∑ что верифицировано чисто → ⎋ условия READY -->

# FINAL VERDICT — независимый final QA meta-refactoring (волны 0–3)

Дата: 2026-08-25 · Диапазон: `38699a9..HEAD` (235 файлов, +18 648/−1 842) · План: 11-DevPlan,
леджер: 12-StatusReport.

# Вердикт

# **NOT_READY**

---

## Основания (сводка)

1. **Рабочее дерево не соответствует аудированному и прогейт-ованному состоянию** (C1):
   29 файлов незакоммиченного WIP (+882/−165) поверх волн; `check-manifests` на дереве RED;
   acceptance-критерий плана AC-7.2 сейчас невыполним. Леджер фиксирует чистоту коммитного дерева —
   текущее состояние никем не гейтилось.
2. **Четыре HIGH противоречат собственным инвариантам рефакторинга**:
   - REF-0012/0001: шаблонные проекты получают деплой-канал от stale-pin `4425ce0` (2026-08-18, до
     всех волн) — без permissions/concurrency/SHA-pins/gitleaks-checksum (C2); adopter-канал
     генерирует `@main`+tag-pins (R9).
   - REF-0006: L1-deny-set обходится через top-level volumes → docker.sock (C3).
   - REF-0104: provisioner абортит фазу на любом transient (except-кортеж не ловит
     LiteLLMTransportError), fetch-once не реализован, спящий генератор дублей ключей (C4).
   - REF-0007: AGE-мастер-ключ в локальном argv fallback-deliver (C5).
3. **Off-site DR неактивен по умолчанию** (C6): AGE_RECIPIENT вне матрицы секретов и без канала
   доставки → nightly upload SKIP всегда → заявленный RPO 24ч фиктивен до ручного шага оператора.
4. **Все staging-гейты и drills волны 4 не выполнены** (по признанию леджера): REF-0007 node-update,
   REF-0017 full-stack, REF-0009 полный цикл бэкапа, reboot/restore/age-key/load-smoke/e2e-scaffold/
   chaos T1–T12. Release-checklist шаги 1–2 открыты.

## Fix-forward чеклист до перевода в READY_WITH_WARNINGS

| # | Действие | Закрывает | Оценка |
|---|----------|-----------|--------|
| 1 | WIP: закоммитить отдельной волной с полным гейт-циклом или откатить; зелёный check-manifests | C1 | S |
| 2 | Перепин шаблонных workflows на HEAD + freshness-критерий в sha-pins гейт | C2, G6 | XS |
| 3 | verify_contracts: top-level volumes deny + ipc/security_opt/volumes_from/uts + R5-негативы | C3 | M |
| 4 | key_provisioner: TransportError в кортежи; re-lookup вместо fall-through-generate; fetch-once; тесты G2 | C4 | M |
| 5 | core-deliver.sh: убрать --age-secret-key из argv (читать в Python из env/file) | C5 | XS |
| 6 | AGE_RECIPIENT: secret-definitions + канал доставки + шаг release-checklist | C6 | S |
| 7 | GRANT `-d <db_name>` в postgres hook + ассерт цели БД (G4) | R15 | XS |
| 8 | Merge-guard: fatal на непарсабельные строки при parse>0 | R5 | S |
| 9 | Redact→truncate порядок; timeout-minutes на deploy-job; honesty *.yaml-щель | R6, R8, R13 | XS |

После п.1–9: **READY_WITH_WARNINGS** с обязательными условиями (staging node-update, full-stack,
backup-cycle, drills В4, e2e scaffold→push→deploy). После drills — READY по процедуре
release-checklist.

## Что верифицировано чисто (баланс)

- **Freeze P3**: соблюдён полностью — leaf-контракты аддитивны, AGE_SECRET_KEY/verbs/network-names/
  detector-names/suite-ID не тронуты, wire-DTO цел, __init__ ordering сохранён, docker_orchestrator.py
  пуст, lib/*.sh — тонкие правки. Ни одного rename контракта.
- **Backward compat**: публичные сигнатуры (FileLock, drain_all_count, wait_health, orchestrator_cli
  dispatch, latest_snapshot require_healthy, atomic_writer) совместимы; новых third-party зависимостей
  нет; pyproject/version не тронуты; digest-pin политика соблюдена (единственный bare-tag — локальный build).
- **Ядро аварийных путей работает**: flock-before-copy периметр receive; unhealthy→FAILED rc≠0 +
  critical-notify; payload restore ТОЛЬКО после успешного compose-rollback; monotonic deadline poller;
  previous_image якорь до compose-up; BUG-0100 rider; EOFError/orphan-sweep контракты forced-command.
- **Secrets fail-fast ядро**: empty-parse→fatal, sanitize sops stderr (тест с утечкой ключа И пути),
  tmpfs temp-key dd-wipe, signal-contract через реальный subprocess, NODE-dispatch негативы.
- **Backup truth**: sentinel строго после HEAD-confirm, fail-closed age-encrypt (plaintext никогда не
  уходит), gzip -t полный проход, restore ON_ERROR_STOP в обеих ветках + pre-dumpall abort.
- **Качество тестов структурно здоровое**: 0 skip / 0 broad-except / высокая assert-плотность по всем
  18 крупнейшим файлам; реальные red→green негативы на drain/waitpid; выборочные прогоны зелёные.
- **Monitoring**: noeviction+evicted_keys alert+memory>90%, noDataState=Alerting на DiskSpace/
  HighMemory, nginx slowloris-гвардия с документированным TRAP, job_name parity file_sd.

## Coverage отчёт (честность метода)

| Срез | Метод | Глубина |
|------|-------|---------|
| Deploy core (orchestrator/receive/engine/poller/rollback/channels) | лид вручную | полный (якоря+ключевые тела) |
| Bootstrap/self-heal (drain/marker/R9/watchdog/kahn/timeouts) | субагент | глубокий |
| Secrets/access (decrypt/deliver/sshd/sudoers) | субагент | глубокий |
| Locks/concurrency/LLM store/history | субагент | глубокий |
| TLS/backup/postgres hooks | субагент | глубокий |
| CI/workflows/supply-chain | субагент | глубокий |
| Gates-honesty/fingerprint/oracle/L1 | субагент | глубокий |
| Freeze/out-of-scope/API-surface | субагент | полный свип |
| Monitoring/config/env | лид spot-scan (субагент упал 2×infra) | частичный — см. ниже |
| Test-quality R1-R5 | лид структурный скан + кросс-верификация выводов 7 агентов | средний |

⚠️ Оговорка: monitoring/env срез покрыт точечным сканом лидом (nginx guards, alert-semantics,
noeviction, jobs, digest-pins, :3000 consumers) без построчного аудита 424-строчного honesty-теста и
render-dir path-parity. Канал субагентов упал 3× по infra (balance/certificate) — по протоколу BLOCKED
зафиксирован, срез закрыт вручную в доступной глубине.

## Известные незакрытые позиции (вне окна, задокументированы леджером)

constants.py вынос (REF-0107 хвост), REF-0007/0017/0009-staging, все drills В4, резерв
REF-0102/0109/0113/0114, zai-провайдер и macos DATABASE_URL="" (внесплановые — требуют явного
решения владельца), DR-offnode-backup debt (Rev 2026-08-31).
