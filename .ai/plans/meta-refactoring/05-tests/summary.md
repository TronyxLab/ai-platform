# Test Suite Audit — Summary

**Дата:** 2026-08-22 · **Метод:** adversarial static audit, 10 направлений, 522 файла / ~176k LOC · Тесты не переписывались

## Executive Summary

| Метрика | Значение |
|---|---|
| Находок | 58 (TEST-001..096) |
| Вердикт по suite | **AMBER: объём ≠ защита.** Детерминированные слои (unit/gates trinity) дисциплинированы выше среднего; риск концентрируется в seams — process/service границы, failure arms, CI-каналы |
| Ключевой парадокс | ~35% недавних инженерных времени уходит на ремонт верификационной инфраструктуры (ARCH-091), при этом ровно те гарантии, ради которых suite существует (healthcheck rollback, shared-DB isolation, alert severity), не имеют ни одного исполняющего их теста |
| Оценка false confidence | MEDIUM (TEST-091..095): 9 каналов empty-collection→PASS, generator-self-consistent parity, honesty allowlist позади реальности, cache слеп к toolchain |

Сводка направлений: [README.md](README.md).

## TOP-10 рисков тестовой системы

1. **«Healthcheck rollback» — миф на главном пути** [TEST-001]: unhealthy деплой → PARTIAL → rc=0 → CI зелёный, post-deploy hooks исполняются, rollback НЕ срабатывает (он wired только на compose-failure). Заявленная в AGENTS.md страховка не существует на этом пути.
2. **DEPLOY_PARALLEL деплоит без единой health-проверки** [TEST-061 + ARCH-072]: реальный drain_all_count очищает pid_to_name до вычисления all_names → групповые healthchecks перебирают 0 модулей; маркер пишется безусловно → standalone тоже скипается. Моки скрыли живой продакшн-баг.
3. **Freshness gates не ловят закоммиченный stale** [TEST-091]: pytest-гейт = git diff vs index (вакуум на CI); реальная parity судится тем же генератором (детерминированный баг генератора = вечный green). Секрет может деплоиться вне manifest-трекинга.
4. **Docker-tier может исчезнуть молча** [TEST-095 + TEST-093]: 9 каналов rc=5→PASS; переименование маркера опустошает gates-docker на всех ветках с зелёными бейджами; honesty-pin гейт проверяет 2 из 3+ workflow.
5. **Shared-DB guarantee потеряла единственный spanning-тест** [TEST-042]: local_stack exclusion (4425ce0) убрал из CI pgbouncer wildcard + роль/GRANT end-to-end; R5-negative против исправленного «no such database» бага больше не исполняется.
6. **Rollback-машины никогда не исполняются тестами** [TEST-050/051]: реальные тела _rollback_compose/_restore_payload_files — 0% покрытия (DI bypass), failure arm rollback_ok=False не ассертится нигде; аварийный rollback может репортить успех без восстановления.
7. **Alert severity контракт не запинен** [TEST-052]: FAILED/ROLLBACK → critical + 💥 не покрыто ничем; регрессия делает failed deploy «info» — on-call спит сквозь outage.
8. **Wire format client/server никогда не встречались** [TEST-043 + TEST-003]: forced-command отправитель и парсер эволюционируют под независимыми suite; receive-verb без adversarial негативов (`receive ../../etc/passwd`).
9. **Обязательный агентский гейт нефальсифицируем** [TEST-010 + TEST-012]: agent_check 1266 LOC — ноль тестов (twin-тесты тестируют другую реализацию); manifest_driver — чекер внутри make check — сам непроверен.
10. **Cache и env-гигиена тиражируют прошлые инциденты** [TEST-094 + TEST-074 + TEST-073]: fingerprint key слеп к .venv/env → replay stale greens после апгрейда инструментов; прямые os.environ записи без undo (класс отгруженного TRAP[BUG] 2026-08-02).

## Сильные стороны (подтверждено)

R1 AST-гейт полицит pass-тесты (~0 живых инстансов); mock-only верификация 1 функция из 3367; tests/gates почти mock-free; quarantine/skip-гигиена идеальна (ноль stale skips); R5-пробы исполняют настоящие детекторы; golden parity рукописная; два concurrency-теста золотого стандарта (state_store concurrent writers, interprocess flock); clock-дисциплина unit-слоя образцовая (DI sleep_fn/clock); 42/44 module-скриптов покрыты; top-churn файлы 18/20 покрыты.

## Приоритетный порядок закрытия

1. **Критично (безопасность деплоя):** TEST-061 real-drain тест (+ фикс ARCH-061/072), TEST-001 unhealthy-path пин решения, TEST-050/051 failure arms, TEST-052 severity.
2. **CI-целостность:** TEST-095 collection floors, TEST-093 deny-by-default honesty, TEST-091 независимый semantic validator манифестов, TEST-094 toolchain-aware cache key.
3. **Seam-spanning:** TEST-042 port local_stack в ci-docker, TEST-043 round-trip wire test, TEST-003 adversarial negatives.
4. **Гигиена:** TEST-010 agent_check golden-тесты, TEST-074 env-writes через monkeypatch, TEST-081..084 дедупликация (~230 строк removable).
