# Направление 7 — Duplicated business logic

Метод: сверка канонических singleton'ов (ssh_opts, timeouts, retry, yaml_loader, criterion healthcheck) с фактическими копиями; поиск кластеров ≥2 реальных копий. Агент: explore, 19 tool calls. Дата 2026-08-22.

## ARCH-0009 — третий rogue-экземпляр healthcheck-retry в bootstrap reporting
- Severity: MEDIUM · Confidence: HIGH · Churn: S · WHEN: pre-launch
- Files: core/internal/bootstrap/lifecycle/helpers/reporting.py:130-165 (rogue); канон: shared/module_interface.invoke + bootstrap/deploy/healthcheck_runner.py:98-137
- Symbols: run_healthchecks vs run_healthcheck/_invoke_healthcheck_full
- Evidence: reporting.py строит `bash -c "source paths.sh && invoke_module_interface …"` inline, литералы hc_max_retries=10/hc_retry_interval=10/timeout=30 вне timeouts.py; runner использует invoke_healthcheck_full c политикой из SoT
- Scenario: изменение политики healthcheck в SoT никогда не доезжает до bootstrap-верdict'ов φ11/φ13; inline bash-обёртка обходит фикс TRAP[BUG] 2026-07-24 в module_interface.invoke
- Impact: расхождение verdict'ов деплоя против канона; тихий обход багфикса
- Minimal fix: run_healthchecks → цикл shared module_interface.invoke(mod,"healthcheck","liveness"), политика из timeouts.py

Примечание (doc-drift, не код): канонический docker-критерий физически живёт в shared/docker_compose.py:593 (`health in {"healthy","","none"}`), poller корректно делегирует; формулировка в AGENTS.md («Python-реализация — только deploy/healthcheck_poller.py») устарела локацией, не семантикой.

## ARCH-0010 — SSH-флаги захардкожены в 4 местах CI вразрез с ssh_opts.py SoT
- Severity: MEDIUM · Confidence: HIGH · Churn: S · WHEN: pre-launch
- Files: .github/workflows/deploy-project.yml:342,362,372; .github/workflows/mirror.yml:162; SoT core/internal/shared/ssh_opts.py:40-51
- Symbols: SSH_OPTS / build_rsync_ssh_opts vs inline `-o BatchMode=yes -o StrictHostKeyChecking=accept-new …`
- Evidence: divergence УЖЕ есть: CI опускает ConnectTimeout (нет connect-bound на production receive-канал), добавляет UserKnownHostsFile; mirror.yml использует IdentitiesOnly=yes
- Scenario: тюнинг SoT (ServerAlive/timeout) молча не доезжает до tar/ssh forced-command канала деплоя
- Impact: production deploy-канал живёт на неуправляемом наборе флагов
- Minimal fix: один job-level `SSH_OPTS` из `python3 -m core.internal.shared.ssh_opts --shell`, переиспользовать во всех шагах

## ARCH-0011 — YAML load+cast+ручные key-checks размножены по манифестам
- Severity: MEDIUM · Confidence: HIGH · Churn: M · WHEN: pre-launch
- Files: bootstrap/deploy/spool_validator.py:199; compose_preflight.py:196; secrets_validator.py:158,209,264,411,456; discover_modules.py:141; SoT: shared/yaml_loader.py (покрывает лишь 2 SoT-файла)
- Symbols: cast(...)+yaml.safe_load+or{} паттерн ×8
- Evidence: идентичный каркас с РАЗНОЙ graceful-degradation: `{}` vs `None` vs `[]` vs raise — на каждый файл своя
- Scenario: изменение типизации module.yaml обновляет часть загрузчиков; вердикты spool_validator/secrets_validator/compose_preflight расходятся → несогласованные deploy-гейты
- Impact: inconsistent gate behavior на манифестах
- Minimal fix: расширить shared/yaml_loader.py типизированными ридерами (load_module_yaml/load_secrets_manifest) с единым degrade-каноном; перевести 4 callers

## ARCH-0012 — самописные retry/backoff петли рядом с каноном shared/retry.py
- Severity: LOW · Confidence: HIGH (у backup-cron — MED: слой modules→lib может оправдывать локальность) · Churn: S · WHEN: post-launch
- Files: core/modules/backup-cron/scripts/upload.py:449-479; bootstrap/install_tor_proxy.py:557-574; bootstrap/issue_cert.py:393-401; SoT shared/retry.py:155-199
- Symbols: upload_with_retry / verify_tor_circuit / _acme_issue_with_retry
- Evidence: три bespoke-петли со своими константами (VERIFY_SLEEP_SEC=5, 10, ctx.max_attempts) при существующем retry(func, attempts, backoff_seconds, retryable)
- Impact: тюнинг retry-политики требует правок N файлов; тихое расхождение sleep/attempts
- Minimal fix: bootstrap-пару (install_tor_proxy, issue_cert) → shared.retry + timeouts.py; upload.py оставить с комментарием-исключением слоя

## ARCH-0013 — «git clone + idempotent re-run + timeout» реализовано дважды
- Severity: LOW · Confidence: MED · Churn: S · WHEN: post-launch
- Files: bootstrap/deploy/context_overlay.py:237-276 (канон _clone_context_repo); bootstrap/install_acme.py:159-190
- Evidence: разные источники таймаута (LIFECYCLE_CMD_TIMEOUT vs собственный) и разная семантика re-run (non-fatal WARN vs merge-fallback .clone-tmp, TRAP[BUG] 2026-08-04 P1)
- Impact: низкий — разные репо; риск только при дрейфе acme-ветки
- Minimal fix: при очередном касании — extract clone_with_merge_fallback(repo, dest, timeout) в shared

## Checked clean (копий ≥2 не найдено)
- Расшифровка секретов: единственная в core/internal/secrets/decrypt_secrets.py
- Template-рендер: template_engine.py единственный {{UPPER_SNAKE}}-движок (Jinja2 — llm/status-page by design)
- JSONL-writers: audit_logger и test_journal — два различных домена/схемы, третьего ручного аппендера нет
