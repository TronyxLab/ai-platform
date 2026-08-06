# CI Findings — session 141 cycle 2 (ci-ops)

Append-only. TS in MSK (UTC+3).

## 2026-08-06T12:57:00+03:00 (MSK) — ВЕХА: SSH pre-flight SUCCESS (ключ работает!), но provision падает — РЕАЛЬНЫЙ БАГ CI-доставки scripts/
- Триггер: push local-validation b9fbc47f (FIXES_AVAILABLE 12:40Z) → platform-gate-fast 31102362497 SUCCESS (~12:53Z) →
  workflow_run-цепочка: core-deploy 31103410072 failure, Mirror success, Build Platform Agent failure.
- core-deploy прогресс против 1-го цикла: **SSH pre-flight ✅** (ключ ci-core-deploy принят новым сервером —
  EXPECTED-fail 4❌ 1-го цикла закрыт!), **Rsync core ✅** (core + makefiles + platform-env.yaml доставлены),
  **Provision ❌** → `make: /opt/platform/scripts/make-log-shell.sh: No such file or directory` →
  `makefiles/helpers.mk:109: provision] Error 127`.
- РУТ-КОЗА (диагностика полная, доказано git-историей):
  1. `Makefile:80` — `SHELL := $(_platform_root)/scripts/make-log-shell.sh` (make-recipe обёртка логирования,
     MAKEFILE_LOG_LOGGING, коммит 11ef2c74 docs(140), 2026-08-06 01:39 MSK, добавил и скрипт scripts/make-log-shell.sh).
  2. НИ ОДИН канал доставки не копирует `scripts/`:
     - CI core-deploy.yml rsync-шаги: `./core/`→/opt/platform/core/, platform-env.yaml+Makefile+makefiles→/opt/platform/, node-configs/→/opt/node-configs/. scripts/ НЕ входит.
     - Bootstrap core_deliverer.py (deliver_all: deliver_core/deliver_platform_env/deliver_makefile/deliver_node_configs/deliver_secrets) — scripts/ НЕ входит.
  3. На старом сервере /opt/platform/scripts/ существовал от исторического деплоя — маскировал дыру; на чистом
     переустановленном сервере (11:26 MSK) его нет → ЛЮБАЯ make-цель (provision/node-update/converge) падает Error 127.
  4. Следствие для дорожки server-ops: те же make-операции на ноде должны падать тем же образом — вероятный
     вклад в затянувшийся бутстрап (P2_BOOTSTRAPPED ещё не получен на 12:57Z).
- ВЕРДИКТ: RED — блокирует первый core-deploy SUCCESS; НЕ связан с состоянием сервера (чистый сервер — обязательное условие проявления). Фикс (минимальный, 2 правки):
  (а) core-deploy.yml rsync-шаг: добавить `./scripts $USER@$HOST:/opt/platform/` (по образцу makefiles, с guard);
  (б) core_deliverer.py: deliver_scripts() фаза (bootstrap-канал — тот же дефект).
  Кандидат: local-validation (Code-агент). До фикса workflow_dispatch core-deploy бессмысленен — падение детерминировано.
- Build Platform Agent 31103410152: тот же известный RED (smoke `undefined volume hermes-data`) — не новое.
- Mirror to TronyxLab 31103410463: success.

## 2026-08-06T14:03:00+03:00 (MSK) — core-deploy 31108723544 (bc3a448b, B19/B20): тот же provision-фейл — детерминированность подтверждена
- Гейт 31107580294 SUCCESS → цепочка: core-deploy 31108723544 failure (31s), Mirror success, Build Platform Agent failure (тот же smoke).
- core-deploy: SSH pre-flight ✅ / rsync ✅ / provision ❌ — `make: /opt/platform/scripts/make-log-shell.sh: No such file or directory` → Error 127 (helpers.mk:109).
- Вердикт: фейл 100% детерминирован до фикса доставки scripts/ (REQ_FIX 12:57Z). local-validation в цикле B19/B20 (deploy-project) — scripts/-фикс в очереди.
