# Direction 3 — Weak Assertions

Агент: adversarial-аудит направления «weak assertions» · Дата: 2026-08-22

Итог направления: набор необычно хорошо защищён от классических слабых ассертов — R1 AST-гейт (test_gate_r1_no_pass_tests.py) активно полицит `assert True`/pass-тесты (живых инстансов ≈ 0), mock-only верификация — 1 функция из 3367 (~0.03%), тривиально-истинных ассертов нет вне документированных R5-фикстур. Остаточная слабость в двух полосах: (a) shell/config гейты, ассертящие presence строк там, где важны значение/control-flow (~19 substring-only функций + healthcheck/make-contract гейты ≈ 5-8% suite); (b) LDD non-enforcement — ~45% тестовых функций без IMP:9-ассертов, включая 68, печатающих траектории «для вида» (крупнейшая полоса, отчасти легитимная для чистых параметризованных калькуляторов). Оценка: 8-12% suite несёт ассерты слабее заявленных гарантий. Вердикт: AMBER — системной гнили нет, но comment-string ассерт healthcheck-гейта (TEST-021) — false-green опасность на продакшн-критичном контракте; LDD-хвост (TEST-022) — самый объёмный cleanup.

---

### TEST-021: Healthcheck gate ассертит строки комментариев и presence имён wrapper'ов, не control flow
- Test: tests/gates/test_gate_healthcheck_contract.py:43-59 (`test_deep_mode_has_early_exit`), :64-77 (`test_litellm_uses_check_http`), :91-105 (`test_postgres_deep_includes_pgbouncer`, `test_logging_deep_includes_alloy`)
- Production code: контракты deep-mode `core/modules/*/healthcheck.sh` (семантика early-exit, вызов `check_http()`, deep-пробы pgbouncer/alloy)
- Claimed guarantee: deep mode исполняет полную диагностику, затем exit 0 без fallthrough в liveness; litellm проверяет реальный HTTP endpoint через check_http
- Actual guarantee: литеральная строка `"exit 0  # ранний выход"` присутствует где-то в файле (ассерт НА КОММЕНТАРИЙ); токен `check_http` и отсутствие `curl -sf`; слова «pgbouncer»/«alloy» встречаются case-insensitively где угодно — включая комментарии
- Blind spot: control flow, значения аргументов и цели проб не верифицированы. `exit 0`, размещённый ДО исполнения диагностики, проходит при наличии магического комментария; переименование комментария = ложный RED; `check_http http://127.0.0.1:4001/wrong-path` или `# TODO add alloy check` проходят зелёными
- Possible production bug: рефакторинг healthcheck поднимает early exit выше deep-диагностики — контейнеры healthy при сломанном Postgres/pgbouncer; platform healthcheck_poller видит зелёный во время outage
- Recommended test: парсить скрипт в блоки (extract deep branch), ассертить порядок (`диагностика → exit 0`) и извлекать фактический URL/port, переданный в `check_http`, сравнивая с SoT platform_ports.py; для pgbouncer ассертить argv `pg_isready -h ... -p 6432`, не presence слова
- Existing test to remove/merge: усилить на месте; comment-pattern ассерт удалить полностью
- Confidence: HIGH

### TEST-022: Anti-Illusion разрыв — 68 тестов печатают LDD-траекторию, но не ассертят IMP:9; ~45% тестовых функций без IMP:9-enforcement
- Test: напр. tests/unit/test_platform_export_metrics.py:123 (`test_docker_collector_containers`); полный AST-скан: 68 trajectory-печатающих тестов без `found_imp9`/`assert_ldd_imp9` и без `@ldd_trajectory`
- Production code: варьируется — sampled: `docker_collector.get_containers` (core/internal/healthcheck/platform_export_metrics)
- Claimed guarantee (house rule): success-сценарии эмитят и верифицируют ≥1 `[IMP:9]` business-logic лог
- Actual guarantee: только value-ассерты; траектория напечатана для человека, presence IMP:9 не проверяется
- Blind spot: логирование-регрессии невидимы — удаление/даунгрейд IMP:9-телеметрии в production-модулях оставляет каждый тест зелёным
- Possible production bug: оператор теряет всю business-телеметрию во время инцидента (кто-то перевёл логгеры на INFO без IMP-тегов) — ни один тест не заметит
- Counts: `@ldd_trajectory` на 1724 из 3367 unit+gate тестовых функций (~51%) + 428 ручных `assert_ldd_imp9` ⇒ оценка 55% enforcement; 333/355 файлов упоминают IMP, многие — только в комментариях/принтах
- Recommended test: применить `@ldd_trajectory` (или `assert_ldd_imp9(caplog)`) к оставшимся недекорированным success-сценариям; чистые параметризованные калькуляторы exempt'нуть явно
- Existing test to remove/merge: none — декорировать на месте
- Confidence: HIGH

### TEST-023: `pytest.raises(Exception)` без пина — validation-by-accident может проходить
- Test: tests/unit/test_llm_policy_schema.py:307 (`test_invalid_policy_empty_providers`, без follow-up message/type ассерта вообще); :242 + вакуумный `assert isinstance(exc_info.value, Exception)` на :258; tests/unit/test_llm_config_renderer.py:429
- Production code: пути отклонения schema-validation `LLMPolicy.from_yaml` / `render_litellm_config`
- Claimed guarantee: policy без `aliases` / с пустым `providers:{}` отклоняется schema validation
- Actual guarantee: любое исключение покидает `from_yaml` — включая yaml.YAMLError, TypeError или краш fixture-формы ДО запуска валидации
- Blind spot: тип и сообщение исключения не верифицированы (:307); :258 — R2-нефальсифицируем (pytest.raises уже гарантирует Exception-инстанс)
- Possible production bug: schema validator перестаёт кидать ValidationError и вместо этого падает посторонним KeyError глубже в loader — негативный тест остаётся зелёным, реальные malformed policies падают в prod с путаными stack trace
- Recommended test: `pytest.raises((ValidationError, pydantic.ValidationError), match="providers")` — пин типа + имени поля, как уже правильно делает tests/unit/test_firewall.py:42-71 с `match=`
- Existing test to remove/merge: isinstance-ассерт убрать; match= добавить во все три сайта
- Confidence: HIGH (broad raises всего 5 по репо — blast radius мал)

### TEST-024: Единственный mock-only тест верифицирует call count, а не исполняемую команду
- Test: tests/unit/test_shared_docker_compose.py:549-567 (`test_nginx_reload_failure_mode`) — единственный ассерт тела: `assert mock_run.call_count == 1`
- Production code: `docker_compose.nginx_reload(container, timeout)` (non-fatal reload facade над docker_ops.docker_exec)
- Claimed guarantee: rc≠0 от `docker exec <container> nginx -s reload` → без raise, обрабатывается как non-fatal
- Actual guarantee: subprocess.run вызван единожды с любым argv, любым timeout; WARN-лог, обещанный контрактом, не ассертится
- Blind spot: неверное имя контейнера, потерянный флаг `-s reload`, проигнорированный `timeout=30`, отсутствие warning-телеметрии — всё проходит
- Possible production bug: рефакторинг меняет порядок аргументов (коррупция вида `docker exec nginx -s reload nginx`) — каждый релиз молча «перезагружает» ничего; тест зелёный
- Recommended test: ассертить `mock_run.call_args` argv содержит `["exec", "nginx", "nginx", "-s", "reload"]` и `timeout=30`; плюс caplog-ассерт IMP-tagged warning при rc≠0
- Existing test to remove/merge: усилить на месте (AST-скан: это ЕДИНСТВЕННАЯ mock-only функция на 176k LOC — остальные парят моки с value-ассертами)
- Confidence: HIGH

### TEST-025: Makefile restart-semantics gate проверяет ключевые слова, не команды
- Test: tests/gates/test_gate_make_contract.py:450-478 (`test_root_makefile_restart_is_soft`); sibling :579 тот же паттерн для manifest
- Production code: root Makefile / makefiles/modules.mk таргет `restart:` (оркестрация рестарта стека)
- Claimed guarantee: restart мягкий — `stop && start`, никогда `down && up -d`
- Actual guarantee: подстроки «stop»/«start» присутствуют, «down»/«up -d» отсутствуют в извлечённом recipe-тексте
- Blind spot: `docker compose kill`, `stop --timeout 0` (hard SIGKILL-семантика) или stop/start против неверного `-p project` проходят; слово «down» внутри echo/комментария даёт ложный RED
- Possible production bug: кто-то «оптимизирует» restart в `kill && start` — in-flight DB-записи трунцируются при каждом рестарте; гейт зелёный, т.к. «kill» не в deny-листе
- Recommended test: токенизировать recipe-строки, ассертить точные формы команд (`$(COMPOSE) stop` + `$(COMPOSE) start ...`), отсутствие kill/down/up как целых слов
- Existing test to remove/merge: усилить на месте
- Confidence: MED

### TEST-026: Structural preservation ассертится только key-substring presence (~19 таких тестов кластером)
- Test: репрезентативный худший: tests/unit/test_discover_modules.py:123-140 (`test_update_compose_include_preserves_other_sections`: `"networks:" in content`, `"    external: true" in content`); также tests/gates/test_gate_status_page.py:75-82 (presence `profiles:`/`healthcheck:`), tests/unit/test_backup_cron_dockerfile.py:56 (`COPY scripts/`), tests/gates/test_gate_wave_sort_contract.py:42 (prose-подстроки `'Sort contract'`, `'items.sort('`)
- Production code: `discover_modules.update_compose_include` (compose include rewriting); контракты status-page module.yaml/compose
- Claimed guarantee: include-update сохраняет секции networks/volumes нетронутыми
- Actual guarantee: четыре YAML key-фрагмента встречаются где-то в файле — дублированные секции, переставленные ключи, потерянный `name:` под network или битый indentation между фрагментами проходят
- Blind spot: идемпотентность/структура — двойной запуск update, дублирующий `platform:` под networks, всё ещё показывает оба фрагмента
- Possible production bug: compose-rewriter дублирует external networks между регенерациями; `docker compose up` падает с «network declared multiple times» на клиентских нодах при зелёном CI
- Recommended test: `yaml.safe_load` после rewrite и deep-compare `data["networks"] == expected_networks_dict` (полное равенство, не substring); suite уже предпочитает этот стиль в других местах (test_gate_local_stack.py:234-238 missing/extra set comparison)
- Existing test to remove/merge: усилить на месте; prose-substring вариант в wave-sort-contract слить с behavioral determinism тестом на :95/:126
- Confidence: MED
