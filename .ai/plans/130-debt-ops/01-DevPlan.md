# 130-debt-ops — 01-DevPlan.md

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Закрыть операционные долги: D-12 (dev-локали: cron status-metrics/htpasswd отсутствует, файлы генерируются вручную — .ai/debt/121-rc-deferred.md), D-15 (e2e φ8 pydantic non-fatal — закрыть как FIXED), P3-4/D11 (POSTGRES_PASSWORD rotation risk), D24 (mirror.yml manual force-sync). D-2 (L1 push 403 GHCR) закрыт пользователем 2026-08-03 — верифицировать и снять.
DESCRIPTION:           4 волны. W1 — D-12: dev-механизм генерации status-metrics.json + htpasswd (make-таргет на базе существующих platform_export_metrics.py и secrets_manager._ensure_htpasswd; запуск через make/скрипт на dev-локали). W2 — D-15: верификация FIXED (pydantic в requirements.txt RC-сессия; e2e error-path не воспроизводится) — снять запись. W3 — P3-4/D11: анализ ротации POSTGRES_PASSWORD (secrets_manager ensure-passwords уже генерит; ротация = регенерить + restart postgres + обновить потребителей) — минимальное решение: процедура ротации (runbook) + валидация через существующие ensure-механизмы. W4 — D24: mirror.yml force-sync — проверить актуальность, документировать процедуру ИЛИ снять (keep by design). + D-2: снять (FIXED пользователем, верификация через make hermes-push-l1).
RATIONALE:             D-12: status-page на dev-локали показывает stale-данные без ручной генерации — инцидент-риск при локальной разработке. D-15: pydantic теперь ставится на ноды (python_deps Step 2, RC 121) — запись устарела. P3-4: пароль postgres ротируется вручную → риск рассинхрона потребителей (litellm, backup-cron). D24: force-sync — ручная процедура, требует документации.
ACCEPTANCE_CRITERIA:   (1) D-12: make-таргет (напр. `make dev-metrics`) генерирует status-metrics.json + htpasswd на dev-локали без ручных шагов; задокументирован в README/dev-доке. (2) D-15: запись снята как FIXED (с обоснованием); e2e φ8 деплой контекста без pydantic-ошибок (проверка на test-VPS при доступности). (3) P3-4: процедура ротации POSTGRES_PASSWORD зафиксирована (runbook в модуле postgres или AGENTS.md-доке) ИЛИ автоматизирована через secrets_manager с тестами; Rev-условие обновлено. (4) D24: документация force-sync ИЛИ снятие (keep by design). (5) D-2: снят как FIXED (пользователь 2026-08-03). (6) make check зелёный.
IMPLEMENTS:            Решение пользователя 2026-08-03 (D-2 починен; D-12/D-15 «проверь сам»); .ai/debt/121-rc-deferred.md; P3-4 реестра 001.
IMPACTS:               core/internal/healthcheck/platform_export_metrics.py (если нужен CLI-вызов), core/internal/bootstrap/lifecycle/secrets_manager.py (htpasswd-вызов), Makefile (новый таргет dev-metrics), core/modules/postgres/ (runbook/ротация), .github/workflows/mirror.yml (комментарий/док), .ai/debt/121-rc-deferred.md (статусы).
REQUIRES:              Локальный dev-стек (docker compose) для W1-проверки; доступ к mirror-воркфлоу (GitHub) для W4 — по желанию.
$END_ARTIFACT_CONTRACT

## 0. Draft Code Graph (XML)

```xml
<graph>
  <entity name="core_internal_healthcheck_platform_export_metrics_py" TYPE="MODULE"
    keywords="status-metrics,export,json,dev,cron"
    annotation="D-12: существующий Python-экспортёр status-metrics.json (нода: /etc/cron.d/platform-metrics через platform-export-metrics.sh фасад). Dev-локали: make-таргет вызывает тот же экспортёр + htpasswd из secrets_manager."
    CrossLinks="core/internal/healthcheck/platform-export-metrics.sh; core/internal/bootstrap/lifecycle/secrets_manager.py"/>
  <entity name="core_modules_postgres" TYPE="MODULE"
    keywords="postgres,password,rotation,runbook"
    annotation="P3-4: процедура ротации POSTGRES_PASSWORD: regen в secrets → restart postgres → переподключение потребителей (litellm/backup-cron) → верификация."
    CrossLinks="core/modules/postgres/docker-compose.base.yml; core/internal/bootstrap/lifecycle/secrets_manager.py"/>
</graph>
```

## 1. Data Flow (шаг за шагом)

```
W1 ── D-12: inventory (platform-export-metrics.sh фасад → platform_export_metrics.py;
     htpasswd → secrets_manager._ensure_htpasswd) ─► make-таргет dev-metrics:
     python3 -m ... export + htpasswd-генерация (dev-пути /tmp/run/platform) ─►
     документация (README dev-стек)
W2 ── D-15: grep pydantic в контексте φ8 (context_deployer/deploy) — 0 обязательных
     импортов вне requirements; pydantic в requirements.txt ─► FIXED
W3 ── P3-4: анализ потребителей POSTGRES_PASSWORD (compose env, litellm, backup-cron)
     ─► runbook ротации (шаги + верификация) в core/modules/postgres/ROTATION.md
     (или AGENTS.md-секция) ─► при дешевизне — автоматизация в secrets_manager
W4 ── D24: mirror.yml:209 — проверить актуальность force-sync; документировать
     процедуру (комментарий в workflow) ИЛИ снять как keep by design;
     D-2: снять как FIXED (пользователь 2026-08-03)
```

## 2. File Manifest

| Файл | Действие | Волна |
|------|----------|-------|
| `Makefile` (или makefiles/*.mk) | + таргет `dev-metrics` | W1 |
| `core/modules/postgres/ROTATION.md` (NEW) | runbook ротации POSTGRES_PASSWORD | W3 |
| `.github/workflows/mirror.yml` | D24: комментарий/документация | W4 |
| `.ai/debt/121-rc-deferred.md` | D-2/D-15 → FIXED; D-12 → PLANNED 130 | W2/W4 |

## 3. Волны

### W1 — D-12: dev-механизм metrics
1. Инвентаризация: `platform-export-metrics.sh` — фасад над `platform_export_metrics.py`
   (Python); на ноде cron `install_cron_metrics` (helpers/system.py:186). На dev-локали
   (macOS) cron.d нет.
2. Make-таргет `dev-metrics`: вызывает `python3 -m core.internal.healthcheck.platform_export_metrics`
   (с dev-путями: STATUS_METRICS_JSON=/tmp/run/platform/status-metrics.json) + htpasswd
   генерацию через secrets_manager (dev-профиль). Идемпотентен.
3. Документация: README dev-стека — «после up: make dev-metrics» (или автовызов в up-safe
   dev-режиме, если тривиально).
4. Проверка: status-page на dev-локали показывает свежие данные без ручных шагов.

**Acceptance W1:** `make dev-metrics` работает на dev-локали; status-metrics.json + htpasswd
свежие; задокументировано.

### W2 — D-15: FIXED-верификация
1. Проверка: pydantic в requirements.txt (python_deps Step 2 ставит на ноды);
   обязательных pydantic-импортов в deploy-пути φ8 нет (policy_schema.py — llm-домен,
   ставится в составе requirements).
2. e2e φ8 deploy_context на test-VPS (при доступности) — без «No module named 'pydantic'».
3. Снять запись D-15 как FIXED (RC 121: «на проде не воспроизвёлся»).

**Acceptance W2:** D-15 → FIXED.

### W3 — P3-4/D11: POSTGRES_PASSWORD rotation
1. Анализ: где живёт POSTGRES_PASSWORD (secrets.env, docker-compose.base.yml:50,
   потребители: litellm, backup-cron), как ensure-passwords генерит.
2. Решение (минимальное): runbook `core/modules/postgres/ROTATION.md` — шаги
   ротации (regen через secrets_manager → restart postgres → перезапуск потребителей
   → верификация healthcheck) + Rev-условие (ротация ≥1 раз в квартал ИЛИ автоматизация).
3. Если автоматизация ≤30 LOC и тестируема — реализовать в secrets_manager
   (rotate_postgres_password с unit-тестами), runbook — fallback.

**Acceptance W3:** процедура ротации зафиксирована (и/или автоматизирована); Rev обновлён.

### W4 — D24 + D-2
1. D24: mirror.yml:215 (manual force-sync) — проверить, актуальна ли процедура;
   добавить комментарий-инструкцию в workflow (как выполнять force-sync) ИЛИ
   снять как keep by design (ручной канал резервируется).
2. D-2: снять как FIXED (пользователь 2026-08-03 починил GHCR push; верификация —
   `make hermes-push-l1` при доступности токена).

**Acceptance W4:** D24 закрыт (документирован/keep); D-2 → FIXED.

## 4. Критерии приёмки волн — сводка

| Волна | Критерий |
|-------|----------|
| W1 | dev-metrics работает, задокументирован |
| W2 | D-15 FIXED (обоснование) |
| W3 | P3-4 runbook/автоматизация, Rev обновлён |
| W4 | D24 закрыт, D-2 FIXED |

## 5. Риски и митигации

| Риск | Митигация |
|------|-----------|
| W1: dev-пути расходятся с прод-путями | Явные переменные (STATUS_METRICS_JSON/дефолты), тест на dev-локали |
| W3: ротация ломает потребителей | Runbook с верификацией; тесты автоматизации; не ротировать в проде без окна |
| W4: mirror force-sync опасен | Документация предупреждает о ручном режиме; без автоматизации |

$END_DEVPLAN
