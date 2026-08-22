# Failures: S1 migration / S2 rollback — часть 2 (политика fix-forward, периметр rollback, проектные миграции)

- companion: findings-migration-rollback.md (FAIL-0801–0805); ID range: FAIL-0800–0899

### FAIL-0806 · MED · Подтверждено: rollback откатывает ТОЛЬКО образ (+payload-файлы); схема БД остаётся на новой версии — политика fix-forward реализована, но без runbook downgrade
- scenario: S2; заявленная политика подтверждается фактической реализацией.
- evidence: `engine/lifecycle.py:86-113` perform_rollback — docker_tag(previous_image.id)
  + `compose up -d --force-recreate`; НИЧЕГО больше. Orchestrator-уровень добавляет только
  восстановление payload-файлов: `orchestrator.py:586-629` (_rollback_deploy →
  _restore_payload_files, T9.8: compose/ai-platform.yaml/.env.platform из payload_backup_dir).
  Схема БД, volumes, minio buckets, clickhouse-схемы — вне периметра (нет ни одного
  вызова в rollback-пути). Политика задокументирована: root AGENTS.md «Fix-forward
  политика rollback (C5)».
- 1) происходит: деплой с миграцией упал по healthcheck → старый код работает против
  НОВОЙ схемы (обратно-совместимость не проверяется ничем).
- 2) отказ: by design (lifecycle.py:perform_rollback); риск-точка — отсутствие
  contract-check «старый код vs новая схема».
- 3) авто-recovery: нет; если старый код падает на новой схеме — healthcheck красный
  уже после rollback (см. FAIL-0804).
- 4) broken state: сервис даун при несовместимой миграции; данные при этом целы.
- 5) retry безопасен: повторный деплой той же версии — да.
- 6) user impact: окно простоя до fix-forward коммита.
- 7) alert: deploy-канал (telegram notify + audit), ServiceDown при полном падении.
- 8) восстановление: fix-forward коммит; при необходимости данных — FAIL-0803 цепочка (опасна).
- 9) минимальный фикс до launch: НЕ код, а runbook в core/AGENTS.md §Fix-forward:
  «миграции писать expand-contract (обратно-совместимыми)» + шаблон manual-downgrade
  (git revert + ручной down-migration при необходимости). Ноль изменений в deploy-коде.
- confidence: high.
- action: документация до launch (1 час), соответствует критерию min churn.

### FAIL-0807 · LOW · В шаблоне проекта нет никакого migration-контракта — миграции целиком на совести приложения внутри CMD
- scenario: S1; проект с БД добавляет alembic/django-migrate.
- evidence: `templates/template-backend/Dockerfile` — `CMD ["python3", "main.py"]`,
  миграционных шагов нет; `templates/template-backend/snippets/db.py` (HYPOTHESIS: не
  проверял содержимое) — греп alembic|migrate по templates/ — 0 совпадений в compose/workflow.
  Payload не содержит исходников (FAIL-0801), значит pre-start migration hook в CI невозможен.
- 1) происходит: если проект кладёт миграции в app-startup: упавшая миграция = упавший
  контейнер → healthcheck красный → engine rollback (образ), схема — по FAIL-0806.
  Half-migrated schema возможна, если приложение мигрирует порциями без транзакции.
- 2) отказ: отсутствие контракта (нет файла — нет символа).
- 3) авто-recovery: как FAIL-0804/0806.
- 4) broken state: зависит от проекта.
- 5) retry: зависит от проекта.
- 6) user impact: потенциальный; сейчас проектов с миграциями нет (шаблон без БД-слоя).
- 7) alert: нет специализированного.
- 8) восстановление: ручное, per-project.
- 9) минимальный фикс: абзац в templates/template-backend/README.md: «миграции —
  ответственность приложения; писать expand-contract; упавший старт = rollback образа,
  схема НЕ откатывается». Ноль кода.
- confidence: high (отсутствие контракта), low (фактические поломки — их ещё нет).
- action: документация, опционально.

### FAIL-0808 · LOW · Периметр rollback: что осознанно НЕ откатывается (инвентарь для runbook)
- scenario: S2; полнота картины для оператора.
- evidence: rollback-путь трогает ровно два артефакта: образ
  (lifecycle.py:96-110) и payload-файлы (orchestrator.py:_restore_payload_files,
  staging→os.replace в receive_flow.py:436-460). Вне периметра (проверено грепами):
  vhosts nginx — платформенные, рендерятся render-vhosts, в payload проекта не входят
  (deploy-project.yml:352-356 — FILES только ai-platform.yaml/compose/.env.platform/practices.lock);
  secrets — sops-канал, отдельный от payload; minio buckets — init-container
  minio-createbuckets (module.mk контракт), идемпотентны; clickhouse-схемы — только
  langfuse-миграции (FAIL-0805); БД-схема — FAIL-0806.
- 1) происходит: после rollback окружение остаётся «смешанным» в перечисленных слоях —
  это осознанный дизайн, но нигде не сведено в один список для оператора.
- 2) отказ: нет (информационный).
- 3-7): n/a (дизайн).
- 8) восстановление: per-layer вручную.
- 9) минимальный фикс: таблица «rollback: что откатывается / что нет» в core/AGENTS.md
  §Fix-forward (5 строк markdown).
- confidence: high.
- action: объединить с FAIL-0806 в один doc-фикс.

## Сводка

| ID | Sev | Суть | Отказ |
|----|-----|------|-------|
| FAIL-0801 | CRITICAL | Шаблонные проекты без build-канала образа | template-backend/deploy.yml + flow.pull_images |
| FAIL-0802 | HIGH | adopter передаёт несуществующий input image_tag | project_adopter.py:224-240 |
| FAIL-0803 | HIGH | Restore: нет pre-restore снапшота, psql без ON_ERROR_STOP поверх живого кластера | postgres/Makefile:59-63 |
| FAIL-0804 | MED | Rollback не верифицируется healthcheck'ом | engine.py:256-265 |
| FAIL-0805 | MED | Platform auto-migrate фейл не блокирует node-update (severity normal) | litellm/langfuse module.yaml + deploy_orchestrator |
| FAIL-0806 | MED | Fix-forward подтверждён кодом; нет runbook expand-contract/downgrade | lifecycle.py:perform_rollback |
| FAIL-0807 | LOW | Нет migration-контракта в шаблоне проекта | templates/template-backend (отсутствие) |
| FAIL-0808 | LOW | Инвентарь вне периметра rollback | orchestrator.py/lifecycle.py |

Counts: CRITICAL 1 · HIGH 2 · MED 3 · LOW 2

Launch-blocker candidates:
1. FAIL-0801 — без решения не деплоится ни один новый проект (проверить e2e на test-VPS:
   scaffold → push → наблюдать pull-fail).
2. FAIL-0803 — DR-цепочка заявлена в каноне (RTO/RPO секция), фактически опасна
   (частичный restore как успех). Минимальный фикс: ON_ERROR_STOP + pre-restore дамп + 5 строк README.

Не-блокеры с фикс-до-launch (дёшево): FAIL-0802 (1 строка), FAIL-0806+0808 (один doc-фикс).
