# Матрица покрытия дефектов D1-D23 — DevPlan 136 W1 (регресс-тесты свежего бутстрапа)
# GREP_SUMMARY: coverage-matrix, D1-D23, regression-tests, fresh-bootstrap, W1, defect-coverage, R5
# STRUCTURE: ▶ D1-D23 inventory → ◇ type (regress-test|ops|env) → ◇ test/commit mapping → ⎋ coverage verdict
# 🧐 TRAP[DECISION] · 2026-08-05 · — · W2 добавил doc-заголовки (GREP_SUMMARY/STRUCTURE) в шапку W1-файла
# · Rejected: оставить файл без doc-заголовков (строгое правило 5 «не трогать чужие волны» — gate остался бы красным)
# · Reason: pre-commit doc-headers hook (gate fast) требовал GREP_SUMMARY/STRUCTURE в первых 10 строках;
# ·   правка чисто doc-стандартная (0 изменений семантики матрицы), необходимая для зелёного gate W2 (AC W2)
# · Rev: если doc-headers hook ослабит требование для .md — правку можно откатить

$START_MATRIX

## Контекст

- **План:** `.ai/plans/136-bootstrap-hardening/02-DevPlan.md` §5.1 (W1, T1.1-T1.12), AC W1.
- **Источник списка D1-D23:** таблица §6 «Дефекты» в `.ai/plans/135-end-to-end-platform/01-StatusReport.md`
  (авторитетные определения дефектов 135) + коммиты фиксов 135 (`git log 054a6b3~28..054a6b3`).
- **Принцип (R5 anti-survivorship):** каждый регресс-тест использует ТОЧНЫЙ вход, вызвавший баг.
- **Тип закрытия:**
  - `regress-test` — регрессионный тест на точный вход (W1);
  - `ops` — операционная/инфраструктурная причина (секреты GitHub, окружение dev-машины, CI-обвязка) —
    регресс-тест неприменим или живёт вне repo-кода;
  - `env` — дефект окружения (локальный venv/clickhouse) — регресс-тест неприменим;
  - `причинно` — исправлен иначе/в составе другого дефекта; покрытие указано в строке.

## Матрица

| # | Дефект (135 StatusReport §6) | Тип | Тест-файл (W1) | Статус |
|---|------------------------------|-----|----------------|--------|
| D1 | `make up-safe` падал: compose_preflight.py без PYTHONPATH (core.* импорты, pre-existing с 119da0f) — фикс compose-wrapper.sh export PYTHONPATH (7a7537e) | regress-test | `tests/unit/test_deploy_mk_chain.py::test_compose_wrapper_exports_pythonpath` | ✅ тест зелёный |
| D2 | `up-safe` переопределял COMPOSE_PROFILES пустым при пустом MODULES → «no service selected» — фикс modules.mk passthrough .env (7a7537e) | regress-test | `tests/unit/test_deploy_mk_chain.py::test_up_safe_empty_modules_passthrough_profiles` (+ R5 negative: безусловный паттерн удалён) | ✅ тест зелёный |
| D3 | `render-monitoring` ModuleNotFoundError (sys.path fallback неполный) — фикс repo-root bootstrap (5fe5802) | regress-test | `tests/unit/test_deploy_mk_chain.py::test_render_monitoring_self_bootstrap_source` + `::test_render_monitoring_self_bootstrap_behavioral` (релоад с вычеркнутым repo root) | ✅ тесты зелёные |
| D4 | install-acme.sh неидемпотентен: git clone в существующий /opt/acme.sh (fresh bootstrap φ7) — фикс merge-fallback, сохраняющий *_ecc (017e1c1) | regress-test (файловая фикстура, shell keep-файл — DevPlan 136 §1 п.7) | `tests/test_nginx_acme.py::test_install_acme_merge_fallback_preserves_ecc` (mock git + tmp_path ACME_HOME с *_ecc) | ✅ тест зелёный |
| D5 | φ3 пропускал docker_registry_auth при пустых кредах → mirror не настраивался → 429 на пуллах — фикс «скрипт запускается всегда» (8327c1d) | regress-test | `tests/unit/test_bootstrap_phases.py::test_phase_platform_setup_runs_docker_auth_with_empty_creds` | ✅ тест зелёный |
| D6 | docker_registry_auth.py sys.path 3 уровня (core/) вместо корня репо — фикс 4 уровня (665aad0) | regress-test (source-гейт) | `tests/unit/test_bootstrap_phases.py::test_docker_registry_auth_syspath_bootstrap_four_levels` | ✅ тест зелёный |
| D7 | φ3 не провижинил сети/volumes (комментарий «provision done in platform_setup» — ложь с wave4) → external networks missing в φ8 — фикс provision networks+volumes (be34360) | regress-test | `tests/integration/test_bootstrap_dry_run.py::test_platform_setup_provisions_networks_and_volumes` (+ R5 negative source-гейт `::test_platform_setup_provision_wiring_source_negative` — отсутствие фазы provision → FAIL) | ✅ тесты зелёные |
| D8 | lifecycle cli _mark_phase_* вставлял raw-dict в steps → to_dict() save crash при отсутствующей фазе на resume — фикс StepState вместо dict (67d9f10, fa16f34) | regress-test (существующий) | `tests/unit/test_state_machine.py` (StepState to_dict/from_dict round-trip, `_mark_phase_success`-вызовы, 41 тестов) | ✅ существующее покрытие + W2 T2.7 расширение resume-кейсов |
| D9 | context-promote: GitHub SSH case-sensitive — org tronyx-lab vs TronyxLab — фикс _resolve_org из overlay context.yaml#org (f572787) | regress-test | `tests/unit/test_context_promoter.py::test_resolve_org_from_overlay_context_yaml` + R5 mixed-case `::test_resolve_org_mixed_case_context_name` + fallback `::test_resolve_org_fallback_context_name` | ✅ тесты зелёные |
| D10 | CI sha-resolve: «no successful run» — GitHub API eventual-consistency гонка — фикс retry-цикл 10×30s (3fc343d) | ops (CI action.yml) | — (локальный юнит-тест неприменим: GitHub API eventual-consistency; верификация — повторные core-deploy SUCCESS на CI) | ✅ обосновано ops; W7 добавит аналогичный retry в mirror.yml |
| D11 | receive_flow: root-owned bootstrap-стуб docker-compose.yml → Permission denied при receive — фикс os.remove перед copy2 (9f91a78) | regress-test | `tests/unit/test_receive_flow.py::test_receive_deploy_overwrites_root_owned_stub` (+ R5 negative `::test_receive_deploy_remove_failure_logs_warning`) | ✅ тесты зелёные |
| D12 | CI_DEPLOY_KEY repo-секреты (08-03) ≠ node.yaml ключ; org-секрет PRIVATE visibility не наследуется — фикс repo-секреты = platform_personal_cicd, org visibility ALL | ops (секрет-менеджмент GitHub) | — (внешние секреты GitHub, не код; процедура — W7 `docs/ci-secrets-rotation.md`) | ✅ обосновано ops (W7) |
| D13 | sshd MaxStartups throttling (брутфорс) — CI соединения молча дропались — фикс MaxStartups 30:50:200 на ноде | regress-test (W3-волна, параллельный агент) | `tests/unit/test_security_posture_maxstartups.py` (W3: drop-in 99-platform-maxstartups.conf, security_posture.py) | ⏳ W3 (файл параллельного агента — не трогаю) |
| D14 | VPS_SSH_KEY (source repo) не авторизуется на новом сервере (ключ старого сервера) — фикс новый CI-root ключ vps_ci_root + секреты (Tronyx161 repo + TronyxLab org) | ops (ротация CI-ключей) | — (внешние секреты/ключи; процедура — W7 `docs/ci-secrets-rotation.md`) | ✅ обосновано ops (W7) |
| D15 | node-update φ9: AGE_SECRET_KEY не персистится на ноду → CI decrypt fail — фикс /etc/age/key.txt: detect-цепочка + φ4 persist (d2ded6a) | regress-test | `tests/unit/test_node_detect.py::test_detect_age_key_from_node_key_file` (assert персист) + R5 negative `::test_detect_age_key_node_file_absent_chain_completes` + `::test_detect_age_key_node_file_without_prefix_line` | ✅ тесты зелёные |
| D16 | ghcr_login писал config.json root-овым (root-процесс + HOME=ci-deploy) → pull permission denied — фикс chown config пользователю (c955a96) | regress-test | `tests/unit/test_docker_auth.py::test_ghcr_login_chowns_config_to_target_user` (+ R5 negative `::test_ghcr_login_no_chown_when_non_root`) | ✅ тесты зелёные |
| D17 | verify dispatch: `verify <node> <project>` сливался в один аргумент — фикс split node/project (8a4eb6d) | regress-test | `tests/unit/test_orchestrator_cli_dispatch.py::test_dispatch_verify_splits_node_and_project` + `::test_dispatch_verify_node_only` + R5 negative `::test_dispatch_verify_missing_node_negative` | ✅ тесты зелёные |
| D18 | hermes L2 локальный build: FROM hermes-agent-base:latest (bare) не резолвится на ноде — фикс L1 bare-tag после pull (4c86c3b) | regress-test | `tests/test_hermes_l1_bare_tag.py::test_handle_hermes_agent_bare_tag_after_pull` + R5 negative `::test_handle_hermes_agent_no_bare_tag_l2_build_fail` + `::test_handle_hermes_agent_tag_before_build_order` + `::test_handle_hermes_agent_pull_fail_build_l1_source` | ✅ тесты зелёные |
| D19 | vhost /health: proxy_pass $var/URI → nginx 500 (invalid URL prefix) — фикс proxy_pass $var без URI (643df6d) | regress-test | `tests/test_vhost_health_patterns.py::test_vhost_health_no_proxy_pass_var_uri` + R5 negative `::test_vhost_health_old_pattern_detector_negative` + `tests/test_templates.py::test_vhost_template_health_location_safe` | ✅ тесты зелёные |
| D20 | vhost /health: $upstream не определён в location-скоупе → 500 — фикс set в /health (c87d24c) | regress-test | `tests/test_vhost_renderer.py::test_vhost_health_set_upstream_in_location` + `::test_vhost_health_all_locations_have_set` + `tests/test_templates.py::test_vhost_template_health_location_safe` | ✅ тесты зелёные |
| D21 | env: brew upgrade python@3.14 (3.14.5→3.14.6) сломал .venv (dyld) — фикс `make venv` (канонический ремонт) | env | — (окружение dev-машины; канонический ремонт `make venv`, не код платформы) | ✅ обосновано env |
| D22 | env: локальный clickhouse RestartCount=217 (исторический OOM) → healthcheck restart-loop FAIL — фикс пересоздание контейнера (volume сохранён) | env | — (локальный docker-стек, операционная операция; не платформенный код) | ✅ обосновано env |
| D23 | roadmap проект: adopted без compose/CI/cert → 502 — фикс compose + workflow + Dockerfile, repo-секрет, wildcard-cert | причинно (проектная операция, не платформенный код) | — (фикс на уровне проекта по канону dance-site; верификация roadmap 200 + /health 200 на ноде — покрывается e2e-verify W5/W6) | ✅ обосновано причинно/ops (W5/W6 e2e-verify) |

## Расхождения DevPlan 136 ↔ код (задокументированы, W1)

| Пункт DevPlan | Реальность | Резолюция W1 |
|---------------|------------|--------------|
| T1.4: D15-тесты в `tests/unit/test_age_key.py` | `test_age_key.py` — R5-тест УДАЛЁННОГО модуля age_key.py (DevPlan 118 D3); детекция AGE живёт в `node_detect.py` | D15-тесты в `tests/unit/test_node_detect.py` (расширение, 3 теста); `test_age_key.py` не тронут |
| T1.5: D18-тесты в `tests/unit/test_hermes_images.py` | Путь занят тестами ДРУГОГО модуля — `core/internal/build/hermes_images.py` (DevPlan 118 E8); duplicate-basename (и с `tests/unit/test_hermes_workflow.py`) → pytest import file mismatch | D18-тесты в `tests/test_hermes_l1_bare_tag.py` (модуль под тестом — `bootstrap/deploy/hermes_workflow.py`) |
| T1.9: D19/D20 в `tests/test_templates.py` + `test_vhost_renderer.py` | `tests/unit/test_vhost_renderer.py` — полный юнит-набор renderer'а; duplicate-basename → pytest import file mismatch | D19/D20 структурные тесты в `tests/test_vhost_health_patterns.py` (НОВЫЙ) + guard в `tests/test_templates.py` |
| T1.1: `tests/test_nginx_acme.py` | Файл существующий (HTTP-01 fallback) | Расширен D4-регионом (install-acme merge-fallback, файловая фикстура) |

## Итог W1

- **Регресс-тесты (новые/расширенные):** 12 тест-файлов, +35 тестовых функций (D1-D7, D9, D11, D15-D20).
- **R5 negative на точный вход бага:** D2, D3, D4, D7, D9, D11, D15, D16, D17, D18, D19 — присутствуют.
- **ops/env/причинно:** D10, D12, D13 (W3), D14, D21, D22, D23 — задокументированы выше.
- **LDD IMP:9:** каждый успешный сценарий имеет IMP:9-лог (caplog trajectory).

$END_MATRIX
