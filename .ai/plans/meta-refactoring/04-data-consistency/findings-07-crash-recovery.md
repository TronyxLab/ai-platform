# $ARTIFACT_CONTRACT
# PURPOSE: Forensic-аудит crash recovery & partial completion мутаций платформы (bootstrap φ1–φ13, certs, deploy receive, secrets)
# DESCRIPTION: Состояние после прерывания (SIGINT/SIGHUP/OOM/disk-full/reboot): zombie-done, mix версий, недоступный сервис; поведение следующего запуска
# RATIONALE: Волна meta-refactoring/04-data-consistency, срез 07
# ACCEPTANCE_CRITERIA: Каждый HIGH/CRITICAL показан цепочкой START → операция → crash → END; поведение next-run классифицировано (recover/stuck/silent)
# IMPLEMENTS: Проверки 1–6 ТЗ (фазы×артефакты, cert atomicity, tar receive, secrets, do/mark-порядок, signals/orphans)
# IMPACTS: Отчёт только для чтения; код не менялся
# REQUIRES: core/internal/bootstrap/lifecycle/*, cert_orchestrator.py, issue_cert.py, shared/ssl_certs.py, deploy/receive_flow.py, secrets/decrypt_secrets.py

# Crash recovery audit
Метод: статический разбор путей мутаций (state machine, cert, receive, secrets, signals) через grep/read; порядок «делай→mark» верифицирован по коду; make-цели не запускались. Позитивный фон: порядок везде «execute → потом mark» (cli.py:811→816, python_deps.py:599→610), state.json пишется атомарно под flock (state_store.py:315–341), secrets.env — tempfile+rename (decrypt_secrets DD5-7) — zombie-done и коррапт-state при crash не обнаружены.

## DATA-701: cert_is_valid игнорирует privkey.pem — crash в install-cert оставляет битую пару, которая навсегда «valid on disk»
- **Severity:** HIGH · **Confidence:** HIGH
- **Files:** core/internal/shared/ssl_certs.py · core/internal/bootstrap/cert_orchestrator.py · core/internal/bootstrap/issue_cert.py · **Symbols:** cert_is_valid, _process_single_domain, _install_cert_via_acme (--install-cert), _generate_self_signed · **Invariant:** «cert на disk valid ⇒ nginx может загрузить пару key/cert»
- **Violating scenario:** START: φ7/φ12, на disk старая валидная пара. acme.sh --install-cert копирует privkey.pem и fullchain.pem двумя отдельными cp (issue_cert.py:577–590), затем reloadcmd. Crash (OOM/SIGINT/reboot) МЕЖДУ копированиями → END: новый fullchain + старый/чужой privkey (или self-signed fallback перезаписал privkey.pem первым и умер до fullchain — cert_orchestrator.py:890–895 пишет оба файла напрямую, без tmp+rename). Следующий прогон: `_process_single_domain` Step 1 видит `fullchain.pem` parseable+LE+SAN+expiry → status=skipped/disk_synced (cert_orchestrator.py:453–460), фаза done → nginx при старте/reload падает «SSL_CTX_use_PrivateKey_file: key values mismatch» → сайт down.
- **Evidence:** ssl_certs.py:362–397 — 4 шага проверки (parseable/LE-issuer/SAN/expiry), ключ не участвует; cert_orchestrator.py:454–460 — решение только по fullchain.pem; issue_cert.py:583–588 — два раздельных файла + `systemctl reload nginx` уже ПОСЛЕ обеих записей.
- **Impact:** silent-stuck: система считает сертификат healthy, самовосстановления нет (ни пере-issue, ни alarm — источник disk_synced), TLS недоступен до ручного вмешательства. ACME rate-limit усугубляется: повторные issue после череды крэшей расходуют лимит (TRAP в bootstrap/AGENTS.md).
- **Minimal fix:** в Step 1 добавить проверку пары: privkey существует И pubkey(privkey)==pubkey(cert) (openssl pkey -pubout сравнение); mismatch/нет ключа → не skip, а путь restore/issue/self-signed.
- **Required test:** unit: cert_is_valid_pair(fullchain, privkey) False при подмене ключа; интеграция: обрыв между записями пары → следующий прогон НЕ даёт skipped.
- **Phase:** φ7 certificates, φ12 deploy-update (ssl-provision)

## DATA-702: статус «running» никогда не пишется; нет run-level лока — crash неотличим от «не начинали», повторный запуск конкурирует с догорающей мутацией
- **Severity:** MEDIUM · **Confidence:** HIGH
- **Files:** core/internal/bootstrap/lifecycle/state_machine.py · core/internal/bootstrap/lifecycle/state_store.py · **Symbols:** PHASE_STATUS_RUNNING (state_machine.py:260), StepState.status, save_state/FileLock · **Invariant:** «state.json отражает, что фаза выполняется прямо сейчас»
- **Violating scenario:** START: `make bootstrap-node` по SSH; обрыв SSH → SIGHUP убивает python посреди φ8. END: state.json показывает предыдущий статус (pending — «running» ни разу не присваивается), при этом dockerd продолжает серверную часть `compose up` (клиент убит — API-запросы уже приняты). Оператор перезапускает bootstrap → второй процесс выполняет ту же pending-фазу ПАРАЛЛЕЛЬНО с догорающими docker-операциями: гонки compose up/down на одних сервисах. FileLock сериализует только запись state.json (state_store.py:318–320), не исполнение фаз.
- **Evidence:** rg '"running"' по lifecycle — единственное совпадение: определение константы (state_machine.py:260), 0 присваиваний; flock — только вокруг save_state; run-level mutex в cli.py/main отсутствует.
- **Impact:** интерливинг частичных мутаций при самом частом crash-сценарии (обрыв SSH/Ctrl-C + retry); идемпотентность фаз гасит большинство эффектов, но compose-гонки и двойной apt/acme дают недетерминированные отказы без диагностического следа.
- **Minimal fix:** перед execute_phase писать status=running (+pid/started_at, атомарно), сбрасывать в pending при старте; advisory-lock файл run.lock на весь run (FileLock уже есть в shared).
- **Required test:** unit: kill между running-маркером и завершением → следующий load видит stale-running и перепрофилирует; тест конкурентного запуска: второй процесс блокируется локом, а не исполняет фазу параллельно.
- **Phase:** все фазы init/update (φ1–φ13)

## DATA-703: обрыв stdin при receive → EOFError вне except-множества: контракт JSON нарушен, диагностика — traceback вместо машиночитаемого отказа
- **Severity:** MEDIUM · **Confidence:** MEDIUM
- **Files:** core/internal/deploy/receive_flow.py · **Symbols:** ReceiveFlow.unpack, ReceiveFlow.run, _read_stdin_limited · **Invariant:** «любой отказ receive → JSON FAILED в stdout + exit 1»
- **Violating scenario:** START: CI/deploy-project льёт tar.gz в forced-command stdin; обрыв SSH посреди потока → `_read_stdin_limited` возвращает частичные байты (EOF — не ошибка чтения). unpack: `tarfile.open(mode="r:gz")` на усечённом gzip при extractall бросает **EOFError** (gzip-слой), который НЕ входит в `except (tarfile.TarError, OSError)` (receive_flow.py:592) → необработанный traceback, exit≠0 БЕЗ JSON. Частичный extract остаётся во временном staging и удаляется finally — mix-деплой НЕ происходит (fail-closed, позитив).
- **Evidence:** receive_flow.py:322–323 extractall из BytesIO; :520–535 пустой-payload отфильтрован, частичный — нет; :592 список типов без EOFError; finally:596–598 staging очищается.
- **Impact:** недоставленный payload детектируется (деплой не стартует), но CI-side deliver получает не-JSON → деградация отчётности («unexpected end of data» traceback в ssh stderr вместо FAILED-JSON); при других точках среза возможен tarfile.ReadError — поведение зависит от места обрыва.
- **Minimal fix:** добавить `(EOFError,)` в except run() (и/или ловить в unpack, возвращая False с причиной).
- **Required test:** unit: unpack(io.BytesIO(tar_bytes[:-100])) → типизированный отказ, run() печатает JSON FAILED, exit 1.
- **Phase:** деплой (receive-канал CI)

## DATA-704: замена payload — пакет per-file os.replace без транзакционности: crash посреди цикла оставляет смешанный target_dir и осиротевший backup
- **Severity:** MEDIUM · **Confidence:** HIGH
- **Files:** core/internal/deploy/receive_flow.py · **Symbols:** ReceiveFlow.deploy (staging_copy → os.replace цикл), payload-backup-dir · **Invariant:** «target_dir содержит целиком старую или целиком новую версию payload»
- **Violating scenario:** START: L1-гейт прошёл, backup сделан; цикл os.replace заменяет файлы по одному (receive_flow.py:443–460). OOM/SIGKILL на середине → END: docker-compose.yml новой версии + practices.lock/.env.platform старых (набор файлов несогласован); `finally rmtree(backup_dir)` не выполнен → мусорный payload-backup-* остаётся. Контейнеры продолжают работать со старой конфигурацией (compose up не дошёл) — runtime-mix нет, но диск в mix-состоянии.
- **Evidence:** receive_flow.py:437–462 — комментарий честно называет схему «per-file os.replace»; атомарен каждый файл, не набор; backup_dir создаётся mkdtemp ВНУТРИ target_dir (:427) и чистится только в finally (:485–486).
- **Impact:** до следующего receive converge/status/render-monitoring читают смешанный ai-platform.yaml/env; осиротевшие backup-каталоги накапливаются на диске. Next-run: полный новый payload затирает всё — recover одним повтором; «детектор недоставленного payload» на стороне VPS отсутствует (полагается на CI retry).
- **Minimal fix:** заменять набор через swap каталога (payload.new → rename) или маркер-манифест версии с проверкой консистентности набора перед deploy; backup выносить за target_dir.
- **Required test:** unit: исключение на i-м replace → целостность набора либо старая, либо новая версия; отсутствие backup-остатков после сбоя.
- **Phase:** деплой (receive → copy)

## DATA-705: неграфовое исключение фазы минует sm.save()/audit/notify — T9.6-след потери отсутствует
- **Severity:** MEDIUM · **Confidence:** HIGH
- **Files:** core/internal/bootstrap/lifecycle/cli.py · **Symbols:** _run_phases, _audit_failed, main · **Invariant:** «любое завершение фазы (ok/warn/fail) оставляет audit-след и сохранённый state»
- **Violating scenario:** START: фаза бросает generic-исключение (OSError/CalledProcessError/баг), не входящее в три перехвата _run_phases (PhaseDependencyError/PhasePreconditionError/PlatformFatalError, cli.py:827–857). Исключение выходит из _run_phases; main перехватывает только PlatformError (cli.py:475) → traceback, exit 1. END: status фазы остаётся pending (безопасное направление do→mark), НО `_audit_failed` не вызван, sm.save() не выполнен, Telegram-notify не отправлен — отказ невидим для audit-канала и оператора-нотификации.
- **Evidence:** cli.py:810–857 — ровно 3 except-ветки с _audit_failed+save; cli.py:471–478 — единственный except PlatformError; _call_with_retry ре-райзит исчерпанный/non-retryable exception как есть.
- **Impact:** расследование инцидента опирается только на stdout/stderr упавшей SSH-сессии (часто утрачены при обрыве); audit-log утверждает «последний run был успешным». Recovery поведением — re-run корректен, диагностика — нет.
- **Minimal fix:** четвертая ветка `except Exception` в _run_phases: пометить failed, _audit_failed, sm.save(), return 1 (KeyboardInterrupt — отдельно, без маскировки).
- **Required test:** unit: phase_func_override бросает RuntimeError → exit 1, state.errors содержит запись, audit_impl вызван с result="FAILED".
- **Phase:** все фазы init/update

## DATA-706: обработчики сигналов только в decrypt_secrets; SIGHUP/SIGKILL обходят очистку — temp AGE-ключ переживает смерть процесса, остальные мутации умирают без финализации
- **Severity:** MEDIUM · **Confidence:** HIGH
- **Files:** core/internal/secrets/decrypt_secrets.py · core/internal/shared/subprocess_io.py · core/internal/bootstrap/lifecycle/cli.py · **Symbols:** _signal_handler/atexit.register (decrypt_secrets.py:137–144), _TEMP_FILES/dd-wipe, start_new_session=True+killpg (subprocess_io.py:318,353) · **Invariant:** «temp-ключ уничтожается при любом терминале процесса; дети не переживают родителя бесхозными»
- **Violating scenario #1:** START: φ4 decrypt_secrets держит AGE-ключ в /dev/shm/platform-age-key-*.key. Обрыв SSH → **SIGHUP** — обработчик не зарегистрирован (только SIGTERM/SIGINT), дефолтное действие ядра убивает процесс мгновенно, atexit НЕ исполняется → plaintext-ключ остаётся в /dev/shm до ребута/ручной чистки. SIGKILL (OOM) даёт тот же результат даже при наличии хендлеров.
- **Violating scenario #2:** прочие мутаторы (lifecycle CLI, cert_orchestrator, receive_flow) сигналов не обрабатывают вовсе — смерть с полусделанной работой допустима лишь потому, что порядок do→mark делает re-run безопасным; но ничего не финализирует журналирование (см. DATA-705).
- **Evidence:** rg 'signal\.|atexit|SIGTERM|SIGINT' core/internal → единственный handler-файл decrypt_secrets.py:142–144; список сигналов без SIGHUP (:143–144); wipe только через _cleanup_temp_files (:119–126).
- **Impact:** окно существования plaintext мастер-ключа на ноде после каждого прерванного unlock (углубляет DR/threat-model секцию core/AGENTS.md); систематическая немониторимость умерших мутаций.
- **Minimal fix:** +`signal.signal(SIGHUP, _signal_handler)` в decrypt_secrets; post-mortem sweep `/dev/shm/platform-age-key-*` при старте φ4; опционально prctl PDEATHSIG для детей.
- **Required test:** unit: отправка SIGHUP процессу с temp-ключом → файл удалён; integration: повторный decrypt после симуляции kill стартует со свипом остатков.
- **Phase:** φ4 secrets_provision, φ9 secrets_update

---
### Матрица фаза × артефакт × crash × recovery (сводка по коду)
| Фаза | Артефакт | Crash-последствие | Recovery next-run |
|------|----------|-------------------|-------------------|
| φ1 system | пакеты pip/apt, python-deps.hash | half-installed; маркер пишется ПОСЛЕ установки (python_deps.py:599–610) | recover: hash-mismatch → переустановка; dpkg самодостаточен |
| φ4 secrets | secrets.env, tmp AGE-key | env атомарна (tempfile+rename); ключ-сирота в /dev/shm | recover: повторная расшифровка; утечка ключа — DATA-706 |
| φ7/φ12 certs | privkey/fullchain пара | mismatch/неполная пара при install-cert/self-signed | **silent-stuck** — skip по «valid disk», DATA-701 |
| φ8/φ12 deploy | контейнеры compose | dockerd дозавершает up без журнала; часть сервисов новых | recover: re-run фазы/compose up сходится; параллелизм — DATA-702 |
| receive | payload-файлы проекта | усечённый tar → EOFError (fail-closed); mix при смерти в replace-цикле | recover повторным push; детектора на VPS нет — DATA-703/704 |
| state.json | checkpoint'ы | атомарная запись+flock; corrupt → StateCorruptError/--force | recover: явная ошибка, тихого сброса нет |
