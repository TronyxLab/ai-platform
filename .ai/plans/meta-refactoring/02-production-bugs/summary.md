# Production Bugs Forensics — Summary

**Дата:** 2026-08-22 · **Commit:** 4425ce0 · **Метод:** 10 параллельных bug-hunt направлений, READ-ONLY, код не исправлялся.

## Executive Summary

| Метрика | Значение |
|---|---|
| Находок | ~57 (BUG-0100..BUG-1004) |
| Severity | CRITICAL ×3 · HIGH ×17 · MEDIUM ×~18 · LOW ×~19 |
| HYPOTHESIS | 3 (помечены в файлах, confidence <60%) |
| Вердикт | **BLOCKING для launch:** сломан rollback-контур и self-heal; postgres-provisioning хук не вызывается в production |

## TOP-10 рисков

### B1. Postgres DB-provisioning hook не имеет production-caller'ов
**[BUG-0604]** CRITICAL/90% · core/modules/postgres/hooks/on_project_deploy.py. Хук создания роли/БД/GRANT не зарегистрирован ни в одной production-цепочке — контракт `needs.database` никогда не исполняется; проект деплоится без БД. Fix: регистрация хука в post_deploy chain + smoke `needs.database` после деплоя.

### B2. Converge R9 self-heal сломан для ВСЕХ docker-модулей (live-reproduced)
**[BUG-0701]** CRITICAL/95% · converge/runtime.py:318-322. Compose зовётся без `--profile`/`--env-file`/root-compose → «no service selected» / interpolation error / undefined volume. Мёртвый модуль не восстанавливается никогда; exit-2 шум маскирует реальный дрейф. Fix: build_compose_args как в volumes.py:193-199.

### B3. drain_all_count игнорирует exit-коды детей
**[BUG-0801]** CRITICAL/95% · упавший модуль параллельного деплоя считается задеплоенным → групповой rollback пропускается, exit 0 вместо 2. Fix: проверка WEXITSTATUS каждого ребёнка + агрегация фейлов.

### B4. Rollback-контур неработоспособен end-to-end
**[BUG-0601 + BUG-0101 + BUG-0502]** HIGH/75–85% · снапшоты не сохраняют compose_state → rollback тянет несуществующий тег `previous-rollback` из registry (~135s) → PlatformFatalError; ROLLED_BACK недостижим, откат всегда FAILED + payload-drift при повторном rollback. Три направления сошлись на одном дефекте. Fix: сохранять резолвленный образ в снапшот; rollback из локального тега.

### B5. Healthcheck failure → PARTIAL → exit 0 / CI green, rollback не запускается
**[BUG-0602]** HIGH/85% · вопреки документированной политике «healthcheck rollback»; post-deploy chain выполняется поверх больного деплоя. Fix: PARTIAL с упавшим healthcheck обязан триггерить rollback или блокировать chain.

### B6. S3 restore-first принимает fullchain без privkey → тотальный TLS-outage в DR
**[BUG-0700 + BUG-0901]** HIGH/85–90% · s3_ssl_cache.py:564-575 + cert_orchestrator.py:539-549: статус «restored» ставится по наличию только fullchain.pem; issue_cert fallback не запускается → nginx без ключа падает. Именно в DR-сценарии, ради которого кэш существует. Fix: privkey обязателен + openssl key↔cert match до «restored».

### B7. Stale `.hc_done_in_deploy` навсегда подавляет глубокий healthcheck
**[BUG-0501 + BUG-0703]** HIGH/85–90% · маркер пишется даже при failed-группах и переживает run: φ11 пропускает единственный глубокий health safety net; unhealthy-модули живут до ручного вмешательства (перекликается с ARCH-072 attic-прохода). Fix: маркер только при failed==[] или provenance run-id.

### B8. Orphan DB-role при partial failure хука
**[BUG-0605]** HIGH/85% · CREATE ROLE ok + фейл записи кредов = роль с потерянным паролем; retry раннего return'ит success навсегда. Fix: идемпотентный upsert + запись кредов до CREATE ROLE (или транзакционный компенсатор).

### B9. Corrupt secrets.env глотается в φ4 — нода живёт без секретов
**[BUG-0102 + BUG-0905]** HIGH/70–75% · parse failure → phase marked done, skip forever; `_yaml_to_env` молча пишет ПУСТОЙ secrets.env с «decrypted successfully», exit 0. Отложенный взрыв на первом использовании секрета. Fix: fail-fast при parse error; empty-output guard.

### B10. Backup freshness по mtime: «ok» при мёртвых бэкапах
**[BUG-0803 + BUG-0802]** HIGH/85–90% · коллектор меряет свежесть по mtime лога, который cron обновляет даже при фейле джобы; cleanup удаляет из spool дампы старше 7д без проверки загрузки в S3 → тихая потеря off-site бэкапов (нарушение RPO). Fix: freshness из manifest последней успешной выгрузки; cleanup только после верификации в S3.

## За пределами TOP-10 (достойные упоминания)
- **BUG-0201** — HealthcheckPoller: заявленные 60s превращаются в ~21 мин (умножение окон).
- **BUG-0302** — rollback/remove обходят per-project flock: гонка с in-flight receive.
- **BUG-0303** — FileLock деградирует в no-lock при EACCES (root-owned lockfile отключает guard навсегда).
- **BUG-0204** — context-promote git push без timeout/BatchMode → вечный hang CI.
- **BUG-1001** — auto-domain принимает имена, отвергаемые позже validate_vhost_identifiers → permanent exit-4 vhost после создания проекта.
- Полный реестр — findings-001..010.md.

## Системные паттерны (корень большинства находок)
1. **Success-marker до доказательства успеха** — hc_done, restored, DONE-audit пишутся раньше факта (B5, B7, B9).
2. **Rollback — наименее протестированный контур** — 4 независимые находки сходятся в одну точку (B4, B5).
3. **Best-effort swallowing в promote/post-deploy цепочках** — ошибки редуцируются до WARN/INFO (B9, ARCH-1012).
4. **Расхождение путей вызова compose** — R9 vs build_compose_args canon (B2): фикс применён к deploy/R7, но не ко всем сайтам.
5. **Отказ фундамента далеко от корня** — TLS/DB/secrets ломаются тихо и всплывают на первом использовании (B1, B6, B8, B9).

## Минимальный pre-launch пакет
1. B2 (R9 compose args, S) · 2. B3 (exit codes, S) · 3. B6 (privkey guard, S-M) · 4. B1 (hook registration + smoke, M) · 5. B7 (маркер-условие, S) · 6. B9 (fail-fast parse, S).
Каждому — required regression test по формату из findings-файла.
