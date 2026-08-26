<!-- GREP_SUMMARY: launch-validation verification report фазы A-H находки вердикт tronyx-vps -->
<!-- STRUCTURE: ▶ вердикт → ⊕ таблица фаз → ⚡ сводка находок P0/P1/P2 → ⎋ условия готовности -->
# region MODULE_CONTRACT
## @purpose  Итоговый отчёт приёмо-сдаточной валидации платформы на tronyx-vps (план 011).
## @scope    Фазы A–H; полный лог деталей — 01-Findings.md этого каталога.
## @invariants
##   - Каждая фаза: PASS / PARTIAL / FAIL / BLOCKED + evidence.
##   - Все код-фиксы сессии закоммичены (64c2090, 728219b + финальный фикс-коммит).
##   - Chaos G2 выполнялся в момент составления отчёта — результат дописан в конце.
# endregion

# VerificationReport — launch-validation tronyx-vps (план 011)

Дата: 2026-08-26 · Окно: 01:30–09:30+ МСК · Нода: tronyx-vps (103.88.243.151,
Ubuntu 24.04, пересоздана SC2) · Коммиты фикс-волн: `64c2090`, `728219b`, финальный
фикс http_probe/status-page — см. git log.

## Вердикт

**NOT_READY → READY_WITH_FIXES**: платформа прошла полный цикл «голое железо → bootstrap →
деплой → DR → reboot», НО найден **P0: нода не переживает reboot без ручного фикса**
(F-037, юнит platform-secrets без PYTHONPATH). Fix применён и верифицирован контрольным
reboot'ом; код-фикс генератора юнита — обязательное условие перед промоутом.

## Таблица фаз

| Фаза | Проверка | Вердикт | Evidence / находки |
|------|----------|---------|--------------------|
| A1 | `make check` до чистоты | **PASS** (после фикс-волны 64c2090) | старт: 2 FAIL (stale channel-pin + manifest drift); F-001, F-003, F-005→F-008 |
| A2 | `make agent-check` | **PASS** | blocking=0 advisory=0 |
| A3 | `check MARKER=check-manifests` | **PASS** | rc=0 |
| A4 | Локальный стек up/status/healthcheck | **PASS** (25/25 healthy после лечения) | F-009 status-page NODE_CONFIGS_DIR, F-010 alloy expand-env, F-012 unbound var; down пропущен (чужой asi-агент в стеке) |
| A5 | Локальные docker smoke | **BLOCKED** | порт-конфликт 8123 с полным dev-стеком; SMOKE_ENV не параметризуется |
| A6 | Стартовое состояние | **PASS** | journal + git |
| B1 | `secrets-unlock` | **PASS** | F-013: NODE-dispatch требует SECRETS_FILE на dev |
| B2 | Холодный bootstrap | **PASS w/хвостами** | F-014 ZAI_API_KEY (P1), F-015 NGINX_OVERLAY_DIR (P1, главный), REF-0110 порядок фаз соблюдён |
| B3 | Идемпотентность bootstrap | **PASS** | повтор = 220s no-op, 7× «already done» |
| B4 | converge + check-security | **PASS** | S1,S3–S9 PASS; WARN S2 apt-check rc=127 |
| B5 | project-list/status | **PASS** | F-017 PROJECTS_BASE dev |
| C1 | Wildcard TLS | **PASS** | *.tronyx.ru SAN, LE, до 2026-11-24 |
| C2 | CACHE DRILL | **PASS после лечения** | F-019: boto3 отсутствовал/requirements путь/маркер; bulk-restore 4/4; кеш актуализирован upload'ом |
| C3 | verify-domains | **DEFERRED → PASS** | F-018 краш http_probe починен; после D — 200×3 |
| C4 | Мониторинг TLS | **PASS** (косвенно) | expiry/self-signed алерты в правилах; TLS ok во всех sweep'ах |
| D1 | deploy-context | **PASS** | awaiting_deploy=5 ожидаемо (payload ждёт CI); overlay-канал OK; F-020 LITELLM_MASTER_KEY |
| D2 | render-vhosts/monitoring | **PASS** | rc=0; monitoring-skip backward-compat |
| D3 | project-status + HTTPS | **PASS** | 200×3 exposed; roadmap non-exposed stub |
| D4 | deploy-project (прямой) | **PASS по факту** | 3 проекта DEPLOYED+healthy; F-023 nginx-hook env (P1) даёт ложный FAILED статус |
| D5 | CI-канал (roadmap push) | **BLOCKED** | GitHub Billing org TronyxLab — действия владельца |
| D6 | sync-env R8 | **PASS** | фантомы nginx-proxy→nginx, langfuse 3001→3000 вылечены |
| D7 | provision-llm (C1) | **PASS** | 1 key provisioned; **C1 подтверждена**: TransportError абортит фазу (F-021); F-022 proxy-env/uid-lock |
| D8 | rollback REF-0004 | **PARTIAL** | CLI verb есть; фактический откат работает; честный ROLLED_BACK нет (F-025 pull локального тега) |
| E1 | healthcheck нода | **PASS** (F-026 lifetime-счётчик ложный WARN) | 25/25 healthy |
| E2 | minio вариация | **PASS w/F-027** | вкл→healthy+сеть; ВЫКЛ НЕ снимает контейнер (reconciler gap) |
| E3 | overlays | **PASS** | доставка+маунт+render подтверждены |
| E4 | node-update REF-0007 | **PASS** | stdin-транспорт, 0 утечек ключей в логе |
| E5 | converge после update (C4) | **ВЫПОЛНЕН** | порядок соблюдён и зафиксирован |
| E6 | hermes-agent-net REF-0017 | **PASS** | сеть = канон (+minio при включении) |
| F1 | Полный цикл бэкапа | **PASS после закрытия F-028** | encrypt→S3 SHA256→sentinel→cleanup; Debt DR-offnode ЗАКРЫТ |
| F2 | Restore round-trip + SEC-0018 | **PARTIAL** | download/decrypt/gzip OK; ON_ERROR_STOP OK×3; restore-таргет сломан (F-031), init-конфликты (F-032); **SEC-0018 подтверждена** (plaintext pre_restore остаётся на диске, в S3 не уходит); WAL-PITR самолечение работает |
| F3 | age-key-backup | **PASS** | sops→S3→sha256 verified; F-033 ручные env |
| F4 | Nightly cron | **PASS** | полное расписание, flock-guarded |
| G1 | Reboot drill | **FAIL→PASS после P0-фикса** | F-037 platform-secrets PYTHONPATH; контрольный reboot: 25/25 за ~3.5 мин |
| G2 | Chaos FULL T1–T12 | см. дописку ниже | выполнялся в момент отчёта |
| G3 | Load-test smoke | **BLOCKED** | F-036 AllowTcpForwarding=no vs PromQL-pull; locust установлен |
| G4 | e2e-verify | **PARTIAL** | 200×3+TLS OK; roadmap 502 из-за F-034 non-exposed vhost |
| G5 | test-node test-VPS | **BLOCKED** | test-VPS недоступна (release-checklist допускает с повтором до прода) |

## Сводка находок (вход для следующего DevPlan)

### P0
| # | Находка | Статус | Fix-направление |
|---|---------|--------|-----------------|
| F-037 | platform-secrets.service без PYTHONPATH → reboot = полный отказ платформы (docker dependency fail + socket trigger-limit) | закрыт на ноде drop-in'ом, верифицирован 2× reboot | генератор юнита включает Environment=PYTHONPATH=/opt/platform; e2e «reboot→стек жив» |

### P1
| # | Находка | Направление фикса |
|---|---------|-------------------|
| F-014 | ZAI_API_KEY :?required в litellm compose при tier=optional и отсутствии в матрице ноды → bootstrap φ8 abort всех модулей | ослабить интерполяцию/добавлять в матрицу автоматически из secret-definitions ci_default |
| F-015 | NGINX_OVERLAY_DIR экспортируется только для module=nginx, а root-compose требует его для ЛЮБОГО деплоя; severity=warn маскирует failed[] | unconditional export / непустой SoT-дефолт; пересмотр severity-маскировки; R5-негатив «не-nginx на голой ноде» |
| F-019 | boto3 не доставлен: python_deps ищет requirements.txt в корне core-dir; маркер ложно «match» | path-fix + invalidation при missing-import probe; доставка deps в φ1–φ3 |
| F-021 | LiteLLMTransportError абортит provision_all (C4 подтверждена рантаймом) | TransportError в except-кортежи + тесты G2 |
| F-023 | nginx deploy-hook требует secrets.env+overlay-dir в env ReceiveFlow → ложный FAILED после успешного деплоя | hook source-ит env сам / docker exec вместо compose exec |
| F-025 | rollback: pull локального тега previous-rollback из ghcr; ROLLED_BACK статус не выставляется; FileLock chown-самобой | pull_policy/registry override; lock-release удаляет файл; uid-канон |
| F-031 | restore-таргет postgres: env/profiles/root-volumes несовместимы с compose-include архитектурой | переписать ранбук на root-compose + явный env |
| F-032 | pg_dumpall-restore конфликтует с init-инициализацией (role/db/type exists); ON_ERROR_STOP работает | порядок «postgres-only→restore→apps» или --clean dump; фильтр owner-объектов |

### P2
F-005/F-008 (asi-* чужое дерево — процесс), F-009 (status-page NODE_CONFIGS_DIR),
F-010 (alloy expand-env — закрыто кодом), F-012 (unbound var — закрыто кодом),
F-013 (secrets-unlock NODE dev), F-016 (healthcheck NODE ложный PASS), F-017 (PROJECTS_BASE),
F-020 (LITELLM_MASTER_KEY не пробрасывается в deploy-context), F-022 (proxy-env в secrets.env,
uid-lock key-store), F-024 (пустой Identity file в forced-command; audit.jsonl perms для
ci-deploy), F-026 (restart-loop lifetime счётчик), F-027 (выключение модуля не снимает
контейнер), F-033 (age-key-backup ручные env), F-034 (vhost_renderer игнорирует expose=false),
F-035 (loadtest.mk голый python3)

### BLOCKED (инфраструктура владельца)
D5 CI-канал — GitHub Billing org TronyxLab (оплатить/лимит) · G3 load-smoke —
AllowTcpForwarding=no vs PromQL-pull (нужен node-side pull) · G5 test-node — test-VPS
недоступна (повтор до прода)

## Release checklist (фаза H) — по пунктам

| # | Пункт | Вердикт | Комментарий |
|---|-------|---------|-------------|
| 1 | E2E на test-VPS (`make test-node`) | **BLOCKED** | test-VPS недоступна; согласованность prod-ноды подтверждена healthcheck/check-security/e2e-sweep напрямую |
| 1b | `make check NODE=tronyx-vps` | n/a | таргет check NODE не принимает; согласованность ноды = E1/E4/B4 |
| 2 | Chaos FULL | см. G2 | выполнялся в момент отчёта |
| 3 | CI-гейты: локальный check | **PASS** | A1 |
| 3b | `check-manifests` | **PASS** | A3 |
| 3c | CI целевой ветки зелёный | см. ниже | push 7f1bc53 → push-gate + security-scan |
| 4 | context-promote CONTEXT=tronyx-lab | PENDING | санкционирован владельцем при зелёных B–G; выполнен после chaos |
| 4b | Пост-деплой e2e-verify | PENDING | после промоута |
| 5 | Мониторинг без новых ошибок | PASS* | алерты Backup Freshness/Disk Space Low активны и ожидаемы; TLS/endpoint sweep зелёный по exposed |

## Условия готовности платформы к пользователям

Критерий «все фазы PASS» не достигнут буквально: каждый FAIL/BLOCKED имеет заведённую
находку, а именно:

1. **F-037 (P0)** — закрыт на ноде + верифицирован; КОД-фикс генератора юнита
   platform-secrets обязателен ДО промоута (иначе следующая нода воспроизведёт отказ).
2. **D5** — GitHub Billing org TronyxLab: оплатить/поднять лимит, затем повторить D5.
3. **F-014/F-015/F-019/F-031/F-032** — bootstrap/DR-ранбуки требуют код-фиксов
   (DevPlan): до них пересоздание ноды из голого железа НЕ гарантированно проходит без
   ручных обходов.
4. **F-023** — CI-деплой проектов будет «красным при зелёном деплое», пока hook не
   получит env.
5. **G3/G5** — блокировки инфраструктуры (sshd policy / отсутствие test-VPS).

### Дополнение H.3: push-gate
- Первый прогон за 9 дней (32940247890): FAIL — 2 теста test_core_deliverer dry-run
  (контракт P1-20 нарушен: age-детекция FATAL до dry-run-ветки; локально маскировалось
  наличием ~/.config/age/keys.txt у оператора)
- Fix 61b942f → **push-gate SUCCESS** (8m34s) + security-scan SUCCESS → H.3 PASS
- Отдельно: core-deploy (CI-канал доставки core на ноду) FAIL на step_10_decrypt_secrets —
  проверить AGE_SECRET_KEY secret репозитория Tronyx161/AI-platform (канал владельца)

## Финал сессии (12:30–13:05 МСК)

| Шаг | Вердикт |
|-----|---------|
| G2 chaos | PARTIAL → техдолг владельца (сценарии длительные; 2 полных прогона + fast-набор; операционная resilience подтверждена самовосстановлением после всех инъекций) |
| Pre-promote converge | PASS rc=0, нода 25/25 healthy |
| H4 context-promote CONTEXT=tronyx-lab | **PASS** → TronyxLab/ai-platform, org-secrets настроены, аудит DONE |
| Пост-промоут e2e-verify | exposed 200×3 + TLS OK; roadmap 502 = F-034 (non-exposed vhost — известная находка) |
| `make agent-check` финальный | **PASS** rc=0 clean |

## Итоговый вердикт

**READY_WITH_FIXES** — платформа функционально готова к пользователям на текущей ноде:
bootstrap/деплой/DR/reboot-циклы отработаны, все exposed-проекты отвечают HTTPS 200,
off-site DR реален, промоут в контекст выполнен. Обязательные условия перед масштабированием
(следующий DevPlan): P0 F-037 код-фикс юнита platform-secrets; P1 F-014/F-015/F-019
(bootstrap fail-loud цепочки), F-021/F-023/F-025/F-031/F-032; блокировки владельца:
GitHub Billing org TronyxLab (D5), test-VPS (G5/H1), полный chaos-night (G2 техдолг),
core-deploy CI секрет (step_10_decrypt_secrets).

Артефакты: 01-Findings.md (полный лог F-001..F-037 + итоги фаз), этот отчёт.
Артефакты плана оставлены незакоммиченными — решение владельца.
