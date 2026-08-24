# Refactoring Map — 10-Synthesis (мета-анализ 9 аудитов)

## 1. Входы и метод

| Домен | Папка | ID-пространство | Объём |
|-------|-------|------------------|-------|
| 01 architecture | 3 runs (run-a/run-b/run-c), attic исключён | ARCH-xxxx + синтез-лейблы A-01..A-44 | ~103+48 findings |
| 02 production bugs | forensic, commit 4425ce0 | BUG-0101..1004 (~53 numbered) | 45 distinct после merge |
| 03 security | triaged | SEC-0001..0049 → 18 blockers | 49 |
| 04 data consistency | static | DATA-101..1006 (59) | ~45 distinct |
| 05 tests | adversarial, 522 test files | TEST-001..095 | ~49 distinct |
| 06 dependencies | coupling/cascade (НЕ версии пакетов: CVE/pins = ноль находок) | DEP-0001..0059 | 60 (incl. positive controls) |
| 07 performance | measured+static | PERF-001..095 | 53 cards |
| 08 ai-code | 2-wave verified | AI-0001..0077 | 77 |
| 09 failures | 17 failure classes («-002» файлы = continuation, НЕ вторая генерация) | FAIL-0101..1012 | 106 IDs |

Метод синтеза: параллельная экстракция 9 доменов субагентами в строгом формате → кросс-доменная дедупликация по (файл, механика), а не по ID (ID-namespace'ы пересекаются между доменами) → калибровка severity → dependency graph → волны.

## 2. Дедупликация — главные междоменные кластеры

| Кластер (REF) | Источники (одинаковая механика) |
|---------------|--------------------------------|
| PARTIAL-as-success / no rollback on unhealthy (REF-0003) | BUG-0602 ≡ DATA-602 ≡ FAIL-0102 ≡ FAIL-0708; TEST-01/04 как тестовый слепой слой |
| Rollback contour broken (REF-0004) | BUG-0101 ≡ BUG-0502 ≡ BUG-0601 (3 направления одного аудита) ≡ DATA-604 ≡ FAIL-0707; BUG-0503, FAIL-0804, TEST-03 |
| drain_all_count ignores exit status (REF-0005) | BUG-0301 ≡ BUG-0801 ≡ PERF-002; TEST-02 (fake-drain маскировка); hc_done: BUG-0501 ≡ BUG-0703 ≡ A-10(d)/ARCH-072 |
| Postgres hook chain (REF-0002) | BUG-0604 + BUG-0605 ≡ DATA-201 ≡ DATA-501 + DATA-205 + BUG-0206 ≡ FAIL-0101 + FAIL-0605 |
| FileLock degrade-to-no-lock (REF-0011) | BUG-0104 ≡ BUG-0303 ≡ DATA-302 ≡ DATA-806 ≡ FAIL-0702 + A-19 (_REENTRANT); lock-perimeter: BUG-0302, FAIL-0701 ≡ AI-0006, FAIL-0700 |
| Secrets exposure transport (REF-0007) | SEC-0015 ≡ DATA-1001 (+FAIL-0601 notice); atomic_writer sweep: SEC-0016, SEC-0017, SEC-0029, SEC-0003, DATA-105 sites; argv-family: SEC-0019 ≡ DATA-1004 ≡ AI-0007 |
| TLS cert pipeline (REF-0008) | FAIL-0300 (scan blind) + BUG-0700 ≡ BUG-0901 + DATA-701 (pair validity); FAIL-0301/0302 + BUG-0207; SEC-0026 (FQDN entry) + BUG-1001 (validator mismatch) |
| Backup DR truth (REF-0009) | BUG-0802 ≡ DATA-502 ≡ SEC-0049(flock part) + FAIL-0905; BUG-0803 ≡ FAIL-0903 ≡ FAIL-0405; SEC-0018 ≡ DATA-503; DATA-504 ≡ FAIL-0803; FAIL-0904/0901/0902 — один subsystem пятью линзами |
| Monitoring blindness (REF-0010) | FAIL-0200 ⊃ FAIL-0104 ⊃ FAIL-0513 (3-way eviction); FAIL-0100+FAIL-0108; FAIL-0201 ≈ FAIL-1001 (exporter-alive masking class); FAIL-0402+FAIL-1002+FAIL-0504 (who-monitors-monitor триада); AI-0004 (#1 render-dir) |
| CI supply chain (REF-0012) | SEC-0038 + SEC-0039 + SEC-0040 (amplifier of SEC-0009) + SEC-0010 + A-13/AI-0022 |
| Secrets-chain fail-fast (REF-0013) | BUG-0102 ≡ BUG-0905 ≡ DATA-1002; BUG-0103; DATA-1005/1006; DEP-0040; AI-0023; DEP-0025 ≡ A-20; A-09 ≡ DEP-0026 ≡ HYP-03 |
| Self-heal (REF-0014) | BUG-0701 + BUG-0702 (R9, live-reproduced) + BUG-0804 (watchdog) + FAIL-0403/0900 |
| State-machine honesty (REF-0106) | DATA-705, DATA-802, DATA-203, DATA-803, A-01 ≡ AI-0038, DEP-0039 |
| False-green gates (REF-0107) | DEP-0016 ⊂ DEP-0019 ≡ PERF-071 (live-reproduced); PERF-074; TEST-14; TEST-15; TEST-16 + BUG-0305; TEST-13; DEP-0010/0011/0003/0018/0037 |
| LLM keys (REF-0104) | DATA-902 ≡ DATA-305; DATA-202; PERF-081/082(HYP)/083; FAIL-0305 ≡ AI-0010 |
| Payload transactionality (REF-0105) | DATA-101 ≡ DATA-704; FAIL-0711; FAIL-0704; DATA-703; A-25 |
| Timeouts/hangs (REF-0103) | BUG-0201 ≡ PERF-001+PERF-003; PERF-050 ≡ AI-0012/AI-0014; PERF-010; BUG-0204 ≡ AI-0013; AI-0020 triangle (+AI-0002/0039); BUG-0203; BUG-0603; DATA-606 |
| Status-page/metrics (REF-0108) | PERF-041 (TOP#1) + PERF-040/042; PERF-030; PERF-052/053; A-08; SEC-0044 rider |

## 3. Противоречия и их разрешение

| # | Противоречие | Разрешение |
|---|--------------|------------|
| 1 | Severity-разбросы на дубликатах: drain HIGH vs CRITICAL; FileLock MED-HYP/HIGH/HIGH; org-secrets LOW/MED-HIGH; DATA-104 MED vs DATA-301 HIGH; DATA-305 MED vs DATA-902 CRITICAL | При идентичной механике берём высшую оценку для launch-risk framing (отражено в REF) |
| 2 | Документированный канон ↔ код: «роль/БД/GRANT создаются хуком» (gate закрепляет отсутствие!); «hermes-agent-net isolation»; «пароль только в .platform-db.env»; «CONNECT на свою БД и не больше» (PUBLIC CONNECT жив); redis «кэш/очереди» (queue=noeviction канон нарушен самим платформенным langfuse-redis); «healthcheck rollback» (не реализован); «60s poll window» (~21 мин) | Документация — фиксируется в соответствующих REF; doc-only правки сгруппированы в P2.6 |
| 3 | Фальшивые инварианты/TRAP-тексты: ssh_cmd_builder «ps-hardening», log-collector «:ro достаточно», verify_contracts «C1 закрыта», deploy_paths «рассинхрон закрыт», TRAP PERF-080 «ветка восстановлена» (call продублирован), DevPlan 176 A.2 (гейт reopened фактом SEC-0030) | Трактуются как evidence дефекта, не как опровержение аудита |
| 4 | Healthcheck-criterion: канон «running AND (healthy|""|none)» vs docker_collector/status-page (no-healthcheck=unhealthy) | Прямая нормативная коллизия — canon побеждает (TRAP[DECISION] root AGENTS.md); collector-fix в REF-0010 rider |
| 5 | Таймауты 600 vs 900 (lib/ssh.sh vs timeouts.py + ложный parity-комментарий) | SoT 900; shell default выравнивается (REF-0103) |
| 6 | Учётные расхождения: PERF 45 vs 48 vs 53 карточки; FAIL 96 vs 106 IDs; TEST README ссылается на отсутствующий findings-failure-paths.md; 01-architecture ID-namespace collision (ARCH-0014×2, 0029×2, …); «run2» в 09-failures — миф (continuation-файлы) | Bookkeeping отмечен, на выводы не влияет; дедуп по (файл, механика) |
| 7 | AI-0010 содержит REFUTED механизм (exit-0 swallow) и CONFIRMED исправленный (phase non_fatal silence) под одним ID | Взят только corrected механизм |
| 8 | TEST-48 (committed report-*.xml) REFUTED аудитором | Исключено из плана |
| 9 | Phase-споры: A-01 pre vs post launch; PERF-004 card Pre-launch vs TOP-10 defer | Расщепление: notice/guard сейчас, структурная часть после запуска |
| 10 | Cascade-answer «нет bidirectional пар» vs найденные module-level циклы (check_suite, engine↔lifecycle, shared↔test_runner) | Уровни абстракции: подсистемный claim верен, модульные циклы существуют — оба факта учтены |

## 4. Confirmed vs Hypothesis (сводка)

- **CONFIRMED** (кодовые трассировки, часть live-reproduced): весь состав P0; большинство P1. Live-reproduced: R9 три режима отказа, `--only` skip×14, docker name-filter семантика.
- **HYPOTHESIS** (в план не входят как самостоятельные фиксы): SEC-0040 exploit-path (механизм подтверждён), SEC-0027 latent, DATA-103 (dir-fsync durability gap кодом подтверждён, триггер power-loss), DATA-506 litellm rollback skew, PERF-037/054/073/082 (нужны измерения; 082 верифицируется за 5 минут перед REF-0104), FAIL-0407 (tor Restart — 1 команда verify), FAIL-1008, AI-0070 runtime mail behavior, DEP-0013 lazy-import сайты, BUG-0104 ENOSPC-вариант, DATA-404 impact, TEST-36 frequency, A-23/A-36 (attic-level leads).
- **Positive controls** (не трогать, зафиксировано чтобы не ре-флагнули): log rotation 13/13, zram+PSI alert, monkey-patching отсутствует, tar filter="data", forced-command exact-match, no FLUSHALL в коде, state_store flock+atomic после P1-фикса, idempotent one-shot контейнеры (FAIL-0712/0906/0907).

## 5. Dependency graph между REF (упрощённо)

```
Волна 0 (день 1, независимые):
  REF-0012 pins ─┐
  REF-0010 config(YAML) ─┤
  REF-0005 drain/marker ─┼─→ честные сигналы → всё остальное
  REF-0003 PARTIAL→FAILED ─┘
  REF-0013 secrets fail-fast   REF-0016 XS access   REF-0002 hook register

Волна 1 (день 1-2):
  REF-0004 rollback contour ←─ REF-0003 (ветка вызова)
  REF-0011 locks ←→ REF-0105 payload tx (flock-before-copy)
  REF-0007 exposure sweep   REF-0014 R9/watchdog ←─ REF-0010 (notify)

Волна 2 (день 2-3):
  REF-0006 L1 gate ─→ включает negative-тесты TEST-05
  REF-0001 build channel ─→ e2e drill ─→ требует REF-0002 (hook) рабочим
  REF-0008 cert bundle (6 подпунктов независимы)
  REF-0009 backup truth + drills prep

Волна 3 (день 3-4):
  REF-0103 таймауты (после REF-0003 start_period решения)
  REF-0104 LLM store   REF-0107 false-green gates (можно раньше при ёмкости)
  REF-0110..0114 пакет S-фиксов

Волна 4 (день 5): make check до чистоты + drills:
  reboot test-VPS (FAIL-0400) · restore drill (REF-0009) · age-key-backup (B5)
  load-test smoke (PERF-080 fix обязателен до) · e2e scaffold→push→deploy · chaos T1-T12
```

Критический путь: REF-0005 → REF-0003 → REF-0004 → drills. Блокер drills: REF-0009 restore hardening ДО restore-drill (иначе drill опасен — FAIL-0803).

## 6. Multi-fix карта (одно изменение закрывает несколько находок)

| Изменение | Закрывает |
|-----------|-----------|
| atomic_writer sweep (mode=0600/0640) | SEC-0016, SEC-0017, SEC-0029, SEC-0003, BUG-0305, DATA-105 (6 сайтов), частично DATA-906 |
| drain_all_count status-check (~5 строк) | BUG-0301=BUG-0801=PERF-002 + W5-E1 контракт + делает TEST-02 честным |
| PARTIAL→FAILED mapping | BUG-0602 + DATA-602 + FAIL-0102/0708 + даёт assert-цель TEST-01/04 |
| compose_state в снапшот + skip-second-rollback + require_healthy | BUG-0101/0502/0601 + DATA-604 + FAIL-0707 + BUG-0503 |
| Регистрация postgres hook + ensure-convergence | BUG-0604 + BUG-0605/DATA-201/501 + BUG-0206 + FAIL-0101/0605 + gate-fix + REVOKE CONNECT (SEC-0008) rider |
| Expiry scan args + pair-match | FAIL-0300 + DATA-701 + leg of BUG-0700/0901 |
| concurrency group + flock-before-copy + chown locks + retryable≠124 | FAIL-0700/0701/0702/0703 + BUG-0104/0302/0303 + DATA-302/806 + AI-0006 |
| SHA-pin actions + gitleaks checksum | SEC-0038 + SEC-0039 (+основа против SEC-0040) |
| Langfuse bundle (port 3000 + network attach + alias) | AI-0001 + AI-0077 + AI-0003 + leg SEC-0034 |
| --only validation exit 2 | DEP-0016 + PERF-071 (live-reproduced false green) |
| Alert-rules render-dir canonicalization | AI-0004 (#1) + leg FAIL-1002 |
| noeviction langfuse-redis (1 строка) | FAIL-0200 (BLOCKER B3) полностью |
| Удаление дубля locust-call (1 строка) | PERF-080 → capacity verdicts становятся пригодными (release-checklist требование) |
| validate.sh repoint (<10 LOC) | A-37 + python→shell инверсия |
| freshness stamp + uploaded sentinel | BUG-0803=FAIL-0903=FAIL-0405 + BUG-0802=DATA-502 |

## 7. Отброшено (low-value / cosmetic — сводный регистр)

- **02 bugs**: BUG-0105, 0205–0207 (rider'ы где дёшево), 0305, 0402/0404, 0704, 0806, 0904, 1003, 1004, HYP-03, backup-cron tmp-name.
- **04 data**: DATA-106, 204, 404, 406, 805, 905.
- **05 tests**: TEST-48 (refuted), 49 (positive), 75, 76, wholesale-LDD band (TEST-41 отложен), census-перекрытия.
- **06 deps**: положительные контролы (DEP-0024/0028/0035), 0023, 0029, 0031/0032/0036/0042/0047 — объединены в пост-launch constant-hygiene группу.
- **07 perf**: полный low-value band (см. P3 §14).
- **08 ai-code**: transient-races/log polish band (AI-0008..0019 subset), signature hygiene (AI-0031..0035), stale STRUCTURE cluster, test-style debt (AI-0046..0050).
- **01 arch**: run-a 0102..0111, 0203..0207, 0034..0036, 0043..0050 minor band; run-c minors (ARCH-0004/0005/0008/0031/0037…).
- **09 failures**: positive controls (0500/0510/0512/0607/0712/0906/0907), LOW/backlog без pre-launch действия (0207/0208/0307/0310/0311/0312/0408/0409/0410/0506–0509/0609/0611/0704/0705/0710/0807/1005/1007/1008).
- **03 security**: defer-with-condition список (SEC-0001 до ротации, 0007/0008 внешние tenant'ы, 0009/0040 public visibility, 0013 roadmap, 0035, 0048 первый месяц, 0049) + latent-only band.

## 8. Оценка churn

| Волна | Основные файлы | ~LOC изменений | Характер |
|-------|----------------|----------------|----------|
| 0 | workflows, monitoring YAML, parallel_runner, orchestrator (predикат), secrets guard'ы, module.yaml | ~400–600 | config + точечные диффы |
| 1 | orchestrator/history/engine/lifecycle, file_lock/receive_flow, secrets writers, converge/runtime, watchdog | ~600–900 | аварийные пути, покрытые characterization-тестами |
| 2 | verify_contracts, templates/adopter, cert pipeline (6 мелких), backup scripts, Makefile restore | ~500–800 | половина — новые job'ы/скрипты |
| 3 | poller/timeouts, llm store, gates, S-фиксы | ~500–700 | декомпозировано на независимые единицы |
| 4 | тесты + drills | ~300–500 test LOC | только целевые deploy-safety тесты |
| Итого | | **~2300–3500 LOC**, ни одного структурного сплита | |

Запрещено в окне (см. P3): сплиты god-файлов, rename контрактов, миграции facades, wholesale test expansion.
