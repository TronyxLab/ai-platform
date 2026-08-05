# 140-debt-close-wave — 01-DevPlan.md

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Закрыть актуальные долги Debt-реестров 126/136 по итогам аудита 2026-08-05: синхронизировать реестры с фактическим состоянием кода (3 записи закрыты кодом, но остались OPEN), исправить живые долги мониторинга (D-3/D-4/D-6/D-8), устранить plaintext-хранение AGE-ключа на ноде (W12-on-node-age-key), закрыть мелкие кандидаты W9/W12 (orphan self-heal, test label, hermes root-500).
DESCRIPTION:           Шесть волн. W1: синхронизация Debt-реестров (CLOSED: W2-firewall-reset by W10, W11-C-14 by TRAP-документация, W11-digest-ec by digest-pinning; переоформление W9-T9.19-legacy, W10-nginx-sudoers, D-7). W2: мониторинг-фиксы alert-rules.yml (D-4 sub-minute правило, D-6 mountpoint-фильтр) + активация Telegram alerting (D-3). W3: Loki resilience (D-8 — toleration clock jump, out-of-order окно) + повторный T4/T8 на test-VPS. W4: AGE-ключ — удаление persist /etc/age/key.txt из φ4, CI-канал AGE_SECRET_KEY env, tmpfs/decrypt-only канон (W12-on-node-age-key, S-13 закрепление) + обновление docs/age-master-key-dr.md. W5: мелочи — label ai-platform.test в test_hermes_init.py + удаление name-fallback (W12-T13), orphan self-heal в _postflight + верификация (W9-T9.15). W6: hermes-root-500 — L2-патч (USER-директива в context/Dockerfile) + non-root верификация на test-VPS.
RATIONALE:             Аудит 2026-08-05 (проверка каждого OPEN-кандидата по коду/workflows/git): 3 записи Debt фактически закрыты реализованным кодом (firewall incremental T10.10, TRAP по macOS-leg, digest-pinning C-6/C-10) — реестр врал, требуя фикса там, где фикс уже есть; D-4/D-6/D-8 подтверждены как живые (alert-rules.yml:82/146, loki-config.yml reject_old_samples); /etc/age/key.txt plaintext 0600 подтверждён пользователем и docs/age-master-key-dr.md (persist в φ4 phases/secrets.py:66-82 + fallback lib/secrets.sh:45-46 + node_detect Check 5) — противоречие инварианту «мастер-копия вне ноды»; W12-T13-label подтверждён (docker run без label, name-fallback в session.py); orphan self-heal существует (remove_orphans) но НЕ вызывается в _postflight (detect-only); hermes root-500 подтверждён (USER отсутствует в L1/L2 Dockerfile, chown-if-root workaround init.py:167).
ACCEPTANCE_CRITERIA:   (1) Debt-реестры 126/136 актуальны: W2-firewall-reset/W11-C-14/W11-digest-ec = CLOSED с указанием закрывающей реализации, 0 OPEN-записей, противоречащих коду; (2) alert-rules.yml: sub-minute правило покрывает падение postgres <1m (fire-тест), DiskSpaceLow expr фильтрует mountpoint="/" (fire-проверка на 90% fill); (3) alerting-цепочка Grafana→Telegram активна (contact-points.yml.telegram активирован, тестовая доставка fire/resolve OK); (4) loki-config.yml tolerates clock jump (out-of-order окно ≥24h), повторный T4/T8: 0 rejected «entry too far behind» при skew ±24h; (5) /etc/age/key.txt НЕ создаётся φ4 (persist удалён), CI node-update передаёт AGE_SECRET_KEY env, decrypt идёт через tmpfs+wipe (S-13), docs/age-master-key-dr.md обновлён; (6) hermes-test- контейнеры создаются с label ai-platform.test=true, name-fallback удалён из session.py; (7) orphan cleanup: remove_orphans вызывается в _postflight ИЛИ верифицирован resume-прогон прерванного deploy-modules (0 orphan после resume); (8) hermes L2 работает non-root (USER в Dockerfile, dashboard 9119 + API 8642 + volume perms OK на test-VPS); (9) make gate MODE=fast зелёный, make check чистый; (10) 0 новых глаголов, 0 изменений поведений канонических таргетов.
IMPLEMENTS:            .ai/plans/136-bootstrap-hardening/04-Debt.md (все OPEN-кандидаты, статусы на 2026-08-05); .ai/plans/126-chaos-resilience/04-Debt.md (D-3..D-8); docs/age-master-key-dr.md (S-12/S-13, W12 completion plan); threat-model «Средний» для AGE-ключа.
IMPACTS:               core/modules/monitoring/config/alerting/ (contact-points, alert-rules), core/modules/logging/config/loki-config.yml, core/internal/bootstrap/lifecycle/phases/secrets.py, core/internal/shared/node_detect.py, core/lib/secrets.sh, core/internal/bootstrap/lifecycle/state_store.py, core/internal/bootstrap/security_posture.py, .github/workflows/core-deploy.yml, tests/test_hermes_init.py, tests/_conftest/session.py, core/modules/hermes-agent/context/Dockerfile, core/internal/bootstrap/deploy/deploy_orchestrator.py, docs/age-master-key-dr.md, Debt-реестры 126/136, тесты (unit: node_detect, secrets, security_posture, alert-rules fire; gate: test_gate_sudoers_hardening остаётся зелёным).
REQUIRES:              main зелёный (136/137/138/139 влиты); TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID_* в secrets.env (D-3, оператор); AGE_SECRET_KEY в GitHub Secrets org (W4, оператор); test-VPS доступ (W3 T4/T8, W6 non-root верификация, W5 orphan-прогон); 0 конфликтов с параллельными волнами 137-139 (иные файлы, кроме tests/_conftest/session.py — проверить статус при старте).
$END_ARTIFACT_CONTRACT

$START_DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- GOAL [Зафиксировать стратегию: реестр должен отражать код, фиксы — минимальные и точечные] => G1 (§1)
- GOAL [Определить целевую архитектуру после фиксов (контракты + code graph)] => G2 (§2)
- GOAL [Развернуть суперпозицию по AGE-ключу (A/B/C) и orphan self-heal (A/B)] => G3 (§3)
- GOAL [Задать детальные контракты: sub-minute правило, mountpoint-фильтр, out-of-order окно, tmpfs-канон, label] => G4 (§4)
- GOAL [Разбить работу на исполняемые волны с AC и чек-листами] => G5 (§5)
- GOAL [Зафиксировать файловый манифест, риски, промт-шаблон] => G6 (§6, §7, §8)
**SECTION_USE_CASES:**
- USE_CASE [Агент исполняет W1 — синхронизация Debt-реестров по фактическому состоянию кода] => SC1 (§5 W1)
- USE_CASE [Агент исполняет W4 — AGE-ключ tmpfs/decrypt-only канон] => SC2 (§5 W4)
- USE_CASE [QA верифицирует AC глобальные] => SC3 (§9)
- USE_CASE [Code-субагент исполняет волну по промт-шаблону] => SC4 (§8)
$END_DOCUMENT_PLAN

## 1. Стратегия

**Реестр должен отражать код, фиксы — минимальные и точечные.** Аудит 2026-08-05 показал: Debt-реестр 136 содержит 3 записи OPEN, которые фактически закрыты реализованным кодом (W2-firewall-reset — firewall.py инкрементальный, T10.10; W11-C-14 — TRAP-документация в platform-test.yml:67-74; W11-digest-ec — digest-pinning в build-platform.yml C-6/C-10). Это рассинхронизация реестра с кодом — первая задача волны: привести реестр в соответствие, чтобы будущие волны не тратили время на «фиксы уже сделанного».

Живые долги разделены на две группы:

```
┌─ Debt-реестры 126/136 (2026-08-05) ──────────────────────────────┐
│  W1 СИНХРОНИЗАЦИЯ: 3 CLOSED (код уже реализовал) + 2 переоформления│
│    W2-firewall-reset → CLOSED-by-136-W10 (incremental ufw)       │
│    W11-C-14          → CLOSED-by-136-W11 (TRAP-документация)     │
│    W11-digest-ec     → CLOSED-by-136-W11 (digest-pin + fallback)  │
│    W9-T9.19-legacy   → остаток: миграция legacy-маркера          │
│    W10-nginx-sudoers → остаток: ревизия шаблона по типам нод     │
│    D-7               → верификация персистентного следа          │
│  W2-W6 ФИКСЫ: 6 живых долгов                                     │
│    D-3 alerting, D-4 sub-minute, D-6 mountpoint, D-8 Loki         │
│    W12-on-node-age-key, W12-T13-label, W9-T9.15-orphan, hermes-500│
└───────────────────────────────────────────────────────────────────┘
```

**Принципы:**
1. **CLOSED только с доказательством.** Каждая запись переводится в CLOSED с указанием коммита/файла, который её закрыл (аудит-трейл, канон 126). Реестр — не пожелания, а фактическое состояние.
2. **Минимальный фикс = одна ответственность.** D-4 и D-6 — правки expr/for в alert-rules.yml (по 1 правилу), без рефакторинга alerting-структуры. D-8 — параметры limits_config, без смены архитектуры Loki.
3. **AGE-ключ — через существующий S-13-канон.** decrypt_secrets.py уже пишет temp-ключ на tmpfs (/dev/shm) + dd-wipe; задача W4 — убрать persist на /etc/age/key.txt и прокинуть env-канал через CI, а не изобретать KMS.
4. **Синхронность тестов и gate.** Каждый фикс сопровождается тестом (fire-тест alert, unit node_detect/secrets, negative-тест name-fallback) — иначе гейт честности R5 (negative для каждого bug-id) блокирует.

## 2. Целевая архитектура

### 2.1 Контракты после фиксов

| Артефакт | Было | Стало |
|----------|------|-------|
| Debt 136 OPEN-записей | 21 (с учётом закрытых кодом 3) | 18 (3 CLOSED + переоформления) |
| `/etc/age/key.txt` на ноде | plaintext 0600, persist в φ4 | НЕ создаётся φ4; ключ — env (CI/оператор) → tmpfs decrypt-only (S-13) |
| CI node-update | БЕЗ AGE_SECRET_KEY (ключ обязан жить на ноде) | AGE_SECRET_KEY env в node-update SSH-команде (GitHub Secrets) |
| Service Down alert | 1 правило `for: 1m` (пропускает 11s-падения, D-4) | 2 правила: `for: 1m` (critical) + sub-minute `for: 15s` (warning) |
| DiskSpaceLow expr | без mountpoint-фильтра (редьюсер берёт tmpfs/overlay) | `{mountpoint="/"}` фильтр (и, при необходимости, tmpfs-правило) |
| Loki limits_config | `reject_old_samples: true` (skew ±24h → 1943 rejected) | out-of-order окно ≥24h (или reject_old_samples_max_age с toleration) |
| hermes-test- контейнеры | без label, sweep по name-fallback | `label=ai-platform.test=true` в создателе, fallback удалён |
| Orphan cleanup в _postflight | detect-only (batch_orphan_reconciliation) | + remove_orphans (self-heal) ИЛИ верифицированный resume-прогон |
| hermes L2 | root (нет USER) | USER-директива + non-root верификация |

### 2.2 Draft Code Graph (XML)

```xml
<knowledge_graph>
  <entity name="alert_rules_yml" type="CONFIG" keywords="service_down disk_space for mountpoint">
    <CrossLink>core/modules/monitoring/config/alerting/contact-points.yml</CrossLink>
  </entity>
  <entity name="loki_config_yml" type="CONFIG" keywords="limits_config reject_old_samples out_of_order">
    <CrossLink>core/modules/logging/config/loki-config.yml</CrossLink>
  </entity>
  <entity name="phases_secrets_py" type="MODULE" keywords="secrets_provision persist age key tmpfs">
    <CrossLink>core/internal/shared/node_detect.py</CrossLink>
    <CrossLink>core/lib/secrets.sh</CrossLink>
  </entity>
  <entity name="node_detect_py" type="MODULE" keywords="detect_age_key /etc/age/key.txt env chain">
    <CrossLink>core/internal/bootstrap/lifecycle/state_store.py</CrossLink>
  </entity>
  <entity name="decrypt_secrets_py" type="MODULE" keywords="tmpfs /dev/shm dd-wipe sops S-13">
    <CrossLink>core/internal/shared/node_detect.py</CrossLink>
  </entity>
  <entity name="core_deploy_yml" type="WORKFLOW" keywords="node-update AGE_SECRET_KEY ssh env">
    <CrossLink>makefiles/bootstrap.mk</CrossLink>
  </entity>
  <entity name="deploy_orchestrator_py" type="MODULE" keywords="_postflight orphans remove_orphans self-heal">
    <CrossLink>core/internal/bootstrap/deploy/orphan_reconciler.py</CrossLink>
  </entity>
  <entity name="test_hermes_init_py" type="TEST" keywords="_run_container_detached label ai-platform.test">
    <CrossLink>tests/_conftest/session.py</CrossLink>
  </entity>
  <entity name="context_Dockerfile" type="BUILD" keywords="USER hermes L2 non-root">
    <CrossLink>core/modules/hermes-agent/build/scripts/init.py</CrossLink>
  </entity>
</knowledge_graph>
```

### 2.3 Step-by-step Data Flow — AGE-ключ (W4)

```
CI (core-deploy.yml)                          Нода
┌────────────────────────────┐               ┌──────────────────────────────────┐
│ AGE_SECRET_KEY (GH Secrets)│               │ φ9 secrets-update               │
│   → env в SSH-команду      │── ssh ───────▶│   node_detect.detect_age_key()  │
│   node-update NODE=$NODE   │               │     env AGE_SECRET_KEY (CI) ✓   │
└────────────────────────────┘               │   decrypt_secrets.py            │
                                             │     temp-key → /dev/shm 0600    │
bootstrap (оператор)                         │     sops --decrypt               │
┌────────────────────────────┐               │     dd-wipe + rm (S-13)          │
│ make bootstrap-node        │               │   /etc/age/key.txt НЕ создаётся │
│   AGE_SECRET_KEY_FILE=...   │               │     (persist удалён из φ4)      │
└────────────────────────────┘               └──────────────────────────────────┘
```

**Восстановление (restore-first, из docs/age-master-key-dr.md):** новая нода бутстрапится, зашифрованный off-node backup доставляется, расшифровывается НА ноде через env-канал — plaintext не пересекает сеть.

## 3. Суперпозиция решений

### S1: AGE-ключ на ноде (W12-on-node-age-key)

| Вариант | Описание | Оценка |
|---------|----------|--------|
| **A. tmpfs-only + CI env (ВЫБРАН)** | persist /etc/age/key.txt удаляется; ключ приходит env (bootstrap: AGE_SECRET_KEY_FILE оператора; CI node-update: AGE_SECRET_KEY из GitHub Secrets); decrypt через существующий tmpfs-канон S-13 (decrypt_secrets.py); fallback /etc/age/key.txt сохраняется ТОЛЬКО для ручного restore-first | Минимальный дифф (phases/secrets.py + core-deploy.yml + node_detect Check 5), использует готовый S-13, закрывает plaintext-at-rest. Риск: CI env расширяет секретную поверхность — смягчается отсутствием persist (ключ в env только на время команды) |
| B. tmpfs-маунт /etc/age (systemd) | Монтировать /etc/age как tmpfs + заполнение при boot из зашифрованного источника | Ключ всё равно должен откуда-то прийти при boot → сводится к A + systemd-юнит; больше движущихся частей |
| C. Принять plaintext как accepted-risk | Оставить /etc/age/key.txt 0600 root, задокументировать в threat-model | Не устраняет противоречие инварианту «мастер-копия вне ноды»; пользователь подтвердил проблему — отклонено |

### S2: Orphan cleanup в _postflight (W9-T9.15)

| Вариант | Описание | Оценка |
|---------|----------|--------|
| **A. Включить remove_orphans (ВЫБРАН)** | В _postflight после batch_orphan_reconciliation вызвать orphan_reconciler.remove_orphans(orphans) — self-heal уже реализован (orphan_reconciler.py:382) и используется docker_orchestrator.py:415 | 3 строки кода, использует существующий механизм; детект+удаление в одном месте деплоя |
| B. Только верификация resume-прогона | Прерванный deploy-modules (kill в середине φ11) на test-VPS + проверка, что resume чистит orphan | Не закрывает detect-only gap: orphan останется до ручного вмешательства, если cleanup не вызывается |

### S3: D-4 sub-minute покрытие

| Вариант | Описание | Оценка |
|---------|----------|--------|
| **A. Дополнительное правило (ВЫБРАН)** | Service Down остаётся `for: 1m` critical; НОВОЕ правило `Service Down Short` `for: 15s` severity warning на том же expr `up == 0` | Анти-флаппинг critical сохранён, sub-minute падения покрыты warning-каналом; T6-кейс (11s) fire |
| B. Снизить for: 1m → 15s | Одно правило, меньше задержка | Жертва анти-флаппинга: кратковременные рестарты стека (healthcheck-окна) дадут ложные critical |

## 4. Контракты

### 4.1 Debt-реестр — правила статусов (W1)

- **CLOSED** — только с доказательством: `CLOSED-by-<волна/коммит>` + что именно закрыло (файл/механизм).
- **Переоформление** — запись остаётся OPEN, но «Суть» уточняется до оставшегося остатка (не дублировать выполненное).
- **D-7** — при верификации персистентного следа (docker logs backup-cron / volume backup-logs postgres.log) → CLOSED-by-140-W1, иначе остаётся с новой Rev.
- Сводка статусов в конце файла обновляется синхронно.

### 4.2 alert-rules.yml (W2)

- Правило `service_down`: `for: "1m"` — БЕЗ изменений (critical, анти-флаппинг).
- Новое правило `service_down_short`: uid `service_down_short`, expr `up == 0`, `for: "15s"`, severity `warning`, summary «Service {{ $labels.job }} down (short)».
- Правило `disk_space`: expr → `node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"} < 0.2`; при необходимости второе правило на tmpfs/overlay (`fstype=~"tmpfs|overlay"`), severity warning.
- Fire-тесты: unit-тест парсинга правил (uid/for/expr присутствуют, mountpoint-фильтр применён) — `tests/unit/test_alert_rules_static.py` (новый).

### 4.3 Loki (W3)

- `limits_config`: toleration clock jump — включить out-of-order окно. В Loki 3.x: `out_of_order_time_window: 24h` (или аналог по версии) ИЛИ `reject_old_samples: false` + `reject_old_samples_max_age` осмысленное. Цель: skew ±24h → 0 «entry too far behind».
- Healthcheck-критерий ingester: проверка `ready` (а не «shutting down»).
- Повторный T4/T8 на test-VPS (harness 126: tests/e2e/test_chaos_resilience.py) — skew-инъекция + длительное окно.

### 4.4 AGE-ключ (W4)

- `phases/secrets.py` φ4: блок persist (строки 66-82) УДАЛЯЕТСЯ — ключ не пишется на диск ноды. Остаётся env-канал.
- `node_detect.py` Check 5 (`/etc/age/key.txt`): сохранить как ПОСЛЕДНИЙ fallback (ручной restore-first, документировано), но перестать быть каноном для φ4.
- `lib/secrets.sh` fallback: остаётся (ручное восстановление), комментарий обновить.
- `state_store.py:227` precondition φ4: `/etc/age/key.txt` → проверка env-цепочки (AGE_SECRET_KEY/SOPS_AGE_KEY/AGE_SECRET_KEY_FILE), key.txt — только fallback.
- `core-deploy.yml` node-update: добавить `AGE_SECRET_KEY=${{ secrets.AGE_SECRET_KEY }}` в env SSH-команды.
- `security_posture.py`: проверка /etc/age остаётся (fallback-файл может существовать при restore), пометка о non-canonical.
- `docs/age-master-key-dr.md`: канон «ключ — env → tmpfs decrypt-only; /etc/age/key.txt — restore-first fallback», таблица источников node_detect обновляется.

### 4.5 test label (W5)

- `test_hermes_init.py::_run_container_detached`: `cmd = ["docker", "run", "-d", "--memory", mem_limit, "--label", "ai-platform.test=true", ...]`.
- `tests/_conftest/session.py`: name-prefix fallback УДАЛЯЕТСЯ (TRAP[DECISION] 2026-08-05 в session.py обновляется — label-first единственный путь).

## 5. Волны

### W1 — Синхронизация Debt-реестров (статусы, 0 кода)

**Scope:** `.ai/plans/136-bootstrap-hardening/04-Debt.md`, `.ai/plans/126-chaos-resilience/04-Debt.md`.

| Запись | Действие | Доказательство |
|--------|----------|----------------|
| W2-firewall-reset | → CLOSED-by-136-W10 | firewall.py build_rules: `ufw --force enable` ПЕРВЫМ, 0 вызовов disable/reset (T10.10, S-14); коммит 431756e6 |
| W11-C-14 | → CLOSED-by-136-W11 | platform-test.yml:67-74 TRAP[DECISION]: macOS-leg отклонён, Rev 2026-10-21 — условие записи «ИЛИ TRAP-документация» выполнено |
| W11-digest-ec | → CLOSED-by-136-W11 | build-platform.yml C-6/C-10: digest-pinning (.github/l1-distribution-digest) + error-маркер + fallback на :latest — bounded-retry реализован |
| W9-T9.19-legacy | Переоформление: остаток — миграция legacy-маркера | per-context форма реализована (orchestrator_metrics.hc_marker_path, CONTEXT env); осталась миграция `.hc_done_in_deploy` без суффикса на существующих нодах при node-update |
| W10-nginx-sudoers | Переоформление: остаток — ревизия шаблона по типам нод | сужение выполнено (T10.1: docker/rsync NOPASSWD удалены, live test-VPS 2026-08-05); остались nginx systemctl* legacy-записи, задокументированы TRAP в setup-node.sh |
| D-7 | Верификация → CLOSED или новая Rev | backup_postgres.py логирует IMP:9/10 (docker logs) + crontab → volume backup-logs/postgres.log — персистентный след появился; проверить наличие записи при симулированном ENOSPC (df-тест/вручную) |

**AC-W1:** (1) 3 записи = CLOSED с указанием реализации; (2) переоформленные записи описывают только остаток; (3) D-7 имеет фактический статус после верификации; (4) сводки статусов в обоих реестрах синхронны; (5) 0 изменений кода.

**Чек-лист W1:**
- [ ] Проверить каждый CLOSED-кандидат в коде (grep disable/reset в firewall.py; grep macOS-leg в platform-test.yml; grep digest в build-platform.yml)
- [ ] Обновить таблицы + сводку в 136 04-Debt.md
- [ ] Обновить D-7 в 126 04-Debt.md (статус после верификации)
- [ ] make check (diff-скоуп: только .md — быстрая проверка)

### W2 — Мониторинг-фиксы (D-4, D-6, D-3)

**Scope:** `core/modules/monitoring/config/alerting/alert-rules.yml`, `core/modules/monitoring/config/alerting/contact-points.yml(.telegram)`, `tests/unit/test_alert_rules_static.py` (новый).

1. **D-4**: добавить правило `service_down_short` (for: 15s, warning) — контракт §4.2.
2. **D-6**: expr disk_space + `{mountpoint="/"}`; при необходимости второе tmpfs-правило.
3. **D-3**: активировать Telegram alerting — rename `contact-points.yml.disabled` → активация `contact-points.yml.telegram` (по инструкции в файле), TELEGRAM_* env в secrets.env (оператор), тестовая доставка fire/resolve на реальном алерте.

**AC-W2:** (1) service_down_short присутствует (uid/for/expr/severity валидны); (2) disk_space expr содержит mountpoint-фильтр; (3) unit-тест парсинга правил зелёный (fire-семантика: `up == 0` + for=15s); (4) alerting-цепочка: тестовый алерт доставлен в Telegram и resolve-цикл подтверждён; (5) D-4/D-6/D-3 → CLOSED в 136 (W2).

**Чек-лист W2:**
- [ ] Правка alert-rules.yml (2 правила)
- [ ] Unit-тест парсинга (новый файл) — negative-тест: без mountpoint-фильтра → fail (R5)
- [ ] Активация contact-points + env (оператор: TELEGRAM_* в secrets.env)
- [ ] Fire/resolve тест (реальный алерт, проверить rules state + доставку)
- [ ] Debt-реестр: D-3/D-4/D-6 → CLOSED-by-140-W2

### W3 — Loki resilience (D-8)

**Scope:** `core/modules/logging/config/loki-config.yml`, повторный T4/T8 на test-VPS.

1. limits_config: out-of-order toleration (контракт §4.3) — проверить версию Loki в образе, применить `out_of_order_time_window: 24h` (или эквивалент).
2. Healthcheck-критерий ingester: ready-проверка.
3. Повторный T4 (skew ±24h) + T8 (длительное окно) через harness 126 на test-VPS.

**AC-W3:** (1) при skew ±24h 0 rejected «entry too far behind» (проверка по Loki-логам/метрикам); (2) T8-окно: promtail НЕ получает 500s, ingester не «shutting down» весь окно; (3) D-8 → CLOSED-by-140-W3 (или переоформление с новой Rev при частичном результате).

**Чек-лист W3:**
- [ ] Определить версию Loki (docker image tag в logging module)
- [ ] Правка limits_config (out-of-order окно)
- [ ] Прогон T4 на test-VPS (skew-инъекция), собрать rejected-счётчик
- [ ] Прогон T8 (длительное окно), проверить promtail/Loki state
- [ ] Debt-реестр: D-8 статус

### W4 — AGE-ключ: tmpfs/decrypt-only канон (W12-on-node-age-key)

**Scope:** `core/internal/bootstrap/lifecycle/phases/secrets.py`, `core/internal/shared/node_detect.py`, `core/lib/secrets.sh`, `core/internal/bootstrap/lifecycle/state_store.py`, `core/internal/bootstrap/security_posture.py`, `.github/workflows/core-deploy.yml`, `docs/age-master-key-dr.md`, тесты.

1. phases/secrets.py: удалить persist-блок (строки 66-82) — ключ не пишется на диск.
2. node_detect.py Check 5: `/etc/age/key.txt` → последний fallback (не канон), комментарий.
3. state_store.py:227: precondition φ4 — env-цепочка первична, key.txt fallback.
4. lib/secrets.sh: комментарий fallback обновить (ручной restore-first).
5. security_posture.py: /etc/age — non-canonical пометка (файл допустим только при restore).
6. core-deploy.yml: `AGE_SECRET_KEY=${{ secrets.AGE_SECRET_KEY }}` в env node-update SSH-команды.
7. docs/age-master-key-dr.md: канон «env → tmpfs decrypt-only», таблица §1 обновляется.
8. Тесты: unit node_detect (env-приоритет без файла), unit secrets-phase (φ4 не создаёт /etc/age/key.txt), negative (без env и без файла → fail), обновить security_posture-тест.

**AC-W4:** (1) φ4 на свежей ноде НЕ создаёт /etc/age/key.txt; (2) CI node-update расшифровывает secrets по env (проверка на test-VPS); (3) bootstrap с AGE_SECRET_KEY_FILE работает (операторский путь); (4) unit-тесты зелёные; (5) docs обновлён; (6) W12-on-node-age-key → CLOSED-by-140-W4.

**Чек-лист W4:**
- [ ] Удалить persist-блок из phases/secrets.py
- [ ] node_detect + state_store + secrets.sh + security_posture правки
- [ ] core-deploy.yml env (оператор: AGE_SECRET_KEY в GitHub Secrets org)
- [ ] Unit-тесты (новые/обновлённые)
- [ ] Верификация на test-VPS: node-update после удаления key.txt (env-канал)
- [ ] docs/age-master-key-dr.md + Debt-реестр

### W5 — Мелочи: label, orphan (W12-T13, W9-T9.15)

**Scope:** `tests/test_hermes_init.py`, `tests/_conftest/session.py`, `core/internal/bootstrap/deploy/deploy_orchestrator.py`, `core/internal/bootstrap/deploy/orphan_reconciler.py` (вызов), тесты.

1. **W12-T13**: label `ai-platform.test=true` в `_run_container_detached`; удалить name-fallback в session.py (контракт §4.5); negative-тест: контейнер без label не подхватывается sweep.
2. **W9-T9.15**: `_postflight` — после `batch_orphan_reconciliation` вызвать `remove_orphans(orphans)` (self-heal, суперпозиция S2-A); unit-тест: remove вызывается при orphan>0.

**AC-W5:** (1) hermes-test- контейнеры создаются с label; (2) sweep sessionfinish работает label-only (0 контейнеров с name-fallback остаётся); (3) _postflight удаляет orphan-контейнеры (unit-тест + опционально прерванный прогон на test-VPS); (4) W12-T13-label → CLOSED; W9-T9.15-orphan → CLOSED (после верификации).

**Чек-лист W5:**
- [ ] label в _run_container_detached
- [ ] fallback removal в session.py + TRAP-обновление
- [ ] remove_orphans в _postflight
- [ ] Unit-тесты (label present, remove called)
- [ ] Debt-реестр: W12-T13, W9-T9.15 статусы

### W6 — hermes-root-500: L2 USER-патч (non-root)

**Scope:** `core/modules/hermes-agent/context/Dockerfile`, `core/modules/hermes-agent/build/scripts/init.py` (проверка chown-пути), верификация на test-VPS.

1. context/Dockerfile: `USER`-директива (non-root uid, напр. 10000 — соответствует chown в init.py) ПОСЛЕ всех COPY/RUN, перед HEALTHCHECK; проверить, что HEALTHCHECK curl работает от non-root (порт 9119 не привилегированный).
2. init.py: chown-if-root workaround остаётся как страховка (L1 может работать root), но L2 non-root — проверить volume perms (тест записи /opt/data).
3. Сборка L2 (`make hermes-build-context CONTEXT=<ctx>`) + деплой на test-VPS + верификация: dashboard 9119, API 8642, Telegram-канал.
4. L1 (build/Dockerfile) НЕ трогается (public distribution base, upstream-зависимость) — только L2.

**AC-W6:** (1) L2-образ содержит USER; (2) контейнер hermes на test-VPS работает non-root (docker inspect Config.User ≠ root); (3) dashboard/API/Telegram живы после пересоздания; (4) hermes-root-500 → CLOSED-by-140-W6 (L2-патч) с пометкой «L1 остаётся root — upstream Rev 2026-10-21».

**Чек-лист W6:**
- [ ] USER-директива в context/Dockerfile
- [ ] Сборка L2 + push локально (не трогая L1)
- [ ] Деплой на test-VPS + non-root верификация (inspect + endpoints)
- [ ] Debt-реестр: hermes-root-500 статус (L2 закрыт, L1 — upstream)

## 6. Файловый манифест

| Файл | Действие | Волна |
|------|----------|-------|
| `.ai/plans/136-bootstrap-hardening/04-Debt.md` | Обновление статусов (3 CLOSED + переоформления + фиксы W2-W6) | W1-W6 |
| `.ai/plans/126-chaos-resilience/04-Debt.md` | Обновление D-7 (статус после верификации) | W1 |
| `core/modules/monitoring/config/alerting/alert-rules.yml` | + service_down_short, mountpoint-фильтр | W2 |
| `core/modules/monitoring/config/alerting/contact-points.yml` / `.telegram` / `.disabled` | Активация Telegram alerting | W2 |
| `tests/unit/test_alert_rules_static.py` | НОВЫЙ: парсинг правил, negative без фильтра | W2 |
| `core/modules/logging/config/loki-config.yml` | out-of-order окно (D-8) | W3 |
| `core/internal/bootstrap/lifecycle/phases/secrets.py` | Удалить persist AGE-ключа | W4 |
| `core/internal/shared/node_detect.py` | Check 5 → fallback (не канон) | W4 |
| `core/internal/bootstrap/lifecycle/state_store.py` | Precondition φ4 → env-цепочка | W4 |
| `core/lib/secrets.sh` | Комментарий fallback (restore-first) | W4 |
| `core/internal/bootstrap/security_posture.py` | /etc/age — non-canonical пометка | W4 |
| `.github/workflows/core-deploy.yml` | AGE_SECRET_KEY env в node-update | W4 |
| `docs/age-master-key-dr.md` | Канон env→tmpfs, таблица источников | W4 |
| `tests/unit/test_node_detect.py` (или аналог) | env-приоритет, negative без ключа | W4 |
| `tests/unit/test_secrets_phase.py` (или аналог) | φ4 не создаёт key.txt | W4 |
| `tests/test_hermes_init.py` | label ai-platform.test=true | W5 |
| `tests/_conftest/session.py` | Удалить name-fallback | W5 |
| `core/internal/bootstrap/deploy/deploy_orchestrator.py` | remove_orphans в _postflight | W5 |
| `tests/unit/test_orphan_reconciler.py` (или аналог) | remove вызывается при orphan>0 | W5 |
| `core/modules/hermes-agent/context/Dockerfile` | USER-директива | W6 |
| `core/modules/hermes-agent/build/scripts/init.py` | (проверка, правки не ожидаются) | W6 |

## 7. Риски

| Риск | Severity | Митигация |
|------|----------|-----------|
| W4: удаление persist сломает node-update, если CI env не настроен (AGE_SECRET_KEY отсутствует в GitHub Secrets) | HIGH | Порядок: сначала core-deploy.yml + операторский шаг настройки секрета, затем удаление persist; fallback /etc/age/key.txt сохраняется (restore-first) — деградация мягкая |
| W2: активация alerting зальёт Telegram ложными алертами при рестарте стека | MEDIUM | sub-minute правило — warning (не critical), fire-тест до активации; D-3 тестовая доставка на тестовом алерте |
| W3: `out_of_order_time_window` несовместим с версией Loki в образе | MEDIUM | Определить версию ДО правки; при несовместимости — `reject_old_samples_max_age` осмысленное значение |
| W6: non-root ломает volume perms или Telegram-канал (Tor/privoxy) | MEDIUM | chown-if-root страховка в init.py остаётся; верификация endpoints на test-VPS ДО промоута; откат — revert Dockerfile |
| W5: удаление name-fallback воскресит 503 false-lead (остатки hermes-test- без label на старых нодах) | LOW | label-first sweep + ручной `docker rm` остатков на test-VPS перед удалением fallback |
| Debt-реестр: рассинхронизация с параллельными волнами 137-139 (session.py — общий файл) | LOW | Проверить git status при старте W5; при конфликте — rebase поверх |

## 8. Промт-шаблон (Code-субагент, волна N)

```
Исполни волну W{N} DevPlan 140 (.ai/plans/140-debt-close-wave/01-DevPlan.md).
Контекст: {краткое описание волны + ссылка на секцию}.
Требования:
1. Читай только указанные в §5 W{N} файлы; не трогай вне скоупа.
2. После каждой задачи — per-task проверка: make test-summary TEST_FILE=... или make check-diff.
3. Финальная верификация: make check (до чистоты), батчами, не серийно.
4. make gate MODE=fast НЕ запускай (pre-push hook исполнит).
5. Debt-реестр обновляй в той же волне (CLOSED с доказательством).
Верни: список изменённых файлов, статусы AC волны, остатки/риски.
```

## 9. Глобальные AC

1. Debt-реестры 126/136 — 0 OPEN-записей, противоречащих коду (аудит-сверка: каждая OPEN имеет живую причину).
2. Все 6 живых долгов (D-3, D-4, D-6, D-8, W12-on-node-age-key, W12-T13-label, W9-T9.15-orphan, hermes-root-500) → CLOSED с доказательством (тесты/верификация на test-VPS).
3. Операционные окна (T9-T11, B6/B7, W10-S-13-drill, D-5) остаются OPEN с Rev — НЕ в скоупе 140 (требуют пересозданную ноду/оператора).
4. `make gate MODE=fast` зелёный (pre-push hook), `make check` чистый, `make check-manifests` зелёный.
5. 0 новых глаголов, 0 изменений канонических таргетов, 0 inline python3 в shell (языковая политика).
6. Каждая закрытая запись Debt несёт доказательство (файл/коммит/тест) — канон 126.

$END_DEVPLAN
