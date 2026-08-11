# GREP_SUMMARY: DevPlan 145-02, debt-elimination, registry-verification, CI-secrets-restore, code-cleanup, security-CI, chaos-windows
# STRUCTURE: ┌контекст+сводка верификации (67 пунктов → вердикты)┐ → ◇ TRAP[DECISION] (5) → ┌код-граф XML┐ → ┌волны W1-W5┐ → ◇ acceptance criteria → ⎋ verification + операторские зависимости

$START_DEVPLAN

# DevPlan 145(02) — Устранение технического долга по верифицированному реестру

$ARTIFACT_CONTRACT
PURPOSE:               Верифицировать ВСЕ 67 пунктов `00-TECHNICAL-DEBT-REGISTRY.md` против
                       фактического состояния (git history, код, CI-workflows, gh-секреты,
                       live-эндпоинты, evidence-папки) и устранить подтверждённый долг волнами:
                       W1 — операторские действия (восстановление секретов CI, блокер R15);
                       W2 — синхронизация реестра (8 пунктов закрыты фактически, реестр устарел);
                       W3 — код-чистка (S-пункты: sudoers, ротация, pipx, IMP:9, TRAP[DEBT] I1-I5);
                       W4 — безопасность/CI (L3/L4/L5, M-пункты);
                       W5 — операционные окна (drill/chaos/деплой-верификация, L/XL).
DESCRIPTION:           Верификация 2026-08-11 (этот девплан): по каждому OPEN/partially-done/
                       unknown/deferred пункту собран evidence (git log, rg-скан, gh CLI,
                       curl, файлы evidence). Итог: 11 пунктов фактически CLOSED, 2 статуса
                       уточнены, 2 описания устарели (chaos r2, B7), 4 решения пользователя
                       зафиксированы. Волны исполнения — от блокера (секреты) к коду и
                       операционным окнам. Реестр — единый SoT; волны W2 обновляют его.
RATIONALE:             Реестр составлен 2026-08-11 агрегацией 4 субагентов (read-only) —
                       без сверки с живым состоянием. Сверка выявила: negative-тест MaxStartups
                       существует, untracked-артефакты закоммичены (db7db81d), README создан
                       (6be4896c), package-lock добавлен (bcb2e741), секрет CI_DEPLOY_KEY
                       отсутствует (14 секретов, проверено gh CLI) при живой локальной паре
                       (~/.ssh/platform_personal_cicd + pub на ноде — receive SUCCESS в 141).
                       Без верификации план «устранения» содержал бы уже-закрытые пункты.
ACCEPTANCE_CRITERIA:   AC1: gh-секреты CI_DEPLOY_KEY (Tronyx161/AI-platform + TronyxLab/*)
                       и DOCKER_HUB_USERNAME/DOCKER_HUB_TOKEN восстановлены; `gh secret list`
                       показывает их; CI-деплой реального проекта проходит (B6/R15/R17).
                       AC2: реестр 00-TECHNICAL-DEBT-REGISTRY.md синхронизирован (версия 2.0):
                       все CLOSED-решения задокументированы с evidence, метрики пересчитаны.
                       AC3: W3-фиксы: `make check` зелёный; `rg "TRAP\[DEBT\]"` в коде = 0
                       (вне .ai/plans); TRAP[DEBT] I1-I5 сняты/закрыты; ROTATION.md удалён;
                       sudoers nginx удалены; pipx-шаг удалён из pre-push-gate.
                       AC4: CI-сканеры L3 (trivy/pip-audit/dependabot-pip) и scheduled-workflow
                       L4 присутствуют в .github/; L5-фундамент (fail2ban/auditd) в security_posture.
                       AC5: операционные окна выполнены с evidence: drill AGE (до 2026-08-31),
                       chaos-окно (до 2026-09-15), deploy.sh удалён после audit-проверки
                       (до 2026-11-01), hermes-500 диагноз закрыт (upstream или L2-патч).
                       AC6: evidence-папки 126/141 заархивированы после закрытия chaos-долга
                       (зависит от 145 W4 + Rev 2026-09-15).
IMPLEMENTS:            Запрос оператора 2026-08-11: «проверь реестр техдолга — реализован ли,
                       требуется ли применение, не устарел ли; создай девплан на устранение».
                       Решения оператора 2026-08-11: (1) R15 — восстановить секрет из локальной
                       пары; (2) VR-гэп — принять git log + 142 VR §3 как evidence; (3) nginx
                       sudoers — удалить; (4) POSTGRES-ротация — НЕ делать, удалить упоминания.
IMPACTS:               .ai/plans/145-repo-cleanup-debt-registry/00-TECHNICAL-DEBT-REGISTRY.md —
                       ОБНОВЛЕНИЕ (статусы/метрики); gh-секреты Tronyx161/AI-platform +
                       TronyxLab/* — ВОССТАНОВЛЕНИЕ; core/internal/bootstrap/setup-node.sh —
                       удаление sudoers nginx; core/modules/postgres/ — удаление ROTATION.md +
                       комментариев ротации; core/entrypoints/pre-push-gate.sh — удаление pipx;
                       core/internal/bootstrap/lifecycle/state_store.py — IMP:9; tests/unit/ —
                       4 фикса TRAP[DEBT]; core/internal/scaffold/vhost_renderer.py — реализация
                       configure_vhost_for_project; .github/ — L3/L4/L5; нода — операционные окна.
REQUIRES:              Оператор: (а) `gh secret set` (R15/R17) — команды в W1; (б) DOCKER_HUB_TOKEN
                       от оператора (R17); (в) окно для DR-drill (до 2026-08-31) и chaos-прогона
                       (до 2026-09-15); (г) доступ к ноде для audit-проверки deploy.sh (W5);
                       (д) решение по evidence-архивации (145 W4) и remote-веткам (145 W2).
$END_ARTIFACT_CONTRACT

---

## 1. Контекст и диагноз — сводка верификации реестра (2026-08-11)

> Метод: git log/history, rg-скан кода и тестов, чтение CI-workflows, `gh secret list`,
> curl live-эндпоинтов, evidence-файлы 126/141/142. Каждый вердикт — с evidence-строкой.

### 1.1 Фактически закрыто (реестр устарел — статус → [CLOSED] в W2)

| ID | Вердикт | Evidence |
|----|---------|----------|
| D-134-S4 | **CLOSED** | `tests/unit/test_security_posture_maxstartups.py::test_negative_default_10_30_100` — MaxStartups=10:30:100 → STATUS_FAIL (точный вход бага, R5); `test_negative_component_below` — rate 40<50 → FAIL. Negative-покрытие есть (имя иное, чем в реестре). |
| D-J1 | **CLOSED** | `git ls-files` — 142/07-StatusReport.md, 143/01-DevPlan.md, 144/01-DevPlan.md tracked; коммит `db7db81d` (145 W1). |
| D-J2 | **CLOSED** | `.ai/plans/README.md` tracked; коммит `6be4896c` (145 W5) документирует коллизию 141. |
| D-142-B37 | **CLOSED** | `templates/template-frontend/package-lock.json` существует; добавлен коммитом `bcb2e741` (143 W+B37). |
| D-140-P5 | **CLOSED** | VR 140:217 — P5 BLOCKED только bash-permission на момент VR; после merge 140 циклы 141-145 прогнали gates зелёными — affected-тесты исполнялись многократно. |
| D-136-W9-T9.19 | **CLOSED** | Оба узла (tronyx-vps, test-e2e) пересозданы 2026-08-06 (141): legacy-маркеров физически нет; код пишет per-context (orchestrator_metrics.py:132-133, CONTEXT→суффикс). Долг obsolete-by-recreation. |
| D-143-W1A-W1B-W2 | **CLOSED** (VR→J3) | Реализовано: `bcb2e741`/`b7a11860`/`95cc9145` (W1A file-scrape, W1B cron-env, W2 guard). Остаточный дефект (Loki binop) закрыт 144 W1. |
| D-144-W1-W2-W3 | **CLOSED** (VR→J3) | `62120d45` + follow-ups `f671491e` (cadvisor 256→512M) + `891e2393` (working_set_bytes); `alerting/alert-rules.yml:43-45` — «expr без binop `< 1`» в файле. |
| D-136-W11-C-8-residual | **resolved-as-decision** | core-deploy.yml:49-60 TRAP[DECISION] 2026-08-05: root-shell риск-принят (setup-ssh, cleanup, known_hosts), Rev 2026-10-21. Долг переоформлен в мониторинг. |
| D-137 / D-138 | **CLOSED** (решение оператора) | Практики реализованы: `core/internal/practices/` (check/escalator/maturity/manifest/set/sync), K2 в deploy-project.yml (gitleaks pin, maturity, blocking); D-138: makefiles/*.mk split (project-practices.mk и др.), глоссарий AGENTS.md. VR не созданы — решение: принять git log + 142 VR §3 (I1-I7) как evidence (вопрос 2). |

### 1.2 Частично сделано — статус уточнён (реестр обновить)

| ID | Вердикт | Evidence |
|----|---------|----------|
| D-136-B6 | **partially-done→OPEN** | 141-цикл РЕАЛЬНО верифицировал core-deploy канал (SSH✅/rsync✅/provision❌ → баг scripts/ найден, фикс в core-deploy.yml:190-199 TRAP[BUG]). Project-деплой через CI workflow НЕ верифицирован (deploy-*-r2.log — все LocalChannel). Закроется после R15 (W1). |
| D-136-B7 | **OPEN→partially-done** | certs-r2.md: все 4 домена — реальные LE (не self-signed), восстановлены из S3-кеша и валидированы; НО 0 вызовов `acme.sh --issue` — fresh-выпуск (ACME rate-limit, DNS-01) не тестирован. |
| D-142-R17 | **OPEN→partially-done** | Механизм есть: platform-test.yml:171-174 docker/login-action (DOCKER_HUB_AUTH-гейт, C-11); НО DOCKER_HUB_USERNAME/DOCKER_HUB_TOKEN отсутствуют в gh-секретах → DOCKER_HUB_AUTH='false' → анонимные пуллы. |

### 1.3 Подтверждённый OPEN (требует работы)

| ID | Вердикт | Evidence |
|----|---------|----------|
| D-142-R15 | **OPEN (HI, блокер)** | `gh secret list -R Tronyx161/AI-platform` — 14 секретов, CI_DEPLOY_KEY ОТСУТСТВУЕТ. Локальная пара жива: `~/.ssh/platform_personal_cicd(.pub)`; pub на ноде работает (141: receive SUCCESS forced-command). Путь (а) реестра «выгрузить из gh-секрета» НЕВОЗМОЖЕН — секрета нет. Фикс: восстановить из локальной пары (решение оператора). |
| D-136-W10-S-13-drill | **OPEN (HI, 2026-08-31)** | В 126/141 evidence нет следов AGE master-key drill (off-node encrypted backup + restore-first). Требует операторского окна. |
| D-126-D5 | **OPEN (2026-09-15)** | 126 files/T7/verdict.json — PARTIAL (kernel-oom found count=12, clickhouse healthy — жертва НЕ clickhouse); chaos-r2.log (141, attempt #30) — T7 FAILED. OOM-clickhouse victim не верифицирован. |
| D-126-T9-T11 | **OPEN (2026-09-15)** | 126: T9/T10/T11 — evidence без verdict.json (не завершены); r2: T9/T10/T11 FAILED. |
| D-142-Chaos-T6-T10 | **OPEN — описание реестра УСТАРЕЛО** | chaos-r2.log (attempt #30): T4/T5/T6 GREEN; T1/T2/T3/T7/T8/T9/T10/T11 RED (прогон шёл во время восстановления ноды — частично сконфаунжен: T1 «backup-cron/cadvisor not recovered»). Реестр утверждал «T4/T11 GREEN, T6 RED» — неверно для r2. |
| D-134-L3 | **OPEN** | dependabot.yml — только github-actions ecosystem; trivy/pip-audit отсутствуют (rg по .github/, pyproject, check-suite = 0). |
| D-134-L4 | **OPEN** | Ни один workflow не содержит `schedule:` (rg cron = 0). |
| D-134-L5 | **OPEN** | security_posture.py:36 — «fail2ban/auditd — L5 follow-up» (комментарий в коде). |
| D-139-T1 | **OPEN (2026-11-01)** | deploy.sh существует (175 LOC), 0 callsites в core (rg); оба узла пересозданы → authorized_keys пишутся φ2 (forced-command dispatcher). Удаление обосновано; audit-проверка лога — 1 SSH (W5). |
| D-136-state-store-IMP9 | **OPEN (S)** | state_store.py save_state: IMP:6 (debug) + IMP:10 (error); IMP:9 на успешный save (flock+tmp→rename) отсутствует. |
| D-142-B38 | **OPEN (S)** | pre-push-gate.sh:48 — `pipx install --force` остаётся (non-blocking, но исполняется на КАЖДЫЙ push; flake-корень probe-тестов закрыт 129 W2). |
| D-135-hermes-500 | **OPEN (LOW)** | LIVE-проверка 2026-08-11: GET / → 302 → /auth/login?provider=basic → **500**; login-GET = 500. Подтверждено. |
| D-I1..I5 | **OPEN (1 MED, 4 LOW)** | Все 5 TRAP[DEBT] живы в коде (test_vhost_configurator.py:25, test_compose_validator.py:21, test_practices_check_project.py:140, test_generate_catalog.py:33, security-headers.conf:20). |
| D-J3 | **решено** (вопрос 2) | VR нет в 127/137/138/143/144 — принять git log + 142 VR §3 как evidence; решение зафиксировать в реестре. |
| D-J4 | **OPEN** (зависит от 145 W4 + chaos) | 126-chaos-resilience/files/ 6.0M + 141-server-recovery/evidence/ 828K на месте. Архивация — в 145 W4; удаление — после 2026-09-15. |

### 1.4 Без изменений

- **Категория H** (D-H2..H11, monitoring): ближайшие Rev-даты 2026-10-21/22 — до них без действий.
- **D-127-issue-cert** (deferred, Rev 2027-02), **D-143-memory-limits-projects / D-143-logrotate** (deferred по решению) — триггеры не сработали.
- **Категории A, F** — audit-trail, закрыты.

---

## 2. TRAP[DECISION]

⚠️ TRAP[DECISION] · 2026-08-11 · HI · R15: секрет CI_DEPLOY_KEY отсутствует в gh, но пара жива локально — восстановить, НЕ регенерировать
· Rejected: (а) «выгрузить из gh-секрета» — невозможно, секрета нет (проверено gh CLI, 14 секретов);
  (в) root-dispatch канал — неканон, расширяет root-поверхность; регенерация пары — лишний ручной
  шаг на ноде (forced-command ключ уже в authorized_keys, receive SUCCESS в 141).
· Reason: решение оператора 2026-08-11. `gh secret set CI_DEPLOY_KEY < ~/.ssh/platform_personal_cicd`
  (плоский PEM; deploy-project.yml:286 поддерживает и base64, и PEM) в Tronyx161/AI-platform +
  TronyxLab/* (botanika, dance-site, roadmap, tronyx-site — remotes проверены). Закрывает R15 + B6.
· Rev: если локальный ключ будет утрачен — регенерация пары по runbook ci-secrets-rotation.md.

⚠️ TRAP[DECISION] · 2026-08-11 · MED · Удаление упоминаний ротации — скоуп ТОЛЬКО POSTGRES_PASSWORD
· Rejected: буквальное «удалить любые упоминания ротации» (риск: ломает рабочие механизмы —
  S3 retention rotation = политика хранения бэкапов; logrotate = ротация логов; clickhouse
  «password rotation converges» = design-контракт per-file ro mount; docs/ci-secrets-rotation.md =
  процедура восстановления CI-секретов, нужна W1; age-master-key-dr.md = drill-план W5)
· Reason: решение оператора 2026-08-11 по вопросу D-130-P3-4 — «ротацию POSTGRES_PASSWORD
  не делать». Удаляются ТОЛЬКО артефакты postgres-ротации: ROTATION.md, комментарии в
  postgres/docker-compose.base.yml:57-62, ссылки на ROTATION.md. Прочие «rotation»-механизмы —
  иные домены, НЕ трогаются (задокументировано для будущих агентов).
· Rev: если появится требование ротации пароля БД — переоткрыть D-130-P3-4 с автоматизацией.

⚠️ TRAP[DECISION] · 2026-08-11 · MED · VR-гэп J3/137/138: git log + 142 VR §3 — достаточный evidence
· Rejected: писать 4 ретроспективных VR (137/138/143/144) — docs-работа без инженерной ценности;
  142 VR §3 уже содержит верификацию интеграций I1-I7; коммиты 143/144 + деплой-фиксы
  задокументированы в git-сообщениях с evidence.
· Reason: решение оператора 2026-08-11. 127 — полностью закрыт коммитами e6ec95f/9eda35f/4593652.
· Rev: если будущий аудит потребует формальных VR — написать ретроспективно по git log.

⚠️ TRAP[DECISION] · 2026-08-11 · LOW · nginx systemctl sudoers — удалить (non-Docker нод нет)
· Rejected: оставить до Rev 2026-10-21 (риск: мёртвые записи sudoers — attack surface без пользы)
· Reason: решение оператора 2026-08-11. Инвентарь нод: tronyx-vps + test-e2e (обе Docker,
  node-configs/ проверены). setup-node.sh:87-91 (platform) + 107-108 (ci-deploy) удаляются;
  Rev-нота «вернуть при появлении non-Docker ноды» остаётся в заголовке setup-node.sh.
· Rev: при добавлении non-Docker ноды.

⚠️ TRAP[DECISION] · 2026-08-11 · MED · Chaos r2-evidence: реестр описывает устаревший прогон
· Rejected: оставить описание D-142-Chaos-T6-T10 как в VR 142 §6 (T4/T11 GREEN, T6 RED) —
  противоречит chaos-r2.log (T4/T5/T6 GREEN, T11 RED)
· Reason: r2 (attempt #30, 2026-08-06) — более поздний и полный прогон; реестр обновляется
  в W2, окно chaos-верификации остаётся в W5 (Rev 2026-09-15) на provisioned-ноде.
· Rev: после chaos-окна 2026-09-15 — финальные статусы T1-T11 в реестре.

---

## 3. Код-граф (XML)

```xml
<devplan number="145-02" slug="debt-elimination">
  <prerequisite>
    <artifact id="00-TECHNICAL-DEBT-REGISTRY" status="DONE"/>
    <artifact id="01-DevPlan" status="W1/W5 committed; W2/W3/W4 pending"/>
    <verification evidence="2026-08-11: registry vs live state — 11 closed, 2 refined, 2 stale"/>
  </prerequisite>
  <wave id="W1" name="restore-ci-secrets" effort="S" blocking="true" operator="true">
    <task action="gh-secret-set" secret="CI_DEPLOY_KEY" value="~/.ssh/platform_personal_cicd"
          repos="Tronyx161/AI-platform, TronyxLab/botanika, TronyxLab/dance-site, TronyxLab/roadmap, TronyxLab/tronyx-site"/>
    <task action="gh-secret-set" secret="DOCKER_HUB_USERNAME" operator_token="true" repo="Tronyx161/AI-platform"/>
    <task action="gh-secret-set" secret="DOCKER_HUB_TOKEN" operator_token="true" repo="Tronyx161/AI-platform"/>
    <task action="verify" cmd="gh secret list -R Tronyx161/AI-platform"/>
    <task action="verify-ci-deploy" note="push в проект (TronyxLab/*) → deploy-project.yml → receive → DEPLOYED"/>
    <task closes="D-142-R15, D-142-R17, D-136-B6"/>
  </wave>
  <wave id="W2" name="sync-registry" effort="S" requires="W1(optional)">
    <task action="edit" path="00-TECHNICAL-DEBT-REGISTRY.md">
      <change>D-134-S4, D-J1, D-J2, D-142-B37, D-140-P5, D-136-W9, D-143, D-144 → [CLOSED]</change>
      <change>D-137, D-138 → [CLOSED] (git log + 142 VR §3, решение оператора)</change>
      <change>D-130-P3-4 → [CLOSED] (решение: ротация отменена, артефакты удалены)</change>
      <change>D-136-W11-C-8 → resolved-as-decision (TRAP core-deploy.yml, Rev 2026-10-21)</change>
      <change>D-136-B6 → partially-done (core-канал верифицирован 141), D-136-B7 → partially-done (LE есть, fresh-issue нет), D-142-R17 → partially-done (механизм есть, секретов нет)</change>
      <change>D-142-Chaos-T6-T10 → описание по chaos-r2.log (T4/T5/T6 GREEN; T1/T2/T3/T7-T11 RED)</change>
      <change>D-J3 → CLOSED-по-решению; метрики пересчитаны; версия 2.0</change>
    </task>
  </wave>
  <wave id="W3" name="code-cleanup" effort="S+M" requires="W2">
    <task action="edit" path="core/internal/bootstrap/setup-node.sh" closes="D-136-W10">
      <change>удалить sudoers nginx systemctl (87-91, 107-108); обновить header-комментарий; Rev-нота</change>
    </task>
    <task action="delete" path="core/modules/postgres/ROTATION.md" closes="D-130-P3-4"/>
    <task action="edit" path="core/modules/postgres/docker-compose.base.yml">
      <change>удалить комментарий ротации (57-62); заменить TRAP-нотой «ротация отменена решением 2026-08-11»</change>
    </task>
    <task action="edit" path="core/entrypoints/pre-push-gate.sh" closes="D-142-B38">
      <change>удалить pipx install --force (46-51); обновить MODULE_CONTRACT/header</change>
    </task>
    <task action="edit" path="core/internal/bootstrap/lifecycle/state_store.py" closes="D-136-state-store-IMP9">
      <change>save_state: logger.info IMP:9 после _atomic_write_json (flock + tmp→rename, путь, размер)</change>
    </task>
    <task action="edit" path="tests/unit/test_compose_validator.py + core/internal/scaffold/compose_validator.py" closes="D-I2">
      <change>try_parse_compose: catch FileNotFoundError/OSError → best-effort None; R5-negative тест</change>
    </task>
    <task action="edit" path="tests/unit/test_practices_check_project.py" closes="D-I3">
      <change>monkeypatch audit_logger.write_audit_entry (паттерн test_escalator_downgrade_audit)</change>
    </task>
    <task action="edit" path="core/internal/catalog/generate_catalog.py + tests/unit/test_generate_catalog.py" closes="D-I4">
      <change>basicConfig(force=True) из module-level → main()/CLI; тест без нейтрализации</change>
    </task>
    <task action="edit" path="core/modules/nginx/config/security-headers.conf" closes="D-I5">
      <change>TODO → TRAP[DEBT] с Rev-датой (SPA-аудит, отложено осознанно)</change>
    </task>
    <task action="edit" path="core/internal/scaffold/vhost_renderer.py + tests/unit/test_vhost_configurator.py" closes="D-I1" effort="M">
      <change>реализовать configure_vhost_for_project(project_dir, domain, node_configs_dir) →
             load_vhost_config → render_vhost → True/False; тест переводится с mock на реальный вызов;
             мёртвый try/except в vhost_configurator.py:63-77 оживает (Python API primary, D4)</change>
      <alternative>если M-реализация нежелательна: удалить мёртвый try/except + тест D4-primary (S)</alternative>
    </task>
    <task action="verify" cmd="make check"/>
  </wave>
  <wave id="W4" name="security-ci" effort="M" requires="W3">
    <task action="edit" path=".github/workflows/security-scan.yml" new="true" closes="D-134-L3">
      <change>trivy (L1/L2 образы, severity gate) + pip-audit (requirements.txt) — на push/PR в main</change>
    </task>
    <task action="edit" path=".github/dependabot.yml" closes="D-134-L3">
      <change>добавить package-ecosystem: pip, directory /, weekly</change>
    </task>
    <task action="edit" path=".github/workflows/hermes-nightly.yml" new="true" closes="D-134-L4">
      <change>schedule cron weekly: hermes-build-context → push → node-update (после S8-детекции)</change>
    </task>
    <task action="edit" path="core/internal/bootstrap/security_posture.py + core/modules/monitoring/*" closes="D-134-L5" effort="L" optional="true">
      <change>fail2ban + auditd provision; security_posture --json → Loki/Grafana; check-security non-blocking в converge</change>
    </task>
    <task action="verify" cmd="make check && make gate MODE=fast (pre-push hook)"/>
  </wave>
  <wave id="W5" name="operational-windows" effort="L+XL" operator="true" requires="W1">
    <task action="drill" closes="D-136-W10-S-13" deadline="2026-08-31">
      <change>AGE master-key: off-node encrypted backup + restore-first на пересозданной ноде; evidence в 145/</change>
    </task>
    <task action="chaos-window" closes="D-126-D5, D-126-T9-T11, D-136-B7, D-142-Chaos-T6-T10" deadline="2026-09-15">
      <change>единое окно на provisioned-ноде: OOM-clickhouse victim (T7), T8 ENOSPC, T9 cert corruption,
             T10 restore-drill, T11 reboot, fresh ACME DNS-01 issue (B7); T4/T5/T6 — regression-check</change>
    </task>
    <task action="audit-and-delete" closes="D-139-T1" deadline="2026-11-01">
      <change>SSH на ноду: проверка audit-лога (0 вызовов deploy.sh как forced-command) → удалить
             core/entrypoints/deploy.sh + обновить core/AGENTS.md/entrypoint-manifest</change>
    </task>
    <task action="diagnose" closes="D-135-hermes-500">
      <change>live-500 подтверждён (2026-08-11): upstream-фикс hermes-agent или L2-патч (redirect chain)</change>
    </task>
    <task action="archive" closes="D-J4" depends="145 W4 + chaos closed">
      <change>126/files + 141/evidence → .tar.gz в _archive/ (по 145 W4 шаблону), удаление после 2026-09-15</change>
    </task>
  </wave>
  <verification>
    <task action="gh-secret-list" expect="CI_DEPLOY_KEY + DOCKER_HUB_* present"/>
    <task action="rg" expect="TRAP[DEBT] в коде = 0; ROTATION.md = 0 упоминаний; pipx = 0 в pre-push-gate"/>
    <task action="make-check" expect="green"/>
    <task action="registry-v2" expect="метрики пересчитаны, 0 unknown-статусов"/>
  </verification>
</devplan>
```

---

## 4. Волны

### W1 — Восстановление секретов CI (BLOCKING, оператор, ~30 мин)

**Цель:** Разблокировать CI-деплой проектов (R15 — блокер `make deploy-project`/e2e remote; R17 — rate-limit).

**Контекст:** `gh secret list -R Tronyx161/AI-platform` = 14 секретов; `CI_DEPLOY_KEY`, `DOCKER_HUB_USERNAME`,
`DOCKER_HUB_TOKEN` ОТСУТСТВУЮТ. Локальная пара `~/.ssh/platform_personal_cicd(.pub)` жива; её pub —
в authorized_keys ноды с forced-command (141: receive SUCCESS на botanika/dance-site/roadmap).

**Шаги:**
1. `gh secret set CI_DEPLOY_KEY -R Tronyx161/AI-platform < ~/.ssh/platform_personal_cicd`
   (плоский PEM; workflow принимает PEM и base64 — deploy-project.yml:286-289)
2. Для каждого репозитория проектов (remotes проверены 2026-08-11):
   `gh secret set CI_DEPLOY_KEY -R TronyxLab/botanika < ~/.ssh/platform_personal_cicd`
   `gh secret set CI_DEPLOY_KEY -R TronyxLab/dance-site < ~/.ssh/platform_personal_cicd`
   `gh secret set CI_DEPLOY_KEY -R TronyxLab/roadmap < ~/.ssh/platform_personal_cicd`
   `gh secret set CI_DEPLOY_KEY -R TronyxLab/tronyx-site < ~/.ssh/platform_personal_cicd`
3. R17: оператор предоставляет DOCKER_HUB_USERNAME + DOCKER_HUB_TOKEN (read-only токен):
   `gh secret set DOCKER_HUB_USERNAME -R Tronyx161/AI-platform`
   `gh secret set DOCKER_HUB_TOKEN -R Tronyx161/AI-platform`
4. Верификация: `gh secret list -R Tronyx161/AI-platform` → 17 секретов; push в любой проект
   TronyxLab → deploy-project.yml → receive → `DEPLOYED healthy` на ноде (закрывает B6).

**Приёмка:** CI-деплой реального проекта проходит; e2e MODE=remote работает.

### W2 — Синхронизация реестра (S, документация)

**Цель:** Реестр — единый SoT; после верификации 8 пунктов закрыты фактически, 2 описания устарели.

**Правки `00-TECHNICAL-DEBT-REGISTRY.md` (версия 2.0):**
- `[CLOSED]` + evidence: D-134-S4, D-J1, D-J2, D-142-B37, D-140-P5, D-136-W9-T9.19, D-143, D-144, D-130-P3-4, D-137, D-138
- `resolved-as-decision`: D-136-W11-C-8 (TRAP core-deploy.yml, Rev 2026-10-21)
- `partially-done` уточнены: D-136-B6 (core-канал ✅ 141; проект-канал — W1), D-136-B7 (LE ✅ restore; fresh-issue ❌), D-142-R17 (механизм ✅; секреты ❌ — W1)
- D-142-Chaos-T6-T10: описание по chaos-r2.log (T4/T5/T6 GREEN; T1/T2/T3/T7-T11 RED, сконфаунженный прогон)
- D-J3: закрыт по решению (git log + 142 VR §3)
- Итог категорий, §СВОДКА, §Метрики — пересчитать; топ-5: R15 (после W1 — снять), drill, chaos-окно, hermes-500, L3/L4.

**Приёмка:** в реестре 0 unknown-статусов; каждый CLOSED имеет evidence-строку.

### W3 — Код-чистка (S+M)

**Цель:** снять все живые TRAP[DEBT] I1-I5 + 5 S-пунктов + 1 M-пункт.

| Задача | Файл | Действие |
|--------|------|----------|
| D-136-W10 | `core/internal/bootstrap/setup-node.sh` | удалить sudoers nginx systemctl: строки 87-91 (platform) и 107-108 (ci-deploy); header-комментарий (32-38) — обновить; Rev-нота «вернуть при non-Docker ноде» |
| D-130-P3-4 | `core/modules/postgres/ROTATION.md` | УДАЛИТЬ (129 строк) |
| D-130-P3-4 | `core/modules/postgres/docker-compose.base.yml` | удалить комментарий ротации (57-62); заменить короткой TRAP-нотой: «Ротация POSTGRES_PASSWORD отменена решением 2026-08-11; пароль генерируется при initdb/bootstrap» |
| D-142-B38 | `core/entrypoints/pre-push-gate.sh` | удалить блок pipx install --force (46-51); обновить GREP_SUMMARY/STRUCTURE/MODULE_CONTRACT (упоминания pipx) |
| D-136-state-store-IMP9 | `core/internal/bootstrap/lifecycle/state_store.py` | `save_state`: `logger.info("[IMP:9][StateMachine][save] ...")` после `_atomic_write_json` (flock + tmp→rename, путь, mode) |
| D-I2 | `core/internal/scaffold/compose_validator.py` + `tests/unit/test_compose_validator.py` | `try_parse_compose`: расширить except на FileNotFoundError/OSError → best-effort None; R5-negative тест (файл отсутствует + docker недоступен → None, не exception) |
| D-I3 | `tests/unit/test_practices_check_project.py` | monkeypatch `audit_logger.write_audit_entry` (паттерн W3-теста escalator); снять TRAP[DEBT] |
| D-I4 | `core/internal/catalog/generate_catalog.py` + `tests/unit/test_generate_catalog.py` | `logging.basicConfig(force=True)` из module-level → `main()`; тест — убрать нейтрализацию; снять TRAP[DEBT] |
| D-I5 | `core/modules/nginx/config/security-headers.conf` | TODO → `TRAP[DEBT]`-нотация с Rev (после SPA-аудита); 0 живых TODO в коде |
| D-I1 | `core/internal/scaffold/vhost_renderer.py` + `tests/unit/test_vhost_configurator.py` | **Реализовать** `configure_vhost_for_project(project_dir, domain, node_configs_dir) -> bool`: `load_vhost_config` → ProjectEntry → `render_vhost` → True/False (M, Python-first, оживляет D4-primary в vhost_configurator.py:63-77). Альтернатива (если оператор против M): удалить мёртвый try/except + тест D4-primary (S). |

**Проверка:** `make check` зелёный; `rg "TRAP\[DEBT\]" core/ tests/ --glob '!*.pyc'` = 0.

### W4 — Безопасность / CI-сканирование (M)

| Задача | Файл | Действие |
|--------|------|----------|
| D-134-L3 | `.github/workflows/security-scan.yml` (новый) | push/PR→main: trivy (L1/L2 образы, severity gate HIGH+) + pip-audit (requirements.txt, `--fail-on=high`) |
| D-134-L3 | `.github/dependabot.yml` | + `package-ecosystem: pip`, `directory: "/"`, weekly (параллельно github-actions) |
| D-134-L4 | `.github/workflows/hermes-nightly.yml` (новый) | `schedule: cron weekly` → `hermes-build-context` (L1→L2) → `hermes-push-l2` → `node-update` (автопересборка после S8-детекции) |
| D-134-L5 | `security_posture.py` + monitoring | (L, опционально, может выноситься в отдельную волну): fail2ban + auditd provision (L4-детекция уже есть), `--json` → Loki/Grafana алерт, check-security non-blocking в converge |

**Приёмка:** security-scan.yml и hermes-nightly.yml в `.github/workflows/`; dependabot.yml — 2 ecosystems; make check зелёный.

### W5 — Операционные окна (L/XL, оператор, дедлайны из реестра)

| Окно | Дедлайн | Закрывает | Содержание |
|------|---------|-----------|------------|
| DR-drill AGE | **2026-08-31** | D-136-W10-S-13 | off-node encrypted backup мастер-ключа + restore-first на пересозданной ноде; evidence в `.ai/plans/145-.../evidence/` |
| Chaos-окно | **2026-09-15** | D-126-D5, D-126-T9-T11, D-136-B7, D-142-Chaos-T6-T10 | provisioned-нода: T7 OOM-clickhouse victim (жертва!), T8 ENOSPC, T9 cert corruption, T10 restore-drill, T11 reboot, fresh ACME DNS-01 (B7); regression T4/T5/T6; финальные статусы T1-T11 → реестр |
| deploy.sh удаление | 2026-11-01 | D-139-T1 | SSH-проверка audit-лога (0 вызовов deploy.sh) → удалить `core/entrypoints/deploy.sh` + обновить core/AGENTS.md/entrypoint-manifest (обоим узлам < 1 мес — forced-command диспетчер) |
| hermes-500 диагноз | без жёсткого дедлайна | D-135-hermes-500 | live-500 подтверждён; upstream hermes-agent ≥ v2026.7.7.2 или L2-патч (redirect chain на basic-auth) |
| Evidence-архивация | после 2026-09-15 | D-J4 | 126/files (6.0M) + 141/evidence (828K) → `.tar.gz` в `.ai/plans/_archive/` (шаблон 145 W4); git rm --cached + .gitignore |

---

## 5. Acceptance Criteria (контрольный лист)

- [ ] **AC1:** `gh secret list -R Tronyx161/AI-platform` — CI_DEPLOY_KEY + DOCKER_HUB_USERNAME + DOCKER_HUB_TOKEN присутствуют; push в TronyxLab/* → CI-деплой → `DEPLOYED healthy` (R15/R17/B6)
- [ ] **AC2:** реестр v2.0: 0 unknown; CLOSED-пункты с evidence; D-142-Chaos-T6-T10 описан по r2; метрики пересчитаны
- [ ] **AC3:** `make check` зелёный; `rg "TRAP\[DEBT\]" core/ tests/` = 0; `rg "ROTATION.md" core/` = 0; pipx-блок удалён; sudoers nginx удалены; `save_state` логирует IMP:9
- [ ] **AC4:** `.github/workflows/security-scan.yml` + `hermes-nightly.yml` существуют; dependabot — pip + github-actions
- [ ] **AC5:** evidence drill (≤2026-08-31) и chaos-окна (≤2026-09-15) в папке 145; deploy.sh удалён (≤2026-11-01); hermes-500 — диагноз/фикс задокументирован
- [ ] **AC6:** evidence-папки 126/141 заархивированы (после chaos-закрытия + 145 W4)

---

## 6. Verification (post-execution)

```bash
# 1. Secrets (W1)
gh secret list -R Tronyx161/AI-platform | rg "CI_DEPLOY_KEY|DOCKER_HUB"

# 2. Registry v2 (W2)
rg -n "Версия реестра" .ai/plans/145-repo-cleanup-debt-registry/00-TECHNICAL-DEBT-REGISTRY.md
# Expected: 2.0

# 3. Code cleanup (W3)
rg -n "TRAP\[DEBT\]" core/ tests/ --glob '!*.pyc'          # Expected: 0
rg -rn "ROTATION" core/ --glob '!*.pyc'                     # Expected: 0 (postgres-ротация)
rg -n "pipx" core/entrypoints/pre-push-gate.sh              # Expected: no match
rg -n "IMP:9" core/internal/bootstrap/lifecycle/state_store.py  # Expected: save_state IMP:9
make check                                                  # Expected: green

# 4. Security CI (W4)
ls .github/workflows/ | rg "security-scan|hermes-nightly"
rg -n "pip" .github/dependabot.yml

# 5. Operational windows (W5) — evidence-файлы:
ls .ai/plans/145-repo-cleanup-debt-registry/evidence/ 2>/dev/null   # drill + chaos + audit logs
```

---

## 7. Зависимости от оператора (вне этого девплана)

1. **W1:** команды `gh secret set` исполняет оператор (или авторизует агента). DOCKER_HUB_TOKEN — из личного кабинета Docker Hub.
2. **W5:** окна 2026-08-31 (drill) и 2026-09-15 (chaos) — операторские; нода должна быть provisioned.
3. **145 W4:** архивация evidence (126/141) — подтверждение оператора (уже заложено в 01-DevPlan).
4. **145 W2/W3:** remote-ветки origin/142-* и `personal` worktree-папка — подтверждение оператора (01-DevPlan §7).
5. **D-I1:** выбор «реализовать configure_vhost_for_project (M)» vs «удалить мёртвый путь (S)» — подтвердить при старте W3.

$END_DEVPLAN
