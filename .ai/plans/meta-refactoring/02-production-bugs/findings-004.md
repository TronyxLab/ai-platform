# Direction 4: resource lifecycle — forensic bug hunt

Date: 2026-08-22 · Commit: 4425ce0 · Mode: read-only audit

Scope audited: `core/internal/deploy/**` (orchestrator, receive_flow, payload_deliverer, channels),
`core/internal/bootstrap/**` (docker_orchestrator, cert_orchestrator, issue_cert, s3_ssl_cache,
core_deliverer, parallel_runner, hermes_workflow), `core/internal/shared/**` (subprocess_io,
http_client, docker_compose, docker_auth), `core/modules/backup-cron/scripts/backup_postgres.py`,
`core/internal/scaffold/{vhost_renderer,nginx_harness}.py`, pollers/watchdogs.

Negative results (audited, no provable defect):
- (a) `= open(` без context manager в core/ — 0 совпадений; все NamedTemporaryFile/mkstemp-сайты
  (tls_check.py:285, config_renderer.py:402, dev_cert_generator.py:356, decrypt_secrets.py,
  atomic_writer.py) имеют finally-cleanup или atexit/signal-протокол.
- (b) PIPE-deadlock не доказан: все docker/rsync/ssh-вызовы идут через `subprocess.run(capture_output=True)`
  (drain через communicate) либо через reader-потоки `run_subprocess_streaming` (subprocess_io.py:295-303).
  Цепочка pg_dumpall|gzip (backup_postgres.py:199-221) корректна: родитель закрывает свою копию
  `dump_proc.stdout` до `wait()`. Bounded-scan zcat (backup_postgres.py:251-266) закрывает pipe и reapит процесс.
- (f) pollers/watchdog: HTTP-ответы закрываются (`with resp:` — service_reload.py:73,83;
  runner_cli.py:398; healthcheck_poller.py:175); fork-дети в parallel_runner reapятся WNOHANG-drain'ом.
- Опровергнута гипотеза о накоплении dangling-образов на VPS: ежемесячный prune-cron существует
  (`/etc/cron.d/platform-prune`, `docker system prune -af --filter until=720h`,
  lifecycle/helpers/system.py:725-727, install_cron_prune).

---

## BUG-0401 — Deploy-payload tar.gz никогда не удаляется после доставки (утечка в TMPDIR)

- Severity: MEDIUM
- Confidence: 95%
- File: core/internal/deploy/payload_deliverer.py:171
- Symbol: `PayloadDeliverer.assemble_payload`; call sites `_handle_deliver` (orchestrator_cli.py:633), `DeployOrchestrator._prepare_deploy` (orchestrator.py:434)
- Trigger: любой запуск `make deploy-project` (`_handle_deliver`) или `DeployOrchestrator.deploy()` — оба пути собирают payload.
- Execution path: trigger → `assemble_payload()` делает `tempfile.mkstemp(prefix=f"payload-{project_name}-")` (payload_deliverer.py:171) → `channel._retry_deliver(payload)` (orchestrator_cli.py:648 / orchestrator.py:466) → функция возвращает rc по ЛЮБОЙ ветке (успех 681, fail 660, VPS-fail 673) **без** `unlink(tar_path)` → файл `/tmp/payload-<project>-XXXX.tar.gz` переживает процесс; каждый следующий деплой добавляет новый.
- Доказательство отсутствия cleanup: `rg -n 'tar_path' core` — единственный `os.unlink` относится к s3_ssl_cache.py:425; ни orchestrator.py, ни channels/{base,scp,forced,local}.py не удаляют `Payload.tar_path`. Собственный docstring контракта (payload_deliverer.py:142): «Creates tar.gz in temp directory (**caller responsible for cleanup**)» — ни один caller не чистит.
```python
tar_fd, tar_path = tempfile.mkstemp(suffix=".tar.gz", prefix=f"payload-{project_name}-")
os.close(tar_fd)
with tarfile.open(tar_path, "w:gz") as tar:
```
- Actual behavior: на каждой доставке в $TMPDIR операторской машины остаётся tar.gz с docker-compose.yml, ai-platform.yaml, .env.platform, practices.lock (mkstemp 0600); накопление неограничено.
- Expected behavior: временный tar удаляется в finally после попытки доставки (success и failure).
- Impact: неограниченный рост мусора в /tmp долгоживущей машины оператора; устаревшие копии инфраструктурных конфигов проектов сохраняются вне /opt/projects; на CI-runner'ах эфемерно, у оператора — персистентно.
- Minimal fix: обернуть доставку в `try/finally: payload.tar_path.unlink(missing_ok=True)` в `_handle_deliver` (orchestrator_cli.py) и в `DeployOrchestrator.deploy` (после apply/verify), либо сделать `Payload` context manager.
- Required regression test: `test_handle_deliver_removes_temp_tar_after_delivery` — fake `channel_factory` перехватывает Payload; assert: (1) success-ветка → `not payload.tar_path.exists()`; (2) delivery-fail ветка → файл также удалён.

## BUG-0402 — render_all оставляет /tmp/vhost_render_* при провале nginx -t (PlatformFatalError минует очистку)

- Severity: LOW
- Confidence: 95%
- File: core/internal/scaffold/vhost_renderer.py:940
- Symbol: `render_all`; исключение `PlatformFatalError`
- Trigger: `make render-vhosts` / пост-деплой рендер, когда хотя бы один отрендеренный vhost валит `nginx -t` (или OSError в шаге ❻ mv).
- Execution path: trigger → `render_all` создаёт `temp_dir = mkdtemp("vhost_render_")` (vhost_renderer.py:914) → `nginx_t_harness(...)` вернул False → `raise PlatformFatalError(msg)` (940) → except-клауза ловит только `(DuplicateDomainError, RuntimeError, ConfigValidationError)` (982) → `PlatformFatalError ⊂ PlatformError ⊂ Exception` (shared/exceptions.py:30,69 — НЕ RuntimeError) → обе точки `shutil.rmtree(temp_dir)` (984 и 994) пропущены → dir навсегда в $TMPDIR.
```python
        if not nginx_t_harness(str(temp_dir)):
            logger.error("[IMP:10][render_all] nginx -t validation FAILED — removing temp dir, aborting")
            msg = "nginx -t validation failed — no files written (all-or-nothing)"
            raise PlatformFatalError(msg)
...
    except (DuplicateDomainError, RuntimeError, ConfigValidationError) as e:
        # Cleanup temp dir on failure
        shutil.rmtree(temp_dir, ignore_errors=True)
```
- Actual behavior: лог обещает «removing temp dir», но cleanup не выполняется; каждый повторный провал рендера добавляет `/tmp/vhost_render_*`.
- Expected behavior: заявленный инвариант функции (docstring, vhost_renderer.py:884): «All-or-nothing: if nginx -t fails, temp dir is cleaned up» — cleanup при любом исходе.
- Impact: медленный рост /tmp на долгоживущей VPS/dev-машине при рекуррентных ошибках рендера (синтаксис конфига, недоступен docker-harness); KB-размеры, но детерминированное накопление и нарушение собственного контракта.
- Minimal fix: включить `PlatformFatalError`/`OSError` в except-кортеж (или вынести шаги ❹-❻ в `try/finally` с безусловным rmtree).
- Required regression test: `test_render_all_cleans_temp_dir_on_harness_failure` — патч `nginx_t_harness` → False (реальный путь raise PlatformFatalError внутри try); assert: после pytest.raises(PlatformFatalError) в каталоге tempdir нет директорий `vhost_render_*`.

## BUG-0403 — webnames API-ключ остаётся несshredенным при сбое setup-фазы до try/finally

- Severity: MEDIUM (security: credential exposure)
- Confidence: 85%
- File: core/internal/bootstrap/issue_cert.py:480
- Symbol: `_issue_acme_webnames`, `_shred_paths`
- Trigger: OSError между инъекцией ключа и входом в try-блок — `dnsapi_tmp.write_text(...)` (485), `chmod(0o755)` (486), `mkdir(parents=True)` (489), `shutil.copy2` (490, напр. ENOSPC/EACCES).
- Execution path: trigger → mkstemp создаёт tmp (480) → ключ записан и файл сделан world-readable/executable (485-486) → копия с ключом уже лежит в `${ACME_HOME}/dnsapi/dns_webnames.sh` (490) → исключение ОС ДО строки 498 (`try:`) → `finally: _shred_paths([dnsapi_tmp, dnsapi_dest])` (510-512) не выполняется → процесс падает с traceback → ОБА файла с plaintext WEBNAMES_API_KEY остаются на диске ноды.
```python
    fd, tmp_name = tempfile.mkstemp(prefix="dns_webnames.", dir=tmp_dir)
    ...
    dnsapi_tmp.write_text(inject_webnames_key(original, api_key), encoding="utf-8")
    dnsapi_tmp.chmod(0o755)
    ...
    shutil.copy2(dnsapi_tmp, dnsapi_dest)
    dnsapi_dest.chmod(0o755)
    ...
    last_rc = 1
    try:
        ...acme retry...
    finally:
        _shred_paths([dnsapi_tmp, dnsapi_dest], ctx.runner)
```
- Actual behavior: shred-протокол покрывает только retry-блок; контракт в docstring (строка 453: «После последней попытки: shred -u tmp + dnsapi/… — всегда») нарушается на setup-фазе.
- Expected behavior: ключ уничтожается из tmp + dnsapi-копии при любом исходе, включая ошибки подготовки.
- Impact: DNS-API кред провайдера остаётся в world-readable (0755) файлах в /tmp и dnsapi/ на прод-ноде до ручной чистки; окно узкое (fs-ошибка), но последствия — утечка кредов.
- Minimal fix: расширить try/finally на весь блок после `write_text`: `try: <setup + issue> finally: _shred_paths(...)`.
- Required regression test: `test_issue_acme_webnames_shreds_when_setup_fails` — monkeypatch `shutil.copy2` → OSError; assert: tmp-файл удалён (`_wipe`/nonexistent) и `dnsapi_dest` отсутствует после pytest.raises(OSError).

## BUG-0404 — s3_ssl_cache: temp fullchain.pem не удаляется на success-пути; account.tar.gz теряется в except-ветке

- Severity: LOW
- Confidence: 90%
- File: core/internal/bootstrap/s3_ssl_cache.py:529
- Symbol: `download_cert`, `upload_cert`
- Trigger: (а) успешный restore сертификата из S3 (bootstrap φ7 ssl-provision / bulk_restore); (б) `tarfile.TarError`/OSError при упаковке acme-account данных в `upload_cert` (cron renew-hook).
- Execution path: (а) `NamedTemporaryFile(delete=False)` для fullchain (529-530) → download OK → validate OK → `_atomic_write(dest_fullchain, ...)` (552) → выполнение продолжается к privkey-блоку, но `tmp_fullchain_path` НЕ удаляется (except 554-558 чистит только failure-пути; privkey/chain/account имеют `finally: unlink` — 573-575, 584-586, 600-602 — fullchain единственный без него); (б) в upload-блоке `os.unlink(tar_path)` стоит ВНУТРИ try (425) → исключение `tar.add()`/упаковки уходит в except (426-432) мимо unlink → tmp tar.gz остаётся.
```python
    with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as tmp_fullchain:
        tmp_fullchain_path = tmp_fullchain.name
    try:
        if not _download_s3_file(f"{s3_base}/fullchain.pem", tmp_fullchain_path):
            ... os.unlink(tmp_fullchain_path); return False
        ...
        _atomic_write(dest_fullchain, tf.read(), mode=0o644)   # ← success: unlink нет нигде дальше
```
- Actual behavior: один осиротевший `/tmp/tmpXXXX.pem` на каждый успешно восстановленный домен; при исключении упаковки — осиротевший tmp tar.gz.
- Expected behavior: симметрично privkey/chain/account-блокам — `finally: if Path(tmp_fullchain_path).exists(): unlink`; unlink tar_path — в finally.
- Impact: медленный рост /tmp + остаточные копии cert-материала (публичного) вне letsencrypt-live; LOW по размерам.
- Minimal fix: добавить `finally`-unlink для `tmp_fullchain_path` в download_cert и перенести `os.unlink(tar_path)` в finally в upload-блоке account-данных.
- Required regression test: `test_download_cert_leaves_no_temp_files` — fake `_download_s3_file` пишет валидный PEM; assert: после `download_cert(...) is True` в патченном tempdir не осталось `*.pem`/`*.tar.gz` файлов.

---

## Итог

| ID | Severity | Confidence | One-liner |
|----|----------|------------|-----------|
| BUG-0401 | MEDIUM | 95% | Deploy-payload tar.gz (compose+.env.platform) никогда не удаляется после доставки — утечка в $TMPDIR на каждый deploy-project/orchestrator.deploy |
| BUG-0402 | LOW | 95% | render_all оставляет /tmp/vhost_render_* при провале nginx -t — PlatformFatalError не входит в except-кортеж очистки |
| BUG-0403 | MEDIUM | 85% | webnames API-ключ (0755 tmp + dnsapi-копия) не shredится при OSError в setup-фазе до try/finally |
| BUG-0404 | LOW | 90% | s3_ssl_cache: temp fullchain.pem живёт после успешного restore; account.tar.gz temp не удаляется в except-ветке |
