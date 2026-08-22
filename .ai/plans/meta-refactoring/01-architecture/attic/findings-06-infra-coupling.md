# Infra/application/domain coupling audit

Метод: grep-обследование `core/internal/` на прямые subprocess/docker/ssh/rsync/psql/http/s3-вызовы вне канонических фасадов `shared/` (docker_ops, docker_compose, ssh_cmd_builder+ssh_opts, http_client, s3_client, telegram_notifier), парсинг CLI-stdout как контракт, литералы портов/таймаутов/путей вне SoT. Проверены все 6 фокусов; каждая находка — чтением контекста вызова.
Счётчик: **269** subprocess-вызовов в `core/internal/**/*.py`, из них **231 вне `shared/`** (86%). Основные пакеты: bootstrap/* (оркестрация ноды — легитимно по ролевой модели), scaffold/*, deploy/channels, deploy/*. Большинство идут через DI-runner + фасады; ниже — НЕпокрытые протекания.

## ARCH-601: remove-project строит SSH-канал строковой интерполяцией мимо ssh_cmd_builder/ssh_opts
- **Severity:** High · **Confidence:** High
- **Files:** core/internal/scaffold/project_remover.py · **Symbols:** `_default_ssh` (L314-331), `ssh_compose_down` (L341-382)
- **Evidence:**
  ```python
  # project_remover.py:322-325
  f'source core/lib/ssh.sh && ssh_exec "{host}" "{user}" "{cmd}" {timeout}',
  ```
  ```python
  # project_remover.py:361-365
  compose_cmd = (
      f"cd /opt/projects/{project} 2>/dev/null && "
      f"docker compose down --timeout {DOCKER_STOP_TIMEOUT} 2>&1 || "
      f"docker compose -p {project} down --timeout {DOCKER_STOP_TIMEOUT} 2>&1"
  )
  ```
- **Scenario:** это единственное место платформы, собирающее remote-команду f-string'ом: host/user/cmd интерполируются внутрь двойных кавычек без shlex.quote (ср. scp.py:126 — там quote есть). Бизнес-решения вшиты в shell-строку: fallback-цепочка юзеров (`ci-deploy` → `current_user`, L350-353), двухвариантный down (по каталогу → по `-p`), путь `/opt/projects/{project}` захардкожен вместо `shared/deploy_paths.projects_base()` — при том что payload_deliverer.py и project_collector.py этот литерал уже мигрировали («литерал /opt/projects удалён»).
- **Impact:** проект с кавычкой/`;` в имени или host из env ломает канал (инъекция в shell); смена канонического владельца проектов или PROJECTS_BASE тихо оставит контейнеры висеть (remove-project = деструктивная операция с fallback на «manual step»); гейт ssh_opts_sole_path обойдён — второй shell-потребитель SSH-флагов возник именно здесь.
- **Minimal fix:** перейти на `shared.ssh_cmd_builder.build_ssh_cmd` / ssh_exec через `remote_executor`, `shlex.quote(project)`, путь из `deploy_paths.projects_base()`; бизнес-fallback (юзер, -p) оставить в Python, не в shell-строке. · **Churn:** ~60 LOC, 1 файл + тесты · **Phase:** Pre-launch

## ARCH-602: reconciler считает результат docker image prune по строкам stdout
- **Severity:** Medium · **Confidence:** High
- **Files:** core/internal/bootstrap/deploy/orphan_reconciler.py · **Symbols:** `_self_heal_aged_images` (L462-509)
- **Evidence:**
  ```python
  # orphan_reconciler.py:473-482 — прямой subprocess, prune не покрыт docker_ops
  result = subprocess.run(["docker", "image", "prune", "-f", "--filter",
      f"until={retention_days * 24}h", ...], ...)
  # :494-495 — бизнес-метрика из текста
  lines = [line for line in stdout.splitlines() if line.strip() and not line.startswith("Total")]
  pruned = len(lines)
  ```
- **Scenario:** self-heal ветка reconciler'а (бизнес-решение «prune образы старше retention_days») идёт напрямую в docker CLI: `docker image prune` отсутствует в shared/docker_ops (проверено — нет ни одной prune-функции), а счётчик удалённых образов выводится из эвристики «число строк, не начинающихся с Total». Плюс локальный таймаут-литерал `DOCKER_RM_TIMEOUT: int = 30` (L56) рядом с каноническим IMAGE_CHECK_TIMEOUT из shared/timeouts.
- **Impact:** изменение формата вывода `docker image prune` (например добавление summary-строк/progress в новых версиях) даст ложный pruned-count → неверные IMP:9-логи и метрики self-heal; retention-политика (30d) размазана между node.yaml и f-string; вторая точка правды для docker-таймаутов.
- **Minimal fix:** добавить `docker_ops.image_prune(filters=...)` в фасад с парсингом в одном месте; счётчик — либо `--format {{.Deleted}}`, либо парсер внутри docker_ops; DOCKER_RM_TIMEOUT → shared/timeouts. · **Churn:** ~40 LOC, 2 файла · **Phase:** Pre-launch

## ARCH-603: K3 verify_contracts несёт собственный _run_docker и таймауты вне SoT
- **Severity:** Medium · **Confidence:** High
- **Files:** core/internal/deploy/verify_contracts.py · **Symbols:** `_run_docker` (L856-870), `_COMPOSE_CONFIG_TIMEOUT`/`_BUILD_CHECK_TIMEOUT` (L100-101), contract_build_check (L830-844)
- **Evidence:**
  ```python
  # verify_contracts.py:100-101
  _COMPOSE_CONFIG_TIMEOUT: int = 30
  _BUILD_CHECK_TIMEOUT: int = 120
  # :775 — compose-вызов мимо shared/docker_compose
  rc, out, err = _run_docker(["docker", "compose", "config", "--quiet"], project_dir, _COMPOSE_CONFIG_TIMEOUT)
  ```
- **Scenario:** K3-канал верификации контрактов на VPS (бизнес-правила L1/L2/L3) реализует инфраструктуру сам: приватный never-raise subprocess-хелпер дублирует семантику `docker_ops._run_docker`/`docker_compose`, а таймауты заданы локальными константами вне shared/timeouts (parity-гейты покрывают SoT-файлы, эти литералы не видят).
- **Impact:** рассинхронизация поведения при сбоях docker (код возврата 127/124 уже своя конвенция) — один и тот же сбой в deploy-канале и K3 трактуется по-разному; тюнинг таймаутов требует правки business-файла; при смене `docker build --check` (BuildKit-only флаг) ломается класс L2 молча.
- **Minimal fix:** `_run_docker` → `docker_ops.run_quiet(...)` (или существующий примитив), таймауты → shared/timeouts (COMPOSE_CONFIG_TIMEOUT, BUILD_CHECK_TIMEOUT). · **Churn:** ~30 LOC, 2 файла · **Phase:** Pre-launch

## ARCH-604: e2e-verify TLS-проверка парсит openssl-текст как контракт
- **Severity:** Medium · **Confidence:** High
- **Files:** core/internal/verify_sweep/tls_check.py · **Symbols:** `_fetch_s_client` (L198+), `_cert_days_left`, `check_san` (L364-367)
- **Evidence:**
  ```python
  # tls_check.py:14-15,77 (контракт вывода зашит в инварианты модуля)
  ##   - SAN берётся из leaf (первого) PEM через `openssl x509 -noout -ext subjectAltName`
  ##   - expiry: _cert_days_left (openssl x509 -enddate) → expiry_verdict (<14d WARN, <0 FAIL)
  ##   - 'DNS:' префикс SAN срезается (openssl x509 -ext subjectAltName формат)
  ```
- **Scenario:** sweep-верификация ноды держит неявный контракт с версией OpenSSL: `-ext` существует только в OpenSSL ≥1.1.1, парсинг SAN опирается на точный префикс `DNS:` и перенос строк `s_client -showcerts`; days_left считается из человекочитаемого `notAfter=` текста.
- **Impact:** обновление ноды/образца LibreSSL/OpenSSL 3.x с изменённым выводом → SAN/expiry-вердикты fail-verbose (ложные RED при e2e-verify) или None-ветки; диагностика release-checklist шага 4 становится недостоверной именно тогда, когда она нужна (после пересоздания ноды).
- **Minimal fix:** изолировать весь openssl-парсинг в один хелпер (shared/ssl_certs.py уже существует — расширить parse_san/parse_expiry туда) с версионным smoke-тестом формата. · **Churn:** ~50 LOC, 2 файла · **Phase:** Post-launch

## ARCH-605: context_deployer инлайнит chown-политику владельца и таймауты 30/60/120
- **Severity:** Low · **Confidence:** High
- **Files:** core/internal/bootstrap/deploy/context_deployer.py · **Symbols:** `_step_projects_stub` (chown L573-581), `_step_vhosts` (L1079-1089), `_step_verify` (L1159-1169)
- **Evidence:**
  ```python
  # context_deployer.py:574-581
  ["chown", "ci-deploy:ci-deploy", project_dir], capture_output=True,
  text=True, timeout=30, check=False,
  ...
  # :1083/:1163 — bash add-vhost.sh timeout=60; domain_verifier timeout=120
  ```
- **Scenario:** φ8-оркестратор бутстрапа держит три класса инфраструктуры инлайн: каноническое имя владельца проектов `ci-deploy:ci-deploy` (дублирует ensure_projects_base/users.py — TRAP[BUG] 141 r2 это уже фиксирует словами «синхронизировать здесь и в users.py»), таймауты-литералы 30/60/120 вне shared/timeouts, и вызов bash-скриптов scaffold по пути, склеенному из core_dir.
- **Impact:** переименование сервисного юзера или пересмотр владельца → тихий no-op (check=False, capture_output) с последующим воспроизведением P1-инцидента 141 (permission denied на receive); таймауты не поднимаются централизованно при медленных нодах.
- **Minimal fix:** константа владельца в shared (рядом с users.py), таймауты → shared/timeouts, chown → общий helper (subprocess_io). · **Churn:** ~20 LOC, 2-3 файла · **Phase:** Post-launch

## ARCH-606: HTTP-слой дублируется там, куда фасад недостижим или отклонён
- **Severity:** Low · **Confidence:** Medium
- **Files:** core/modules/hermes-agent/healthcheck_deps.py, core/internal/llm/admin_client.py · **Symbols:** `urlopen` (healthcheck_deps.py:187), `httpx.Client` (admin_client.py:118)
- **Evidence:**
  ```python
  # healthcheck_deps.py:187 — modules/ не может импортировать internal/shared
  resp = cast("http.client.HTTPResponse", urllib.request.urlopen(url, timeout=timeout))
  # admin_client.py:2 — TRAP[DECISION] 177 W4 S13: admin_client остаётся на httpx
  # · Rev: если появится 3-й httpx-потребитель ИЛИ http_client получит AsyncClient
  ```
- **Scenario:** два неохваченных сетевых потребителя: (а) hermes-healthcheck вынужден тащить сырой urllib + redis-cli-PONG-парсинг (L146-160) потому что cross-layer правило запрещает modules/ импортировать internal/ — инфраструктурное дублирование структурно навязано слоением; (б) LiteLLM-admin осознанно на httpx (async), документировано TRAP'ом с условием ревизии.
- **Impact:** proxy/TLS-политика (единая в http_client.build_opener) не применяется к этим путям — например TOR-proxy для telegram есть, для hermes-healthcheck нет; смена политики исходящих соединений требует поиска всех «вторых» HTTP-стеков; redis-cli-парсинг «PONG» — ещё один version-contract.
- **Minimal fix:** (а) разрешить modules/ импорт read-only подмножества shared/ (или публиковать lib/http.py фасад) и перевести healthcheck_deps на него; (б) для admin_client — отслеживать условие Rev (3-й потребитель → консолидация). · **Churn:** средний (cross-layer правило + 1 модуль) · **Phase:** Post-launch

---

### Не подтверждено (проверено, чисто)
- psql/PGPASSWORD вне postgres-модуля: единственное вхождение project_scaffolder.py:627 — текст генерируемого markdown-чеклиста, не вызов.
- boto3/S3 вне s3_client.py: только modules/backup-cron (исключение по фокусу 5).
- Telegram API из бизнес-логики: watchdog/cert_expiry/reboot/security_updates идут через зарегистрированный notify-hook канал; прямых api.telegram.org вне shared/ нет.
- Retry/rollback: deploy-каналы используют shared.retry (channels/base.py:40), DeployOrchestrator.rollback идёт через engine+docker_ops — бизнес-решения не вшиты в raw-subprocess (кроме ARCH-601 fallback-цепочки).

### Приоритет фиксов
ARCH-601 (деструктивная операция + инъекция) → ARCH-602/603 (парсеры и таймауты в фасады) → 604-606.
