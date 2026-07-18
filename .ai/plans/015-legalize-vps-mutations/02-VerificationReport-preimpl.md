<!-- GREP_SUMMARY: VerificationReport, preimpl, devplan-015, legalize-vps-mutations, evidence-audit, stale-evidence, B1-trigger-refuted, dance-site-proxy-net, step-numbering, chmod-contradiction, verdict-DRIFTED -->
<!-- STRUCTURE: ┌$ARTIFACT_CONTRACT┐ → ◇ SHA-anchor → ◇ evidence matrix (§1) → ◇ drift register (plan↔code) → ◇ internal consistency → ◇ contract/markup audit → ⎋ semantic verdict + рекомендации -->

# $START_VERIFICATION_REPORT

## $ARTIFACT_CONTRACT
- **PURPOSE:** Предреализационный аудит 01-DevPlan.md (задача 015): верификация всех evidence-claims реестра §1 против кодовой базы, внутренней консистентности плана и готовности к передаче Кодерам.
- **DESCRIPTION:** Проверены все 12 строк реестра B1–B5/M1–M7 по file:line, переиспользуемые точки (ensure_docker_network, FQDN-check, _validate_project_name, content-hash.sh, inline-python, checkpoint-механика), состояние 4 репозиториев (ai-platform, tronyx-lab/node-configs, tronyx-site, dance-site), регистрационные поверхности (Makefile, entrypoint-manifest.yaml, gates). Найдено: 2 опровергнутых суб-гипотезы B1, 1 устаревшая предпосылка T2.3, 1 внутреннее противоречие (G3 vs §5.3), 1 коллизия нумерации шагов, пробел покрытия M7.
- **RATIONALE:** Пессимистичный предреализационный gate — план строится на разведке, которая могла устареть; проверка current-state обязательна до делегирования волн.
- **ACCEPTANCE_CRITERIA:** Каждая строка реестра §1 DevPlan имеет статус CONFIRMED/PARTIAL/REFUTED с evidence file:line; все findings имеют severity и fix-предложение; вынесен семантический вердикт.
- **IMPLEMENTS:** Запрос пользователя «Проверь девплан перед реализацией» (2026-07-18).
- **IMPACTS:** .ai/plans/015-legalize-vps-mutations/01-DevPlan.md (требуются правки до реализации).
- **REQUIRES:** Read-only доступ к ai-platform @ 0a8e6adf и sibling-репозиториям tronyx-lab/*.
$END_ARTIFACT_CONTRACT

---

🔒 Verified against SHA `0a8e6adf96978950555dc23e3c93357fe111b768`
⚠️ Рабочее дерево грязное: modified `.gitignore`, `AGENTS.md`; untracked `.ai/plans/` — verification выполнена по working tree.

---

## Section 1 — Evidence Matrix (реестр §1 DevPlan против кода)

| ID | Claim плана | Статус | Evidence |
|----|-------------|--------|----------|
| B1 | DEPLOY_STATUS="success" только на :1010, после tag/prune/hooks/audit/notify (:1000–1007); _finalize_deploy :84 | **CONFIRMED (структура), REFUTED (оба названных триггера)** | deploy-project.sh:1000–1011 ✓, :84 ✓. НО: audit_log() полностью нефатален (audit_logging.sh:46–56 — все записи под `|| rc=$?`, fallback stderr, функция всегда возвращает 0); notify_hook() при 644 уходит в else-ветку с log_imp 6, не падает (deploy-project.sh:169–174). Оставшиеся кандидаты-триггеры: `tag_current`/`prune_old_images`/`_trigger_deploy_hooks` (:1000–1002) |
| M1 | 33 tracked .sh с mode 100644 | CONFIRMED | `git ls-files -s -- '*.sh'` → ровно 33; verify-domains.sh, notify-hook.sh, deploy-project.sh, весь bootstrap в списке ✓ |
| M2 | _ensure_log_dir 0750 root:adm; ci-deploy в adm (setup-node.sh:99) | CONFIRMED (с уточнением симптома) | audit_logging.sh:22–26 ✓; setup-node.sh:99,102 ✓. Уточнение: при текущем коде сбой записи под ci-deploy = **тихая потеря audit-записей** (2>/dev/null), не падение деплоя — R2 остаётся обоснованным |
| M3 | /opt/projects — step_6b (:352); deploy-verb требует каталог (:426) | CONFIRMED | node-lifecycle.sh:352 step_6b_create_projects_base ✓; deploy-project.sh:426–428 «FATAL: project directory not found» ✓ |
| M4/M5 | proxy_pass без resolver; TRAP[DECISION] 2026-06-29 устарел; nginx на proxy-net | CONFIRMED | overlay nginx.conf:88 `proxy_pass http://tronyx-site:80`, resolver только в комментариях; TRAP :78–79 ✓; core/modules/nginx/docker-compose.base.yml:81,98 proxy-net ✓ |
| M6 | `gh secret set` — 0 совпадений в коде | CONFIRMED | Совпадения только в самом DevPlan |
| M7 | prepare_ssh_opts: ssh-keygen -R при каждой доставке + accept-new | CONFIRMED | scp-deliver.sh:65–73 ✓ |
| B2 | Идентичные login-action у обоих проектов | CONFIRMED (not diagnosed — как заявлено) | Разница вне кода, план честно фиксирует |
| B3 | Не подтверждён, требует диагностики | AS-DECLARED | — |
| B4 | GIT_MIRROR_TOKEN отсутствует в enc; context-promote локальный, token fail-fast | CONFIRMED | 0 вхождений в обоих enc-файлах (tronyx-lab/node-configs/secrets/ и tronyx-lab/platform/node-configs/secrets/); context-promote.sh:35–41 fail-fast, :48–55 GIT_ASKPASS ✓ |
| B5 | 4× `listen 443 ssl http2` (:44,45,94,95); мёртвый conf.d/tronyx.ru.conf | CONFIRMED | nginx.conf:44 + :45 (`[::]:443`), :94/95 аналогично; conf.d/tronyx.ru.conf существует и не монтируется ✓ |
| A5 | Все 3 шаблона уже объявляют proxy-net external; adopt не трогает compose (:164) | CONFIRMED | template-{backend,frontend,fullstack}/docker-compose.yml ✓; adopt-project.sh:162–164 exclude ✓ |

Переиспользуемые точки: ensure_docker_network (deploy-modules.sh:85, TRAP[DECISION]:75–78) ✓; check_fqdn_conflict (validate.sh:180, `--check-fqdn`:308) ✓; _validate_project_name (deploy-project.sh:183) ✓; content-hash.sh ✓; inline-python (node-lifecycle.sh:531 — план указал :529, minor) ✓; TRAP[BUSINESS]:454 ✓; auto_detect_node_name (bootstrap.sh:67) ✓; NODE_HOST_MAP-валидация УЖЕ есть (deploy-project.yml:59–64) ✓; tronyx-site hardcode IP (deploy.yml:55) + platform-deploy.yml ✓; node.yaml `domain: www.tronyx.ru` ✓ (edge case T2.2 обоснован); гейты manifest-integrity/thin-wrapper/dead-code/project-compose существуют ✓; глаголы converge/render-vhosts/project-sync-secrets свободны в Makefile+манифесте ✓; 014-StatusReport существует ✓.

## Section 2 — Drift Register (план ↔ текущее состояние кода)

| # | Sev | Finding | Fix |
|---|-----|---------|-----|
| D1 | **MEDIUM** | **B1: оба названных «вероятных триггера» опровергнуты кодом.** audit_log() нефатален (audit_logging.sh:46–56), notify_hook() при 644 нефатален (deploy-project.sh:169–174). Структурный root cause (поздний DEPLOY_STATUS под set -e) и фикс T3.1 остаются валидными — обёртки `|| true` покрывают реальные кандидаты tag_current/prune/_trigger_deploy_hooks | Обновить строку B1 в §1: убрать audit_log/notify как триггеры, указать tag_current/prune/hooks; T3.1 не менять |
| D2 | **MEDIUM** | **T2.3 построен на устаревшем состоянии:** dance-site/docker-compose.yml УЖЕ содержит proxy-net external (:27, :44–45); tronyx-site — тоже (:27, :44–45). «Реальная дыра adopted-проектов» в §1 закрыта до начала реализации | T2.3 переквалифицировать в verify-only (0 правок compose); T2.4/T2.5 (валидация adopt + gate) сохраняют ценность как превентивный слой |
| D3 | **MEDIUM** | **Коллизия нумерации: `step_17_converge` невозможен** — в node-lifecycle.sh уже есть step_16_audit_log (:568) и step_17_telegram (:581); точка врезки «после deploy-modules (step_14_node_update :978), до audit (:980)» = свободный слот **step_15** | Переименовать в `step_15_converge`; соблюсти TRAP[BUSINESS]:454 (порядок объявления = порядок main) |
| D4 | **MEDIUM** | **M7-унификация неполна:** `StrictHostKeyChecking=no` есть также в `.github/workflows/deploy-project.yml:83` (deliver-канал CI) — T1.9 покрывает только project-list.sh:295 и remove-project.sh:330,355 | Добавить :83 в T1.9 либо явно исключить с rationale (эфемерный runner) в TRAP-блоке |
| D5 | LOW | T1.8 арифметика: core/lib содержит **10** .sh с 100644 (не 11) → chmod-список = **23** файла (не ~22) | Уточнить числа в T1.8 (на реализацию не влияет — фильтр по пути корректен) |
| D6 | WARNING | Два рабочих дерева node-configs: `tronyx-lab/node-configs/` И `tronyx-lab/platform/node-configs/` — обе с tronyx-vps/ + secrets/; enc-файлы проверены оба. Q4.5/T2.2 не указывают, какое дерево авторитетно для commit'а GENERATED-vhost | В Q4.5 зафиксировать точный путь авторитетного node-configs репо |
| D7 | INFO | R4 vs инвариант provisioner: platform-env.yaml (proxy-net :20) объявляет себя «the ONLY place where networks are defined»; TRAP[DECISION] 2026-07-17 (deploy-modules.sh:75) закрепил ensure_docker_network как runtime-fallback. План переиспользует ensure_docker_network — консистентно, но R4 обязан остаться fallback-семантикой | В T1.5 добавить ссылку на TRAP[DECISION] deploy-modules.sh:75 |
| D8 | INFO | dance-site/.github/workflows/deploy.yml.bak — мусорный payload в workflows-каталоге стороннего репо | Удалить при T3.x-работах в dance-site |

## Section 3 — Internal Consistency (внутренние противоречия документа)

| # | Sev | Finding | Fix |
|---|-----|---------|-----|
| C1 | **MEDIUM** | **G3 (§3) противоречит §5.3:** G3 — «чинить в git-индексе + defense-in-depth `--chmod` в rsync + R-PERMS»; §5.3 — «rsync `--chmod=Du+rwx` НЕ вводим (ломает семантику -a)». Кодер, читающий только §3, реализует отвергнутое | Привести G3-строку к §5.3 (двухслойная защита: git-индекс + R1) |
| C2 | LOW | T1.1: «main-диспетчер R1–R5», тогда как §5.1-дерево и DESCRIPTION контракта — R1–**R6**. Риск: диспетчер без verify_vhosts | Исправить T1.1 на R1–R6 |
| C3 | WARNING | Волна 4: «Вердикт по единой шкале (SUCCESS/PARTIAL/FAIL/BLOCKED)» — устаревшая шкала; каноническая QA-шкала: STABLE/DRIFTED/DEGRADED/BROKEN/BLOCKED | Заменить шкалу в Q-финале |
| C4 | WARNING | STRUCTURE-заголовок обещает «⎋ File Manifest», но секции File Manifest в документе НЕТ — скоуп восстанавливается только из IMPACTS + Code Graph, что усложняет scope-resolution Кодеров и QA | Добавить явный File Manifest (файл × волна × операция create/modify) |
| C5 | INFO | §11 использует идентификаторы R1–R8 для рисков, коллидирующие с именами юнитов R1–R6 — читателю нужно различать по контексту | Переименовать риски в RISK-1..8 (косметика) |

## Section 4 — Contract / Markup Audit

- $ARTIFACT_CONTRACT: 7/7 полей ✓; $START_DEVPLAN/$END_DEVPLAN ✓; $DOCUMENT_PLAN (GOALS + USE_CASES) ✓.
- Naming: `.ai/plans/015-legalize-vps-mutations/01-DevPlan.md` — соответствует грамматике реестра ✓; тип из закрытого словаря ✓.
- TRAP[DECISION] ×2 в §4 — формат соблюдён (Rejected/Reason/Rev) ✓.
- GREP_SUMMARY + STRUCTURE ✓.
- AC1–AC12 — проверяемые, с привязкой к задачам ✓; AC4/AC12 имеют негативные пары (Test Honesty R5) ✓.
- Регистрационная поверхность новых глаголов (Makefile + entrypoint-manifest.yaml секции lifecycle:149/scaffold:103 + allowed_verbs:449 + 2×AGENTS.md) учтена планом атомарно ✓ — риск R1 плана корректно закрыт.

## Semantic Verdict

**DRIFTED (WARNING)** — блокеров и CRITICAL нет; реализация возможна, но 5 находок уровня MEDIUM (D1–D4, C1) требуют правки DevPlan ДО делегирования волн, иначе Кодеры реализуют: неверный триггер-нарратив B1, лишний no-op T2.3, конфликтующий step_17, отвергнутый rsync --chmod и неполную M7-унификацию.

Сильные стороны плана: 10 из 12 строк реестра подтверждены с точностью до строки; все переиспользуемые точки существуют; edge cases (www.tronyx.ru-канонизация, TRAP[BUSINESS]-порядок, FQDN-uniqueness) предвосхищены корректно; регистрация глаголов атомарна.

# $END_VERIFICATION_REPORT
