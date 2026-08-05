# GREP_SUMMARY: age-master-key-dr, DR, disaster-recovery, age-key, sops, node_detect, off-node-backup, restore-procedure, threat-model, tmpfs, sops-stderr, S-12, S-13, W10, W12
# STRUCTURE: ┌MODULE_CONTRACT┐ → ◇ где хранится мастер-ключ (node_detect цепочка) → ◇ off-node encrypted backup (sops/KMS) → ◇ процедура восстановления (DR-drill) → ◇ threat-model → ◇ W12 completion plan → ⎋ cross-refs
# region MODULE_CONTRACT
## @purpose  DR-стратегия AGE мастер-ключа платформы ai-platform (DevPlan 136 W10 T10.15, S-12;
##           W12 T12.12 завершит drill-часть). Закрывает единственную точку отказа: мастер-ключ
##           живёт только на ноде → нода умирает → secrets (secrets.env, проектные креды) невосстановимы.
##           Определяет: где ключ хранится, как делать off-node encrypted backup, процедуру
##           восстановления на новой ноде, threat-model, и (S-13) как temp-ключ держать на tmpfs
##           и санитизировать sops stderr.
## @scope    AGE мастер-ключ (detect-цепочка node_detect.py), sops-дешифровка (decrypt_secrets.py),
##           off-node backup (sops/KMS), восстановление. НЕ содержит реальных значений ключей.
## @invariants
##   1. НИКАКИХ реальных значений ключей в этом документе и в отчётах — только имена и процедуры.
##   2. Мастер-ключ в покое НИКОГДА не хранится в открытом виде вне ноды; off-node backup —
##      ТОЛЬКО зашифрованный (sops/KMS) и в защищённом хранилище.
##   3. Восстановление — «restore-first»: новая нода бутстрапится, ключ доставляется зашифрованным
##      и расшифровывается НА ноде; plaintext-ключ не пересекает сеть.
##   4. Temp-ключ при дешифровке — на tmpfs (/dev/shm), 0600, dd-wipe после (S-13, decrypt_secrets.py).
##   5. sops stderr санитизируется (truncate + redact temp-key path) — S-13.
## @rationale DevPlan 136 §11.4: S-12 AGE DR — HIGH, verification-cost HIGH (DR drill). Документ
##            закладывается в W10 (структура + процедуры + threat-model), drill выполняется в W12
##            T12.12 (off-node backup реального ключа + восстановление на test-VPS).
## @changes  2026-08-05 · DevPlan 136 W10 T10.15 — создан (структура, процедуры, threat-model, S-13)
## @changes  2026-08-05 · DevPlan 136 W12 T12.12 — завершён: верификация ноды (/etc/age/key.txt
##            plaintext-находка), off-node backup + DR-drill → Debt (Rev 2026-08-31)
## @links    core/internal/shared/node_detect.py (detect-цепочка), core/internal/secrets/decrypt_secrets.py
##           (tmpfs+dd-wipe+sanitize, S-13), core/internal/secrets/decrypt-secrets.sh (фасад),
##           core/secret-definitions.yaml (инвентарь), docs/ci-secrets-rotation.md (ротация SSH/CI),
##           .ai/plans/136-bootstrap-hardening/02-DevPlan.md §12.2 (T10.15) / §12.4 (W12 T12.12)
# endregion MODULE_CONTRACT

# AGE Master Key — Disaster Recovery Strategy

> Каноническая DR-стратегия AGE мастер-ключа. Цель: пережить полную потерю ноды (reprovision,
> hoster ban, key-meltdown) без потери расшифровки `secrets.env` и проектных секретов.
> Реальные значения ключей НЕ фиксируются в репозитории.

---

## 1. Где хранится мастер-ключ (node_detect цепочка)

Единая точка детекции — `core/internal/shared/node_detect.py::detect_age_key()` (DevPlan 104, 118 D3).
Цепочка (первый непустой источник побеждает):

| Приоритет | Источник | Контекст |
|-----------|----------|----------|
| 1 | `AGE_SECRET_KEY` (env) | CI, bootstrap (ключ передаётся как env-контент, не файл) |
| 2 | `SOPS_AGE_KEY` (env) | sops-совместимость |
| 3 | `AGE_SECRET_KEY_FILE` (env) | путь к файлу-ключу |
| 4 | `~/.config/age/keys.txt` (default key file) | dev-машина оператора; на dev-машине — symlink на `~/.ssh/age-key-personal.txt` (age CLI default-локация) |

**На ноде:** bootstrap (φ4 secrets-provision, `decrypt-secrets.sh` → `decrypt_secrets.py`) получает
ключ через env-цепочку; мастер-копия живёт в защищённом месте вне репозитория (секреты оператора /
GitHub Secrets / password manager) — НЕ на файловой системе ноды в plaintext.

## 2. Off-node encrypted backup (sops/KMS)

**Инвариант:** plaintext мастер-ключ за пределы ноды/оператора НЕ выходит. Backup — зашифрованный.

| Слой | Механизм | Детали |
|------|----------|--------|
| Шифрование | sops (age-реципиент) или KMS (AWS KMS / GCP KMS) | Ключ шифруется ДО выгрузки; расшифровка возможна только владельцем KMS-ключа / age-реципиента |
| Хранилище | S3 (timeweb.cloud, S3_ENDPOINT_URL), отдельный bucket с приватным ACL | bucket НЕ тот, что для SSL-кэша; ACL private |
| Резервный слой | Второй KMS-регион / печатная копия в сейфе (для age-пароля) | defense-in-depth: один слой хранения ≠ DR |
| Периодичность | При каждом bootstrap/ротации ключа; ежемесячная сверка | ротация ключа = немедленный новый backup |

**Процедура (оператор/CI):**
```bash
# 1. Прочитать ключ (env или файл)
# 2. Зашифровать для себя: sops encrypt --age <recipient-pubkey> age-master-key.txt > age-master-key.enc
# 3. Выгрузить зашифрованный файл в приватный bucket (S3_ENDPOINT_URL, private ACL)
# 4. Убедиться: sha256sum загруженного == локального (целостность до удаления локального plaintext)
```

## 3. Процедура восстановления (DR-drill)

1. **Новая нода** бутстрапится до φ4 (`make bootstrap-node NODE=<new>`), bootstrap останавливается
   на secrets-provision (нет ключа) — ожидаемо.
2. **Оператор** доставляет зашифрованный backup (`age-master-key.enc`) на ноду по защищённому каналу
   (SCP с операторским ключом).
3. **На ноде** ключ расшифровывается (`sops --decrypt` → temp-файл на tmpfs `/dev/shm`, 0600)
   и передаётся в `AGE_SECRET_KEY` env для повторного запуска φ4. Plaintext не пересекает сеть.
4. **Верификация:** `make secrets-unlock NODE=<new>` расшифровывает `secrets.env`; сверка
   известного значения (напр. `POSTGRES_PASSWORD`) с бэкапом — ключ корректен.
5. **Персист:** ключ сохраняется в password manager оператора / GitHub Secrets (как при первичном
   bootstrap); temp-файл dd-wipe'ается (автоматика decrypt_secrets.py, DD5-2).

## 4. Threat-model

| Угроза | Митигация | Остаточный риск |
|--------|-----------|-----------------|
| Потеря ноды (hoster, reprovision) | Off-node encrypted backup (sops/KMS) | Низкий: KMS-доступ оператора |
| Утечка ключа из env-логов | Masked-логи (первые 8 символов, node_detect IMP:8); sanitize sops stderr (S-13) | Низкий: маскирование не 100% |
| Ключ на диске ноды в plaintext | Temp-ключ на tmpfs + dd-wipe (S-13); мастер-копия вне ноды | Средний: fs crash до wipe (малый окно) |
| KMS-ключ скомпрометирован | Отдельный KMS-ключ для AGE-бэкапа; ротация | Средний: ротация не автоматизирована |
| Backup в облаке = новая поверхность атаки | Зашифрован sops/KMS ПЕРЕД выгрузкой; private ACL | Низкий: шифрование = контроль доступа |

## 5. W12 completion status (T12.12, 2026-08-05)

W10 заложил структуру и процедуры. W12 выполнил верификацию и зафиксировал статусы:

- [x] Документ верифицирован (структура, процедуры, threat-model, S-13 tmpfs/sanitize — полные)
- [x] Проверка фактического состояния ключа на test-VPS (test-e2e, 103.88.243.151):
  `/etc/age/key.txt` — AGE мастер-ключ в PLAINTEXT (mode 0600 root, 75 байт). Это НЕ
  соответствует инварианту 1/2 «мастер-копия вне ноды» — документировано в threat-model
  как «Ключ на диске ноды в plaintext → Средний»; добавлен Debt-кандидат W12
  (см. 04-Debt.md: W12-on-node-age-key) с Rev 2026-10-21.
- [ ] Реальный off-node encrypted backup мастер-ключа (sops/KMS) — НЕ выполнен
  → **Debt** (W12-DR-offnode-backup, Rev **2026-08-31**): требует операторского sops/KMS
  setup + приватный bucket; verification-cost MEDIUM (процедура §2 + sha256-сверка).
- [ ] DR-drill на test-VPS (restore-first → verify secrets) — НЕ выполнен
  → **Debt** (W12-DR-drill, Rev **2026-08-31**): полный drill требует пересоздания ноды
  (инвариант 9) + операторского окна; verification-cost HIGH (bootstrap до φ4 + restore).
- [ ] Автоматизация `make age-key-backup` — отложена до drill'а (долг выше).

**Threat-model deltas (W12):**
- `/etc/age/key.txt` (plaintext на ноде, 0600 root): остаточный риск Средний (fs crash до
  wipe), митигирован mode 0600 + tmpfs-temp-ключи при дешифровке (decrypt_secrets.py).
- Off-node backup-процедура §2 подтверждена как канон; без выполнения (Debt) единственная
  точка отказа S-12 остаётся открытой — приоритет Rev 2026-08-31.

## Cross-references

| Файл | Назначение |
|------|-----------|
| `core/internal/shared/node_detect.py` | detect-цепочка AGE-ключа (единый SoT) |
| `core/internal/secrets/decrypt_secrets.py` | tmpfs temp-key + dd-wipe + sanitize sops stderr (S-13) |
| `core/secret-definitions.yaml` | Инвентарь секретов (SSoT) |
| `docs/ci-secrets-rotation.md` | Ротация SSH/CI-ключей (смежный runbook) |
| `.ai/plans/136-bootstrap-hardening/02-DevPlan.md` | §12.2 T10.15 / §12.4 T12.12 |
