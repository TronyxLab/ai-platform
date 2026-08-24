# Final Summary — Meta-Refactoring Synthesis (10-Synthesis)

Дата: 2026-08-24 · Входы: 9 аудитов `.ai/plans/meta-refactoring/01..09` (~9.3k строк, ~500 raw findings) · Выход: 6 артефактов, 31 REF (17 P0 + 14 P1).

## Вердикт

Платформа архитектурно зрелая (гейты, SoT-дисциплина, DI-швы, thin facades), но **все top-level deploy-safety гарантии сейчас либо не работают, либо ложно-зелёные**: rollback недостижим (ROLLED_BACK unreachable), healthcheck-failure = зелёный CI, параллельный деплой не считает неудачи и пропускает healthchecks, самолечение (R9/watchdog) сломано, мониторинг слеп в момент инцидента, DR-цепочка бэкапов не соответствует заявленному RPO, а scaffolded проекты физически не могут задеплоиться (нет build/push канала). Ни один из этих дефектов не требует архитектурной перестройки — все закрываются точечными изменениями суммарно ~2.3–3.5k LOC за 5 дней.

## Пять системных паттернов (корень большинства находок)

1. **Success-marker до доказательства** — hc_done, restored/DONE-audit, freshness по mtime лога, PARTIAL∈success, «decrypted successfully» с пустым файлом.
2. **Fail-open swallowing** — broad except→WARN на secrets/env-check/org-secrets/post-deploy цепочках при FATAL-обёртке шага.
3. **Canon divergence** — compose-invocation канон применён к deploy/R7, но не R9; healthcheck-criterion ×4 реализации; таймауты 600/900; alert-rules рендер мимо mount; сети SoT≠рантайм.
4. **Gate закрепляет баг** — test_gate_module_hooks утверждает отсутствие postgres hook; `--only` принимает неизвестные детекторы; rc=5=PASS; honesty=skip by default.
5. **SoT без enforcement tail / contract-by-convention** — shared hub заморожен гейтами, но имена-контракты (verbs/env/detectors/networks) дублированы без валидации; документированные инварианты (rollback, CONNECT isolation, network placement, PostgreSQL 16) не соответствуют коду.

## Числа

- Дедупликация: ~500 raw → 31 REF + P2/P3 реестры; крупнейшие кластеры подтверждены 3+ независимыми аудитами (rollback contour — три направления внутри одного аудита + два других домена).
- Confirmed vs hypothesis: P0/P1 состоят только из confirmed (в т.ч. live-reproduced: R9, `--only`×14 skip, docker name-filter); гипотезы изолированы в refactoring-map §4.
- Blockers покрыты: B1(build channel), B2(postgres hook), B3(langfuse queue), B4(TLS scan), B5(AGE backup ops), B1s/B2s(L1 gates), B3s(credential binding→P1 top), B4s/B5s(symlink/FQDN), B6–B8(secrets exposure), B9/B18(access XS), B10/B17(supply chain), B11/B12(resource guards), B13(network truth), B15(backup encryption), B16(retention→P2 первый месяц).
- Отброшено как low-value/cosmetic: ~180 позиций (регистр в refactoring-map §7).

## План на 5 дней (волны)

| День | Содержание |
|------|------------|
| 1 | Волна 0: pins CI (REF-0012), monitoring YAML пакет (REF-0010 config-часть), drain/marker (REF-0005), PARTIAL→FAILED (REF-0003), secrets fail-fast (REF-0013), hook register (REF-0002 start), XS access (REF-0016) |
| 2 | REF-0004 rollback contour, REF-0011 locks, REF-0007 exposure sweep, REF-0014 R9/watchdog, REF-0105 payload tx |
| 3 | REF-0006 L1 gate + negatives, REF-0001 build&push + e2e, REF-0008 cert bundle, REF-0009 backup truth |
| 4 | REF-0103 timeouts, REF-0104 LLM store, REF-0107 false-green gates, S-пакеты REF-0109..0114 |
| 5 | `make check` до чистоты → drills: reboot, restore, age-key-backup, load-test smoke, e2e scaffold→push→deploy, chaos T1-T12 |

Арбитраж качества: `make check` (батч), `make agent-check` перед завершением волны; per-task `make check TEST_FILE=...`; полный gate — только CI.

## Главный ответ

**Какие максимум 20 изменений дадут максимальное повышение production reliability за оставшиеся 5 рабочих дней?**

| # | Изменение | REF | Цена |
|---|-----------|-----|------|
| 1 | Build&push job в шаблоны проектов + fix adopter input | REF-0001 | M |
| 2 | Зарегистрировать postgres hook + ensure-convergence провизии (+REVOKE PUBLIC rider) | REF-0002 | M |
| 3 | Unhealthy → FAILED/critical + exit≠0 (+rollback-ветка) | REF-0003 | S |
| 4 | Починка rollback-контура: compose_state в снапшот, skip double-rollback, require_healthy, re-verify | REF-0004 | M |
| 5 | drain_all_count exit-status + all_names до drain + run-scoped hc_done | REF-0005 | S |
| 6 | L1 deny volumes/socket/host-modes + гейт внутри DeployOrchestrator.deploy + compose-parse blocking | REF-0006 | M |
| 7 | Секреты вне argv/логов (stdin-транспорт AGE/SSH) + atomic_writer 0600/0640 sweep | REF-0007 | M |
| 8 | TLS: privkey обязателен + pair-match + expiry-scan покрывает letsencrypt-live + self-signed alert + ACME backoff + FQDN validation entry | REF-0008 | M-L |
| 9 | Backup truth: uploaded-sentinel + freshness stamp после gzip -t + age-encrypt дампов + restore ON_ERROR_STOP/pre-snapshot + age-key-backup drill | REF-0009 | M |
| 10 | Мониторинг-минимум: noeviction langfuse-redis, pgbouncer/pg_up/redis_up/minio/loki rules, noDataState=Alerting, render-dir fix, внешний heartbeat, warning-push | REF-0010 | M |
| 11 | Конкурентность деплоя: FileLock fail-closed EACCES + chown locks, flock до копирования payload, lock на rollback/remove, CI concurrency group, retryable≠timeout | REF-0011 | M |
| 12 | SHA-pin 22 actions + gitleaks checksum + PR/secrets гигиена workflow'ов | REF-0012 | S |
| 13 | Secrets fail-fast: empty-parse abort, merge-guard Step 3.5, postcondition required∧sops, NODE-dispatch unlock, platform_config latch | REF-0013 | M |
| 14 | Самолечение: R9 через build_compose_args + label-детекция + watchdog stamp-after-success + TG на skip | REF-0014 | S-M |
| 15 | Ingress/receive guards: limit_conn + client-timeouts + SSE ≤300s + uncompressed ceiling + payload cap ↓ | REF-0015 | S-M |
| 16 | XS access hardening: sshd kbd-interactive pin + sudoers arg-spec | REF-0016 | XS |
| 17 | Network truth: hermes-agent-net attach (аддитивно) + PLATFORM_LANGFUSE_URL :3000 + smoke alias + regen | REF-0017 | M |
| 18 | Таймаут-бандл: monotonic poller deadline, healthcheck invoke 60s, GIT_SSH_COMMAND+timeout, lib/ssh.sh 900, litellm request_timeout 120s | REF-0103 | M |
| 19 | LLM key store: atomic_write_json+lock+fail-loud corrupt, 404≠transport-error, pagination verify, phase failure-count ≠ skipped | REF-0104 | M |
| 20 | False-green гейты: --only exit 2, validate discovery roots, collection floors, honesty deny-by-default, fingerprint salt, независимый manifest oracle | REF-0107 | M |

Резерв при высвобождении ёмкости: REF-0102 (dead FQDN preflight, <10 LOC), REF-0114 (org-secrets guard), REF-0109 (node.yaml локи), REF-0113 (SoT-константы).

## Риски плана

1. **REF-0007 транспорт ключей** затрагивает bootstrap.sh/core_deliverer — обязательно staging-прогон node-update; имя AGE_SECRET_KEY заморожено.
2. **Сетевые изменения REF-0017** — только аддитивный attach + full-stack прогон на test-VPS до прод-деплоя.
3. **Строжащие фиксы (REF-0107, 0003)** вскроют накопленные нарушения — это цель; заложить буфер волны 5.
4. **Drills зависят от REF-0009** — restore-drill до харденинга рецепта опасен (FAIL-0803): порядок соблюдать строго.
5. Freeze-лист P3 обязателен: главная угроза неделе — не найденные дефекты, а структурные рефакторинги поверх аварийных путей.
