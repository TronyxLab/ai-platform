# Root Causes — системный анализ

Формат на класс: Observed → Immediate cause → Underlying cause → Systemic cause. Фактура: код bootstrap-контура + история фиксов.

---

## RC1 — Нет повторяемого автоматического clean-server теста

- **Observed:** каждая из 7 холодных попыток находила 2–14 новых проблем; успех каждый раз достигался часами ручных итераций.
- **Immediate:** cold-only дефекты (python 3.12 import, re-exec argv, AGE-транспорт, stub-семантика, /run/lock) не покрывались ничем.
- **Underlying:** `requires_node`-тесты (`tests/e2e/test_bootstrap_pipeline.py`: cold-start T6, rebootstrap T13) существуют, но ручные (`make test-node`) и в 027 BLOCKED (test-VPS недоступна); в CI нет ни одного workflow, бутстрапящего чистую машину (проверено: 8 workflows — только push-gate/platform-test/core-deploy/deploy-project/mirror/security-scan); deploy-arena (план 026) спроектирована, но не построена.
- **Systemic:** целевой сценарий продукта («чистая нода → одна команда») — единственный непроверяемый автоматически сценарий платформы. Всё, что не гоняется автоматом, регрессирует молча.

## RC2 — Разрыв честности статусов (silent rc=0)

- **Observed:** vhost «Vhosts rendered» при rc≠0 (020 F-06); restore rc=0 над пустым кластером (308cbef); nightly-бэкапы не работали с 08-26 (d8d885a, flock ENOENT + rc=0 маскировка); gitleaks exit 1 за 0.3s молча (F-05); ssl «provisioned» безусловен (F-10); delivered=0 без сигнала (017 F-04).
- **Immediate:** успех определялся rc процесса, а не post-condition результата.
- **Underlying:** компоненты репортят «я отработал», а не «цель достигнута»; DR-drill 017 был false-green из-за env-формы (U=postgres) — маскировало P0 сутки.
- **Systemic:** нет контрактного правила «успех = верифицированный end-state» и гейтов, которые ловят новые silent-точки до живого прогона; 16 фиксов недели — цена отсутствия одного такого правила.

## RC3 — «Чистая нода ≠ прогретая»: ветки существования без верификации

- **Observed:** ~6 из 14 находок 027 — расхождение веток exists/not-exists (stub ai-platform.yaml → expose:false → 0 vhosts → exit 10 и payload delivery не выполнялся — курица-яйцо, F-01; удалённый серт не восстанавливался до R6, F-02; absent enabled-модуль «no action needed», F-09; «provisioned» на no-op, F-10; hc-маркеры гасили φ11, REF-0005).
- **Immediate:** код пишет «если файл/контейнер/статус существует → другая ветка», на прогретой ноде ветка иная, чем на чистой; тесты гоняют одну ветку.
- **Underlying:** state machine: dependency-gate удовлетворяется `{done, done_with_warnings}` (state_machine.py:54, проверено); done-фазы не перепроверяются (φ4 decrypt, φ6 токен); sub-step resume отсутствует — фаза перевыполняется целиком; критическая локальная фаза доставки payload'ов — **вне state machine** (bootstrap.sh:95-97, exit 2 → exit 10, без checkpoint).
- **Systemic:** идемпотентность фаз заявлена инвариантом, но не имеет обязательного теста; «чистый vs повтор» не различается в тестовых сьютах.

## RC4 — Транспорт секретов/AGE/env: форма входа не верифицируется

- **Observed:** multi-line AGE_SECRET_KEY ломал stdin-prelude → φ4 FATAL «no identity matched» (d1337ab); zshrc-ключ перекрыл контурный (TRAP «второй инцидент → warning» — второй инцидент произошёл); fail-loud ×3 итерации (source=sops → module-aware → systemd auto-detect); reboot-путь терял NODE_NAME и autogen-ensure (b3b3100, 9ef5db9).
- **Immediate:** env-чеки не нормализованы (файловые были санитизированы 08-12, env — только 09-01); глобальный env перекрывает файл.
- **Underlying:** диагностика возможна только живым прогоном (5 диагностических коммитов digest-трассировки на одну проблему).
- **Systemic:** каноническая форма входа (single-line, env-vs-file приоритет) нигде не проверяется префлайтом; fail-loud семантика фиксируется per-path, а не единым транспортным контрактом.

## RC5 — CI-канал: runner-контекст невоспроизводим локально + красный не блокирует

- **Observed:** один прогон D5 (09-02) убили подряд 3 P0 — gitleaks v8.30.1 переименовал checksums.txt (curl «Not Found» молча) → SSH key литерал `$RUNNER_TEMP` в job-level env → dispatch наивный split() кавычек (F-05/F-06/F-07). Пины stale ×3, shallow ×2 (e5d76fa → 688055c — первый фикс не закрыл второй checkout-путь), runner disk (F-12).
- **Immediate:** каждый подкласс CI-багов повторился ровно дважды — фикс первого вхождения не распространялся на второй.
- **Underlying:** deploy-канал верифицируется только живым CI-прогоном на реальном runner'е.
- **Systemic:** platform-test был красен **с 2026-08-17 по 09-02** и не блокировал промоуты (F-13 NOTE: нет branch protection на push-путь) — CI-сигнал существует, но не обязателен.

## RC6 — Внешние предпосылки вне контрактов

- **Observed:** DNS A-записи — ручной шаг runbook; AGE-ключ на машине оператора (форма/приоритет); sops-секреты (webnames/S3/GHCR/TELEGRAM) должны существовать до SCP; SSH-ключи в node.yaml; deploy-key overlay — manual runbook шаги 3-4; локальные исходники `~/projects/<ctx>/<p>`; S3-креды asi невалидны; GitHub billing; пересоздание ноды владельцем посреди валидации (020 F-08).
- **Immediate:** bootstrap честно падает (exit 10 на φ4/φ8) — но падение приходит **после** SCP/φ1-φ3, диагностика затратна.
- **Underlying:** нет pre-flight верификации входного контракта; внешние блокеры трижды маскировались под незавершённость продукта.
- **Systemic:** «данные-вне-репо» (secrets/DNS/ключи/S3) — не часть ни одного проверяемого контракта; их отсутствие обнаруживается глубиной в фазы, а не на входе.

## RC7 (P1) — Тест-дрейф и загрязнение окружения

- **Observed:** platform-test красен 2.5 недели (T2.0a hardening не обновил smoke); chaos-сьют — 9 коммитов через 4 кампании без устойчивости (RestartCount → docker 29 → OOM 3G → hardcoded list → dynamic baseline → full-id scope); NODE_NAME-утечка из тест-фикстуры дала ложный зелёный DR-restore.
- **Underlying:** нет env-hermeticity garde; смоук-контракты не гейтятся при платформенных изменениях.
- **Systemic:** тесты — часть системы поставки; их дрейф = невидимые регрессии.

---

## State leakage между прогонами (что переживает запуск)

| Состояние | Где | Должно жить | При полном отсутствии / маскировка |
|---|---|---|---|
| `state.json` | /var/lib/platform/.bootstrap/ | Да | Без него полный прогон; **маскирует**: done-фазы не перепроверяются (φ4/φ6) |
| Маркер python-deps + import-probe | state_machine.py:721-773 | Да (self-heal, F-019) | pip rc=0 без boto3 → S3-кеш мёртв сквозь все прогоны |
| hc-done маркеры | phases/docker.py | Нет (run-scoped) | Прошлый прогон гасил φ11 (REF-0005); свипается при φ8 |
| Stub docker-compose.yml / ai-platform.yaml | context_deployer.py / converge R3 | Временные | На чистой ноде stub-семантика ломала vhost-render (F-01) |
| acme.sh state + S3-кеш сертов | ~/.acme.sh, S3 | Да | Выпуск сертов зависит от внешнего состояния; S3 miss → ACME (rate-limit) |
| /opt/<ctx>/platform/ git-клон | context_overlay | Да | Без deploy key — clone падает (manual шаги runbook) |
| Deploy-снапшоты | .deploy-snapshots | Да | Без них rollback честно FAILED |
| Cooldown R9 (3 прогона) | converge/runtime.py | Защита | Маскирует повторяющийся дрейф 3 прогона подряд |

**Ключевой паттерн:** история багов недели — это история расхождения «чистый vs частично-прогретый», а не «чистый vs идемпотентный повтор»: ветки exists/not-exists вели на чистой ноде в непротестированный путь.

## Идемпотентность фаз bootstrap (φ1–φ13, фактическая структура)

| Фаза | Идемпотентность | Повтор после частичного сбоя |
|---|---|---|
| φ1 system_bootstrap | Условно (FATAL-шаги повторяемы; non-fatal → done_with_warnings → перевыполнение) | Перевыполняется целиком; self-heal import-probe (F-019) |
| φ2 user_accounts | Полная | no-op |
| φ3 platform_setup | Условно (всё non-fatal) | no-op |
| φ4 secrets_provision | Ломается при отсутствии входа; **done не перепроверяется** | Повтор доводит; ошибки расшифровки после done не видны |
| φ5 node_configuration | Полная | — |
| φ6 registry_auth | Условно: токена нет → **skip, не issue** (скрытая деградация до pull в φ8) | — |
| φ7 certificates | Условно (skipped_import → done_with_warnings; S3 miss → ACME rate-limit) | — |
| φ8 deploy_services | Strict INIT: failed≠∅ → exit 10; DEPLOY_TIMEOUT 900s | Повтор по failed-фазам |
| φ8.5 converge | rc 1/2 → done_with_warnings → перевыполнение | — |
| φ9–φ13 UPDATE | Условная: F-10 был нарушением инварианта (исправлен); φ12 → --skip-provision (сети от φ3 = state-зависимость) | DEPLOY_BEST_EFFORT: WARN→0 |
| *(вне машины)* payload delivery | bootstrap.sh:95-97, rc 2 → exit 10, **без checkpoint** | Курица-яйцо F-01 |

## Может ли действительно чистый сервер пройти весь путь автоматически сегодня?

- **Вручную одной командой — да** (027-B, 09-02: rc=0 → 3 проекта live → повтор no-op 66s → reboot 25/25 → e2e-verify 3/3) — при заранее подготовленных: DNS A-записях, AGE-ключе канонической формы, sops-секретах в node-configs, SSH-ключах, deploy-key overlay, локальных исходниках проектов.
- **Автоматически — нет**: единственное подтверждение end-to-end — ручной прогон; `make test-node` ручной и BLOCKED; в CI нет cold-bootstrap; deploy-arena не построена; branch protection отсутствует.
- **Вердикт:** путь проходим, но это подтверждённый один раз результат фазовой машины с неявными контрактами, а не защищённое свойство. Разрыв «вручную да / автоматически нет» — и есть оставшаяся работа.