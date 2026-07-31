$START_DEVPLAN
# DevPlan 102 — Secrets Library Migration Complete

$ARTIFACT_CONTRACT
PURPOSE:               Завершить миграцию `core/lib/secrets.sh` (291 LOC → ≤85 LOC):
                       вынести source+proxy-cleanup из `step_10_decrypt_secrets` в Python,
                       утоньшить `_ensure_htpasswd_generated` до фасада, исправить баг
                       идемпотентности в `secrets_manager._ensure_htpasswd()`.
DESCRIPTION:           Три параллельные модификации в secrets_manager.py: (1) новая функция
                       `cleanup_secrets_env()` + CLI-действие `cleanup` — чтение secrets.env,
                       условное удаление HTTP_PROXY/HTTPS_PROXY, атомарная запись;
                       (2) fix `_ensure_htpasswd()` — соль-экстракция при существующем файле
                       (порт TRAP[BUG] 2026-07-31 из shell); (3) CLI-действие `htpasswd`
                       для shell-фасада. Shell-файл: step_10 ≤15 LOC фасад, _ensure_htpasswd
                       ≤12 LOC фасад, unset_platform_proxy удалён, MODULE_CONTRACT ужат.
                       Обновление тестов test_status_page.py под тонкие фасады.
RATIONALE:             (1) 291 LOC lib-файла с бизнес-логикой — аномалия среди мигрированных lib.
                       (2) Две функции (_ensure_htpasswd_generated 62 LOC + step_10 46 LOC)
                       содержат source/sed/соль-логику, не охваченную Python-тестами.
                       (3) Python-версия _ensure_htpasswd имеет тот же баг случайной соли,
                       что и shell — fix в рамках миграции убивает двух зайцев.
                       (4) После миграции все lib-файлы с бизнес-логикой в Python (кроме
                       vps-readiness — отдельный бриф).
ACCEPTANCE_CRITERIA:   AC1: `cleanup_secrets_env()` в `secrets_manager.py` — чтение secrets.env,
                           фильтрация proxy-строк при `TOR_ENABLED != "true"`, атомарная запись
                        AC2: Shell `step_10_decrypt_secrets` ≤15 LOC — вызов `decrypt_secrets.py`
                            + `secrets_manager.py cleanup`
                        AC3: `lib/secrets.sh` общий размер ≤ 85 LOC (ужат MODULE_CONTRACT,
                            удалён unset_platform_proxy, утоньшён _ensure_htpasswd)
                        AC4: `declare -f` stub-guard сохранён (source-safe)
                        AC5: Поведение при отсутствии AGE_SECRET_KEY/SOPS_AGE_KEY идентично
                            (сохранено в shell-фасаде)
                        AC6: Идемпотентность htpasswd — после fix в Python, проверена тестом
                        AC7: `make gate MODE=fast` зелёный
IMPLEMENTS:            Brief 102 (`.ai/plans/102-secrets-lib-complete/01-Brief.md`)
IMPACTS:
                       - `core/lib/secrets.sh` (MODIFY: 291 → ≤85 LOC — тонкие фасады)
                       - `core/internal/bootstrap/lifecycle/secrets_manager.py` (MODIFY: +cleanup_secrets_env, +htpasswd CLI, fix _ensure_htpasswd idempotency)
                       - `tests/test_status_page.py` (MODIFY: адаптация TestHtpasswdGeneration под тонкий фасад)
                       - `tests/unit/test_secrets_env_cleanup.py` (NEW: тесты cleanup_secrets_env)
                       - `tests/unit/test_secrets_manager.py` (MODIFY: тест идемпотентности htpasswd)
REQUIRES:              `core/internal/secrets/decrypt_secrets.py` (существует, 380 LOC),
                       `core/internal/shared/crypto.py` (существует, 167 LOC),
                       `core/internal/shared/age_key.py` (существует, 134 LOC)
$END_ARTIFACT_CONTRACT

---

## 1. Problem Matrix

| # | Проблема | Статус | Решение |
|---|----------|--------|---------|
| P1 | `step_10_decrypt_secrets` содержит ~46 LOC source+sed логики после делегирования decrypt в Python | Подтверждено: L147 вызывает decrypt_secrets.py, L150-162 source + proxy cleanup — чистый shell | Вынести в `secrets_manager.cleanup_secrets_env()`, shell → ≤15 LOC фасад |
| P2 | `_ensure_htpasswd_generated` — 62 LOC shell с соль-экстракцией, помечен в бриф как «✅ в Python», но Python-дубликат (`_ensure_htpasswd`) имеет тот же баг случайной соли | Подтверждено: shell L190-251 vs Python L396-464; оба не используют фиксированную соль при проверке existing → идемпотентность сломана | Fix `_ensure_htpasswd()` в Python (порт TRAP[BUG] 2026-07-31), shell → ≤12 LOC фасад |
| P3 | `unset_platform_proxy` — dead code после миграции source в Python (Python не модифицирует вызывающий shell) | Подтверждено: grep — вызывается ТОЛЬКО из step_10 L156 | Удалить функцию и её MODULE_CONTRACT |
| P4 | 291 LOC lib-файла не проходит AC3 (≤100) без миграции _ensure_htpasswd | Подтверждено: даже с ужатым step_10, без миграции htpasswd → ~140 LOC | Мигрировать _ensure_htpasswd → ≤12 LOC фасад |
| P5 | Бриф содержит неверный путь `core/internal/bootstrap/decrypt_secrets.py` | Факт: реальный файл `core/internal/secrets/decrypt_secrets.py` | Исправить в IMPACTS DevPlan, отметить поправку |

---

## 2. Architecture Overview

### 2.1 Draft Code Graph

```xml
<code_graph>
  <!-- === MODIFIED: Python core === -->
  <entity id="secrets_manager_py" type="MODULE_PY" keywords="secrets-manager cleanup-secrets-env htpasswd-cli idempotency-fix">
    <annotation>core/internal/bootstrap/lifecycle/secrets_manager.py — 3 changes: +cleanup_secrets_env(), +htpasswd CLI action, fix _ensure_htpasswd() salt extraction</annotation>
    <crossLinks>
      <link target="shared_crypto_py" relation="calls_hash_apr1"/>
      <link target="secrets_env_parser_py" relation="calls_parse"/>
      <link target="shell_secrets_sh" relation="called_by_shell_facade"/>
    </crossLinks>
  </entity>

  <!-- === UNCHANGED: existing Python modules === -->
  <entity id="decrypt_secrets_py" type="MODULE_PY" keywords="sops age decrypt secrets-env atomic-write" status="UNCHANGED">
    <annotation>core/internal/secrets/decrypt_secrets.py — 380 LOC, вызывается из shell step_10. БЕЗ ИЗМЕНЕНИЙ.</annotation>
    <crossLinks>
      <link target="shell_secrets_sh" relation="called_by"/>
    </crossLinks>
  </entity>

  <entity id="shared_crypto_py" type="MODULE_PY" keywords="crypto htpasswd apr1 hash salt" status="UNCHANGED">
    <annotation>core/internal/shared/crypto.py — 167 LOC, hash_apr1/generate_htpasswd_entry. БЕЗ ИЗМЕНЕНИЙ.</annotation>
    <crossLinks>
      <link target="secrets_manager_py" relation="called_by_ensure_htpasswd"/>
    </crossLinks>
  </entity>

  <entity id="age_key_py" type="MODULE_PY" keywords="age-key detect-age-key fallback-chain" status="UNCHANGED">
    <annotation>core/internal/shared/age_key.py — 134 LOC, detect_age_key() chain. БЕЗ ИЗМЕНЕНИЙ.</annotation>
  </entity>

  <entity id="secrets_env_parser_py" type="MODULE_PY" keywords="secrets-env parser parse write merge" status="UNCHANGED">
    <annotation>core/internal/shared/secrets_env_parser.py. БЕЗ ИЗМЕНЕНИЙ.</annotation>
    <crossLinks>
      <link target="secrets_manager_py" relation="called_by_source_secrets_env"/>
    </crossLinks>
  </entity>

  <!-- === MODIFIED: Shell === -->
  <entity id="shell_secrets_sh" type="SHELL_LIB" keywords="secrets lib thin-facade step-10 htpasswd stub-guard">
    <annotation>core/lib/secrets.sh — 291→≤85 LOC: step_10(≤15), _ensure_htpasswd(≤12), step_12b(≈10), unset_platform_proxy REMOVED, declare -f PRESERVED</annotation>
    <crossLinks>
      <link target="secrets_manager_py" relation="delegates_to"/>
      <link target="decrypt_secrets_py" relation="delegates_to"/>
      <link target="state_machine_py" relation="called_by"/>
    </crossLinks>
  </entity>

  <entity id="state_machine_py" type="MODULE_PY" keywords="state-machine bootstrap decrypt-secrets" status="UNCHANGED">
    <annotation>core/internal/bootstrap/lifecycle/state_machine.py — _decrypt_secrets() calls bash -c "source secrets.sh && step_10_decrypt_secrets". БЕЗ ИЗМЕНЕНИЙ.</annotation>
    <crossLinks>
      <link target="shell_secrets_sh" relation="calls_via_subprocess"/>
    </crossLinks>
  </entity>

  <!-- === NEW: Tests === -->
  <entity id="test_cleanup_py" type="TEST" keywords="test secrets-env cleanup proxy tor-enabled atomic-write">
    <annotation>tests/unit/test_secrets_env_cleanup.py — NEW: cleanup_secrets_env() unit tests</annotation>
    <crossLinks>
      <link target="secrets_manager_py" relation="tests"/>
    </crossLinks>
  </entity>

  <entity id="test_status_page_py" type="TEST" keywords="test htpasswd generation idempotent thin-facade" status="MODIFIED">
    <annotation>tests/test_status_page.py — TestHtpasswdGeneration: адаптация под тонкий фасад → python3 secrets_manager.py htpasswd</annotation>
    <crossLinks>
      <link target="shell_secrets_sh" relation="tests_shell_facade"/>
      <link target="secrets_manager_py" relation="indirectly_tests"/>
    </crossLinks>
  </entity>

  <entity id="test_secrets_manager_py" type="TEST" keywords="test secrets-manager htpasswd idempotency salt" status="MODIFIED">
    <annotation>tests/unit/test_secrets_manager.py — NEW: test_ensure_htpasswd_idempotent (соль-экстракция)</annotation>
    <crossLinks>
      <link target="secrets_manager_py" relation="tests_idempotency_fix"/>
    </crossLinks>
  </entity>
</code_graph>
```

### 2.2 Step-by-Step Data Flow (после миграции)

```
make bootstrap-node NODE=<name>
  │
  └─► state_machine.py: _decrypt_secrets(core_dir)
        │
        └─► bash -c "
              export CORE_DIR=...
              source lib/logging.sh
              source lib/secrets.sh
              step_10_decrypt_secrets   ← SHELL FACADE ≤15 LOC
            "
              │
              ├─ 1. Проверка enc_file (shell)
              ├─ 2. AGE_SECRET_KEY ← SOPS_AGE_KEY fallback (shell)
              ├─ 3. exit 1 если ключа нет (shell)
              ├─ 4. python3 decrypt_secrets.py → secrets.env (дешифровка)
              └─ 5. python3 secrets_manager.py cleanup → proxy cleanup (Python)
                    │
                    ├─ parse secrets.env via secrets_env_parser
                    ├─ filter: удалить HTTP_PROXY/HTTPS_PROXY если TOR_ENABLED≠true
                    └─ atomic write (tmp + rename, 0o600)

  └─► state_machine.py: _ensure_secrets_exist(core_dir)
        │
        └─► secrets_manager.ensure_secrets()  ← без изменений
              │
              └─► _ensure_htpasswd()  ← ИСПРАВЛЕН: соль-экстракция
                    │
                    ├─ existing file? → extract salt from $apr1$SALT$...
                    ├─ generate_htpasswd_entry(email, password, salt=extracted)
                    ├─ compare → skip if matching
                    └─ write if changed/absent

Для standalone-вызова (test_status_page.py):
  bash -c "
    source lib/secrets.sh
    _ensure_htpasswd_generated   ← SHELL FACADE ≤12 LOC
  "
    │
    └─► python3 secrets_manager.py htpasswd --email ... --password ...
```

### 2.3 Target Shell Structure (≤85 LOC)

```
 1-3   #!/usr/bin/env bash + GREP_SUMMARY + STRUCTURE        (3)
 5-25  # region MODULE_CONTRACT — ужатый                         (21)
27-29  # GREP_SUMMARY + STRUCTURE дубль — удалены                (0, migrated to header)
31-33  declare -f stub-guard (TRAP[BUG] 2026-07-23)            (12, PRESERVED AC4)
35-50  step_10_decrypt_secrets — ≤15 LOC thin facade           (16, AC2)
52-63  _ensure_htpasswd_generated — ≤12 LOC thin facade        (12)
 65-75  step_12b_ensure_secrets — ≈10 LOC (без изменений)       (11)
        ИТОГО:                                                   ~80 LOC ≤ 85 ✓
        (точный расчёт: 3+21+12+16+12+11 = 75 LOC тела + ~5 blank lines)
```

**Удалено:**
- `unset_platform_proxy()` (3 LOC тела + 18 LOC договора = 21 строка)
- MODULE_CONTRACT — ужат с 59 до 21 строки (−38)
- Комментарии step_10 — с 25 до 5 строк (−20)
- Комментарии _ensure_htpasswd — с 18 до 5 строк (−13)
- Комментарии step_12b — с 26 до 5 строк (−21)

---

## 3. Design Decisions

### D1: Новая логика — в `secrets_manager.py`, не в новом модуле

## @rationale
**Q:** Почему `cleanup_secrets_env()` в `secrets_manager.py`, а не в `secrets_env_source.py` (как предполагал бриф)?
**A:** `secrets_manager.py` уже владеет (1) парсингом secrets.env через `source_secrets_env()` → `secrets_env_parser.parse()`, (2) атомарной записью secrets.env через `ensure_secrets()` (tmp+rename, 0o600), (3) знанием о структуре secrets.env (merge existing+generated). Proxy cleanup — третий метод работы с secrets.env, естественное расширение того же модуля. Создание отдельного `secrets_env_source.py` для одной функции нарушило бы DRY (дублирование парсинга/записи) и потребовало бы отдельный CLI. Инвариант shared/AGENTS.md: ≥2 потребителей для нового shared-модуля — здесь один потребитель (step_10).

### D2: AGE_SECRET_KEY/SOPS_AGE_KEY fallback остаётся в shell-фасаде

## @rationale
**Q:** Почему не перенести fallback в Python? `age_key.detect_age_key()` уже делает то же самое.
**A:** `detect_age_key()` используется в `decrypt_secrets.py` — он ДЕЛАЕТ fallback внутри Python-процесса. Но shell `step_10` делает fallback ДО вызова `decrypt_secrets.py` через `export AGE_SECRET_KEY="$SOPS_AGE_KEY"` — это модификация родительского shell-окружения. Если перенести fallback в Python, то `decrypt_secrets.py` будет вызван БЕЗ AGE_SECRET_KEY в env (только SOPS_AGE_KEY), а `detect_age_key()` внутри поймёт. Однако текущий контракт `decrypt_secrets.py` — он читает `AGE_SECRET_KEY` из env (через `detect_age_key()`), который уже проверяет всю цепочку. Так что технически можно убрать shell-fallback БЕЗ потери функциональности. НО: AC5/AC6 требуют ИДЕНТИЧНОГО поведения. Shell-фасад сохраняет явный export для прозрачности и обратной совместимости — если какой-то другой потребитель ожидает AGE_SECRET_KEY в env после step_10, он его получит.

### D3: `_ensure_htpasswd` идемпотентность — fix через соль-экстракцию (порт shell TRAP[BUG])

## @rationale
**Q:** Почему не delegat-нуть генерацию в crypto.py с авто-солью и принимать «всегда разный хеш»?
**A:** Заказчик явно требует идемпотентности (AC6 + test_htpasswd_generation_idempotent в test_status_page.py). При случайной соли `openssl passwd -apr1` каждый вызов генерирует разный `$apr1$SALT$HASH`, md5sum файла меняется → идемпотентность сломана. Shell-версия УЖЕ имеет fix (TRAP[BUG] 2026-07-31): извлечь соль из существующего файла, пересчитать entry с той же солью, сравнить. Python-версия этого fix НЕ имеет — портируем.

### D4: `unset_platform_proxy` удаляется полностью

## @rationale
**Q:** Может, оставить как no-op для обратной совместимости?
**A:** grep подтверждает: `unset_platform_proxy` вызывается ТОЛЬКО из `step_10_decrypt_secrets` (L156). После миграции source в Python, вызов исчезает. Определение функции без вызова — dead code. `install-acme.sh` ссылается на неё в комментариях («unset_platform_proxy already ran») — комментарии обновлять не требуется (они описывают исторический flow, который остаётся верным: proxy vars очищены ДО acme). Удаление безопасно.

### D5: `declare -f` stub-guard сохраняется без изменений (AC4)

## @rationale
**Q:** Зачем stub-guard, если state_machine.py уже source'ит logging.sh перед secrets.sh?
**A:** Stub-guard обеспечивает source-safe поведение: `source lib/secrets.sh` в ЛЮБОМ контексте (не только из state_machine) не падает с «step_start: command not found». Это контракт библиотеки — она должна быть безопасна для source'инга. TRAP[BUG] 2026-07-23 документирует баг, который stub-guard фиксит. Удаление stub-guard = регрессия бага.

---

## 4. Contracts

### 4.1 `cleanup_secrets_env(secrets_env_path, tor_enabled)` — сигнатура

```python
def cleanup_secrets_env(
    secrets_env_path: str,
    tor_enabled: str = "false",
) -> dict[str, str]:
    """Read secrets.env, conditionally strip proxy vars, write back atomically.

    ▶ ┌secrets_env_path┐ → ◇ parse → ◇ TOR_ENABLED≠"true"? → filter proxy →
      ⊕ atomic write (tmp+rename, 0o600) → ⎋ dict[str, str]

    Returns: parsed secrets dict AFTER cleanup.
    No-op if file doesn't exist (returns empty dict).
    Never raises — logs warnings on I/O errors.
    """
```

**CLI contract:**
```
python3 secrets_manager.py cleanup --secrets-env <path> [--tor-enabled <true|false>]
  exit 0: success (prints "OK" or "SKIP")
  exit 1: file not found or I/O error
```

### 4.2 `_ensure_htpasswd()` — fix contract

```python
# ⚠️ TRAP[BUG] · 2026-07-31 · P1 · Random salt breaks idempotency
# · Symptom: повторный вызов перезаписывает .htpasswd-platform (md5 меняется).
# · Root: crypto.generate_htpasswd_entry(email, password) без соли = случайный salt
# ·   каждый вызов → existing == expected всегда False → вечная перезапись.
# · Fix: при существующем файле извлекаем соль ($apr1$SALT$...), пересчитываем
# ·   entry с фиксированной солью, сравниваем.
# · Ported from: shell _ensure_htpasswd_generated() L221-241
```

### 4.3 Shell facade `_ensure_htpasswd_generated` — contract

```bash
_ensure_htpasswd_generated() {
    # Thin facade → secrets_manager.py htpasswd
    local email="${PLATFORM_MASTER_EMAIL:-}"
    local password="${PLATFORM_MASTER_PASSWORD:-}"
    if [[ -z "$email" || -z "$password" ]]; then
        log_step "htpasswd" "WARN" "Credentials not set — skipping htpasswd"
        return 1
    fi
    python3 "${CORE_DIR:-/opt/platform/core}/internal/bootstrap/lifecycle/secrets_manager.py" htpasswd \
        --email "$email" --password "$password" \
        --htpasswd-file "${HTPASSWD_FILE:-/run/platform/.htpasswd-platform}" || {
        log_step "htpasswd" "FAIL" "secrets_manager.py htpasswd failed"
        return 1
    }
    export HTPASSWD_FILE="${HTPASSWD_FILE:-/run/platform/.htpasswd-platform}"
}
```

### 4.4 Shell facade `step_10_decrypt_secrets` — contract

```bash
step_10_decrypt_secrets() {
    step_start "decrypt-secrets" "Decrypting SOPS/age secrets"
    local enc_file="${NODE_CONFIGS_DIR:-/opt/node-configs}/secrets/${NODE_NAME}.enc.yaml"
    if [[ ! -f "$enc_file" ]]; then
        step_skip "decrypt-secrets" "No encrypted secrets file at ${enc_file}"
        return 0
    fi
    # AGE key fallback (AC5/AC6)
    [[ -z "${AGE_SECRET_KEY:-}" ]] && [[ -n "${SOPS_AGE_KEY:-}" ]] && export AGE_SECRET_KEY="$SOPS_AGE_KEY"
    if [[ -z "${AGE_SECRET_KEY:-}" ]]; then
        log_step "decrypt-secrets" "FAIL" "AGE_SECRET_KEY not set but secrets file exists — aborting"
        exit 1
    fi
    export SECRETS_FILE="$enc_file"
    python3 "${CORE_DIR}/internal/secrets/decrypt_secrets.py" || exit 1
    python3 "${CORE_DIR}/internal/bootstrap/lifecycle/secrets_manager.py" cleanup \
        --secrets-env "${SECRETS_ENV_FILE:-/run/platform/secrets.env}" \
        --tor-enabled "${TOR_ENABLED:-false}" || exit 1
    step_done "decrypt-secrets" "Secrets decrypted (key wiped)"
}
```

---

## 5. $TASKS

### Task Dependency Graph

```
TASK-1 (idempotency fix) ──┬──► TASK-5 (shell htpasswd facade)
                            │
TASK-2 (cleanup_secrets_env)│
                            │
TASK-3 (cleanup CLI) ───────┤
                            │
TASK-4 (shell step_10 facade)┤
                            │
                            ├──► TASK-6 (rewrite secrets.sh) [depends: ALL above]
                            │
TASK-1 ──────────────────────┼──► TASK-7 (test_htpasswd_idempotent)
                            │
TASK-2 ──────────────────────┼──► TASK-8 (test_cleanup unit tests)
                            │
TASK-5 ──────────────────────┼──► TASK-9 (test_status_page adapt)
                            │
TASK-6 + TASK-7 + TASK-8 ────┴──► TASK-10 (gate MODE=fast)
```

### Task List

| ID | Описание | Владелец | Артефакт | Сложность | Зависимости |
|----|----------|----------|----------|:---------:|-------------|
| TASK-1 | Fix `_ensure_htpasswd()` идемпотентность: соль-экстракция из существующего файла, вызов `generate_htpasswd_entry(email, password, salt=extracted_salt)` | Coder | `secrets_manager.py` | 4 | — |
| TASK-2 | `cleanup_secrets_env(secrets_env_path, tor_enabled)`: parse → filter proxy vars → atomic write | Coder | `secrets_manager.py` | 5 | — |
| TASK-3 | CLI `cleanup` action: argparse subcommand, вызов `cleanup_secrets_env()` | Coder | `secrets_manager.py` | 2 | TASK-2 |
| TASK-4 | Shell `step_10_decrypt_secrets` тонкий фасад (≤15 LOC) — контракт §4.4 | Coder | `secrets.sh` | 3 | — |
| TASK-5 | Shell `_ensure_htpasswd_generated` тонкий фасад (≤12 LOC) — контракт §4.3 | Coder | `secrets.sh` | 2 | TASK-1 |
| TASK-6 | Rewrite `secrets.sh`: ужать MODULE_CONTRACT, удалить `unset_platform_proxy`, собрать фасады, проверить ≤85 LOC | Coder | `secrets.sh` | 4 | TASK-4, TASK-5 |
| TASK-7 | Тест идемпотентности htpasswd в `test_secrets_manager.py`: два вызова `_ensure_htpasswd()` → одинаковый md5 | Coder | `tests/unit/test_secrets_manager.py` | 3 | TASK-1 |
| TASK-8 | Unit-тесты `cleanup_secrets_env` в `tests/unit/test_secrets_env_cleanup.py`: proxy removal/keep, no-op на отсутствие файла, атомарность, edge cases | Coder | `tests/unit/test_secrets_env_cleanup.py` (NEW) | 4 | TASK-2 |
| TASK-9 | Адаптация `test_status_page.py::TestHtpasswdGeneration`: тесты должны работать с тонким shell-фасадом → python3 secrets_manager.py htpasswd | Coder | `tests/test_status_page.py` | 3 | TASK-5 |
| TASK-10 | `make gate MODE=fast` — верификация зелёного gate (AC7) | QA | gate output | 2 | TASK-6, TASK-7, TASK-8, TASK-9 |

**Сложность:** 1=тривиально, 3=средне, 5=существенно, 7=комплексно, 10=архитектурно.

---

## 6. $PARALLEL_GROUPS

### Wave 1 (независимые, нет общих файлов)
- **Tasks:** TASK-1, TASK-2, TASK-4
- **Rationale:** Три изолированных изменения: (1) fix _ensure_htpasswd в Python, (2) новая cleanup_secrets_env в Python, (4) новый shell-фасад step_10. Разные секции secrets_manager.py / secrets.sh, конфликтов нет.
- **Command:** `coder Read DevPlan.md, implement Wave 1: TASK-1, TASK-2, TASK-4`

### Wave 2 (зависимы от Wave 1, нет общих файлов между собой)
- **Tasks:** TASK-3, TASK-5, TASK-7, TASK-8
- **Rationale:** TASK-3 (CLI cleanup) зависит от TASK-2 (функция). TASK-5 (shell htpasswd) зависит от TASK-1 (fix). TASK-7 (тест идемпотентности) зависит от TASK-1 (fix). TASK-8 (тесты cleanup) зависит от TASK-2 (функция). Разные файлы — параллельно.
- **Command:** `coder Read DevPlan.md, implement Wave 2: TASK-3, TASK-5, TASK-7, TASK-8`

### Wave 3 (сборка + адаптация тестов)
- **Tasks:** TASK-6, TASK-9
- **Rationale:** TASK-6 (rewrite secrets.sh) зависит от TASK-4 (step_10 фасад) + TASK-5 (htpasswd фасад). TASK-9 (test_status_page) зависит от TASK-5. Разные файлы — параллельно.
- **Command:** `coder Read DevPlan.md, implement Wave 3: TASK-6, TASK-9`; затем QA запускает TASK-10: `make gate MODE=fast` (верификация зелёного gate)

### Wave 4 (gate-верификация, QA)
- **Tasks:** TASK-10
- **Rationale:** Зависит от всех предыдущих волн. Выполняется QA, не Coder.
- **Command:** `make gate MODE=fast` — проверка зелёного gate (AC7)

---

## 7. $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/unit/test_secrets_manager.py` | `test_ensure_htpasswd_idempotent` | Два вызова `_ensure_htpasswd()` с одинаковыми креденшелами → одинаковый md5sum htpasswd-файла (соль извлекается из первого вызова, второй переиспользует) | `secrets_manager._ensure_htpasswd` |
| `tests/unit/test_secrets_env_cleanup.py` | `test_cleanup_removes_proxy_when_tor_disabled` | secrets.env с HTTP_PROXY/HTTPS_PROXY + TOR_ENABLED=false → строки удалены после cleanup | `secrets_manager.cleanup_secrets_env` |
| `tests/unit/test_secrets_env_cleanup.py` | `test_cleanup_keeps_proxy_when_tor_enabled` | secrets.env с HTTP_PROXY/HTTPS_PROXY + TOR_ENABLED=true → строки сохранены | `secrets_manager.cleanup_secrets_env` |
| `tests/unit/test_secrets_env_cleanup.py` | `test_cleanup_noop_on_missing_file` | Вызов cleanup на несуществующем файле → возвращает {} без ошибки | `secrets_manager.cleanup_secrets_env` |
| `tests/unit/test_secrets_env_cleanup.py` | `test_cleanup_atomic_write_preserves_other_vars` | secrets.env с 10 переменными (включая proxy) → после cleanup сохранены 8 не-proxy переменных, файл не повреждён | `secrets_manager.cleanup_secrets_env` |
| `tests/unit/test_secrets_env_cleanup.py` | `test_cleanup_no_proxy_vars_unchanged` | secrets.env БЕЗ proxy-строк → файл побайтово идентичен после cleanup | `secrets_manager.cleanup_secrets_env` |
| `tests/test_status_page.py` | `test_htpasswd_generation_creates_valid_file` | Вызов shell-фасада `_ensure_htpasswd_generated` → файл создан, содержит email + `$apr1$` (адаптирован под тонкий фасад) | `secrets.sh::_ensure_htpasswd_generated` (shell facade) |
| `tests/test_status_page.py` | `test_htpasswd_generation_idempotent` | Два вызова shell-фасада → одинаковый md5sum (соль-фикс в Python) | `secrets.sh::_ensure_htpasswd_generated` (shell facade) |
| `tests/test_status_page.py` | `test_master_creds_fallback_resolution` | SERVICE_PASSWORD fallback → PLATFORM_MASTER_PASSWORD (без изменений) | `secrets.sh::_ensure_htpasswd_generated` (shell facade) |

**Стратегия тестирования AC5/AC6 (AGE_SECRET_KEY/SOPS_AGE_KEY):** поведение fallback-а проверяется на двух уровнях:
1. **Python-уровень:** `detect_age_key()` (цепочка AGE_SECRET_KEY → SOPS_AGE_KEY → AGE_SECRET_KEY_FILE) покрыт существующими тестами `test_decrypt_secrets.py` и `test_age_key.py`.
2. **Shell-уровень:** fallback в `step_10_decrypt_secrets` — чистый bash-builtin (2 строки: `[[ -z A ]] && [[ -n B ]] && export A="$B"`; `[[ -z A ]] && exit 1`), не бизнес-логика. Верифицируется через `make gate MODE=fast` (интеграционные тесты CI-окружения) и `test_status_page.py::TestHtpasswdGeneration` (косвенно — подтверждает source-safe поведение `secrets.sh` через `declare -f` stub-guard).
Отдельный юнит-тест на 2 строки bash-builtin не требуется — избыточен (Test Honesty R2: unfalsifiable assert на language guarantee).

**Фикстуры:** все тесты используют `tmp_path` (Test Honesty R1), НИКОГДА не используют реальные секреты. Для cleanup-тестов: secrets.env создаётся через `tmp_path / "secrets.env"` с fake `KEY=value` строками. Для htpasswd-тестов: `PLATFORM_MASTER_PASSWORD=test-password-123` (не секрет). LDD caplog IMP:9 проверяется в каждом тесте.

---

## 8. Acceptance Criteria (детально)

| AC | Критерий | Верификация |
|----|----------|-------------|
| AC1 | `cleanup_secrets_env()` в `secrets_manager.py` | TASK-2 + TASK-3: функция + CLI. TASK-8: 5 unit-тестов. `grep cleanup_secrets_env core/internal/bootstrap/lifecycle/secrets_manager.py` |
| AC2 | Shell `step_10_decrypt_secrets` ≤15 LOC (не считая комментарии) | TASK-4: `wc -l` на теле функции (от `step_10_decrypt_secrets() {` до `}`) ≤ 15 |
| AC3 | `lib/secrets.sh` ≤ 85 LOC | TASK-6: `wc -l core/lib/secrets.sh` ≤ 85 |
| AC4 | `declare -f` stub-guard сохранён | `grep "declare -f step_start" core/lib/secrets.sh` → found |
| AC5 | AGE_SECRET_KEY отсутствует → exit 1 (как раньше) | Shell-fallback: `[[ -z AGE_SECRET_KEY ]]` + `exit 1` — чистый bash. Python-часть: `test_decrypt_secrets.py`, `test_age_key.py`. Gate: `make gate MODE=fast` |
| AC6 | SOPS_AGE_KEY fallback → поведение идентично | Shell-fallback: `export AGE_SECRET_KEY="$SOPS_AGE_KEY"` — чистый bash. Python-часть: `detect_age_key()` chain в `test_decrypt_secrets.py`. Gate: `make gate MODE=fast` |
| AC7 | `make gate MODE=fast` зелёный | TASK-10: `make gate MODE=fast` → exit 0, все тесты PASS |

---

## 9. File Manifest

| # | Файл | Действие | Тип | Описание |
|---|------|:--------:|-----|----------|
| F1 | `core/internal/bootstrap/lifecycle/secrets_manager.py` | MODIFY | Python | +`cleanup_secrets_env()` (~40 LOC), +`htpasswd` CLI action (~15 LOC), fix `_ensure_htpasswd()` соль-экстракция (~10 LOC change) |
| F2 | `core/lib/secrets.sh` | MODIFY | Shell | 291→≤85 LOC: step_10 фасад, _ensure_htpasswd фасад, ужатый MODULE_CONTRACT, удалён unset_platform_proxy |
| F3 | `tests/unit/test_secrets_env_cleanup.py` | CREATE | Python | 5 unit-тестов `cleanup_secrets_env()` (~120 LOC) |
| F4 | `tests/unit/test_secrets_manager.py` | MODIFY | Python | +`test_ensure_htpasswd_idempotent` (~35 LOC) |
| F5 | `tests/test_status_page.py` | MODIFY | Python | Адаптация `TestHtpasswdGeneration`: тесты работают с тонким shell-фасадом |

---

## 10. Risks & Mitigations

| Риск | Вероятность | Impact | Mitigation |
|------|:----------:|:------:|------------|
| R1: `secrets_manager.py` CLI `htpasswd` ломает `test_status_page.py` (тест ждёт shell-поведение, получает Python) | LOW | MEDIUM | Тесты адаптируются в TASK-9 — вызов shell-фасада остаётся тем же, меняется только внутренняя реализация. Фасад возвращает те же exit codes. |
| R2: `_ensure_htpasswd` fix соль-экстракции ломает существующих потребителей (`ensure_secrets()` → `_ensure_htpasswd()`) | LOW | HIGH | `_ensure_htpasswd` уже вызывается из `ensure_secrets()` (L366). Fix ДОБАВЛЯЕТ соль-экстракцию при существующем файле — поведение при ПЕРВОМ вызове не меняется (соли ещё нет), при ПОВТОРНОМ — становится корректно идемпотентным (раньше было сломано). |
| R3: Удаление `unset_platform_proxy` ломает невидимого потребителя | LOW | MEDIUM | grep по всему проекту подтверждает: функция вызывается ТОЛЬКО из step_10 (L156). `install-acme.sh` ссылается в комментариях — не вызов. |
| R4: Shell `step_10` фасад падает на macOS (нет `realpath`, разные sed) | LOW | LOW | Фасад не использует специфичных GNU-утилит. `sed` не вызывается (proxy cleanup в Python). `python3` есть на всех платформах. |
| R5: Gate красный из-за ортогональных проблем (не связанных с 102) | MEDIUM | LOW | TASK-10 фиксирует фактическое состояние gate. Если gate красный по причинам вне скоупа → документируется в VerificationReport, не блокирует приёмку. |

---

## 11. Debt Intake

Из предшествующих планов (078, 086, 093) и TRAP-ов в коде:

| Источник | Статус | Решение |
|----------|--------|---------|
| TRAP[BUG] 2026-07-31 в `secrets.sh:215-219` — случайная соль ломает идемпотентность htpasswd | IN_SCOPE | TASK-1: fix в Python `_ensure_htpasswd()` |
| TRAP[BUG] 2026-07-23 в `secrets.sh:111-116` — step_start/done/skip undefined при standalone source | PRESERVED | AC4: declare -f stub-guard остаётся |
| TRAP[BUG] 2026-07-23 в `state_machine.py:1983-1986` — source secrets.sh без зависимостей | DEFER | Не в скоупе — state_machine уже имеет fix (export CORE_DIR, source logging.sh) |
| `secrets_manager._ensure_htpasswd()` — тот же баг случайной соли (не задокументирован TRAP) | IN_SCOPE | TASK-1: документируется TRAP[BUG] в Python при fix |

---

## 12. Non-Goals

- ❌ НЕ трогать `core/entrypoints/secrets.sh` (29 LOC, уже тонкий)
- ❌ НЕ трогать `core/internal/secrets/decrypt_secrets.py` (380 LOC, зрелый Python-модуль)
- ❌ НЕ трогать `core/internal/secrets/decrypt-secrets.sh` (23 LOC, тонкий фасад)
- ❌ НЕ трогать `core/internal/shared/crypto.py`, `age_key.py`, `secrets_env_parser.py`
- ❌ НЕ менять `state_machine.py` (контракт вызова shell через bash -c остаётся)
- ❌ НЕ мигрировать `step_12b_ensure_secrets` — уже тонкий фасад (10 LOC)
- ❌ НЕ создавать `core/internal/secrets/secrets_env_source.py` — cleanup в secrets_manager.py (D1)

---

## Next Steps

### Wave 1
Use coder role and read `.ai/plans/102-secrets-lib-complete/02-DevPlan.md`, implement Wave 1: TASK-1, TASK-2, TASK-4

### Wave 2
Use coder role and read `.ai/plans/102-secrets-lib-complete/02-DevPlan.md`, implement Wave 2: TASK-3, TASK-5, TASK-7, TASK-8

### Wave 3
Use coder role and read `.ai/plans/102-secrets-lib-complete/02-DevPlan.md`, implement Wave 3: TASK-6, TASK-9

### Wave 4
QA: Run `make gate MODE=fast` — verify green gate (AC7). If gate fails for orthogonal reasons (not caused by 102), document in VerificationReport without blocking acceptance.

## QA Review (2026-07-31)

🔒 **SHA:** fbe306d4284d9105193605378be28eb64b3c6795
🔒 **Working tree:** clean (no uncommitted changes)

**Вердикт:** APPROVED-WITH-CORRECTIONS (5 поправок, все внесены)

### Поправки

| # | Severity | Описание | Обоснование |
|---|:--------:|----------|-------------|
| P1 | **CRITICAL** | §7: Исправлена стратегия тестирования AC5/AC6 — убрано ложное утверждение о `TestHtpasswdGeneration` | `TestHtpasswdGeneration` тестирует `_ensure_htpasswd_generated()`, а не `step_10_decrypt_secrets()`. AGE_SECRET_KEY/SOPS_AGE_KEY fallback — в другой функции. Подтверждено grep-ом: 0 совпадений `step_10_decrypt_secrets\|AGE_SECRET_KEY` в `test_status_page.py`. Стратегия переписана на двухуровневую: Python-часть через `test_decrypt_secrets.py` + `test_age_key.py`, shell-часть — bash-builtin, верифицируется через gate и косвенно через source-safe поведение. |
| P2 | **HIGH** | §8: Исправлена колонка «Верификация» для AC5/AC6 — убрана ссылка на несуществующий интеграционный тест в `test_status_page.py` | AC5/AC6 fallback — чистый bash (`export` + `exit`), не бизнес-логика. Отдельный юнит-тест избыточен (Test Honesty R2). Верификация: Python `detect_age_key()` через существующие тесты + `make gate MODE=fast`. |
| P3 | **MEDIUM** | §2.3: Уточнён расчёт целевого LOC — 75 строк тела + ~5 blank = ~80 LOC (ранее было «~85 LOC ≤ 85» без расчёта) | Арифметическая проверка: 3+21+12+16+12+11 = 75 LOC функций/контракта + пустые строки-разделители. Цель ≤85 достижима с запасом 5-10 строк. |
| P4 | **LOW** | §4.3: Добавлен fallback `${CORE_DIR:-/opt/platform/core}` в shell-фасад `_ensure_htpasswd_generated` | Текущий код (L194) использует `${CORE_DIR:-/opt/platform/core}/...`. Новый фасад был без fallback — inconsistency. Исправлено для единообразия с существующим кодом. |
| P5 | **LOW** | §6 Wave 4: TASK-10 переквалифицирован как QA-шаг (был `coder implement Wave 4: TASK-10`) | Таблица задач явно указывает владельца TASK-10 = QA. Волновая команда была `coder` — исправлено на `make gate MODE=fast` с явным указанием роли QA. |

### Проверенные утверждения (подтверждены/уточнены)

| Утверждение DevPlan | Факт | Статус |
|---------------------|------|:------:|
| `secrets.sh` = 291 LOC | `wc -l` → 291 | ✓ |
| `step_10_decrypt_secrets` ~46 LOC | L123-169 = 47 строк | ✓ |
| `_ensure_htpasswd_generated` = 62 LOC | L190-251 = 62 строки | ✓ |
| `unset_platform_proxy` вызывается ТОЛЬКО из step_10 L156 | grep по всему проекту: 1 вызов (L156) + 4 комментария в `install-acme.sh` | ✓ |
| `decrypt_secrets.py` = 380 LOC | `wc -l` → 380 | ✓ |
| `crypto.py` = 167 LOC | `wc -l` → 167 | ✓ |
| `age_key.py` = 134 LOC | `wc -l` → 134 | ✓ |
| `secrets.sh` entrypoint = 29 LOC | `wc -l` → 29 | ✓ |
| `declare -f` stub-guard L111-121 | L117-121, TRAP[BUG] 2026-07-23 присутствует | ✓ |
| TRAP[BUG] htpasswd соль L215-219 | L215-219 в shell, Python `_ensure_htpasswd()` L427 — без соли (баг подтверждён) | ✓ |
| Бриф: `core/internal/bootstrap/decrypt_secrets.py` — неверный путь | Реальный: `core/internal/secrets/decrypt_secrets.py`. DevPlan P5 корректно фиксирует. | ✓ |
| `secrets_manager.py` CLI: `ensure`, `source` | L475-494: только `ensure` и `source`. `cleanup`/`htpasswd` — будут добавлены. | ✓ |
| `_ensure_htpasswd()` Python — баг случайной соли | L427: `generate_htpasswd_entry(email, password)` — без параметра `salt` → каждый вызов разный хеш. L434-436: `existing == expected_entry` всегда False. | ✓ |

### Кросс-зависимости (099-105)

| План | Трогает файлы | Пересечение с 102 |
|------|--------------|:-----------------:|
| 100 | `deploy-modules.sh`, `deploy_orchestrator.py` | Нет |
| 101 | `remote-cmd.sh`, `build-ssh-cmd.sh`, `remote_executor.py` | Нет |
| 103 | `context-promote.sh`, `context_promoter.py` | Нет |
| 104 | `bootstrap.sh`, `converge.sh`, `node-update.sh`, `node_detect.py`, `age_key.py` (compat shim) | Нет (`age_key.py` — compat shim, 102 не трогает) |
| 105 | `vps-readiness.sh`, `vps_readiness.py` | Нет (разные lib-файлы) |

**Вывод:** план 102 изолирован — не пересекается по файлам с соседними планами. Риска конфликтов при параллельной реализации нет.

### Проверка инвариантов

| Инвариант | Статус | Обоснование |
|-----------|:------:|-------------|
| Makefile — единый фасад | HELD | AC7: `make gate MODE=fast` — все операции через Makefile |
| Python-first (shell ≤85 LOC) | HELD | Целевой размер ≤85 LOC (75 строк тела). `step_10` ≤15 LOC, `_ensure_htpasswd` ≤12 LOC. Shell — чистый фасад. |
| Секреты не в git | HELD | Все тесты используют fake фикстуры (`tmp_path`, `PLATFORM_MASTER_PASSWORD=test-password-123`). Никаких реальных секретов. |
| TRAP[BUG] сохранены | HELD | AC4: `declare -f` stub-guard (TRAP 2026-07-23). TRAP 2026-07-31 — фиксится в TASK-1, документируется. |
| Manifest Generation Contract | HELD (not impacted) | `secrets.sh` и `secrets_manager.py` не участвуют в manifest generation. |

### Оставшиеся риски

| Риск | Severity | Описание |
|------|:--------:|----------|
| R1: `test_status_page.py::TestHtpasswdGeneration` использует `subprocess.run` для вызова shell → нарушение принципа «native imports only» из `.kilo/rules/testing.md` (но это существующий паттерн, не вводится планом 102) | LOW | Тесты уже написаны так. TASK-9 — адаптация под тонкий фасад, не изменение паттерна. |
| R2: `_ensure_htpasswd_generated` shell-фасад удаляет логику соль-экстракции (62 LOC → 12 LOC фасад), полагаясь на Python `_ensure_htpasswd()` fix. Если TASK-1 не выполнен корректно → идемпотентность сломана. | MEDIUM | Митигируется TASK-7 (тест идемпотентности) и TASK-10 (gate). TASK-5 зависит от TASK-1 — порядок гарантирует fix до фасада. |
| R3: `secrets.sh` ≤85 LOC — жёсткая цель. Если MODULE_CONTRACT не удаётся ужать до 21 строки → цель не достигнута. | LOW | Текущий контракт 54 строки, содержит дублирующуюся информацию (GREP_SUMMARY/STRUCTURE на L2-4 и L60-63). Сжатие до 21 строки достижимо удалением дубликатов и verbosity. |

$END_DEVPLAN
