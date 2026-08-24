# Direction 9 — Duplicate & Dead Tests

Агент: adversarial-аудит направления «duplicate/dead tests» · Дата: 2026-08-22

Итог направления: Suite дисциплинирован по staleness (ноль stale skips/archived traps/dead quarantine), но несёт реальную дубликацию: ~3 directly removable test функции в unit healthcheck contract, 1 removable root e2e файл (~3 test items, 141 строк), 1 dead helper и ~10 copy-paste тел, консолидируемых в 4 параметризованные функции. Оценка removable без потери защиты: **~7 тестовых функций / ~230 строк** (+ ~10 дальнейших merge'ей в parametrize), плюс 5 untracked report-*.xml локальных файлов. Complementary-by-design пары, подтверждённые НЕ-redundantными: unit generator logic vs manifest freshness gates; Python healthcheck_poller canon vs shell-facade D5 parity (dual-canon TRAP[DECISION]); smoke vs component hermes слои.

---

### TEST-081: Healthcheck facade contract grepped в триплете (unit + 2 gates)
- Test: tests/unit/test_healthcheck_contract.py:104 (test_nginx_docker_healthcheck), :148 (test_litellm_healthcheck_fallback), :188 (test_nginx_healthcheck_deep_http) ↔ tests/gates/test_gate_healthcheck_unification.py:154 (AC3, _DOCKER_MODULES включает nginx+litellm), :64 (AC5 nginx→check_http) ↔ tests/gates/test_gate_healthcheck_contract.py:67 (litellm check_http/no-raw-curl)
- Production code: core/modules/{nginx,litellm}/healthcheck.sh, core/lib/healthcheck.sh
- Claimed guarantee: unit слой ловит регрессии, которые не ловят гейты
- Actual guarantee: unit Tests 2-3 — строгие подмножества gate AC3-цикла по тем же файлам с тем же substring-ассертом (`"check_docker_health" in content`). Unit Test 4 — надмножество unit Test 2 внутри того же файла (добавляет port-80/check_http). Gate suite перепрогоняет всё это на каждом CI pass
- Blind spot при удалении: none для Tests 2-3; уникальный бит Test 4 — presence `"127.0.0.1:80"` — один ассерт, достойный вливания в gate AC5
- Possible production bug: n/a maintenance
- Recommended test: сохранить уникальные активы unit-файла — hermes port-9119 incident regression (:59, TRAP[INCIDENT] 2026-07-10) и lib behavioral моки через PATH injection (:259-454); оба гейта сохранить (разные AC). Влить port-80 ассерт в test_exec_check_used_in_docker_exec_modules
- Existing test to remove/merge: удалить test_nginx_docker_healthcheck и test_litellm_healthcheck_fallback; влить test_nginx_healthcheck_deep_http в gate unification (или наоборот — но гейты там, где живут module-wide контракты)
- Confidence: HIGH

### TEST-082: Root test_e2e_health.py дублирует tests/e2e/ health checks
- Test: tests/test_e2e_health.py:66-105 (параметризованы grafana /api/health + langfuse /api/public/health) ↔ tests/e2e/test_e2e_langfuse.py:33 (тот же endpoint, env var, TRAP[LOCAL] CI-skip скопирован дословно) и tests/e2e/test_e2e_grafana_api.py:44 (authenticated Grafana API incl. login)
- Production code: nginx-routed Langfuse/Grafana endpoints
- Claimed guarantee: GUARD-PRESERVE комментарий tests/test_e2e_health.py:62 говорит «единственное e2e-покрытие health-слоя»
- Actual guarantee: комментарий ложен — tests/e2e/test_e2e_langfuse.py покрывает идентичный URL/env/assert/skip путь; grafana_api дополнительно ассертит datasources/dashboards/login, поглощая голый `/api/health` 200
- Blind spot: none; обе копии бегут под маркером e2e
- Possible production bug: n/a maintenance
- Recommended test: канонические копии tests/e2e/* (таксономия: root = только Docker-dependent; этот файл имеет ноль requires_docker маркеров — нарушение таксономии). Удалить root-файл целиком
- Existing test to remove/merge: tests/test_e2e_health.py (весь файл, 141 строка)
- Confidence: HIGH на дубликацию, MED на выжившую копию (подтвердить, что внешний runner не таргетит root-имя файла)

### TEST-083: Copy-paste scenario families — кандидаты на parametrize (sampled big files)
- Test: tests/unit/test_docker_auth.py:48/84/102/116 (docker_login success/failure/anonymous/env) vs :151/184/202/216 (ghcr_login — идентичные тела modulo registry/user/token аргументов); tests/unit/test_ssl_certs.py:70/78/86 (parseable ok/fail/timeout), :134/143/151 (issuer ok/fail/empty), :163/171/180 (is_le accepts/rejects/failure) — каждый триплет отличается только замоканным returncode/stdout
- Production code: core/internal/shared/docker_auth.py, core/internal/shared/ssl_certs.py
- Claimed guarantee: per-scenario покрытие
- Actual guarantee: та же гарантия могла бы идти от параметризованных кейсов; tests/unit/test_orchestrator.py:213,285,298 уже моделирует паттерн (*_variants)
- Blind spot: none — чистая консолидация, ноль потерянных ассертов
- Possible production bug: n/a
- Recommended test: parametrize по (login_fn, args) → 8→4 в docker_auth; по (rc, stdout, expected) → 9→3 в ssl_certs
- Existing test to remove/merge: 10 тестовых тел схлопываются в 4 параметризованные функции
- Confidence: HIGH

### TEST-084: Dead helper — gate_helpers.module_yaml_paths() имеет ноль потребителей
- Test: tests/helpers/gate_helpers.py:93
- Production code: none (сам helper)
- Claimed guarantee: каноническое перечисление YAML-путей для гейтов
- Actual guarantee: nothing — grep по tests/ + core/ находит только собственное определение/docstring; tests/gates/test_gate_env_shared_consistency.py:107 использует local parameter того же имени с независимой реализацией, доказывая non-use
- Blind spot: deletion risk zero; sibling helpers живы (repo_root :166 call sites, assert_ldd_imp9 :328, load_workflow :1, get_on_section :5 — thin but used)
- Possible production bug: n/a
- Recommended test: удалить функцию; опционально добавить в dead-export lint
- Existing test to remove/merge: только module_yaml_paths()
- Confidence: HIGH

### TEST-085: Гипотеза «committed report-*.xml артефакты» — ОПРОВЕРГНУТА; semi-dead поверхность чиста
- Test: tests/report-{smoke,static,predeploy,contract,ai-instructions}.xml — git ls-files показывает 0 tracked; .gitignore:92 (tests/report*.xml) их игнорирует; producer merge_junit.py жив (core/internal/check_suite/gate.py, core/internal/test_runner.py)
- Production code: JUnit reporting pipeline
- Claimed guarantee: n/a
- Actual guarantee: это untracked локальный прогонный остаток (~615 KB, report-static.xml 560 KB) — безопасный rm, не repo-hygiene баг
- Blind spot: none в репо; только agent-confusion риск от stray файлов в worktree
- Possible production bug: n/a
- Recommended test: действий в git нет; опциональная локальная чистка
- Existing test to remove/merge: none
- Confidence: HIGH

### TEST-086: Негативные находки — R3/quarantine/version-drift гигиена держится (нет кандидатов на удаление)
- Test: full-tree scan: 0 × TRAP[ARCHIVED]; 0 × plain @pytest.mark.skip вне quarantine механизма (tests/_conftest/quarantine.py:145, тестируется tests/unit/test_quarantine.py); единственный skipif файл в unit/gates/integration (tests/unit/test_secrets_validation.py, 4 маркера, все с reason) последний раз тронут 2026-08-22 — далеко от 90-дневного RED; QUARANTINE registry пуст by design; все позиционные pytest.skip("...") с reason
- Production code: n/a
- Claimed guarantee: R3 stale-skip правило enforce'ится
- Actual guarantee: enforce'ится и сейчас удовлетворяется; sys.path.insert встречается в 89 файлах, но по политике tests/AGENTS.md они таргетят module-specific roots (легитимно) — conftest-дублированные задокументированы как tolerated
- Blind spot: version-drift — golden/snapshot файлов нет; tests/test_data/* — behavior-test входы (valid/invalid фикстуры для test_config_merge.py, test_validate.py, test_llm_policy_schema.py), а tests/**/*_generated.py снапшоты — артефакты Manifest Generation Contract, охраняемые test_gate_manifests_up_to_date.py:52 — by design, keep
- Possible production bug: n/a
- Recommended test: none — не трогать
- Existing test to remove/merge: none
- Confidence: HIGH
