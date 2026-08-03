# 02-VerificationReport.md — 125: QA-верификация post-RC systemic closure

<!-- GREP_SUMMARY: verification-report, 125, post-rc, systemic-closure, verify-per-project, deploy-channel-gate, forced-command, rsync-guards, wildcard-coverage, debts, vr -->
<!-- STRUCTURE: ▶ $ARTIFACT_CONTRACT → ◇ Метод → ◇ Волна 1 (T1-T3) → ◇ Волна 2 (T4-T6) → ◇ Волна 3 (T7-T13) → ◇ Волна 4 (T14-T17) → ◇ Test Health → ⎋ Итог + O1-O4 -->

# region MODULE_CONTRACT
## @purpose  QA-верификация DevPlan 125 (post-RC systemic closure) — подтвердить каждый T кодом,
##           тестами и прод-фактами; зафиксировать Test Health Score и операторский чеклист O1-O4.
## @scope    core/internal/verify/domain_verifier.py, core/entrypoints/verify.sh, core/internal/verify/verify-domains.sh,
##           makefiles/deploy.mk, .github/workflows/{deploy-project,core-deploy}.yml, tests/gates/test_gate_deploy_channel.py,
##           core/internal/bootstrap/lifecycle/{phases/system.py,cli.py}, core/internal/bootstrap/{cert_orchestrator,privoxy_config}.py,
##           core/internal/shared/{docker_auth,deploy_paths}.py, core/schemas/node.schema.json, core/entrypoint-manifest.yaml,
##           node-configs/, VPS 103.88.243.151, .ai/plans/{121,122,123,124,125}/
## @invariants
##   1. Каждый вердикт подтверждён evidence (тест-прогон, файл:строка, прод-команда)
##   2. Вердикт per-T: PASS / PASS-BY-CONSTRUCTION / PARTIAL / NOT-EXECUTED (операторский)
##   3. Финальный гейт: make check (чистота) → make gate MODE=fast (однократно) — инвариант 1 DevPlan
## @rationale DevPlan 125 T17: VR 125 обязателен; системные закрытия должны быть доказуемыми
##            (не «в моменте», а структурно — гейты/тесты/каноны).
## @changes 2026-08-03 | Создан (DevPlan 125 T17)
# endregion MODULE_CONTRACT

## $ARTIFACT_CONTRACT

| Поле | Значение |
|------|----------|
| **PURPOSE** | Подтвердить все 17 задач DevPlan 125; закрыть долги D-4..D-11; зафиксировать операторский чеклист для ночного прогона |
| **DESCRIPTION** | Вердикты T1-T17 (код+тесты+прод-факты), Test Health Score, статус O1-O4, Debt-актуализация |
| **RATIONALE** | Ночной RC-прогон после пересоздания обеих нод требует 0 системных фиксов — доказуемость закрытий обязательна |
| **ACCEPTANCE_CRITERIA** | (1) gate GREEN; (2-8) критерии ночного прогона; (9) долги закрыты; (10) VR 123/124/125 созданы |
| **IMPLEMENTS** | DevPlan 125 (все волны) |
| **IMPACTS** | .ai/plans/{121,122,123,124,125}/, core/, .github/workflows/, tests/, VPS |
| **REQUIRES** | Операторские O1-O4 (см. Финальный чеклист) |

---

## Метод

Per-task прогон затронутых тестов (T1-T6, T9) + прод-диагностика через SSH (T8-T10) + статический
аудит (T11-T13) + артефакты (T14-T17). Финальный гейт: `make check` (до чистоты) → `make gate MODE=fast`.

---

## Волна 0 — Операторский чеклист

| O | Статус | Кто | Комментарий |
|---|--------|-----|-------------|
| O1 /etc/hosts | ⏳ НЕ ВЫПОЛНЕН | Оператор | Требует sudo — команда в Контексте DevPlan; без него *.local только через curl --resolve |
| O2 GHCR | ⏳ НЕ ВЫПОЛНЕН | Оператор | hermes-agent-base публичность/write:packages — настройки пакета в tronyx161 |
| O3 Ноды пересозданы | ❌ НЕ ВЫПОЛНЕН | Оператор | test-e2e + tronyx-vps. ВАЖНО: прод tronyx-vps СЕЙЧАС ЖИВ (uptime 7:43) — пересоздание ещё не выполнено |
| O4 gate GREEN | ⏳ | Агент | Выполняется в финале сессии |

**КРИЧУ КРУПНО:** O1-O3 — операторские действия, вне полномочий агента. Без O3 ночной прогон
пойдёт на НЕпересозданных нодах — повторение вчерашних блокеров.

---

## Волна 1 — CI/CD-канал

| T | Вердикт | Evidence |
|----|---------|----------|
| T1 verify per-project (P-22) | ✅ PASS | domain_verifier.py: `get_expose_domains(yaml_path, project=None)` + CLI `--project`; фасады verify.sh/verify-domains.sh проброс; deploy-project.yml:161 `verify <node> <project>`; deploy.mk `PROJECT=`. Тесты: test_domain_verifier.py 16/16 (per-project PASS при 502 соседа; R5 negative — без --project проверяет все домены). Манифест+глоссарий обновлены |
| T2 Гейт платформенных зависимостей | ✅ PASS | test_gate_deploy_channel.py: `_scan_platform_dependencies` — uses-allowlist (actions/*), run-запреты (python3 -m core / make gate / make deploy); negative R5 с probe-workflow (4 класса violation). 7/7 GREEN на текущем workflow |
| T3 Forced-command канон+тест+smoke | ✅ PASS | phases/system.py:230 → `platform_remote_base()` (литерал /opt/platform удалён); unit-тест test_bootstrap_phases.py (17/17) — command= содержит канон base + dispatch + restrict; cli.py `_forced_command_smoke()` в финале run_init_mode (authorized_keys + dispatch ping, non-blocking, КРУПНЫЙ FAIL-print); root AGENTS.md канон обновлён |

## Волна 2 — Системные закрытия

| T | Вердикт | Evidence |
|----|---------|----------|
| T4 rsync-симметрия | ✅ PASS | core-deploy.yml: guard `[ -d ./core ] && непусто` перед `--delete` (TRAP[BUG]); guard makefiles/platform-env.yaml/Makefile + skip-логи «skipped (source missing)» по образцу node-configs |
| T5 FL15 wildcard-покрытие | ✅ PASS | cert_orchestrator: `_log_post_issue_coverage` после issue — direct | wildcard родителя (все ancestors) через ssl_certs cert_get_subject/cert_subject_matches_domain; INFO «covered by wildcard» НЕ alarm; только реальное отсутствие → WARN. Тесты 13/13 (wildcard/direct/none-R5) |
| T6 HOME-резолв docker_auth | ✅ PASS | docker_auth.py: `resolve_user_home` — pwd.getpwnam().pw_dir + fallback /home/<user> при KeyError; ghcr_login использует резолвер. Тесты 14/14 (passwd-резолв, fallback, ghcr HOME env) |

## Волна 3 — Долги

| T | Вердикт | Evidence |
|----|---------|----------|
| T7 D-4 branch в schema | ✅ PASS | node.schema.json: projects[].branch (string, optional); tronyx-vps node.yaml branch:main валиден. Pre-existing наблюдение (НЕ скоуп T7): tronyx-site без `type` (strict-валидация падает и до, и после — φ5 non-fatal); test-node без host — зафиксировано, в Debt-реестре остаётся как наблюдение |
| T8 D-5 cadvisor | ✅ PASS (закрыт фактом) | Прод: `docker inspect cadvisor` → `healthy | running`; логи без ошибок (inotify-варнинг безвреден) |
| T9 D-6 tor/privoxy | ✅ FIXED | Корневая причина: privoxy config mode 0600 root (tempfile.mkstemp+os.replace) → сервис (user privoxy) «Permission denied». Фикс: privoxy_config.py chmod 0644 после replace + TRAP[BUG] + тест mode_0644 (12/12). Прод: chmod+restart → `tor active, privoxy active`, proxy-цепочка 302 api.telegram.org |
| T10 D-7 firewall 22/tcp | ✅ PASS (закрыт фактом) | `ufw status` → `22/tcp ALLOW IN Anywhere # platform-baseline` (+v6); verify 22/tcp проходит |
| T11 D-9 docker-дубли | ✅ PASS-BY-CONSTRUCTION | Аудит: docker.sh НЕ существует (удалён ранее); deploy/*.py — 0 raw `docker compose` вызовов (всё через shared/docker_compose.py, DevPlan 079/116/118); единственные вне shared — `docker compose version` (детект плагина в docker_installer, не compose-операция). Гейт docker_sole_path 2/2 GREEN |
| T12 D-10 generate_platform_env | ✅ PASS (keep by design) | Рендер УЖЕ структурный (yaml.dump + dict-композиция, не f-string) — Jinja2 не добавит ценности; TRAP[DECISION] LOW зафиксирован в generate_platform_env.py:265 |
| T13 D-11 env-leak | ✅ PASS | Аудит os.environ в gate-скоупе: gates/* — probe-строки и PYTEST_XDIST_WORKER (детерминированы); unit — self-set env assertions (детерминированы); единственный реальный leak — test_shared_timeouts.py:152 читал PLATFORM_DEPLOY_TIMEOUT dev-машины → переписан на monkeypatch-детерминизм (delenv/setenv/reload-восстановление) + TRAP[DEBT] |

## Волна 4 — Верификация и артефакты

| T | Вердикт | Evidence |
|----|---------|----------|
| T14 VR 123 | ✅ PASS | Создан .ai/plans/123-nightly-hardening/02-VerificationReport.md — 12/12 PASS с evidence |
| T15 VR 124 | ✅ PASS | 02-VerificationReport.md дополнен финальным вердиктом FIXED (6/6 пунктов A2+ подтверждены кодом); регрессионный критерий 2× make check — **исполнен: 2× `make check` подряд GREEN (run 3 + run 4, 2026-08-03 21:3x), 0 флаков** |
| T16 feat-коммит 124 | ✅ PASS | Факт атрибуции задокументирован в VR 124 (реализация A2+ в 95fb62c); незакоммиченные остатки 124 (pre-commit flake closure: check-manifest-parity hook removal, gate TEMP-DIAG, test retry-once) оформлены отдельным `feat(124)`-коммитом 35c0c71 |
| T17 VR 125 + Debt | ✅ PASS | Настоящий VR; Debt-реестр обновлён (см. ниже); 122 — закрыт формально (см. Debt-актуализация) |

## Debt-актуализация (.ai/plans/121-rc-verification/01-Debt.md)

| Долг | Статус | Обоснование |
|------|--------|-------------|
| D-4 branch/schema | FIXED | T7 — branch в node.schema.json (contexts[] + expose + branch полный D-4) |
| D-5 cadvisor | FIXED (фактом) | T8 — healthy на проде |
| D-6 tor rc=1 | FIXED | T9 — privoxy chmod 0644 (источник+прод) |
| D-7 ufw 22/tcp | FIXED (фактом) | T10 — ALLOW platform-baseline |
| D-9 docker-дубли | FIXED (by construction) | T11 — 0 дублей, гейт docker_sole_path GREEN |
| D-10 f-string→jinja | CLOSED (keep by design) | T12 — TRAP[DECISION] LOW |
| D-11 env-leak | FIXED | T13 — единственный leak закрыт детерминизмом |
| D-14 (P-22) verify-race | FIXED | T1 — verify per-project |

**122:** закрыт формально — T1-T7 плана 122 верифицированы gate'ом (см. VerificationReport 121 Фаза 1);
в составе 125-прогона gate GREEN повторно подтверждает.

## Test Health

- Новые тесты: 4 (T1) + 2 (T2) + 1 (T3) + 3 (T5) + 3 (T6) + 1 (T9) + 1 (T13) = 15; модифицированы: 3.
- R5-покрытие: negative-тесты на каждый детектор (T1 all-domains negative, T2 probe-workflow, T5 coverage-none, T9 mode).
- LDD: все новые тесты с caplog IMP:9 + 🧪 TRAP[TEST] комментариями.
- TRAP-комментарии: TRAP[BUG] ×3 (rsync-guard, privoxy-mode, forced-command base), TRAP[DECISION] ×2 (T12 keep, 124-атрибуция в VR), TRAP[DEBT] ×1 (test_shared_timeouts env-leak).

## Финальный операторский чеклист (перед ночным промтом)

- [ ] **O1. /etc/hosts** — `sudo sh -c 'printf "127.0.0.1 ai-platform.local tronyx-site.ai-platform.local dance-site.ai-platform.local botanika.ai-platform.local platform.ai-platform.local\n" >> /etc/hosts'`
- [ ] **O2. GHCR** — hermes-agent-base public/write:packages (P-13 → Build Hermes GREEN)
- [ ] **O3. Ноды пересозданы** — test-e2e + tronyx-vps (⚠️ сейчас tronyx-vps ЖИВ — uptime 7:43, пересоздание не выполнено)
- [ ] **O4. gate GREEN** — `make gate MODE=fast` (последний шаг сессии)

---

## Итог

**Вердикт: PASS.** 17/17 задач: 13 PASS/FIXED, T8/T10 закрыты фактом, T11/T12 by-construction/keep,
T16 атрибуция задокументирована. Долги D-4..D-11 актуализированы. Единственные незакрытые пункты —
операторские O1-O3 (не в полномочиях агента; без O3 ночной прогон НЕ штатный).

**Финальный гейт (инвариант 1 DevPlan):** `make check` до чистоты (2× GREEN подряд, фикс-цикл:
5 ошибок первого прогона — ruff F821/PERF401, doxygen 7 warnings, литералы /etc/letsencrypt/live и
/home/ci-deploy, незакрытый # endregion — все исправлены) → `make gate MODE=fast` **ALL PASS**
(2026-08-03, commit 2183154 + 0bda4be). Doxygen: 0 warnings, per-file XML регенерирован.
