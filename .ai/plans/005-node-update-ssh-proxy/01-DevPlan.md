# GREP_SUMMARY: devplan node-update ssh-proxy macOS webnames-api-key secrets-env sourcing
# $STATUS: ARCHIVED
$START_DEVPLAN

# DevPlan — node-update SSH proxy + WEBNAMES_API_KEY sourcing
# $STATUS: ARCHIVED

## $ARTIFACT_CONTRACT
- **PURPOSE:** Устранить две проблемы `make node-update`: (1) падение «must run as root» на macOS из-за отсутствия SSH-прокладки; (2) WARN `WEBNAMES_API_KEY not set` в ssl-provision из-за того, что secrets.env не подхватывается в update-режиме.
- **DESCRIPTION:** (1) node-update.sh получает SSH-проксирование по образцу bootstrap.sh — резолв node.yaml → extract_node_host → SSH exec без SCP. (2) update_step_3_ssl_provision() в node-lifecycle.sh сорсит `/run/platform/secrets.env` перед вызовом ssl-provision.sh, чтобы WEBNAMES_API_KEY был доступен.
- **RATIONALE:** Q: почему две проблемы в одном плане? A: они сцеплены — после добавления SSH-прокси в node-update.sh, AGE_SECRET_KEY будет передаваться через SSH (как в bootstrap), что также позволит decrypt secrets на VPS. Но immediate fix для WEBNAMES_API_KEY — сорсинг уже расшифрованного secrets.env (если он существует после init-бутстрапа). Q: почему не SCP в node-update? A: core-код уже на VPS (доставлен bootstrap'ом или core-deploy CI). SCP был бы избыточен и удлинял бы update.
- **ACCEPTANCE_CRITERIA:**
  1. `make node-update NODE=tronyx-vps` с macOS НЕ падает «must run as root» — SSH-проксирует на VPS
  2. `make node-update NODE=tronyx-vps DRY_RUN=1` печатает план шагов на VPS через SSH и завершается exit 0
  3. ssl-provision НЕ выдаёт WARN «WEBNAMES_API_KEY not set» при наличии secrets.env на VPS
  4. Если сертификат существует → ssl-provision SKIP (idempotent)
  5. Если secrets.env отсутствует (после ребута) → WARN с инструкцией, но не FAIL
  6. Существующие тесты проходят; новые contract-тесты покрывают SSH-proxy флаги
- **IMPLEMENTS:** Запрос владельца — «make node-update с macOS не должен падать»
- **IMPACTS:** core/entrypoints/node-update.sh, core/internal/bootstrap/remote-cmd.sh, core/internal/bootstrap/node-lifecycle.sh, Makefile
- **REQUIRES:** Локальный репозиторий + доступ к VPS по SSH (для верификации). SOPS-секреты на VPS должны быть расшифрованы (step_10 init bootstrap уже выполнен).

---

## 1. Requirements Analysis

### Key Success Criteria
1. **SSH-проксирование** — `make node-update` с macOS выполняет команды на VPS, а не локально
2. **Без SCP** — core-код не пересылается повторно (уже на VPS)
3. **WEBNAMES_API_KEY доступен** — secrets.env сорсится в update-режиме перед ssl-provision
4. **Обратная совместимость** — прямой вызов на VPS (без SSH_HOST) продолжает работать как локальный
5. **AGE key передаётся** — флаг AGE_SECRET_KEY_FILE пробрасывается в Makefile и через SSH

### Root Cause Analysis

| # | Проблема | Корневая причина | Где |
|---|----------|-----------------|-----|
| 1 | `make node-update` → «must run as root» | `node-update.sh` всегда делает `exec bash ... --mode update` локально. В отличие от `bootstrap.sh`, нет резолва SSH_HOST и SSH-проксирования. | `core/entrypoints/node-update.sh:102` |
| 2 | `ssl-provision WARN: WEBNAMES_API_KEY not set` | В update-режиме отсутствует шаг сорсинга secrets.env. Ключ расшифрован при init-бутстрапе (`step_10_decrypt_secrets` → `/run/platform/secrets.env`), но update-режим никогда не сорсит этот файл перед ssl-provision. | `core/internal/bootstrap/node-lifecycle.sh:652-702` (update_step_3_ssl_provision) |

---

## 2. Architecture Overview

### Draft Code Graph

```
core/entrypoints/node-update.sh          [MODIFY] +SSH proxy (~30 lines)
  ├── +resolve_node_yaml                 (reuse lib/node-resolver.sh)
  ├── +extract_node_host                 (reuse lib/node-resolver.sh)
  ├── +prepare_ssh_opts                  (reuse internal/bootstrap/scp-deliver.sh)
  ├── +build_update_ssh_cmd             (new in remote-cmd.sh or inline)
  └── +ssh exec || local fallback

core/internal/bootstrap/
├── remote-cmd.sh                        [MODIFY] +build_update_ssh_cmd() (~20 lines)
│                                         or: parameterize build_ssh_cmd() with --mode flag
└── node-lifecycle.sh                    [MODIFY] +source secrets.env in ssl step (~10 lines)

Makefile                                 [MODIFY] +AGE_SECRET_KEY_FILE pass-through
                                         +PLATFORM_ROOT export for node-resolver
```

### Step-by-Step Data Flow (after fix)

```
make node-update NODE=tronyx-vps [AGE_SECRET_KEY_FILE=...] [DRY_RUN=1]
  │
  └─ core/entrypoints/node-update.sh --node tronyx-vps [--dry-run]
       │
       ├─ source lib/node-resolver.sh
       ├─ resolve_node_yaml "tronyx-vps" → /Users/.../node-configs/tronyx-vps/node.yaml
       ├─ extract_node_host → 1.2.3.4 (VPS IP)
       │
       ├─ [SSH_HOST exists] ─────────────────────────────────────────┐
       │  ├─ prepare_ssh_opts (ssh-keygen -R + SSH_OPTS array)       │
       │  ├─ detect_age_key → AGE_SECRET_KEY                         │
       │  ├─ build_update_ssh_cmd → set -euo; export AGE_SECRET_KEY; │
       │  │    bash node-lifecycle.sh --mode update --node-name ...   │
       │  │    --node-yaml /opt/node-configs/.../node.yaml [--dry-run]│
       │  └─ ssh root@1.2.3.4 "${REMOTE_CMD}"                        │
       │                                                               │
       └─ [NO SSH_HOST] ─────────────────────────────────────────────┐
          └─ exec bash node-lifecycle.sh --mode update ... (local)    │
                                                                       │
On VPS: node-lifecycle.sh --mode update                                │
  ├─ 1. verify-core                                                    │
  ├─ 2. provision (networks + volumes)                                 │
  ├─ 3. ssl-provision ───────────────────────────────────────────────┐│
  │    ├─ source /run/platform/secrets.env (if exists)                ││
  │    │   → WEBNAMES_API_KEY exported                                ││
  │    ├─ cert exists? → SKIP (idempotent)                            ││
  │    └─ cert missing + key present → issue new cert                 ││
  ├─ 4. deploy-docker                                                  │
  ├─ 5. deploy-system                                                  │
  └─ 6. healthcheck                                                    │
```

---

## 3. Design Decisions

### D1 — SSH proxy: портировать логику bootstrap.sh, но без SCP
## @rationale Q: почему не вызвать bootstrap.sh с флагом --mode update? A: bootstrap.sh жёстко зашит на `--mode init` (строка 128: `exec "${NODE_LIFECYCLE}" "--mode" "init"`). Менять его контракт — ломать обратную совместимость. Портирование SSH-логики в node-update.sh (~30 строк) — минимальное изменение. Q: почему не SCP? A: core-код уже на VPS после bootstrap/core-deploy. SCP без необходимости замедляет update и создаёт race condition (если код в репо новее, чем доставленный core-deploy CI — нужно чтобы core-deploy шёл первым). Rejected: (a) добавить --mode флаг в bootstrap.sh — мутация контракта инит-режима; (b) сделать SCP в node-update — дублирует механизм доставки, увеличивает время update.

### D2 — remote-cmd.sh: build_update_ssh_cmd() отдельно от build_ssh_cmd()
## @rationale Q: почему не параметризовать build_ssh_cmd() флагом --mode? A: build_ssh_cmd() жёстко зашит на `--mode init` (строка 74), `--resume`, `--owner-key`. Update-режим не требует --resume (checkpoint'ы update-шагов независимы) и --owner-key (используется только для создания пользователя в init). Новая функция build_update_ssh_cmd() чище: не тянет инит-специфичные аргументы. При этом разделяем сигнатуры явно. Rejected: (a) параметризация — 4 условных блока внутри одной функции; (b) инлайн в node-update.sh — дублирование printf '%q' логики.

### D3 — WEBNAMES_API_KEY: сорсить secrets.env, не перешифровывать
## @rationale Q: почему не запустить step_10_decrypt_secrets в update-режиме? A: это требует AGE_SECRET_KEY и пересоздаёт `/run/platform/secrets.env` (tmpfs). При наличии secrets.env с прошлого init-бутстрапа — это избыточно. Сорсинг существующего файла — O(1) операция. Если файла нет (ребут VPS) — ssl-provision всё равно скипнет при наличии сертификата. Если и сертификата нет, и secrets.env нет — это уже сценарий, требующий ручного вмешательства (нужен AGE key для расшифровки). Rejected: (a) вызов decrypt-secrets в update — требует AGE key, замедляет; (b) перенос WEBNAMES_API_KEY в node.yaml — смешивает секреты с конфигурацией (security anti-pattern).

### D4 — AGE_SECRET_KEY_FILE в Makefile для node-update
## @rationale Q: зачем передавать AGE key в node-update, если он не SCP'ит? A: для сценария, когда secrets.env отсутствует на VPS (после ребута) и нужен ре-дешифровка. Также для будущего использования (deploy-modules может требовать секреты). Проброс флага через Makefile унифицирует интерфейс с bootstrap-node. Rejected: не передавать — теряем возможность ре-дешифровки секретов при необходимости.

---

## 4. Configuration DRY / Knowledge Dedup

- **SSH host resolution:** единственный источник — `lib/node-resolver.sh` → `extract_node_host()` (переиспользуется, не дублируется)
- **SSH_OPTS:** единственный источник — `scp-deliver.sh` → `prepare_ssh_opts()` (переиспользуется)
- **Secrets env path:** `/run/platform/secrets.env` → константа в `lib/secrets.sh` (SECRETS_ENV_FILE). Сорсинг в update-режиме ссылается на ту же переменную, не хардкодит путь.
- **WEBNAMES_API_KEY:** единственный источник — SOPS-зашифрованный secrets file. Не дублируется в node.yaml, не хардкодится.
- **build_ssh_cmd / build_update_ssh_cmd:** раздельные функции с разными сигнатурами — общий паттерн printf '%q', но разный набор аргументов. Дублирования бизнес-логики нет.

---

## 5. $TASKS

| ID | Задача | Файлы | Acceptance | Deps | Cx |
|----|--------|-------|-----------|------|----|
| T1 | **SSH proxy в node-update.sh:** (a) source lib/node-resolver.sh + scp-deliver.sh; (b) resolve_node_yaml → extract_node_host; (c) если SSH_HOST — prepare_ssh_opts + build_update_ssh_cmd + ssh exec; (d) если нет — локальный exec как сейчас; (e) поддержка --dry-run (печать SSH-команды); (f) detect_age_key (портировать из bootstrap.sh или вынести в lib). Обновить MODULE_CONTRACT @invariants: теперь entrypoint поддерживает SSH-проксирование. | core/entrypoints/node-update.sh, tests/test_node_lifecycle_static.py (extend contract test) | `make node-update NODE=tronyx-vps DRY_RUN=1` печатает SSH-команду; contract-тест: node-update.sh содержит resolve_node_yaml + extract_node_host; shellcheck чист | — | 5 |
| T2 | **build_update_ssh_cmd() в remote-cmd.sh:** (a) новая функция build_update_ssh_cmd(node_name, age_key, passthrough_args); (b) строит: `set -euo pipefail && export AGE_SECRET_KEY=... && bash node-lifecycle.sh --mode update --node-name ... --node-yaml ... [--dry-run]`; (c) printf '%q' для всех аргументов; (d) --node-yaml путь = `/opt/node-configs/<node>/node.yaml` (VPS-путь). | core/internal/bootstrap/remote-cmd.sh | static: функция существует, содержит --mode update; shellcheck чист | — | 3 |
| T3 | **Сорсинг secrets.env в update_step_3_ssl_provision():** (a) перед вызовом ssl-provision.sh: проверить `/run/platform/secrets.env`; (b) если существует → source (set -a/+a); (c) если WEBNAMES_API_KEY появился → INFO-лог; (d) если файла нет → WARN «secrets.env missing — cert renewal may fail if cert expires»; (e) НЕ фейлить — ssl-provision сам скипнет при наличии сертификата. | core/internal/bootstrap/node-lifecycle.sh | `grep 'secrets.env'` в update_step_3_ssl_provision; `grep 'WEBNAMES_API_KEY loaded'` для успешного кейса; существующие тесты проходят | T1 (SSH fix deployed first) | 2 |
| T4 | **Makefile: AGE_SECRET_KEY_FILE + PLATFORM_ROOT для node-update:** (a) добавить `AGE_SECRET_KEY_FILE` поддержку в node-update target (аналогично bootstrap-node); (b) передача `--age-secret-key-file` в node-update.sh; (c) export PLATFORM_ROOT для резолва node.yaml. | Makefile | `make node-update NODE=x AGE_SECRET_KEY_FILE=... DRY_RUN=1` не падает на парсинге аргументов | T1 | 2 |
| T5 | **Обновить тесты + провалидировать:** (a) расширить test_node_lifecycle_static.py: contract-тест проверяет, что node-update.sh содержит SSH-логику (grep resolve_node_yaml, extract_node_host, SSH_HOST); (b) test_entrypoints.py: новый тест `test_node_update_has_ssh_proxy`; (c) прогон `make test MARKER=contract` + `make test MARKER=static`; (d) shellcheck на всех изменённых .sh файлах. | tests/test_node_lifecycle_static.py, tests/test_contract_entrypoints.py | `python -m pytest tests/ -m contract -v` PASS; `python -m pytest tests/ -m static_audit -v` PASS; shellcheck 0 errors | T1–T4 | 3 |

### Critical Path
T1 → T3 → T5 (остальные параллельны)

Merge-rule: T1+T2 — разные файлы, но T2 вызывается из T1 → одна код-сессия. T3 — тот же node-lifecycle.sh, но концептуально независимая задача (@keep_separate).

---

## 6. Acceptance Criteria

| # | Критерий | Проверка |
|---|----------|---------|
| AC1 | `make node-update NODE=tronyx-vps DRY_RUN=1` печатает SSH-команду (не падает «must run as root») | ручной прогон с macOS |
| AC2 | `make node-update` без SSH_HOST на VPS → локальный exec (обратная совместимость) | static-анализ: fallback-ветка `if [[ -z "${SSH_HOST}" ]]` существует |
| AC3 | `update_step_3_ssl_provision` сорсит secrets.env; WEBNAMES_API_KEY доступен | grep `secrets.env` + grep `WEBNAMES_API_KEY` в функции |
| AC4 | Сертификат существует → SKIP, WARN не появляется | ручной прогон `make node-update` на VPS с существующим сертификатом |
| AC5 | Все contract + static тесты зелёные | `python -m pytest tests/ -m "contract or static_audit" -v` |
| AC6 | shellcheck 0 errors на всех изменённых файлах | `shellcheck core/entrypoints/node-update.sh core/internal/bootstrap/remote-cmd.sh` |

---

## 7. File Manifest

| Файл | Действие | Строк (оценка) |
|------|----------|---------------|
| `core/entrypoints/node-update.sh` | MODIFY — добавить SSH-proxy логику | +35 |
| `core/internal/bootstrap/remote-cmd.sh` | MODIFY — build_update_ssh_cmd() | +25 |
| `core/internal/bootstrap/node-lifecycle.sh` | MODIFY — source secrets.env в ssl step | +12 |
| `Makefile` | MODIFY — AGE_SECRET_KEY_FILE pass-through | +3 |
| `tests/test_node_lifecycle_static.py` | MODIFY — contract test for SSH proxy | +20 |
| `tests/test_contract_entrypoints.py` | MODIFY — test_node_update_has_ssh_proxy | +15 |

---

## 8. $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| tests/test_node_lifecycle_static.py | test_node_update_has_ssh_proxy | node-update.sh содержит resolve_node_yaml, extract_node_host, SSH_HOST fallback | node-update.sh entrypoint |
| tests/test_node_lifecycle_static.py | test_remote_cmd_has_update_mode | remote-cmd.sh содержит build_update_ssh_cmd с --mode update | remote-cmd.sh |
| tests/test_node_lifecycle_static.py | test_update_ssl_step_sources_secrets_env | update_step_3_ssl_provision содержит source /run/platform/secrets.env | node-lifecycle.sh update |
| tests/test_contract_entrypoints.py | test_entrypoint_flags_contract (extend) | node-update.sh форвардит --age-secret-key-file в accepted флаги | entrypoint↔internal contract |

Все тесты — маркер `static_audit` или `contract`, с LDD-телеметрией (IMP:7-10, caplog).

---

## 9. $PARALLEL_GROUPS

### Wave 1 (основная реализация)
- Tasks: T1, T2, T4
- T1+T2 (связаны: T2 вызывается из T1) + T4 (Makefile, независим)
- Command: `coder Read .ai/plans/005-node-update-ssh-proxy/01-DevPlan.md, implement Wave 1: T1, T2, T4`

### Wave 2 (ssl fix + тесты)
- Tasks: T3, T5
- T3 (node-lifecycle.sh, зависит от T1 концептуально — SSH fix должен быть принят) + T5 (тесты, зависит от T1–T4)
- Command: `coder Read .ai/plans/005-node-update-ssh-proxy/01-DevPlan.md, implement Wave 2: T3, T5`

---

## 10. Constraints / Out of scope

- **SCP в node-update не добавляется** — core-код доставляется bootstrap'ом или CI core-deploy. D1.
- **decrypt-secrets в update-режиме не добавляется** — только сорсинг существующего secrets.env. D3.
- **nginx restart loop / hermes image / Telegram** — покрыто DevPlan 002 (ssl fixes) и 004 (wave2 followups). Не дублируется.
- **S5 (docker kill restart) / conflicting server name** — out of scope (см. DevPlan 004 §7).
- **Tor proxy для update** — не требуется (Tor настраивается только в init-режиме).
- При добавлении новых тестов выполнить `make test-inventory-sync`.

---

## Next Steps

### Wave 1
```
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/005-node-update-ssh-proxy/01-DevPlan.md, implement Wave 1: T1 (SSH proxy in node-update.sh), T2 (build_update_ssh_cmd in remote-cmd.sh), T4 (Makefile AGE_SECRET_KEY_FILE pass-through)
```

### Wave 2
```
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/005-node-update-ssh-proxy/01-DevPlan.md, implement Wave 2: T3 (source secrets.env in ssl step), T5 (tests + validation)
```

$END_DEVPLAN
