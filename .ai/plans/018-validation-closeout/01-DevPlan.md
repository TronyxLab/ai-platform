# 01-DevPlan · 018-validation-closeout

$START_DEVPLAN
## $ARTIFACT_CONTRACT
- PURPOSE: Закрыть хвост приёмо-сдаточной валидации 017 (PASS_WITH_CONDITIONS) и провести отложенный промоут платформы в контекст tronyx-lab
- DESCRIPTION: Три isolated chaos-кейса F-21 (watchdog-channel/oom-sizing/disk-PromQL) + детерминированный F-22 (in-composition pollution TLS-metrics тестов) + NOTE-N7 (легаси S3_ENDPOINT в sops-матрице ноды) + внешние гейты D5/G5 + финальный release-checklist с `make context-promote CONTEXT=tronyx-lab`
- RATIONALE: Критерий владельца 017 достигнут (bootstrap одной командой, delivered=3 healthy, идемпотентность ×3); промоут заблокирован только дисциплиной гейта «B–G полностью зелёные» — остаток точно сфокусирован в 02-VerificationReport 017 и 01-Findings 017 (F-21 с гипотезами, F-22 с 3-шаговым планом)
- ACCEPTANCE_CRITERIA: (1) make check финальный 0 fail (включая TestStatusPageMetrics в полном составе); (2) chaos fast на tronyx-vps 9/9 GREEN; (3) backup upload verified sha256 только с каноническим S3_ENDPOINT_URL; (4) D5 CI-канал verified ИЛИ задокументирован owner-gate; (5) context-promote выполнен, пост-промоут healthcheck/e2e-verify ALL GREEN
- IMPLEMENTS: 017-launch-validation-tronyx-vps/{01-Findings.md F-20…F-22, NOTE-N7; 02-VerificationReport.md §Остаток}
- IMPACTS: tests/e2e/test_chaos_resilience.py; tests/unit/test_status_page.py; возможно core/modules/status-page/app.py; node-configs/secrets/<node>.enc.yaml (sops, tronyx-vps); нода tronyx-vps (chaos-инъекции, converge φ9); контекст tronyx-lab (промоут)
- REQUIRES: NODE=tronyx-vps SSH+AGE-доступ; владелец: решение по GitHub Billing (D5), по test-VPS/G5-waiver (release-checklist п.1), окно для chaos на prod-ноде (прецедент 017 §0.4 — разрешены)

---

## §0 Контекст и протокол волны (наследие 017)

- Нода tronyx-vps live, 13/13 модулей healthy, delivered=3 (tronyx-site/dance-site/botanika).
  Нода мутируется ТОЛЬКО последовательно из главной сессии; перед нодовыми операциями
  закрывать посторонние worktree-сессии (NOTE-N3 017: параллельный писатель).
- Единственная тестовая команда агента — `make check` (+MARKER/TEST_FILE/check-diff);
  `make gate MODE=fast` вручную не гонять; `make test`/`test-summary` запрещены.
- requires_node-тесты НЕ входят в make check/gate; канон запуска chaos-подмножества на
  prod-ноде (паттерн 017, фаза G): `NODE=tronyx-vps .venv/bin/pytest tests/e2e/test_chaos_resilience.py -m "chaos and not night"` —
  single-worker, последовательные инъекции. Raw pytest не журналируется runs.jsonl —
  вывод сохранять в `.ai/plans/018-validation-closeout/logs/`; полный `make test-node NODE=tronyx-vps`
  — только финальный сигнал W7 (полный requires_node-набор на одной ноде).
- Fix-forward: чинить до победного, fail → Coder-субагент → ре-верификация → дальше.
- Findings чанками ≤40 строк в собственный 03-Findings.md этой папки (восстановление:
  этот план + 017-артефакты + logs/latest.log).

## §1 Draft Code Graph

```xml
<knowledge_graph>
  <entity name="test_chaos_resilience_py" type="FILE"
          keywords="chaos,resilience,requires_node,drill,injection,assert_injection_landed"
          annotation="tests/e2e; FAST=-m 'chaos and not night' F1-F9; NIGHT=-m night N1-N3">
    <crosslink target="test_watchdog_heals_unhealthy"/>
    <crosslink target="test_oom_clickhouse_kernel_kill"/>
    <crosslink target="test_disk_pressure_alert_and_recovery"/>
  </entity>
  <entity name="test_watchdog_heals_unhealthy" type="FUNC"
          keywords="F6,watchdog,redis,unhealthy,restart_count,state_file"
          annotation="L515-687: inject=docker update --health-* (СЛОМАН docker29); restore=_restore_healthcheck через docker update (СЛОМАН); proof=RestartCount+watchdog-state-file">
    <crosslink target="watchdog_py"/>
  </entity>
  <entity name="watchdog_py" type="FILE"
          keywords="watchdog,unhealthy_since,cooldown,WATCHDOG_UNHEALTHY_MIN"
          annotation="core/internal/healthcheck/watchdog.py — лечит unhealthy≥N мин рестартом; НЕ меняем (инвариант канона; трогаем только тест-канал инъекции)">
  </entity>
  <entity name="test_oom_clickhouse_kernel_kill" type="FUNC"
          keywords="F7,oom,clickhouse,cgroup,memory_bomb,journalctl"
          annotation="L690-746: bomb=400×8MB≈2.98GiB захардкожен под старый лимит 1GiB; compose-лимит СЕЙЧАС 3G (DevPlan 144 W3 1G→2G; v1.0.1 2G→3G) — гипотеза root-cause: bomb НЕ превышает лимит">
    <crosslink target="clickhouse_compose_base_yml"/>
  </entity>
  <entity name="clickhouse_compose_base_yml" type="FILE"
          keywords="clickhouse,deploy,resources,memory_3G,TRAP_merge"
          annotation="core/modules/clickhouse/docker-compose.base.yml L77: memory 3G SoT; read-only референс (НЕ повышать ради теста)">
  </entity>
  <entity name="test_disk_pressure_alert_and_recovery" type="FUNC"
          keywords="F8,disk_pressure,fallocate,node_filesystem,promql,ratio"
          annotation="L749-858: df-proof landed (used≥92%), PromQL ratio<0.2 за 150s НЕ сошёлся; диагностика пути node-exporter→prometheus">
    <crosslink target="prom_ratio_cmd"/>
  </entity>
  <entity name="prom_ratio_cmd" type="CONST"
          keywords="prometheus,127.0.0.1:9090,node_filesystem_avail_bytes,mountpoint"
          annotation="L750-764: curl loopback 9090 изнутри ноды — F-036-loopback совместим; проверять наличие серий и свежесть timestamp">
  </entity>
  <entity name="test_status_page_py" type="FILE"
          keywords="status_page,metrics,tls_gauges,xdist,pollution"
          annotation="tests/unit; F-22: test_metrics_renders_tls_gauges/test_metrics_tls_days_left_nan_when_missing fail ТОЛЬКО в составе make check (solo GREEN); body содержит backup/deploy-гейджи, data.tls пуст">
    <crosslink target="setup_app_env"/>
    <crosslink target="status_page_app_py"/>
  </entity>
  <entity name="setup_app_env" type="FUNC"
          keywords="reload_safe,DI_binding,functools_partial,STATUS_METRICS_JSON"
          annotation="L289-332: reload_module(app)+functools.partial get_all_checks+setattr STATUS_METRICS_JSON на свежем инстансе; канон 167 D4 (0 env-мутаций) — не откатывать">
  </entity>
  <entity name="status_page_app_py" type="FILE"
          keywords="_handle_metrics,tls_section,platform_tls_days_left,collector"
          annotation="core/modules/status-page/app.py; кандидат-источник загрязнения: модульные глобалы/кэш collectors между reload'ами или двойной импорт app под разными sys.modules-ключами">
  </entity>
  <entity name="node_config_secrets_enc_yaml" type="FILE"
          keywords="sops,matrix,tronyx_vps,S3_ENDPOINT,legacy"
          annotation="node-configs/secrets/<node>.enc.yaml; NOTE-N7: несёт легаси S3_ENDPOINT; канон S3_ENDPOINT_URL (platform-infra.yaml L253); читатели кода — только канон (backup_config.py L122/200, upload-s3.sh L36)">
  </entity>
</knowledge_graph>
```

## §2 Волны и задачи

### W1 · F-22: локализация и фикс in-composition pollution TLS-metrics (локально, детерминированно)

Файлы: `tests/unit/test_status_page.py`, возможно `core/modules/status-page/app.py`.
Симптом (017 F-22): `TestStatusPageMetrics::test_metrics_renders_tls_gauges` / `..._nan_when_missing`
стабильно FAIL в составе полного `make check` («HELP platform_tls_days_left отсутствует»),
соло и парный xdist — GREEN ×N. Body содержит backup/deploy-гейджи → binding живой,
но `data.tls` пуст на момент чтения. Класс: кросс-тестовая мутация модульного состояния
того же xdist-воркера. Карантин ЗАПРЕЩЁН (unit-слой, «флак = баг»).

Шаги (план из Findings, детализация):
1. Локализовать воркер: `PYTEST_XDIST` gw-лог / `make check` с `-n` фиксированным →
   собрать первые ~40 тестов воркера, предшествующих tls-фейлу. Быстрая локальная
   репродукция состава: `pytest tests/unit/test_status_page.py tests/unit/<кандидат>.py`
   парно; расширять состав, пока фейл не воспроизведётся (bisect по составу).
2. Кандидаты: тесты, импортирующие/перегружающие status-page app или collectors;
   искать `setattr`/присваивание модульных глобалов (`STATUS_METRICS_JSON`, `NODE_NAME`,
   кэши collectors), двойной импорт app под разными `sys.modules`-ключами
   (`tests/unit` добавляет module-specific `sys.path` — ключ импорта может разойтись
   с `reload_module("app", expected_file_substring="status-page")`).
3. Фикс по канону: monkeypatch / восстановление атрибута в `finally` / перезагрузка
   внутри `TestStatusPageMetrics.setup`; НЕ откатывать DI-канон 167 D4.
4. Регрессия: тест-фейл стал воспроизводимым до фикса (negative-R5 стиль —
   зафиксировать polluter-состав в TRAP[TEST]).

Верификация: `make check TEST_FILE=tests/unit/test_status_page.py` → затем полный
`make check` до чистоты (состав — и есть тестируемое свойство).
Выход: полный make check без фейлов (впервые с 14:36 2026-08-27).

### W2 · F-21a: watchdog-инъекция без `docker update --health-*` (нода)

Файлы: `tests/e2e/test_chaos_resilience.py` (только тест F6). `watchdog.py` НЕ трогать.
Root-факт: docker 29 удалил `--health-*` из `docker update` (проверено на ноде, F-21);
сломаны ОБА канала теста: inject (L592-596) и `_restore_healthcheck` (L574-588).
Свойство теста неизменно: «watchdog ЛЕЧИТ unhealthy-but-alive, выше restart policy».

Дизайн (гипотеза — предпочтительный вариант): сервисный канал инъекции через сам
healthcheck-предмет. Redis healthcheck = `redis-cli ping`; `docker exec redis redis-cli
CONFIG SET requirepass <tmp>` → probe получает NOAUTH → unhealthy при живом контейнере
(unhealthy-but-alive ✅). Рестарт (watchdog) сбрасывает runtime-CONFIG (не persisted,
CONFIG REWRITE не вызывается) → канонический `Config.Healthcheck` в `docker inspect`
НЕИЗМЕНЕН → restore-ветка упрощается до проверки возврата healthy, `docker update`-флаги
удаляются. Reject: Docker Engine API `/containers/{id}/update` — то же удаление флагов;
recreate-контейнера — ломает «alive»-полусвойство и RestartCount-доказательство.
Guard: если healthcheck redis в compose — не `redis-cli ping`, взять фактический
`docker inspect` Test-массив и подобрать инъекцию под него (принцип: ломаем ЗАВИСИМОСТЬ
probe'а, не сам probe). TRAP[TEST] обновить (Remove if/Root).

Верификация: `NODE=tronyx-vps .venv/bin/pytest tests/e2e/test_chaos_resilience.py -k test_watchdog_heals_unhealthy`
(rc=0, RestartCount+1, state-file запись, healthy ≤60s, sites alive в окне).

### W3 · F-21b: oom_clickhouse — сайзинг bomb'а от фактического лимита (нода)

Файлы: `tests/e2e/test_chaos_resilience.py` (только тест F7).
Root-гипотеза (высокая уверенность): bomb захардкожен `400 × 8MB ≈ 2.98 GiB` под старый
лимит 1GiB (docstring L694 «лимит 1GiB»), а SoT-лимит поднят 1G→2G→3G
(clickhouse/docker-compose.base.yml L72-77, merge-инцидент v1.0.1) → 2.98 GiB < 3 GiB →
cgroup-OOM не наступает за 90s.
Фикс: (1) читать фактический лимит из `docker inspect clickhouse --format '{{.HostConfig.Memory}}'`;
(2) размер bomb'а = динамически от лимита (≥1.3×, cap по свободной памяти ноды с
предпроверкой `MemAvailable` — OOM-kill бьёт memcg-жертву, но headroom ноды проверяем
assert'ом, НЕ skip'ом — R4); (3) docstring обновить (лимит — из инспекции, не из памяти);
(4) kernel-паттерн жертвы по cgroup-id оставить как есть (закрытый TRAP VR 142 §6).
Если гипотеза не подтвердится (OOM по-прежнему нет при bomb > лимита): проверить
`max_server_memory_usage_to_ram_ratio`-перехват (CH убивает аллокатор сам — тогда жертва
именована иначе) и journalctl-паттерн; зафиксировать фактический канал в TRAP[TEST].

Верификация: `NODE=tronyx-vps .venv/bin/pytest ... -k test_oom_clickhouse_kernel_kill`
(kernel-OOM-строка в journalctl по cgroup-id, clickhouse healthy ≤120s, TTR ≤120s).

### W4 · F-21c: disk_pressure — PromQL data-path диагностика (нода, diagnostic-first)

Файлы: `tests/e2e/test_chaos_resilience.py` (тест F8); возможно конфиг node-exporter
(`core/modules/node-metrics/`) — по результату диагностики.
Факт: df-proof (`used≥92%`) падает ✅, PromQL `ratio<0.2` за 150s — ❌ (017 F-21).
Гипотезы по убыванию вероятности:
1. node-exporter не видит rootfs хоста (нужен `/`→`/host:ro,rslave` + `--path.rootfs=/host`;
   иначе метрики — fs контейнера) → серии `node_filesystem_*{mountpoint='/'}` отсутствуют/
   не двигаются. Проверка: `curl 127.0.0.1:9100/metrics | grep node_filesystem_avail` ДО/ВО
   время fallocate.
2. scrape_interval/staleness: серии есть, но обновляются реже окна → проверить
   `scrape_interval` в prometheus-конфиге и timestamp последнего сэмпла; при подтверждении —
   выровнять timeout теста от scrape_interval (×2+), а не фикс 150s.
3. Составитель «минимум по сериям» (`min(vals)`, L761-762): лишняя серия (overlay/дубликат
   mountpoint='/') маскирует провал → уточнить выборку серий.
Заполнитель не трогать (fallocate-канал доказан df-ом). Rule-expr алертов — вне скоупа
(Debt D-N, docstring L771-772). F-036 (loopback-prometheus) НЕ блокирует: тест ходит
с самой ноды.
Фикс = по подтверждённой гипотезе: exporter-конфиг (модульный фикс + converge) ИЛИ
таймаут/выборка теста. TRAP[TEST] обновить фактическим root.

Верификация: `NODE=tronyx-vps .venv/bin/pytest ... -k test_disk_pressure_alert_and_recovery`
(ratio<0.2 в окне, recovery ratio>0.5, sites alive).

### W5 · NOTE-N7: легаси S3_ENDPOINT → канон S3_ENDPOINT_URL (нода, sops-матрица)

Файлы: `node-configs/secrets/<node>.enc.yaml` (sops, нода tronyx-vps). Код НЕ трогать —
все читатели уже канонические (`S3_ENDPOINT_URL`: backup_config.py L122/200,
upload-s3.sh L36, wal_sync.py L221; `S3_ENDPOINT` читателей в core/ нет).
Шаги: (1) `sops`-редакция матрицы ноды: удалить/переименовать легаси `S3_ENDPOINT`
(значение endpoint'а живёт в platform-infra.yaml L253 — SoT; дубль в матрице не нужен);
(2) `make converge NODE=tronyx-vps` (φ9 secrets_update) → secrets.env ноды без легаси-ключа;
(3) верификация DR-канала: `make backup NODE=tronyx-vps` → UPLOAD VERIFIED sha256;
(4) TRAP[DECISION] в матрице/модуле не нужен — NOTE-N7 закрыть ссылкой на верификацию.
⚠️ Порядок: до W7 (release-checklist §5 проверяет DR-активность на канонической матрице).

Верификация: `ssh <node> grep -c '^S3_ENDPOINT=' /var/lib/platform/run/secrets.env` = 0;
`S3_ENDPOINT_URL` присутствует; backup upload verified.

### W6 · Внешние гейты (owner-gates, без кода)

| Гейт | Действие | Верификация |
|------|----------|-------------|
| D5 CI-push | Владелец: включить GitHub Billing org TronyxLab → разблокировать Actions | push любого проекта (tronyx-site) → deploy-project.yml → receive → DEPLOYED healthy; `make project-status PROJECT=tronyx-site` |
| G5 test-VPS | Владелец: либо восстановить доступ к test-VPS, либо явный waiver на release-checklist п.1 (E2E на prod-validated evidence 017/018) | решение зафиксировано в 02-VerificationReport 018 |
| G3 load-smoke | Остался owner-gate F-036 (prometheus loopback-only + AllowTcpForwarding=no) — вне скоупа этой волны | n/a (задокументирован) |

### W7 · Финал: полный зелёный контур + release-checklist + промоут

Предусловия: W1-W5 закрыты, W6-D5 решён (или зафиксирован waiver).
1. Полный `make check` — 0 fail; `make agent-check` — exit 0; `make check MARKER=check-manifests` — чисто.
2. Chaos fast полный: `NODE=tronyx-vps .venv/bin/pytest tests/e2e/test_chaos_resilience.py -m "chaos and not night"` — 9/9 GREEN (F-20 фиксы на месте).
3. Chaos night: `-m night` — N1 reboot ✅ (повтор), N2/N3 — первый GREEN-прогон этой волны (017 выполнил только fast+reboot).
4. `make e2e-verify NODE=tronyx-vps` — 3/3 HTTP 200; `make healthcheck NODE=tronyx-vps` — ALL MODULES HEALTHY.
5. Release-checklist §5: `AGE_RECIPIENT` непуст в env backup-cron; последние nightly uploads без `BackupUploadFailure`.
6. `make context-promote CONTEXT=tronyx-lab` (владелец-разрешение 017 §0.5 действует).
7. Пост-промоут: `make healthcheck` + e2e-verify + мониторинг без новых ошибок; вердикт
   → 02-VerificationReport.md этой папки.

## §3 Data Flow (сквозной сценарий волны)

▶ W1 локально: make check (состав) → bisect-состава → polluter найден → monkeypatch/DI-фикс → make check 0 fail
→ W2-W4 нода (строго последовательно): pytest -k watchdog → инъекция requirepass → unhealthy → watchdog restart → GREEN
→ pytest -k oom → bomb = inspect-limit×1.3 → kernel-OOM по cgroup-id → healthy ≤120s → GREEN
→ fallocate → node-exporter/prometheus диагностика → подтверждённая гипотеза → фикс → ratio<0.2 → GREEN
→ W5: sops-матрица − S3_ENDPOINT → converge φ9 → backup upload verified
→ W6: D5 billing (владелец) → CI-push verified
→ W7: make check + chaos fast 9/9 + night + e2e-verify + DR-чек → context-promote → пост-промоут мониторинг → ⎋ VERDICT: SUCCESS

## §4 Acceptance Criteria (проверяемые)

| # | Критерий | Проверка |
|---|----------|----------|
| AC1 | Полный make check 0 fail (включая оба TLS-metrics теста) | финальный `make check` rc=0 |
| AC2 | Chaos fast 9/9 GREEN на tronyx-vps | pytest rc=0, F6/F7/F8 PASS с evidence в `_out_dir` |
| AC3 | Chaos night GREEN (N1-N3) | pytest -m night rc=0 |
| AC4 | Backup verified на канонической матрице | `make backup NODE=tronyx-vps` UPLOAD VERIFIED sha256; легаси-ключ отсутствует в secrets.env |
| AC5 | D5 CI-канал verified или owner-gate задокументирован | project-status DEPLOYED после push ИЛИ запись в VerificationReport |
| AC6 | Промоут выполнен, пост-промоут зелёный | context-promote rc=0; healthcheck ALL MODULES HEALTHY; e2e-verify 3/3 |
| AC7 | Артефакты волны | 03-Findings.md (evidence), 02-VerificationReport.md (вердикт), журналы в logs/ |

## §5 File Manifest

| Файл | Операция | Волна |
|------|----------|-------|
| `.ai/plans/018-validation-closeout/01-DevPlan.md` | create (этот) | — |
| `.ai/plans/018-validation-closeout/03-Findings.md` | create (по ходу, чанками ≤40 строк) | W1-W7 |
| `.ai/plans/018-validation-closeout/02-VerificationReport.md` | create (QA, финал) | W7 |
| `tests/unit/test_status_page.py` | modify (pollution-фикс) | W1 |
| `core/modules/status-page/app.py` | modify — ТОЛЬКО если polluter в app-глобалах | W1 |
| `tests/e2e/test_chaos_resilience.py` | modify (F6 inject/restore, F7 сайзинг, F8 timeout/выборка) | W2-W4 |
| `core/modules/node-metrics/…` (node-exporter конфиг) | modify — только по гипотезе 1 W4 | W4 |
| `node-configs/secrets/<node>.enc.yaml` (tronyx-vps, sops) | modify (−S3_ENDPOINT) | W5 |
| `.ai/plans/018-validation-closeout/logs/` | evidence raw-прогонов (pytest не журналируется) | W2-W4, W7 |

Вне скоупа: watchdog.py, лимит clickhouse 3G (TRAP merge v1.0.1 — не трогать), F-036
load-smoke, NOTE-N6 (сети прошлых эпох — оператор после промоута), NOTE-N2 (stderr-лимит
телеметрии), NOTE-N8 (load-test PATH-подсказка).

## §6 Риски

| Риск | Митигация |
|------|-----------|
| Chaos на prod-ноде (sites живые в окнах — обязательные assert'ы тестов) | Инъекции изолированы (017-протокол: окна короткие, сайты проверяются в окне); pre-condition «13/13 healthy» перед каждой инъекцией |
| Параллельный писатель на dev (NOTE-N3 017) | Re-apply решений владельца перед нодовыми операциями; закрывать чужие worktree-сессии |
| W4 гипотезы не подтвердятся | Diagnostic-first ladder — фикс только по evidence; escalate в 03-Findings с новым фактом |
| D5 не разблокируется к W7 | Промоут остаётся заблокированным гейтом «B–G зелёные» только по W1-W5; решение владельца документируется (PASS_WITH_CONDITIONS повторно) |
| sops-правка матрицы | Только добавление/удаление ключа S3_ENDPOINT; AGE-цепочка и остальные 57 ключей не трогаются; converge φ9 + backup-верификация как proof |
$END_DEVPLAN
