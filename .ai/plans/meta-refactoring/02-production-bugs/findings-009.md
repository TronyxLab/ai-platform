# Direction 9: external dependencies — forensic bug hunt

Date: 2026-08-22 · Commit: 4425ce0 · Mode: read-only audit
Scope: ghcr/build fallback, gh CLI org-secrets, ACME/S3 cert pipeline, age/sops decrypt, SSH facade, S3 client factory.

---

## BUG-0901
- **Severity:** HIGH
- **Confidence:** 85%
- **File:** core/internal/bootstrap/s3_ssl_cache.py (с cert_orchestrator.py)
- **Symbol:** `download_cert` / `cert_orchestrator._plw_body__try_s3_restore`
- **Trigger:** В S3-кэше есть `fullchain.pem`, но `privkey.pem` отсутствует или не скачивается (частичный bucket, прерванный прошлый upload, права).
- **Execution path:** S3 fullchain OK → `download_cert()` пишет fullchain атомарно (s3_ssl_cache.py:534→552) → ветка privkey `_download_s3_file(...)` =False, miss НЕ логируется и не влияет на результат (s3_ssl_cache.py:565–575) → `return True` (s3_ssl_cache.py:605) → `cert_orchestrator._plw_body__try_s3_restore` проверяет ТОЛЬКО существование fullchain.pem (cert_orchestrator.py:539–542) → `DomainCertResult(status="restored")` с IMP:9 «cert restored from S3» → следующий прогон: `_process_single_domain` видит валидный fullchain на диске → «skipped» (cert_orchestrator.py:455–460); обратная загрузка в S3 молча падает (`upload_cert` требует оба файла, s3_ssl_cache.py:377–389).
- **Actual behavior:** Сертификат объявлен «restored» без приватного ключа: nginx не стартует (нет ключа) или подхватывается старый несоответствующий privkey (cert/key mismatch, обрыв рукопожатий). Состояние перманентно маскируется как «skipped/healthy»; диагностика дополнительно отравлена инвертированным логированием опциональных файлов: на УСПЕХ пишется и «restored», и «not in S3 — skipping» (privkey s3_ssl_cache.py:569–570, chain :488–493, account :470–477; bulk_restore пишет «Bulk cache miss» даже при успешном restore :715).
- **Expected behavior:** Restore без privkey.pem = отказ (`download_cert` → False) либо явная проверка пары cert+key перед `status="restored"`; логи успеха/промаха не должны дублироваться/инвертироваться.
- **Impact:** Ломает весь смысл restore-first: bootstrap «зелёный», TLS фактически сломан до ручного вмешательства; повторный self-heal блокирован skip-веткой.
- **Minimal fix:** В `download_cert` трактовать privkey как обязательный (return False при промахе); в `_plw_body__try_s3_restore` проверять наличие обоих файлов; исправить вложенность трёх двойных логов.
- **Required regression test:** Unit: fake-S3 только с fullchain → `download_cert()==False`; orchestrator: `download_cert()` True + отсутствие privkey на диске → статус ≠ «restored»; тест на единственное корректное сообщение для miss-ветки.

## BUG-0902
- **Severity:** MEDIUM-HIGH
- **Confidence:** 75%
- **File:** core/internal/shared/s3_client.py (+ s3_ssl_cache.py, cert_orchestrator.py)
- **Symbol:** `get_s3_client` / `s3_ssl_cache._download_s3_file`
- **Trigger:** `S3_ACCESS_KEY`/`S3_SECRET_KEY` не заданы в окружении (контекст cron renew-hook, неполный source secrets.env).
- **Execution path:** env без кредов → `get_s3_client` тихо подставляет `""` для обоих ключей (s3_client.py:69–70; контракт «никогда не raise», :16–18) → boto3-клиент создаётся успешно → любой вызов → `ClientError` (InvalidAccessKeyId/AccessDenied) → `_download_s3_file` ловит, WARN, `False` (s3_ssl_cache.py:203–217) → `check_cert`/`bulk_restore` классифицируют как обычный cache-miss (s3_ssl_cache.py:645–647) → `cert_orchestrator._process_single_domain` уходит в acme.sh issue для ВСЕХ доменов (cert_orchestrator.py:466–474).
- **Actual behavior:** Ошибка аутентификации S3 неотличима от «ключа нет в кэше»: каждый bootstrap/renewal повторяет DNS-01 issue через Let's Encrypt.
- **Expected behavior:** Пустые креды на фабрике — fail-fast/distinct-маркер; `ClientError` c кодами авторизации → статус «error», а не «miss»; ACME-reissue не должен быть безусловным fallback при S3-auth-failure.
- **Impact:** Выжигание лимита LE 50 certs/domain/week (задокументированный TRAP в bootstrap/AGENTS.md), лишние DNS API-вызовы, маскировка реальной причины («cache miss» в INFO вместо auth-ошибки).
- **Minimal fix:** В `get_s3_client` WARN/исключение при пустых ключах; в `_download_s3_file` различать `InvalidAccessKeyId`/`AccessDenied` от NoSuchKey и прокидывать distinct-статус наверх.
- **Required regression test:** Unit: env без S3_* → фабрика сигнализирует о пустых кредах; fake-ClientError(InvalidAccessKeyId) в `check_cert` → статус «error» (не «miss»), orchestrator НЕ вызывает issue-путь.

## BUG-0903
- **Severity:** MEDIUM-HIGH
- **Confidence:** 80%
- **File:** core/internal/deploy/org_secrets_provisioner.py (+ context_promoter.py)
- **Symbol:** `ensure_context_secrets` / `promote_context`
- **Trigger:** `gh` сбой (истёк auth, rate-limit, нет прав) или частичный резолв значений во время `make context-promote`.
- **Execution path:** `_set_one_secret` rc≠0 → False (org_secrets_provisioner.py:211–222) → цикл аккумулирует `ok=False`, возврат False (:266–271) → `promote_context`: `secrets_ok=False` → «SUCCESS with WARN» печатается через `logger.info` (context_promoter.py:322–326; маркер IMP:10, но Python-level INFO) → audit DONE → `return 0`.
- **Actual behavior:** Промежуточный отказ внешнего вызова (gh) не отражается ни в exit code, ни в уровне логирования, ни (по факту) в вердикте аудита — promote «успешен». Дополнительно MODULE_CONTRACT инвариант №1 обещает «return True (promote продолжает)», тогда как реализация возвращает False (org_secrets_provisioner.py:11–12 vs :271) — расхождение контракта и кода.
- **Expected behavior:** Best-effort допустим, но отказ обязан быть наблюдаемым: `logger.warning`, отдельная audit FAIL-запись по секретам; контракт привести к фактическому поведению.
- **Impact:** Точное воспроизведение задокументированного инцидента из @purpose того же модуля: mirror-org core-deploy падает за ~9s с пустым VPS_HOST после «успешного» promote (org_secrets_provisioner.py:5–7).
- **Minimal fix:** В `promote_context` ветку `secrets_ok=False` вести через `logger.warning` + `write_audit_entry(tag, "WARN"/"FAIL", ...)`; синхронизировать инвариант №1 контракта с кодом.
- **Required regression test:** Интеграционный: run_fn-фейк с rc=1 → promote rc==0 (best-effort сохранён), но в audit-логе есть FAIL/WARN-запись и stderr содержит WARNING-строку; тест консистентности докстринг↔поведение.

## BUG-0904
- **Severity:** LOW-MEDIUM
- **Confidence:** 95%
- **File:** core/internal/bootstrap/deploy/context_deployer.py
- **Symbol:** `build_parser` / `main` / `_deploy_single_project_via_orchestrator(_ghcr_fallback_build)`
- **Trigger:** Оператор запускает `deploy-context --no-fallback-build` (или читает @invariants модуля, ожидаю pull→build fallback).
- **Execution path:** `--no-fallback-build` парсится (context_deployer.py:1201) → `main()` вызывает `deploy_context(...)` вообще без этого аргумента — параметра не существует в сигнатуре (:893–905, вызов :1258–1263) → `_ghcr_fallback_build` принят, но ни разу не прочитан в теле (:352–451) → единственный путь — DeployOrchestrator; pull→build fallback удалён (комментарий :660–665), при этом @invariants №2 всё ещё обещает «ghcr.io pull primary → build on-node fallback» (:15).
- **Actual behavior:** Флаг молча игнорируется, процесс завершается 0 — оператор уверен, что изменил поведение.
- **Expected behavior:** Мёртвый флаг удалить/депрекейтить с предупреждением; доктринг-инвариант синхронизировать с DevPlan 091.
- **Impact:** Ложная уверенность в конфигурируемости канала деплоя; вводящий в заблуждение контракт модуля (класс «exit 0 при no-op»).
- **Minimal fix:** Убрать аргумент из `build_parser`/`_CliArgs` и параметр `_ghcr_fallback_build`; поправить @invariants №2 и @purpose.
- **Required regression test:** Парсер: неизвестный/удалённый флаг → argparse error; статический гейт: отсутствие неиспользуемых underscore-параметров публичных функций деплоя.

## BUG-0905
- **Severity:** MEDIUM
- **Confidence:** 75%
- **File:** core/internal/secrets/decrypt_secrets.py
- **Symbol:** `_yaml_to_env` / `main`
- **Trigger:** Расшифрованный `<NODE>.enc.yaml` содержит не-плоскую структуру (вложенные map'ы, списки `- item`, многострочные значения) — расхождение с ожидаемым форматом `KEY: scalar`.
- **Execution path:** `sops --decrypt` rc=0 (decrypt_secrets.py:267–296) → `_yaml_to_env` сохраняет только строки под regex плоских `key: value`; всё остальное отбрасывается молча (:188–207) → ноль совпадений ⇒ `env_content=""` → `write_secrets_env("")` атомарно пишет ПУСТОЙ secrets.env (:338–359) → `[IMP:9] Secrets decrypted successfully` (:439) → exit 0 (:496).
- **Actual behavior:** Частичная/нулевая расшифровка завершается успехом: пустой или неполный secrets.env + success-лог + exit 0;下游 потребители (φ6 registry-auth: GHCR_PULL_TOKEN — warning-only precondition) получают пустые креды.
- **Expected behavior:** Валидация результата: если исходник непуст, а извлечено 0 ключей — PlatformFatalError; предупреждение с количеством пропущенных не-плоских строк.
- **Impact:** Класс «partial decrypt continuing»: бутстрап продолжается без секретов; отказ всплывает далеко внизу с пустыми credentials (диагностика затруднена).
- **Minimal fix:** После `_yaml_to_env`: `if decrypted_yaml.strip() and not env_content.strip(): raise PlatformFatalError(...)`; подсчёт и WARN пропущенных строк; сверка числа извлечённых ключей с числом top-level ключей YAML.
- **Required regression test:** Unit: YAML со списком/вложенным map → main ≠ 0 (или явный skip-каунт в логе); пустой вход → прежнее поведение; happy-path flat YAML → byte-identical env.

---

## Итог

| ID | Severity | Confidence | One-liner |
|----|----------|------------|-----------|
| BUG-0901 | HIGH | 85% | S3-restore объявляет «restored» без privkey.pem (проверяется только fullchain), состояние перманентно маскируется skip-веткой; логирование опциональных файлов инвертировано |
| BUG-0902 | MED-HIGH | 75% | Пустые S3-креды тихо превращаются в «cache miss» → безусловный ACME re-issue каждого домена → выжигание LE rate-limit |
| BUG-0903 | MED-HIGH | 80% | Отказ `gh secret set` при context-promote даёт exit 0 + audit DONE (WARN только в INFO) → mirror-CI падает с пустым VPS_HOST |
| BUG-0904 | LOW-MED | 95% | `--no-fallback-build` — мёртвый флаг: параметр `_ghcr_fallback_build` нигде не читается, контракт модуля обещает удалённый build-fallback |
| BUG-0905 | MEDIUM | 75% | `_yaml_to_env` молча отбрасывает не-плоские строки: пустой secrets.env записывается с «decrypted successfully» и exit 0 |
