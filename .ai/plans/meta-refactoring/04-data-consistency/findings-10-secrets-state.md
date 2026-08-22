# Secrets & sensitive state audit

Метод: статический обход unlock-цикла (`decrypt_sectors.py` → `node_detect` → `lib/secrets.sh` → φ4/φ9 → `secrets_manager`), каналов доставки core (core_deliverer, CI core-deploy.yml), DR-процедуры (`age_key_backup`) и manifest-тройке (secret-definitions ↔ generated manifest ↔ enc.yaml + gates). Поверхности утечки искал grep'ом argv/stdout/logger-интерполяций; значения секретов не воспроизводятся. Unlock-атомарность файла корректна (atomic replace 0600, temp-ключ tmpfs 0600 + dd-wipe, atexit/SIGTERM; остаток: SIGKILL оставляет 0600-ключ на /dev/shm до reboot — задокументировано в threat-model).

## DATA-1001: AGE мастер-ключ в argv ssh-команды (ps-visible локально и на ноде) и в dry-run логах
- **Severity:** HIGH · **Confidence:** HIGH
- **Files:** core/internal/bootstrap/core_deliverer.py · .github/workflows/core-deploy.yml · **Symbols:** `deliver_fallback` · **Invariant:** plaintext-ключ не попадает в process argv/логи (DD5-4, инвариант 1 age_key_backup)
- **Violating scenario:** START → `make core-deliver` (fallback-deliver) или CI core-deploy step 5 → `update_cmd = ssh … "cd /opt/platform && AGE_SECRET_KEY='<мастер-ключ>' make node-update NODE=…"` → всё время node-update (минуты) ключ виден в `ps aux` дважды: в argv локального ssh и в `bash -c` на ноде; при `DRY_RUN=1` та же строка с ключом печатается в stderr-лог (IMP:8). END: компрометация мастер-ключа через процесс-листинг или журнал.
- **Evidence:** core_deliverer.py:637 `age_env = f"AGE_SECRET_KEY='{age_secret_key}' "`; :642 встраивание в команду; :646 dry-run печатает `" ".join(update_cmd)`; core-deploy.yml:263 — тот же паттерн с `${{ secrets.AGE_SECRET_KEY }}`.
- **Impact:** утечка мастер-ключа = расшифровка всех секретов ноды (верх threat-model).
- **Minimal fix:** передавать ключ вне argv (stdin/heredoc: `ssh … "make node-update" <<EOF` c env-присваиванием внутри удалённого скрипта, либо scp на tmpfs+shred); в dry-run маскировать значение (_mask_key-паттерн).
- **Required test:** расширить `tests/gates/test_gate_local_path_in_remote.py`: ни одна remote-строка не содержит `AGE_SECRET_KEY='<не-пусто>'`; unit: dry-run-вывод не содержит ключ.
- **Phase:** immediate

## DATA-1002: φ4 продолжает работу при нечитаемом secrets.env — bootstrap с частичным набором секретов
- **Severity:** HIGH · **Confidence:** HIGH
- **Files:** core/internal/bootstrap/lifecycle/helpers/secrets.py · core/internal/bootstrap/lifecycle/secrets_manager.py · **Symbols:** `ensure_secrets_exist`, `source_secrets_env` · **Invariant:** «Decryption failure is FATAL» (MODULE_CONTRACT phases/secrets.py) должно покрывать весь provision-контур
- **Violating scenario:** START → φ4: decrypt OK, но secrets.env бит/не парсится (усечение, сбой квотинга `_yaml_to_env`) → `parse_secrets_env` кидает ValueError/OSError → `source_secrets_env` возвращает `{}` (warning) → `ensure_secrets_exist` глотает исключение как warning → autogen заполняет только tier=generated + master/derived креды; операторские required/sops (POSTGRES_PASSWORD, S3_*, TELEGRAM_*) отсутствуют → модули деплоятся с пустыми env. END: стек «зелёный», сервисы деградируют; state.json помечает φ4 done → resume не перевыполняет.
- **Evidence:** helpers/secrets.py:122-123 `except Exception … logger.warning("Failed to source secrets.env…")`; :134-135 — autogen-fail тоже warning; FATAL — только decrypt-шаг; secrets_manager.py:196-201 — parse-ошибка → `{}`.
- **Impact:** тихая потеря набора секретов при живом enc-файле; рассинхрон state ↔ реальность.
- **Minimal fix:** различать «enc-файла нет» (skip, валидно) от «env есть/ожидался, но parse/write fail» → PlatformFatalError; пост-условие: parsed-набор ⊇ {manifest: required & source=sops}.
- **Required test:** unit: битый secrets.env + валидный enc → φ4 FATAL; happy-path LDD IMP:9.
- **Phase:** next wave

## DATA-1003: Ротация мастер-ключа неатомарна; age-key-backup не привязан к состоянию ротации (DR-окно)
- **Severity:** HIGH · **Confidence:** MEDIUM
- **Files:** core/internal/deploy/age_key_backup.py · core/AGENTS.md §Ротация/§DR · core/internal/shared/node_detect.py · **Symbols:** `run_backup`, `detect_age_key` · **Invariant:** off-node backup обязан восстанавливать способность расшифровать ТЕКУЩИЕ enc-файлы
- **Violating scenario:** START → оператор обновил ключ в одном звене цепочки (GitHub Secret или ~/.config/age/keys.txt) ДО перешифровки enc-файлов → `make age-key-backup` берёт первый непустой источник node_detect (уже НОВЫЙ ключ) → шифрует/выгружает его → действующие `*.enc.yaml` всё ещё под старым ключом; по чек-листу старый shred → END: DR-backup бесполезен для текущих секретов; loss при reprovision. Отдельно: `sops update-keys` выполняется вручную по файлам — crash посреди = смесь old/new получателей, детектируемая только decrypt-фейлом φ4/φ9 в момент деплоя.
- **Evidence:** age_key_backup.py:438-447 — ключ из цепочки без сверки recipient'ов/metadata enc-файлов; core/AGENTS.md «Чек-листы ротации»: последовательность ручная, verify-фаза — один `make secrets-unlock`.
- **Impact:** молчаливая непригодность DR-бэкапа; mixed-state обнаруживается постфактум на проде.
- **Minimal fix:** в `run_backup` перед exit 0 — контрольная расшифровка одного `*.enc.yaml` этим же ключом (или сверка age-pubkey recipient с sops-metadata всех enc-файлов ноды); runbook-скрипт ротации с атомарным verify-all шагом до shred.
- **Required test:** integration (tmp_path): backup «нового» ключа при old-recipient enc → FAIL; DR-drill тест restore-first.
- **Phase:** next wave

## DATA-1004: Значения секретов в argv subprocess (sops --set, openssl passwd) + non-fatal persist → enc/env drift
- **Severity:** MED · **Confidence:** HIGH
- **Files:** core/internal/bootstrap/lifecycle/secrets_manager.py · core/internal/shared/crypto.py · **Symbols:** `_persist_to_sops`, `hash_apr1` · **Invariant:** секретные значения не передаются через command line
- **Violating scenario:** START → первый bootstrap генерирует LITELLM_MASTER_KEY/LANGFUSE_* → `subprocess.run(["sops","--set", f'["{var}"] "{value}"', enc_file])` → значение читаемо в `ps aux` на время вызова; htpasswd-путь: PLATFORM_MASTER_PASSWORD аргументом `openssl passwd -apr1`. Параллельно: отказ `sops --set` — non-fatal warning → значение живёт в secrets.env, но НЕ в SOPS → после reprovision секрет перегенерится другим → расхождение кредов. END: leak-окно + рассинхрон enc↔env.
- **Evidence:** secrets_manager.py:403 (argv-интерполяция значения), :409-414 (warning-only); crypto.py:83-86 `cmd.append(password)`; CLI `htpasswd --password` тоже argv (secrets_manager.py:913-914).
- **Impact:** краткое окно чтения значения любым локальным процессом; drift персистентный.
- **Minimal fix:** значение через stdin/env sops (`sops --set '["VAR"]'` + stdin; exec-env); openssl: `printf %s | openssl passwd -apr1 -stdin`; htpasswd CLI — читать пароль из env/файла.
- **Required test:** static gate: в secrets-домене нет интерполяции значений в subprocess list-args.
- **Phase:** backlog

## DATA-1005: Свежий decrypt перекрывается устаревшим os.environ (`if k not in os.environ`)
- **Severity:** MED · **Confidence:** HIGH
- **Files:** core/internal/bootstrap/lifecycle/helpers/secrets.py · core/internal/bootstrap/lifecycle/secrets_manager.py · **Symbols:** `ensure_secrets_exist` Step 2, `ensure_secrets` Step 1 · **Invariant:** после φ9 re-source значения secrets.env — источник истины
- **Violating scenario:** START → CI/оператор экспортирует VAR (GHCR_PULL_TOKEN, TELEGRAM_*) в окружение node-update → φ9 успешно расшифровал ОБНОВЛЁННОЕ значение → оба source-цикла пропускают ключи, уже присутствующие в os.environ процесса → compose получает старое env-значение. END: ротация секрета через SOPS молча не вступает в силу; unlock зелёный, эффекта нет.
- **Evidence:** helpers/secrets.py:117-119 `for k,v in env_vars.items(): if k not in os.environ: os.environ[k]=v`; идентично secrets_manager.py:458-460.
- **Impact:** тихий stale-secret, трудно диагностируемый (все проверки «зелёные»).
- **Minimal fix:** после успешного decrypt — file-wins семантика для decrypted-набора (перезаписывать os.environ), explicit override-список только для известных operator-переменных (NODE_NAME, SECRETS_ENV_FILE…).
- **Required test:** unit: env OLD + secrets.env NEW → после ensure `os.environ[var]==NEW`.
- **Phase:** next wave

## DATA-1006: Manifest-дрейф: enc.yaml ↔ secrets-manifest не проверяется никем; glob-first при нескольких enc-файлах
- **Severity:** MED · **Confidence:** HIGH
- **Files:** core/internal/secrets/decrypt_secrets.py · tests/gates/test_gate_manifests_up_to_date.py · core/secrets-manifest.yaml · **Symbols:** `resolve_enc_path` · **Invariant:** тройка SoT(definitions) ↔ generated(manifest) ↔ реальные sops-файлы согласована
- **Violating scenario:** START → имя секрета есть в manifest (gate byte-check generated-vs-SoT зелёный), но ключ отсутствует в `node-configs/<node>/secrets/<node>.enc.yaml` → unlock успешен с частичным набором; отсутствие ловится лишь deploy-time `check_runtime_env` — и только для зарегистрированных consumers. Если в secrets dir оказалось два `*.enc.yaml` (артефакт ротации/ручной копии) — `sorted(glob)[0]` выбирается молча. END: silent partial-secret set, детект в рантайме ноды, не в CI.
- **Evidence:** decrypt_secrets.py:389-391 `matches = sorted(pathlib.Path(secrets_dir).glob("*.enc.yaml")); return str(matches[0])`; gates: `rg enc.yaml tests/gates/` — 0 совпадений; test_gate_manifests_up_to_date.py:52 покрывает только definitions↔generated.
- **Impact:** третий источник triple-source-модели остаётся вне enforce-контура (инвариант 11 нарушается по духу).
- **Minimal fix:** post-unlock verifier: KEY-set secrets.env ⊇ {manifest | tier=required ∧ source=sops}; FATAL/warning при >1 `*.enc.yaml` в secrets dir.
- **Required test:** unit: manifest требует X, decrypted YAML без X → verifier RED; два enc-файла → ошибка резолва.
- **Phase:** backlog
