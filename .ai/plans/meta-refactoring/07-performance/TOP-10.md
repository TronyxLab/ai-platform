# TOP-10 real bottlenecks — performance audit

Проект: ai-platform (~290k LOC всего / ~114k production) · 1 неделя до production launch
Принцип: maximum production risk reduction / minimum code churn. Код НЕ исправлялся.
Полный реестр: findings-001…008 (48 findings, ID PERF-001…PERF-095).

## TOP-10 по (риск × стоимость фикса⁻¹)

| # | ID | Файл::символ | Суть | Impact | Fix effort |
|---|-----|--------------|------|--------|-----------|
| 1 | PERF-041 | `status-page/collectors/aggregate.py::fan_out_checks` | Dead timeout: `as_completed(futures)` без timeout + `gethostbyname` без таймаута → вечная блокировка, утечка threads | Зависший DNS → каскад 500s, healthcheck flaps, рестарты контейнера в инцидент | S |
| 2 | PERF-002 | `bootstrap/deploy/parallel_runner.py::drain_all_count` | waitpid status игнорируется: упавшие модули считаются deployed → atomic rollback не срабатывает, отчёт об успехе при провале | Тихий failed-deploy на проде; ломает главный safety-mechanism деплоя | S |
| 3 | PERF-040+042 | `status-page/aggregate.py::get_all_checks` + `app.py::main` | Полный probe suite (~11 subprocess) на каждый запрос без кэша + unbounded threads vs pids=256/128M лимиты | Постоянная фоновая нагрузка ноды; бёрст = 500s + рестарты | M |
| 4 | PERF-080 | `internal/loadtest/runner_cli.py::_run_one_step` | Дубль `_run_locust_process` в `if remote:` ветке: двойной трафик + false FAIL после полной второй нагрузки | Capacity verdicts непригодны за неделю до launch — а load-test обязателен по release-checklist | XS |
| 5 | PERF-082 [HYP] | `llm/admin_client.py::get_key_by_metadata` | Нет пагинации /key/info → дубликаты virtual keys (budget-bearing) на каждом provision | Рост затрат LLM и мусор в DB; проверить до launch | S |
| 6 | PERF-030 | `shared/audit_logger.py::read_audit_log` | Full-file readlines append-only лога (уже 21MB/119k строк на dev) каждую минуту | RSS-рост метрик-экспорта линейно во времени → OOM через недели uptime | S |
| 7 | PERF-001+003 | `deploy/healthcheck_poller.py` + `context_deployer.py::_is_project_healthy` | Вложенные polling-окна: unhealthy проект ≈41 мин вместо 60s; холодный проект = 60s сна до старта работы | Launch-week сбой одного проекта блокирует очередь деплоя на десятки минут | M |
| 8 | PERF-050 | `healthcheck/modules_healthcheck.py::check_module` | Нет timeout на invoke → наследует 180s/модуль; один wedged контейнер вешает весь healthcheck sweep | Release-checklist шаг виснет до ~39 мин worst case | XS |
| 9 | PERF-004+010 | `deploy/orchestrator.deploy_many` + `lifecycle/helpers/reporting.run_healthchecks` | Стек сериализации деплоя (DEPLOY_PARALLEL=false default) + 10×10s serial retry per module | Bootstrap/node-update = Σ вместо ÷parallel_limit; риск CI-timeout | M–L |
| 10 | PERF-053+052 | `metrics/cert_collector.get_certs` + `docker_collector.get_containers` | «TTL cache» реально uncached: O(D×L) x509 parse/min + docker stats 2–15s/мин с silent zeroing при таймауте | Деградация status-page данных ровно в момент инцидентов | S |

## Оптимизации, стоящие делать сейчас (pre-launch)

Все — точечные, ≤50 LOC каждый, не архитектурные:

1. **PERF-002** — проверка WEXITSTATUS в `drain_all_count` (5 строк). Correctness, не только perf.
2. **PERF-080** — удалить дублирующий вызов locust (`runner_cli.py:606-609`). 1 строка.
3. **PERF-041** — `as_completed(futures, timeout=...)` + TimeoutError handler. ~10 строк.
4. **PERF-040** — mtime-keyed кэш aggregate на 30–60s. ~20 строк.
5. **PERF-030** — tail-read последних N записей audit.jsonl. ~15 строк (docstring уже содержит спецификацию).
6. **PERF-050** — пробросить `timeout=HEALTHCHECK_POLL_TIMEOUT` в invoke. 1 строка.
7. **PERF-008x verify** — PERF-082: прогнать два подряд `make provision-llm`, сверить число ключей; если дубликаты — добавить пагинацию.
8. **PERF-003** — single-shot `docker ps` для skip-gate перед поллингом. ~10 строк.
9. **PERF-071/074** — мёртвые гейты (`--only exception_patterns` underscore-vs-hyphen; vacuous validate discovery): 2 строки суммарно, убирают false-green.
10. **PERF-033** — объединить 4 pre-flight SSH-команды в одну (или ControlPersist). ~10 строк, −2–6s с каждого деплоя всех проектов.

## Оптимизации, которые должны ждать (post-launch)

- **PERF-004/009** — параллелизация deploy_many (worker pool поверх topo-групп): высокий churn в критическом пути деплоя, требует staging-прогона; сейчас достаточно починить PERF-001/003 (вложенные окна), чтобы последовательность стала терпимой.
- **PERF-063/067** — единый file-walk snapshot + однопроходные проверки в practices: рефактор интерфейсов хендлеров.
- **PERF-070** — shared AST-parse cache на 5 детекторов static: чистая CI-скорость, не прод-риск.
- **PERF-034/051** — батчинг docker inspect: экономия секунды, трогает общий healthcheck-канон (единый критерий), лучше после стабилизации.
- **PERF-044/045** — pigz/zstd + flock в backup-cron: мониторить с запуска, чинить по факту перекрытий.
- **PERF-081/083** — batch /key/info fetch + переиспользование httpx.Client: сделать вместе с PERF-082 одним PR после подтверждения гипотезы о пагинации.
- Все LOW/Post-launch из findings-001…008.

## Метрика покрытия аудита

| Скоуп | LOC (≈) | Findings | Статус |
|-------|---------|----------|--------|
| bootstrap/deploy + internal/deploy | 14k | 7 | ✅ агент |
| bootstrap/lifecycle | 7k | 2 | ✅ агент |
| bootstrap root *.py | 12k | 0 | ✅ spot-check (2 пустых агентских прогона согласованы) |
| internal/shared + template_engine | 14k | 8 | ✅ агент |
| core/modules | 7.4k | 8 | ✅ агент |
| scripts + healthcheck | 9.4k | 8 | ✅ агент |
| scaffold + practices | 11.4k | 8 | ✅ агент |
| QA tooling (static/check_suite/lint/etc.) | 12k | 6 | ✅ агент |
| llm + monitoring + loadtest | 9.3k | 6 | ✅ агент |
| global anti-pattern sweep | cross | 0 новых | ⚠️ 2 пустых прогона; паттерны покрыты доменными агентами |

Оговорка: тесты (~176k LOC) вне скоупа по принципу prod-risk-first; vendor/ исключён.
