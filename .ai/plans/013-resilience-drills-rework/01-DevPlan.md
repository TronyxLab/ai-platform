$START_DEVPLAN

# DevPlan 013 — Resilience Drills Rework (переработка chaos-тестов)

## $ARTIFACT_CONTRACT
```yaml
PURPOSE: Заменить 12 долгих (часы реального времени) chaos-тестов T1–T12 на два быстрых тира
         resilience-drill'ов, проверяющих РЕАЛЬНЫЕ отказы сервисов/модулей: корректную деградацию,
         самовосстановление (restart policy / watchdog / reboot) и целостность данных.
DESCRIPTION: Rewrite tests/e2e/test_chaos_resilience.py (12 сценариев, ~1770 LOC, часы рантайма)
         → 9 fast-сценариев (≤25 мин суммарно) + 3 night-сценария (отдельное окно); slim хелперов
         (chaos_audit.py: LogAuditManifest/Loki/alerts/export ceremony → ~150 LOC lean-хелперов);
         синхронизация документации (README e2e, ci.mk, AGENTS.md release checklist).
RATIONALE: Сессия 011-launch-validation (2026-08-26): chaos FULL не уложился в 30-минутное окно —
         T01 один занял >25 мин; владелец: «Не надо долгих хаос тестов… долгие поставь под удаление».
         Причина медленности — не инъекции (секунды), а аудит-церемониал: multi-source log-forensics,
         экспорт логов по SSH, Loki-дубликаты, cron-alignment sleep'ы (T5 +10 мин), watchdog через
         реальный cron (T12 ≥15 мин). Ускорение достигается удалением церемониала и прямым вызовом
         watchdog с env-оверрайдами порогов (уже поддержаны кодом).
ACCEPTANCE_CRITERIA:
  - AC1: fast-тир (`-m "chaos and not night"`) на забутстрапленной ноде — PASSED полностью,
         wall-clock ≤30 мин (типично ≤20), каждый сценарий ≤6 мин.
  - AC2: каждый drill доказывает 4 факта: (a) инъекция приземлилась (state-poll/probe до recovery),
         (b) деградация соответствует дизайну (сайты/зависимости ведут себя канонично),
         (c) самовосстановление в бюджет TTR, (d) нода чиста после теста.
  - AC3: экзотические сценарии удалены: DNS resolved stop, clock skew ±24h, cert/secrets corruption,
         кросс-бут аудит связок T1-T10, restore-drill (переносится в отдельный план — см. Debt Intake).
  - AC4: контракты синхронизированы: tests/e2e/README.md, makefiles/ci.mk (комментарий),
         AGENTS.md §Release checklist (строка «Chaos FULL T1–T12»); маркер `night` зарегистрирован.
  - AC5: `make check` локально зелёный (requires_node исключён фильтром — гейты не затронуты);
         ruff/agent-check чистые.
IMPLEMENTS: Запрос владельца (2026-08-26): существенно упростить chaos-тесты; покрывать реальные
         отказы сервисов/модулей/проектов; проверять отказоустойчивость и самовосстановление.
IMPACTS: tests/e2e/test_chaos_resilience.py (rewrite), tests/e2e/chaos_audit.py (rewrite),
         pyproject/pytest markers (+night), tests/e2e/README.md, makefiles/ci.mk, AGENTS.md.
REQUIRES: Забутстрапленная tronyx-vps (операторское окно для верификации); NodeSSHClient
         (tests/_conftest/node.py); env-оверрайды WATCHDOG_UNHEALTHY_MIN/WATCHDOG_COOLDOWN_MIN
         (core/internal/healthcheck/watchdog.py — VERIFY перед реализацией).
```

## 1. Requirements Analysis

**Проблема (evidence):**
- Прогон 011: chaos v2 шёл >100 мин, не завершён; fast-подмножество T01/T02/T05/T07 не уложилось
  в 30 мин (T01 >25 мин). Вердикт сессии: «сценарии сами длинные».
- Первопричины медленности: (1) `LogAuditManifest.check_all` = 15–20 последовательных SSH-команд
  на тест (docker logs/journald/Loki/alerts) + `export_logs` (journalctl tail 4000 + 3-4 контейнера
  × docker logs tail 3000 + Loki limit 500); (2) `_marker_http_sites` = 4 внешних HTTPS-curl на
  каждый manifest; (3) sleep-окна: T5 cron-alignment (до 5+5 мин), T12 watchdog через реальный cron
  (unhealthy ≥10 мин + cron */5 → worst-case 25 мин), T8 dd-заполнение диска мегабайтными чанками;
  (4) кросс-бут аудит T11 требует, чтобы все инциденты T1-T10 случились ДО reboot (жёсткая связка).
- Странные сценарии без прямой связи с «отказ сервиса → плавная деградация → self-heal»:
  stop systemd-resolved (T2), clock skew ±24h (T4, породил известный Loki-debt), byte-flip cert/secrets
  (T9, дублирует cache-drill из launch-validation C2).

**Ключевые критерии успеха:**
1. Полный fast-прогон ≤30 мин — иначе тесты снова не запускают (главный практический провал T1-T12).
2. Каждый drill — реальный отказ реального компонента платформы (не симуляция), с proof-of-injection
   (уроки TRAP[BUG] T6/T7/T9/T10 VR 142 §6: инъекция обязана быть доказана до recovery-ждания).
3. Проверяются ОБА свойства: graceful degradation (что живёт/падает во время отказа — по дизайну
   модуля) и self-healing (restart policy, watchdog, boot-оркестрация) с жёстким бюджетом TTR.
4. Нода остаётся чистой после каждого drill (никакых остаточных правил/файлов/сломанных healthcheck).

## 2. SUPERPOSITION (набор необходимых тестов)

### Option A: «Декларативный drill-engine» [score: 6/10]
YAML-спецификация (target/inject/expect/recover), тесты — строки матрицы, движок интерпретирует.
Trade-offs: элегантно и расширяемо, но over-engineering (12 сценариев не окупают движок), хуже
читаемость/отладка, нарушение Small Simple Blocks.

### Option B: «Два тира + slim-хелперы» [score: 9/10] ← RECOMMENDED
Fast-тир (9 drills, marker `chaos and not night`, ≤30 мин): crash-инъекции (postgres с data-integrity,
redis, litellm), degraded-dependency (stop redis/litellm → сайты живы), watchdog direct-invocation
(env-пороги), OOM kernel-kill, disk pressure через fallocate (секунды вместо dd-минут), tor-канал.
Night-тир (3 drills, marker `night`, отдельное окно ~25 мин): reboot + ΔRestartCount==0,
outbound-partition (iptables auto-revert), docker daemon restart.
Хелперы: lean-функции вместо LogAuditManifest (локальный site-probe `--resolve 127.0.0.1`,
`await_condition`, evidence = один контейнер × tail 200 + verdict.json).
Best when: нужна скорость + покрытие обоих свойств (degradation/self-healing) без движков.

### Option C: «Максимальный даунскейл» [score: 7/10]
Только crash-матрица (kill main-pid × N модулей) + reboot; всё прочее удалить.
Trade-offs: максимально быстро (~12 мин), но теряются классы degradation (stop ≠ kill),
watchdog self-heal (уникальный механизм выше restart policy) и OOM (kernel-initiated kill).

### Option D: «Перенос в статические gate-контракты» [score: 4/10]
Compose-restart-policy lint + healthcheck-contract gates вместо live-тестов.
Rejected: static-гейт доказывает КОНФИГУРАЦИЮ, но не рантайм-самовосстановление (требование
владельца — «убедиться, что всё деградирует верно и плавно, поднимается само»).

**Collapse:** Option B (autonomous collapse — владелец запросил superposition + DevPlan в одном шаге;
переопределить можно до старта Wave 1).

## 3. Architecture Overview — Draft Code Graph

```
tests/e2e/
├── test_chaos_resilience.py        ← REWRITE (~600-700 LOC вместо 1769)
│   ├── FAST (marker chaos):
│   │   ├── test_crash_postgres_data_integrity      (SIGKILL pid под INSERT-нагрузкой → unless-stopped
│   │   │                                            → WAL recovery → committed == rows; TTR ≤120s)
│   │   ├── test_crash_redis_restart_policy         (kill -9 host-pid → exited-proof → healthy ≤90s;
│   │   │                                            сайты живы в окне смерти)
│   │   ├── test_crash_litellm_restart_policy       (kill → сайты живы → healthy ≤120s)
│   │   ├── test_degraded_redis_sites_alive         (docker stop redis 45s → сайты 200 всё окно
│   │   │                                            → start → healthy)
│   │   ├── test_degraded_litellm_sites_alive       (stop 30s → сайты 200 → start → healthy)
│   │   ├── test_watchdog_heals_unhealthy           (health-cmd false + health-interval 5s → unhealthy
│   │   │                                            → ручной запуск watchdog.py c WATCHDOG_UNHEALTHY_MIN=1
│   │   │                                            COOLDOWN=0 → RestartCount+1 + state-file → вернуть
│   │   │                                            канонический healthcheck → healthy ≤60s)
│   │   ├── test_oom_clickhouse_kernel_kill         (memory-bomb в cgroup 1GiB → journalctl -k OOM
│   │   │                                            victim по cgroup-id → restart → up ≤120s)
│   │   ├── test_disk_pressure_alert_and_recovery   (fallocate /tmp до ≥92% cap 94% → Prometheus
│   │   │                                            ratio<0.2 → rm → ratio>0.5 → сайты живы)
│   │   └── test_tor_channel_fails_loud             (stop tor@default+privoxy → send_telegram=False
│   │   │                                            + fail-лог → start → «Privoxy → Tor forward:
│   │   │                                            working» ≤180s; БЕЗ telegram-stage токена)
│   └── NIGHT (markers chaos+night):
│       ├── test_reboot_self_start_zero_loops       (systemctl reboot → SSH ≤900s → стек ≤300s →
│       │                                            сайты → ΔRestartCount==0 ∀ контейнеров)
│       ├── test_outbound_partition_inbound_alive   (OUTPUT DROP v4+v6 45s auto-revert → inbound жив
│       │                                            (локальный probe), outbound curl exit 7/28 →
│       │                                            revert → outbound restored; safety-net сохранён)
│       └── test_docker_daemon_restart_containers_kept (systemctl restart docker → StartedAt
│                                                    непрерывен → стек healthy ≤240s → сайты)
├── chaos_audit.py                  ← REWRITE → lean-хелперы (~150-200 LOC):
│   ├── await_condition(fn, timeout_s, interval_s) → (ok, last)     # единый poll-примитив
│   ├── probe_sites_local(ssh) → dict[url, code]                    # curl --resolve <host>:443:127.0.0.1
│   ├── wait_sites_up(ssh, timeout_s)                               # поверх probe_sites_local
│   ├── wait_containers_healthy(ssh, timeout_s, containers|all)     # docker ps формат-парс (как раньше)
│   ├── container_pid(ssh, name) → int                              # docker inspect .State.Pid + guard >0
│   ├── assert_injection_landed(...)                                # state-poll exited/unhealthy/blocked
│   ├── capture_evidence(ssh, out_dir, container)                   # docker logs tail 200 + verdict.json
│   └── host_epoch_seconds(ssh)                                     # без изменений
└── conftest.py                     ← goloty-skip для chaos — БЕЗ ИЗМЕНЕНИЙ (night ⊂ chaos-сессия)
pyproject.toml / pytest ini          ← регистрация маркера `night` (рядом с `chaos`)
```

**Списки сайтов/контейнеров:** SITE_URLS и baseline-контейнеры резолвить из node-configs/<NODE>/node.yaml
(projects[] expose + modules[]) через существующий фикстурный резолв node-state; fallback при
отсутствии — явный FAIL (R4), не hardcode. Если существующий fixture не отдаёт projects/modules —
читать node.yaml напрямую тем же резолвером, который использует conftest.

## 4. Data Flow (каждый drill)

```
▶ precondition (healthy snapshot) → ⚡ inject (docker/systemd/fallocate) → ◇ proof-of-injection
(state-poll: exited | unhealthy | blocked-probe | ENOSPC-marker) → ⚡ observe degradation window
(sites/dependency-канон, ≤60s) → ⚡ recovery trigger (policy/wait/manual watchdog/start/revert/rm)
→ ◇ await_condition(recovery-предикат, TTR-budget) → ∑ assert: proof ∧ degradation ∧ ttr ∧ clean
→ capture_evidence (verdict.json + docker logs tail) → ⎋ PASS
```

Инвариант потока: ни один drill не переходит к recovery-жданию без доказанной инъекции
(наследие TRAP[BUG] VR 142 §6); любой сброс состояния (health-cmd, iptables, файлы) —
в finally-семантике теста даже при assert-fail (try/finally, не pytest-finalizer-магия).

## 5. $TASKS

| ID | Артефакт | Владелец | Зависимости | Complexity |
|----|----------|----------|-------------|------------|
| TASK-1 | chaos_audit.py rewrite (lean-хелперы) | Coder | — | 4 |
| TASK-2 | Документация: README e2e §Chaos, ci.mk комментарий, AGENTS.md release-checklist строка, маркер `night` | Coder | — | 2 |
| TASK-3 | test_chaos_resilience.py rewrite: 9 fast + 3 night drills | Coder | TASK-1 | 7 |

Critical path: TASK-1 → TASK-3. TASK-2 параллелен (имена тестов зафиксированы в §3).

**TASK-1 — chaos_audit.py rewrite**
- Удалить: LogAuditManifest, LogMarker, MarkerResult, compute_verdict, _check_* (docker/journald/
  loki/alerts/http/state), export_logs, record_verdict, Loki/Grafana константы.
- Создать (§3 Graph): await_condition, probe_sites_local, wait_sites_up, wait_containers_healthy,
  container_pid, assert_injection_landed, capture_evidence, host_epoch_seconds.
- wait_containers_healthy: сохранить семантику старого wait_all_containers (running + healthy|"") —
  канон healthcheck-критерия (TRAP[DECISION] root AGENTS.md); список контейнеров — параметр или
  резолв из node.yaml.
- Acceptance: `make check TEST_FILE=<unit-если появятся>` n/a; статика ruff чистая; импорты нового
  файла не тянут Loki/Grafana API; LOC ≤250.

**TASK-2 — документация + маркер**
- README.md: заменить раздел «Chaos-тесты (DevPlan 126…)»: два тира, команды запуска (§7 Next Steps),
  бюджеты времени, состав сценариев одной таблицей; убрать «до нескольких часов».
- makefiles/ci.mk: комментарий над test-node (строки 32-35) — новая команда fast-тира.
- AGENTS.md: строка 422 «Chaos: `chaos FULL T1–T12`» → «Resilience drills: fast (`-m "chaos and not
  night"`, ≤30 мин) + night (`-m night`, отдельное окно)».
- Маркер `night`: добавить в регистрацию маркеров pytest (там же, где зарегистрирован `chaos`;
  grep `markers` в pyproject.toml/setup.cfg/pytest.ini).
- Acceptance: grep «T1–T12\|FULL T1» по AGENTS.md/README/ci.mk — 0 совпадений; `night` виден в
  `pytest --markers`.

**TASK-3 — rewrite suite**
- Реализовать 12 drills по §3 (имена, инъекции, бюджеты TTR — канон плана).
- VERIFY перед реализацией: watchdog.py env-оверрайды (WATCHDOG_UNHEALTHY_MIN/WATCHDOG_COOLDOWN_MIN,
  строки ~86-98) и CLI (--dry-run/--state-file); способ возврата канонического health-cmd
  (docker inspect .Config.Healthcheck.Test — как T12).
- Каждый drill: try/finally восстановление; proof-of-injection до recovery; LDD [IMP:9] по фазам
  inject/window/recovery/verdict + assert_ldd_imp9_e2e(caplog); capture_evidence в /tmp/chaos-<ts>/<TEST>/.
- postgres-crash: нагрузка упрощается до counter-таблицы (committed-батчи), 200 батчей → 40;
  инвариант целостности: rows == committed_batches×50 (сохранить — это главный assertion данных).
- disk-pressure: fallocate -l <расчёт> (df-based, cap 94%) вместо dd-цикла; spool-fill hack удалить;
  alert-rule-state НЕ проверять (Debt D-N, см. §7).
- tor: НЕ выравнивать по cron; send_telegram напрямую (fail-loud assert) + privoxy-stage recovery.
- Night reboot: сохранить ΔRestartCount-логику W3-2 (_restart_count_map переносится в suite);
  кросс-бут аудит удалить целиком.
- Acceptance: `PYTEST_NO_ESCALATION=1 python3 -m pytest tests/e2e/test_chaos_resilience.py
  -m "chaos and not night" --collect-only -q` = 9 тестов; `-m night --collect-only -q` = 3;
  ruff чистый; локально (без NODE) коллекция проходит, прогон даёт R4-FAIL с понятным сообщением.

## 6. $PARALLEL_GROUPS

### Wave 1 (independent, no shared files)
- Tasks: TASK-1, TASK-2
- Command: `coder Read .ai/plans/013-resilience-drills-rework/01-DevPlan.md, implement Wave 1: TASK-1, TASK-2`

### Wave 2 (depends on TASK-1)
- Tasks: TASK-3
- Command: `coder Read .ai/plans/013-resilience-drills-rework/01-DevPlan.md, implement Wave 2: TASK-3`

### Wave 3 (runtime verification, операторское окно)
- Не код: прогон fast-тира на tronyx-vps (см. Next Steps), фиксация TTR-фактов, при флаках —
  правка бюджетов (не логики) одним фикс-коммитом.

## 7. Design Decisions

## @rationale Q: Почему два тира, а не один быстрый? A: reboot/partition/daemon-restart — реальные
сценарии с ценой времени (boot ~4-8 мин, partition-окно, daemon-recovery ~4 мин); удаление их совсем
(Option C) теряет единственный live-доказательств boot-самостарта (W3-2 поймал P0 F-037 именно им).
Тир-граница по маркеру сохраняет один файл/один запуск pytest.

## @rationale Q: Почему удалён log-forensics (LogAuditManifest)? A: Цель владельца — отказоустойчивость
и самовосстановление, а не реконструкция аудита. Поведенческие assertions (proof/degradation/ttr)
проверяют то же свойство за секунды; Loki-конвейер и alerting покрыты monitoring-модулем и его
собственными тестами. Экспорт логов оставлен точечно (affected container tail 200) для посмертного
разбора флаков.

## @rationale Q: Почему watchdog через ручной вызов, а не реальный cron? A: Тестируемое свойство —
«watchdog ЛЕЧИТ unhealthy», а не «cron срабатывает каждые 5 мин» (расписание тривиально и уже
законтрактовано CRON_WATCHDOG_LINE + CI-gate test_gate_watchdog_clean_env). Ручной вызов той же
команды, что в cron.d (flock+timeout+путь), с env-порогами 1/0 мин — та же кодовая ветка, −20 минут.

## @rationale Q: Почему fallocate вместо dd? A: fallocate резервирует блоки мгновенно (space reservation),
dd пишет данные последовательно — минуты. Инъекция «места нет» идентична для ENOSPC-поведения ФС;
cap 94% и rm в finally сохранены.

## @rationale Q: Почему restore-drill (T10) удалён, а не упрощён? A: Канонический restore-ранбук
сломан (F-031/F-032, сессия 011) — тест на сломанном канале родит RED не по своей вине; inline-boto3
heredoc (как T10) — обход модуля, анти-паттерн. Возвращается ночным drill'ом ПОСЛЕ фикса ранбука.

## @rationale Q: Почему локальный site-probe (--resolve 127.0.0.1) вместо внешних URL? A: Внешние
HTTPS-пробы добавляют сетевую дисперсию (DNS/маршрут/CDN) в TTR-замер — на 011 это давало
минуты ожиданий и ложные таймауты. --resolve бьёт в локальный nginx: проверяется ingress+vhost+TLS,
детерминированно и <1s на URL.

## @rationale Q: Почему имя файла test_chaos_resilience.py сохранено? A: На него ссылаются ci.mk,
README, conftest-документация и история планов (126/136/142/147/162); rename — churn без пользы.
Маркер `chaos` сохранён (фильтр `requires_node and not chaos` в test-node не меняется), `night` —
аддитивный.

## 8. Debt Intake

| Источник | Классификация | Решение |
|----------|---------------|---------|
| D-N: Grafana DiskSpaceLow expr без mountpoint-фильтра (rule не срабатывает) | DEFER | F8 проверяет только Prometheus DATA-path; фикс expr — вне скоупа (metrics-модуль). Rev: следующее касание core/modules/monitoring |
| F-031/F-032: restore-ранбук backup-cron сломан (сессия 011) | DEFER → отдельный план | После фикса — новый night-drill restore round-trip через канонический ранбук (наследование T10) |
| D-8: Loki ingestion loss при clock skew | ARCHIVED-мотив | Сценарий-генератор (T4) удалён; debt-записи в коде остаются валидными |
| Telegram token 404 (находка T5, pre-existing) | DEFER | Новый tor-drill зависит ТОЛЬКО от transport-стадии (privoxy→tor), токен не трогает |
| Watchdog cooldown-бухгалтерия между прогонами | IN_SCOPE | F6-наследник очищает записи redis в state-file перед инъекцией (паттерн T12 шаг 2) |

## 9. Change Impact (cascade)

Документационный каскад (3 файла): tests/e2e/README.md · makefiles/ci.mk · AGENTS.md — все в TASK-2.
Make-глаголы НЕ добавляются (запуск = pytest -m, как сегодня) — каскада entrypoint-manifest/glossary нет.
Гейт-контур не затрагивается: requires_node исключён из make check/gate фильтром (проверка AC5).

## 10. $TEST_SPEC

Новые тесты — сами drills (e2e, маркер chaos):

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| tests/e2e/test_chaos_resilience.py | test_crash_postgres_data_integrity | SIGKILL pg под нагрузкой → WAL recovery → 0 потерянных committed строк | core/modules/postgres (restart policy) |
| tests/e2e/test_chaos_resilience.py | test_crash_redis_restart_policy | kill main-pid → policy поднимает ≤90s, сайты живы | core/modules/cache |
| tests/e2e/test_chaos_resilience.py | test_crash_litellm_restart_policy | kill main-pid → policy поднимает ≤120s, сайты живы | core/modules/litellm |
| tests/e2e/test_chaos_resilience.py | test_degraded_redis_sites_alive | stop 45s → graceful degradation сайтов → start | core/modules/cache |
| tests/e2e/test_chaos_resilience.py | test_degraded_litellm_sites_alive | stop 30s → graceful degradation → start | core/modules/litellm |
| tests/e2e/test_chaos_resilience.py | test_watchdog_heals_unhealthy | broken healthcheck → watchdog (ручной вызов, пороги 1/0) → рестарт → heal | core/internal/healthcheck/watchdog.py |
| tests/e2e/test_chaos_resilience.py | test_oom_clickhouse_kernel_kill | cgroup OOM → kernel kill → policy → up ≤120s | core/modules/clickhouse |
| tests/e2e/test_chaos_resilience.py | test_disk_pressure_alert_and_recovery | fallocate ≥92% → Prom ratio<0.2 → rm → ratio>0.5 | core/modules/monitoring (data-path) |
| tests/e2e/test_chaos_resilience.py | test_tor_channel_fails_loud | stop tor/privoxy → send fails loud → start → forward working | tor-proxy + telegram_notifier |
| tests/e2e/test_chaos_resilience.py | test_reboot_self_start_zero_loops | reboot → стек ≤300s, ΔRestartCount==0 | bootstrap/boot orchestration |
| tests/e2e/test_chaos_resilience.py | test_outbound_partition_inbound_alive | OUTPUT DROP 45s auto-revert → inbound alive, outbound fail-loud | ufw/iptables + backup-cron egress |
| tests/e2e/test_chaos_resilience.py | test_docker_daemon_restart_containers_kept | systemctl restart docker → uptime continuity → healthy | docker/containerd runtime |

Unit/static тесты не требуются — `$TEST_SPEC` дополнительного слоя: NONE — @rationale: pure e2e
rework; хелперы chaos_audit.py покрываются исполнением drills; static-гейты (ruff, agent-check,
naming) применяются автоматически.

## 11. Acceptance Criteria (summary)

| AC | Проверка |
|----|----------|
| AC1 | Прогон Wave 3 на tronyx-vps: 9 PASSED, wall-clock из runs.jsonl ≤1800s |
| AC2 | В каждом verdict.json есть injection_proof; код каждого drill содержит state-poll до recovery |
| AC3 | grep «resolved\|clock\|fullchain.pem\|cross_boot» в test_chaos_resilience.py — 0 совпадений |
| AC4 | pytest --markers содержит night; grep «T1–T12» по AGENTS.md/README/ci.mk пуст |
| AC5 | `make check` rc=0; `make agent-check` exit 0 |

## 12. File Manifest

| Файл | Операция |
|------|----------|
| tests/e2e/chaos_audit.py | rewrite (~150-200 LOC) |
| tests/e2e/test_chaos_resilience.py | rewrite (~600-700 LOC) |
| tests/e2e/README.md | edit §Chaos + Running Tests |
| makefiles/ci.mk | edit комментарий (строки 32-35) |
| AGENTS.md | edit строка release-checklist №2 |
| pyproject.toml (или ini с маркерами) | edit +маркер night |

## Next Steps

### Wave 1
Use coder role and read .ai/plans/013-resilience-drills-rework/01-DevPlan.md, implement Wave 1: TASK-1, TASK-2. Верификация: `make check-diff`, `pytest --markers | grep night`.
### Wave 2
Use coder role and read .ai/plans/013-resilience-drills-rework/01-DevPlan.md, implement Wave 2: TASK-3. Перед реализацией: прочитать core/internal/healthcheck/watchdog.py (env-оверрайды, CLI) и tests/_conftest/node.py (NodeSSHClient контракт). Шаги: `PYTEST_NO_ESCALATION=1 python3 -m pytest tests/e2e/test_chaos_resilience.py --collect-only -q` (12 тестов) → `make check-diff` → `make agent-check`.
### Wave 3 (операторское окно, tronyx-vps)
```
# fast-тир (≤30 мин):
NODE=tronyx-vps PYTEST_NO_ESCALATION=1 python3 -m pytest tests/e2e/test_chaos_resilience.py \
  -m "chaos and not night" -v --tb=short -rs
# night-тир (отдельное окно ~25 мин, предупредить владельца):
NODE=tronyx-vps PYTEST_NO_ESCALATION=1 python3 -m pytest tests/e2e/test_chaos_resilience.py \
  -m night -v --tb=short -rs
```
Пост-прогон: `make healthcheck NODE=tronyx-vps` (нода чиста), артефакты /tmp/chaos-<ts>.

$END_DEVPLAN
