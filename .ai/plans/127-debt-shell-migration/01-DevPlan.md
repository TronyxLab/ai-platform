# 127-debt-shell-migration — 01-DevPlan.md

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Закрыть shell-долги SHELL-RESIDUAL (реестр .ai/debt/001-Strangler-Fig-Closeout.md): мигрировать единственные два живых кандидата — install-tor-proxy.sh (S2, 321 LOC) и node-resolver.sh (S8/P2-1, 215 LOC) — на Python по языковой политике (AGENTS.md, Strangler-Fig); закрыть устаревшие записи S4/S5/S6 (FIXED волнами 117/118) и keep-решения S1/S3/S7.
DESCRIPTION:           3 волны. W1 — install-tor-proxy.sh → Python-модуль (apt/systemd-оркестрация; бизнес-логика tor_setup/tor_transport/privoxy_config уже мигрирована DevPlan 118 E1 / 119 D2/D3) + тонкий фасад <50 LOC. W2 — node-resolver.sh → Python (перенос резолва ноды, фасад <100 LOC, как P2-1). W3 — верификация закрытия: S4/S5/S6 подтвердить FIXED (LOC <50, бизнес-логика в Python), S1/S3/S7 оставить keep (AGENTS.md таблица Shell-исключений), обновить таблицу в root AGENTS.md (убрать мигрированные, оставить актуальные keep).
RATIONALE:             Реестр 001 (2026-07-31) устарел: install-docker.sh 218→23 (118 E2), setup-node.sh 215→110 (117 D1), platform-secrets/install.sh 223→25 (118 E7) — уже фасады. Живые кандидаты: install-tor-proxy.sh 321 (порог фасада 150) и node-resolver.sh 215 (P2-1, дедлайн 2026-09-30). Решение пользователя 2026-08-03: мигрировать S2+S4-S6, issue-cert.sh и stable libs оставить. После реализации — записи удаляются из реестра (DevPlan 131 cleanup).
ACCEPTANCE_CRITERIA:   (1) install-tor-proxy.sh и node-resolver.sh — фасады <150 LOC (языковая политика); бизнес-логика в Python-модулях с unit-тестами (Native Pytest, tmp_path). (2) Поведение фасадов байт-совместимо: те же exit-коды (0/1), те же аргументы, те же env-переменные; e2e bootstrap φ1 (install-tor-proxy) и node-update (node-resolver) проходят на test-VPS без изменений поведения. (3) root AGENTS.md таблица Shell-исключений актуальна: мигрированные удалены, keep-позиции (issue-cert.sh, libs) обновлены по LOC. (4) make check зелёный; unit-тесты покрывают новые Python-модули (IMP:9 LDD). (5) Реестровые записи S2/S8 → FIXED (с датой), S4/S5/S6 → FIXED (верифицировано), S1/S3/S7 → SUPERSEDED/keep-пометка.
IMPLEMENTS:            Решение пользователя 2026-08-03 (опрос долгов: «Мигрировать S2+S4-S6, оставить issue-cert+libs»); P2-1 из .ai/debt/001 (node-resolver 2026-09-30); языковая политика AGENTS.md (Strangler-триггер Tier 2: ≥3 Tier-1 экстракций → плановая декомпозиция).
IMPACTS:               core/internal/bootstrap/install-tor-proxy.sh (S2), core/lib/node-resolver.sh (S8), core/internal/bootstrap/ (новый Python-модуль tor_proxy_setup/install_tor_proxy.py или расширение существующих tor_setup.py/privoxy_config.py), core/internal/shared/node_yaml_cli.py (расширение), tests/unit/ (2 новых тест-файла), root AGENTS.md (таблица Shell-исключений), core/entrypoint-manifest.yaml (при изменении entrypoint-путей), .ai/debt/001 (статусы).
REQUIRES:              Решение пользователя (выполнено 2026-08-03). Доступ к test-VPS для e2e-верификации bootstrap φ1/update (опционально, W2 acceptance). Актуальные LOC (верифицировано 2026-08-03): install-tor-proxy.sh 321, node-resolver.sh 215.
$END_ARTIFACT_CONTRACT

## 0. Draft Code Graph (XML)

```xml
<graph>
  <entity name="core_internal_bootstrap_install_tor_proxy_py" TYPE="MODULE"
    keywords="tor,privoxy,apt,systemd,orchestration,install-cron-healthcheck"
    annotation="Python-оркестрация установки Tor+Privoxy: apt-пакеты, конфиги (tor_setup/privoxy_config — существующие 118 E1/119 D2/D3), systemd enable/start, install_cron_healthcheck. Вызывается из lifecycle phases φ1 (system-bootstrap)."
    CrossLinks="core/internal/bootstrap/tor_setup.py; core/internal/bootstrap/tor_transport.py; core/internal/bootstrap/privoxy_config.py; core/internal/bootstrap/install-tor-proxy.sh"/>
  <entity name="core_lib_node_resolver_sh" TYPE="SHELL"
    keywords="node-resolver,facade,node-yaml-cli"
    annotation="215 LOC → <100: резолв ноды (NODE_YAML, контекст из пути) переносится в Python (расширение node_yaml_cli или новый shared/node_resolver.py); shell остаётся тонким фасадом."
    CrossLinks="core/internal/shared/node_yaml_cli.py; core/internal/shared/node_yaml/"/>
</graph>
```

## 1. Data Flow (шаг за шагом)

```
W1 ── прочитать install-tor-proxy.sh (321) ─► выделить оставшуюся оркестрацию
     (apt install tor/privoxy; генерация конфигов через tor_setup/privoxy_config;
     systemctl enable/start; install_cron_healthcheck) ─► Python-модуль
     core/internal/bootstrap/install_tor_proxy.py (main() -> int, exit 0/1) ─►
     фасад install-tor-proxy.sh → exec python3 -m ... (guard root) ─► unit-тесты
W2 ── прочитать node-resolver.sh (215) ─► перенести резолв в Python
     (shared/node_yaml_cli расширение или shared/node_resolver.py) ─►
     фасад <100 LOC (тонкие обёртки node_yaml --get/--resolve) ─► unit-тесты
W3 ── верификация: wc -l всех фасадов <150; grep python3 -c / heredoc = 0;
     make check; обновить root AGENTS.md таблицу Shell-исключений и реестр 001
     (S2/S8 FIXED, S4/S5/S6 FIXED-верифицировано, S1/S3/S7 keep)
```

## 2. File Manifest

| Файл | Действие | Волна |
|------|----------|-------|
| `core/internal/bootstrap/install_tor_proxy.py` | создать (Python-оркестрация) | W1 |
| `core/internal/bootstrap/install-tor-proxy.sh` | сократить до фасада <50 LOC | W1 |
| `tests/unit/test_install_tor_proxy.py` | создать | W1 |
| `core/internal/shared/node_resolver.py` (или расширение `node_yaml_cli.py`) | создать/расширить | W2 |
| `core/lib/node-resolver.sh` | сократить до <100 LOC | W2 |
| `tests/unit/test_node_resolver.py` (расширить существующий) | создать/обновить | W2 |
| `AGENTS.md` (root) | обновить таблицу Shell-исключений | W3 |
| `.ai/debt/001-Strangler-Fig-Closeout.md` | статусы S2/S8/S4/S5/S6/S1/S3/S7 | W3 |

## 3. Волны

### W1 — install-tor-proxy.sh (S2, 321 LOC)
1. Инвентаризация оставшейся логики: apt-установка tor/privoxy, конфиг-генерация
   (tor_setup.py / privoxy_config.py — уже Python, 118 E1 / 119 D2/D3), systemd
   unit enable/start, install_cron_healthcheck (паттерн install_cron_metrics в
   helpers/system.py:186). Всё, что не в Python-модулях — перенести.
2. `core/internal/bootstrap/install_tor_proxy.py`: `def main() -> int` (контракт
   exit-кодов shared/contracts.py), LDD [IMP:9] на ключевые шаги, идемпотентность
   (повторный запуск = no-op при уже установленном).
3. `install-tor-proxy.sh` → фасад: guard root + `exec python3 -m
   core.internal.bootstrap.install_tor_proxy` (паттерн install-docker.sh).
4. Unit-тесты: apt/systemd-вызовы через DI (передача команд или monkeypatch
   subprocess-хелперов), tmp_path для конфигов, идемпотентность, exit-коды.
5. Проверка: `wc -l install-tor-proxy.sh < 50`, `rg "python3 -c|PYEOF" = 0`,
   make check.

**Acceptance W1:** фасад <50 LOC; Python-модуль покрыт unit-тестами (IMP:9);
поведение контракта сохранено (аргументы/env/exit-коды).

### W2 — node-resolver.sh (S8/P2-1, 215 LOC)
1. Инвентаризация: резолв NODE_NAME/NODE_YAML/context-из-пути; текущие вызовы
   node_yaml --get/--resolve (node_yaml_cli.py, DevPlan 123 T6).
2. Перенос логики резолва в Python: расширение `node_yaml_cli.py` или новый
   `shared/node_resolver.py` (чистые функции, LDD, exit-контракт).
3. `core/lib/node-resolver.sh` → фасад <100 LOC: только проброс аргументов в
   Python-CLI и проброс exit-кода (паттерн lib/ssh.sh фасад).
4. Тесты: резолв по NODE env, по node.yaml, отсутствие → читаемая ошибка;
   регрессия существующих потребителей (lib/node-resolver.sh вызовы: grep
   потребителей и обновить при изменении контракта).
5. Проверка: make check + тесты потребителей (test_lib_node_resolver.py).

**Acceptance W2:** фасад <100 LOC; резолв в Python с unit-тестами; 0 потребителей
сломано (make test-summary TEST_FILE=tests/test_lib_node_resolver.py зелёный).

### W3 — Верификация закрытия + обновление документации
1. S4/S5/S6: подтвердить FIXED — wc -l (install-docker.sh 23, setup-node.sh 110,
   platform-secrets/install.sh 25), бизнес-логика в Python (docker_installer.py,
   helpers/users.py, installer.py) — уже выполнено волнами 117 D1 / 118 E2/E7.
2. S1 (issue-cert.sh 700), S3 (healthcheck.sh 251), S7 (module-interface.sh 26):
   keep-решения подтверждены (acme.sh executor by design U-85; stable libs —
   политика AGENTS.md п.2) — помечаются SUPERSEDED в реестре (keep by design,
   живут в AGENTS.md).
3. root AGENTS.md таблица Shell-исключений: убрать мигрированные (install-tor-proxy
   после W1, node-resolver после W2, install-docker/setup-node/platform-secrets —
   уже не в таблице или обновить LOC), оставить актуальные keep с Rev-условиями.
4. `.ai/debt/001` §SHELL-RESIDUAL: статусы → FIXED/SUPERSEDED (удаление строк —
   волна cleanup 131).
5. `make check` → `make gate MODE=fast` зелёные.

**Acceptance W3:** реестр и AGENTS.md актуальны; все S-записи закрыты
(FIXED/SUPERSEDED); gate зелёный.

## 4. Критерии приёмки волн — сводка

| Волна | Критерий |
|-------|----------|
| W1 | install-tor-proxy фасад <50 LOC, Python-модуль + unit-тесты, контракт сохранён |
| W2 | node-resolver фасад <100 LOC, Python-резолв + тесты, потребители живы |
| W3 | S1-S8 закрыты (FIXED/SUPERSEDED), AGENTS.md актуален, check+gate зелёные |

## 5. Риски и митигации

| Риск | Митигация |
|------|-----------|
| install-tor-proxy: регрессия Tor-конфигурации на проде (tronyx-vps) | Байт-совместимость контракта; конфиг-генерация уже в Python (tor_setup/privoxy_config); e2e φ1 на test-VPS перед прод-деплоем |
| node-resolver: сломать потребителей (converge/healthcheck/project-list) | Инвентаризация потребителей (grep) до рефакторинга; тесты потребителей в acceptance W2 |
| Объём W1 (321 LOC оркестрации) | Тор-бизнес-логика уже мигрирована (118/119) — остаток чистая оркестрация; Small Simple Blocks: перенос по шагам |

$END_DEVPLAN
