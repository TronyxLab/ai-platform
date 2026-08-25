<!-- GREP_SUMMARY: final-qa test-gaps false-green untested-paths negative-tests coverage-matrix honesty -->
<!-- STRUCTURE: ▶ структурный скан (здоров) → ⚡ G1-G8 дыры покрытия → ⊕ матрица REF→тесты → ⎋ -->

# TEST-GAPS — дыры тестового покрытия (final QA, независимо)

## Структурное здоровье: ЗДОРОВО

Автоскан 18 крупнейших новых тест-файлов (assert/raises/mock/skip/broad-except плотность):

| Метрика | Результат |
|---------|-----------|
| assert-плотность | 4–78 на файл, все ≥3 нетривиальных |
| `pytest.mark.skip` | **0** во всех проверенных (R3/R4 чисто) |
| `except Exception` вокруг act | **0** (R1 чисто) |
| pytest.raises для негативных | присутствует в файлах с негатив-сценариями |

Выбочные реальные прогоны субагентами: test_file_lock 7/7, test_on_project_deploy 15 passed,
test_gate_workflow_sha_pins 8 passed — коллекции живые, не silent-empty. Тесты drain/waitpid
(test_parallel_runner) эмулируют реальную семантику с mocked waitpid и имеют red→green R5-негатив.

Итог: массовой проблемы «формально зелёных» тестов НЕТ. Проблема точечная — непокрытые пути и
отсутствующие негативы:

---

## G1 · RemoteExecutor stdin-ветка не покрыта ничем [HIGH]

`tests/unit/test_remote_executor.py:255` ассертит только legacy argv-путь (`cmd[-1] == REMOTE_CMD_UPDATE`);
ветка `if stdin_payload:` → `bash -s` + input= не тестируется никем (grep input=/bash -s по файлу — 0).
Это единственная точка, где секрет реально покидает argv-мир: удаление/поломка ветки вернёт ключ в
/proc молча, все тесты останутся зелёными. REF-0007.

## G2 · Обещанные тесты REF-0104 не существуют [HIGH]

TRAP в `key_provisioner.py:391` утверждает «corruption-chain unit-тест» — ложь:
- corruption-chain (truncate store → следующий load fail-loud) — отсутствует;
- pagination ≥2 страницы на MockTransport (`total_pages`) — отсутствует;
- transport-error ≠ no-key (404) для sync-методов — отсутствует.
Центральные обещания среза (fail-fast стора, пагинация, различение 404) не защищены от регрессии;
в связке с C4/R6-L6 это критично.

## G3 · Floors gate без mutation-негатива; floor≥1 маскирует частичную потерю [MEDIUM]

`tests/gates/test_gate_collection_floors.py` содержит только позитив и parity-селекторов. Нет теста
«опустевший слой → RED». По канону R5 гейт, ссылающийся на REF-ID, обязан иметь негатив на исходной
форме бага. Плюс принципиальное ограничение floor≥1: было 20 тестов → остался 1 = PASS (задокументировано
как «нижняя граница», но осознаваемая маска). Parity сверяет маркер/корень, не полную эквивалентность
(floor static_audit `-m static_audit` ≠ catch-all expr реального сюита).

## G4 · GRANT-тест ассертит SQL-строку, не целевую БД [MEDIUM]

`test_on_project_deploy.py:236` — регрессия R15 (GRANT в admin-DB) непоймаем текущим тестом. Нужен
ассерт `-d {db_name}` в команде psql / целевой БД выполнения.

## G5 · Pair-match протестирован только на RSA; прод использует ec-256 [LOW]

`test_ssl_certs_pair_match.py:46` генерирует rsa:2048; `issue_cert.py:103` KEY_LENGTH="ec-256".
Реализация type-agnostic (SPKI-compare), но EC-ветка не покрыта ни одним прогоном.

## G6 · SHA-pin гейт не проверяет свежесть пина [MEDIUM]

`test_gate_workflow_sha_pins` ловит mutable-tag/SHA-без-комментария/raw-interpolation, но stale-SHA-
с-комментарием проходит (так просочился C2: пин 2026-08-18 с комментарием «2026-08-24»). Нужен
freshness-критерий (пин ≥ даты последнего изменения пинуемого файла или сверка с HEAD).

## G7 · Honesty/oracle/self-гейты: детекторы без self-негативов [MEDIUM]

- honesty: нет тестов на три щели из R13 (*.yaml, прямой channel-вызов, pin-in-comment).
- oracle: independence тест AST-структурный есть, но нет mutation-теста «расхождение манифеста → RED»
  на живом дереве; read_text без except → исключение ≠ красный вердикт в терминах отчёта.

## G8 · Watchdog state-save failure path и R9 ps-failure path [LOW]

Watchdog: тесты покрывают stamp-after-success ordering и skip-notify, но не OSError при re-save
(прерывание батча, L3). Converge runtime: label-детекция e2e-тест есть, но нет теста «ps rc≠0 → НЕ
converged» (R4).

---

## Матрица покрытия TOP-20 → тесты

| REF | Файлы | Вердикт |
|-----|-------|---------|
| 0002 | test_postgres_ensure_convergence(328), test_on_project_deploy, gate_module_hooks, gate_shared_db_seam | VERIFIED (кроме цели БД, G4) |
| 0003 | test_healthcheck_failed_rc(258), test_deploy_many_observability, severity-mapping | VERIFIED |
| 0004 | test_rollback_contour(568: characterization+сквозной ROLLED_BACK) | VERIFIED |
| 0005 | test_parallel_runner(red→green real-drain), test_hc_marker_run_scope(169) | VERIFIED ядро / PARTIAL fresh-семантика (R2, R3 хвосты) |
| 0006 | test_verify_contracts(240,78 asserts), orchestrator_gate(259), traversal ×8 | PARTIAL (top-level volumes/ipc/volumes_from — C3) |
| 0007 | test_secret_writers_mode(307), ssh_cmd_builder(+178), core_deliverer(+216), access_surface(408) | PARTIAL (G1 executor-звено; R1 persist-path) |
| 0008 | pair_match(186), fqdn_validator(207), expiry_scan(164), issue_cert_backoff(119) | PARTIAL (G5 EC; R11 бюджет) |
| 0009 | cleanup_spool(290), backup_postgres(+136), collector, restore_recipe_ref0009(105) | VERIFIED (fail-closed шифрование подтверждено) |
| 0010 | test_ref0010_monitoring_honesty(424,56 asserts) | VERIFIED |
| 0011 | file_lock(263, реальный прогон 7/7), interleave(154) | PARTIAL (L7 TOCTOU окно) |
| 0012 | workflow_sha_pins(444), workflow_consistency | PARTIAL (G6 freshness; R9 adopter) |
| 0013 | decrypt_failfast(188), merge_guard(266), node_dispatch(147), postcondition(272), signal_contract(214) | PARTIAL (R5 partial-parse bypass) |
| 0014 | watchdog(+210), reconciler_r9_runtime(199) | PARTIAL (R4 blind-zone, G8) |
| 0015 | receive_flow_resource_guards(227), nginx_ingress_guards(92) | VERIFIED |
| 0103 | ref0103_subprocess_error_excepts(101), healthcheck_poller(+168) | VERIFIED (хвост L1) |
| 0104 | llm_config_renderer_integration | SHALLOW→MISSING для стора/пагинации/transport (G2) |
| 0107 | collection_floors, honesty_mode, fingerprint_salt(119), manifest_oracle(250), static_only(83) | PARTIAL (G3, G7, R12-R14) |
| 0017 | provides_networks_parity(252), smoke_env_host_resolution(194) | PARTIAL (негатив parity есть; runtime — только staging) |

## Рекомендации (без автофикса)

1. G1+G2 закрыть первыми — оба маскируют security/reliability регрессии заявленных инвариантов.
2. В freshness-критерий в sha-pins гейт (G6) — дешёво, предотвращает повтор C2.
3. Mutation-негативы для floors/honesty/oracle (G3/G7) — по одному тесту на гейт.
