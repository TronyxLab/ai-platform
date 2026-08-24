# Critical Blind Spots

Продакшн-поведение, которое можно сломать, оставив весь CI зелёным. Отсортировано по бизнес-риску.

## BS-1 · Healthcheck-rollback не существует на unhealthy-пути
Claimed: «атомарный деплой + healthcheck rollback» (root AGENTS.md).
Actual: unhealthy контейнер после деплоя → PARTIAL → exit 0 → post-deploy chain как при успехе; auto-rollback wired ТОЛЬКО на compose non-zero. Ни один тест не прогоняет unhealthy через pipeline. [TEST-001]
Пробой: crash-looping image уходит в прод, каталог регенерируется, алерт — info. Обнаружение — человеком.

## BS-2 · DEPLOY_PARALLEL пропускает все healthchecks
Actual bug (не только gap): drain_all_count вычищает pid_to_name до сбора all_names → 0 модулей в healthcheck-цикле; маркер `.hc_done_in_deploy` пишется безусловно → φ11 standalone healthcheck тоже скипается. Фейковые drain-функции в тестах маскируют это. [TEST-061, ARCH-072]
Пробой: `DEPLOY_PARALLEL=true` релизы зеленеют без единой проверки здоровья.

## BS-3 · Manifest freshness не проверяется на большинстве веток
push-gate (все ветки) гоняет git-diff вариант гейта = вакуум на свежем checkout; единственная содержательная проверка — regeneration через platform-test.yml (main). Parity судится тем же генератором → баг генератора вечен. Два file-list'а (_GENERATED_FILES=6 vs _GENERATED_PATHS=7) дрейфуют под комментарием «MUST stay synced». [TEST-091]
Пробой: секрет добавлен в secret-definitions.yaml, манифесты stale — все ветки зелёные до main.

## BS-4 · Целые тестовые ярусы исчезают без красного
9 каналов rc=5→PASS (check-suite allow_no_tests ×3, executor passed_no_tests, workflow exits ×5). Переименование маркера requires_docker/integration опустошает docker-tier везде. Honesty-pin гейт знает 2 workflow из 5+, гоняющих pytest. [TEST-095, TEST-093, TEST-041]
Пробой: рефакторинг маркеров → docker-гейты перестают существовать, бейджи зелёные.

## BS-5 · Shared-DB isolation недоказуема в CI
Единственный spanning-тест hook→pgbouncer→role-isolation исключён из CI (local_stack, 4425ce0). Unit-слой мокает docker exec. R5-negative против исправленного hard-list бага не исполняется. [TEST-042]
Пробой: регрессия wildcard pgbouncer/GRANT модели доезжает до staging/production.

## BS-6 · Forced-command wire: две половины без встречи
Отправитель (channels/forced.py shlex-формат) и приёмник (ssh_command_parser) тестируются независимо с собственными предположениями; round-trip теста нет; receive-verb без traversal/semicolon негативов через _dispatch; receive-dispatch тест принимает ЛЮБОЙ из пяти статусов. [TEST-043, TEST-003]
Пробой: format drift → JSON dispatcher error на VPS при зелёном CI; либо security-guard регрессия как ci-deploy user.

## BS-7 · Rollback-код не исполняется ни одним тестом
Реальные тела _rollback_compose (:306-343) и _restore_payload_files (:268-295) — 0% исполнения (все тесты подменяют лямбдами); failure arm rollback_ok=False → FAILED + audit row не ассертится; test_rollback_with_snapshot принимает оба исхода. [TEST-050, TEST-051, TEST-005]
Пробой: во время инцидента аварийный rollback репортит DEPLOYED, восстановив ничего.

## BS-8 · Алертинг худшего момента не запинен
_notify_hook: FAILED/ROLLBACK → critical+💥 — ноль тестов; PARTIAL-as-info тоже конвенция без ассерта. [TEST-052, TEST-001]
Пробой: failed deploy/rollback анонсируется как info — on-call не разбужен.

## BS-9 · Секреты: stderr-redaction и signal-cleanup без тестов
DD5-8 sanitization (redact temp-key path + truncate 500) никогда не исполнена тестом; DD5-3 SIGTERM/atextit очистка /dev/shm ключа — контракт «заменяет shell trap», заменённых тестов нет. [TEST-002, TEST-064]
Пробой: утечка путей/материала ключей в audit/alerts при decryption-инциденте.

## BS-10 · Гейты, гейтящие агентов, сами нефальсифицируемы
agent_check (1266 LOC, обязательный шаг каждого агента) — 0 тестов; name-twin тесты проверяют другую реализацию; manifest_driver — freshness-checker внутри make check — сам непроверен. [TEST-010, TEST-012]
Пробой: parser-регрессия → PASS на сломанном дереве для всех агентов.

## BS-11 · make check может заверить вчерашний toolchain
Fingerprint key хеширует дерево, но не .venv (версии ruff/pyright/deptry) и не env исполнителя (REQUIRE_HONESTY_MODE!) → апгрейд инструмента или смена честности режима переигрывает кэшированный зелёный. Плюс несбрасываемый file_lock._REENTRANT и os.environ утечки между тестами — класс уже отгруженного инцидента 2026-08-02. [TEST-094, TEST-073, TEST-074]

## Мета-вывод
Suite силён там, где легко (детерминированные чистые функции, структурные контракты, golden-файлы) и пуст именно там, где дорого: process/service границы, failure arms, security-инварианты, само-верификация инфраструктуры. Формула риска: **зелёный CI здесь доказывает корректность units, но ни одна заявленная deploy-safety гарантия верхнего уровня (BS-1/2/6/7) сейчас не enforceится исполняемым тестом.**
