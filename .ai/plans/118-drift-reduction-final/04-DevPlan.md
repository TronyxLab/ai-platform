# 04-DevPlan — Бриф C: SoT-унификация констант/путей/таймаутов

<!-- $ARTIFACT_CONTRACT
PURPOSE:          Устранение остаточных дублей констант/путей/таймаутов/политик после волны 117 (SoT-dedup был частичным).
DESCRIPTION:      11 задач: C1 docker_ops таймауты, C2 context_promoter SSH, C3 COMPOSE_PROFILES loader, C4 --timeout 30 + порты,
                  C5 invoke_module_interface, C6 litellm-config путь, C7 deploy_paths активация, C8 audit.jsonl, C9 cert-политика,
                  C10 run_subprocess, C11 healthcheck_poller/scaffold таймауты.
RATIONALE:        Дубли констант — источник дрейфа при будущих правках (правка в одном месте не применяется в другом). Гейты ловят часть,
                  но не всё (scaffold-слой, poller, пути). Унификация = меньше точек правки.
ACCEPTANCE_CRITERIA:
  - AC-C1: docker_ops.py — все таймауты из shared/timeouts; gate _DOMAIN_FILES расширен.
  - AC-C2: context_promoter — SSH_OPTS из shared/ssh_opts (0 ручных -o флагов).
  - AC-C3: COMPOSE_PROFILES — единственный loader (platform-infra.yaml SoT); 2 потребителя делегируют.
  - AC-C4: 0 литералов `--timeout 30` в core/; sync_env_defaults без fallback-портов (обязательное чтение SoT).
  - AC-C5: shared/module_interface.py — единственная bash-обёртка invoke; 2 потребителя делегируют.
  - AC-C6: путь litellm-config.yml — 1 константа в shared; 4+ потребителя импортируют.
  - AC-C7: deploy_paths.py — реальные потребители для /etc/letsencrypt/live, /opt/node-configs, /opt/platform (или удалён как gate-only).
  - AC-C8: converge/infra.py импортирует DEFAULT_LOG_FILE из shared/audit_logger (0 копий).
  - AC-C9: единая функция cert-валидности в shared/ssl_certs; s3_ssl_cache/cert_orchestrator делегируют; приватный _is_cert_valid удалён.
  - AC-C10: один канон run_subprocess (единая сигнатура/семантика); converge/infra делегирует.
  - AC-C11: poller использует HEALTHCHECK_POLL_TIMEOUT/INTERVAL; scaffold ssh_read — SSH_READ_TIMEOUT; gate scope расширен на scaffold.
  - AC-C12: gate MODE=fast, check-manifests, ruff — зелёные.
IMPLEMENTS:       118 01-Brief задачи C1-C11.
IMPACTS:          core/internal/shared/{timeouts,ssh_opts,deploy_paths,ssl_certs,audit_logger,module_interface.py(new)},
                  core/internal/{deploy,scaffold,healthcheck,bootstrap/converge,bootstrap/deploy,llm,phases}, modules/hermes-agent/watchdog, tests/, tests/gates/.
REQUIRES:         118 01-Brief; C5 — вход для B8 (Вариант 1); C7 пересекается с A3 (projects_base).
-->

---

## 1. Технический анализ и решения

### C1 (MED) — docker_ops.py в timeout-гейте

**Факты:** `modules/hermes-agent/watchdog/docker_ops.py` — 6 литералов `timeout=30/10` (строки 123,147,173,186,193,214). Гейт `test_gate_timeout_literals.py` декларирует watchdog-домен, но `_DOMAIN_FILES` включает только `agent_watchdog.py`.

**Решение:** заменить литералы на `DOCKER_STOP_TIMEOUT`/`DOCKER_CMD_TIMEOUT` из shared/timeouts; расширить `_DOMAIN_FILES` на docker_ops.py.

**Тест:** gate после расширения зелёный (литералов нет).

### C2 (LOW) — context_promoter SSH-флаги

**Факты:** `deploy/context_promoter.py:80-87` собирает `-o ConnectTimeout=... BatchMode=yes` вручную для `ssh -T git@github.com`.

**Решение:** импорт `SSH_OPTS` из shared/ssh_opts (документированный allowlist в гейте сохраняется).

**Тест:** gate ssh_opts_sole_path + unit probe.

### C3 (MED) — COMPOSE_PROFILES единый loader

**Факты:** `scaffold/scaffold_helpers.py:60-77` читает platform-env.yaml (generated), `docker_orchestrator.py:168-175` — platform-infra.yaml (SoT). Гейт check-profiles-parity закрепляет platform-infra.yaml.

**Решение:** единый `shared/compose_profiles.load_profiles() -> list[str]` (чтение platform-infra.yaml). Оба потребителя делегируют.

**Тест:** unit — loader читает platform-infra.yaml; parity-гейт остаётся зелёным.

### C4 (MED) — `--timeout 30` ×3 + fallback-порты

**Факты:** `deploy_engine.py:485`, `orchestrator.py:705` — `["--timeout","30"]`; `project_remover.py:293-294` — строка `down --timeout 30`; sync_env_defaults.py — 6 fallback-литералов портов (6379/9000/9001/8080/9090/11434).

**Решение:** литералы → `DOCKER_STOP_TIMEOUT`; sync_env_defaults — обязательное чтение env_defaults SoT (`_get_val_required`) без fallback.

**Тест:** grep-гейт «0 литералов down --timeout» (расширить существующий); sync_env_defaults unit.

### C5 (MED) — invoke_module_interface консолидация

**Факты:** идентичные `bash -c "source paths.sh && source module-interface.sh && invoke_module_interface ..."` в `docker_orchestrator._invoke_healthcheck_full` (~1225-1254) и `bootstrap/deploy/deploy_orchestrator._invoke_module_interface` (~688-709). Различаются таймаутами/возвратами.

**Решение:** `shared/module_interface.py` с `invoke(module, interface, *args, timeout=...) -> (bool, output)`. Оба файла делегируют. **Вход для B8 (Вариант 1 — wire module-hooks).**

**Тест:** unit на invoke (subprocess-bash фасад).

### C6 (MED) — litellm-config.yml путь

**Факты (верифицированы):** минимум 4 копии вывода + 1 шаблон: `context_deployer.py:78` (LITELLM_CONFIG_PATH), `deploy_orchestrator.py:768`, `llm_provision.py:50`, `phases.py:910`, шаблон в `config_renderer.py:58`.

**Решение:** `shared/llm_paths.py` (или в config_renderer) — `LITELLM_CONFIG_PATH(core_dir) -> Path`. Все 5 потребителей делегируют.

**Тест:** unit — единый путь; grep-гейт «1 источник пути».

### C7 (MED) — deploy_paths.py активация

**Факты:** `shared/deploy_paths.py` (90 LOC) — 0 прод-потребителей (только gates). Пути размножены: `/etc/letsencrypt/live` (20 копий), `/opt/node-configs`, `/opt/platform` (в core_deliverer/overlay_deliverer/orchestrator_cli), `/opt/projects` (A3).

**Решение:** наполнить deploy_paths.py реальными константами + резолверами (letsencrypt_live(), node_configs_remote(), projects_base() — для A3) и перевести топ-потребителей (s3_ssl_cache, cert_collector, cert_orchestrator, core_deliverer, overlay_deliverer). Если после переноса остаются 0 прод-потребителей — удалить как gate-only (по контракту shared/ ≥2).

**Тест:** unit-резолверы; grep-гейт на /etc/letsencrypt/live (только deploy_paths + generated).

### C8 (LOW) — converge/infra AUDIT_LOG_FILE

**Факты (верифицированы):** `converge/infra.py:39` — `AUDIT_LOG_FILE = f"{AUDIT_LOG_DIR}/audit.jsonl"` с комментарием «синхронизирован с shared/audit_logger.DEFAULT_LOG_FILE».

**Решение:** заменить на `from core.internal.shared.audit_logger import DEFAULT_LOG_FILE` (убрать второй источник правды).

**Тест:** unit — converge пишет в DEFAULT_LOG_FILE.

### C9 (MED) — единая cert-политика

**Факты (верифицированы):** `shared/ssl_certs.py` уже содержит примитивы (cert_check_expiry, cert_is_le_issuer, cert_is_parseable), но комбинация «валиден» реализована в 2 местах: `s3_ssl_cache._validate_cert` (parseable + LE + domain match + expiry) и `cert_orchestrator._is_cert_valid` (expiry + LE). context_deployer:652 использует приватный `_is_cert_valid` (см. A5).

**Решение:** `shared/ssl_certs.cert_is_valid(cert_path, threshold, expected_domains=None) -> bool` — единая комбинация. Все 3 файла делегируют; приватные методы удаляются.

**Тест:** unit-комбинация; negative: expired/not-LE/domain-mismatch.

### C10 (MED) — двойной run_subprocess

**Факты (верифицированы):** `lifecycle/helpers/subprocess_io.py:44` `run_subprocess(cmd, step_name, *, non_fatal, check_required, timeout=120)` (raise PlatformFatalError) и `converge/infra.py:191` `run_subprocess(cmd, timeout=30, check=False)` (никогда не raise, rc 127/124). Несовместимые сигнатуры/семантики.

**Решение:** единый канон `shared/subprocess_io.py` (сигнатура: cmd, *, timeout, check, non_fatal → возвращает CompletedProcess | бросает). converge/infra делегирует с сохранением своей семантики через параметры (check=False). subprocess_io.py — единственный источник.

**Тест:** unit-обе семантики (raise и no-raise) через один канон.

### C11 (MED) — healthcheck_poller + scaffold таймауты

**Факты (верифицированы):** `healthcheck_poller.py:37-38` — `DEFAULT_POLL_TIMEOUT=30`, `DEFAULT_POLL_INTERVAL=10` vs канон `timeouts.py:46,49` — `HEALTHCHECK_POLL_TIMEOUT=60`, `HEALTHCHECK_POLL_INTERVAL=3` (poller уже импортирует MAX_RETRIES оттуда). scaffold-слой: `project_remover.py:286` ssh_read timeout=10, `:297` compose up 120; `project_lister.py:332` ssh_read 10 — против `SSH_READ_TIMEOUT=60`/`COMPOSE_UP_TIMEOUT=180`.

**Решение:** poller → импорт HEALTHCHECK_POLL_TIMEOUT/INTERVAL (выровнять с каноном; проверить влияние на тесты — изменение окна поллинга 200с→60с). scaffold → импорт из timeouts. Расширить scope `test_gate_timeout_literals` на scaffold/.

**Тест:** gate scope; unit-поллер с канон-таймаутами.

---

## 2. Порядок выполнения

```
C2 → C4 → C8 → C11     ← точечные константные замены (независимы)
   │
C1 → C3 → C6 → C7      ← SoT-модули + перевод потребителей
   │
C9 (cert-политика)     ← вход для A5
   │
C5 (module_interface)  ← вход для B8
   │
C10 (run_subprocess)   ← самый рискованный (2 семантики), отдельно
```

## 3. Оценки

| Метрика | Значение |
|---------|----------|
| Задач | 11 |
| LOC | −80…−120 (нетто; часть — перевод потребителей) |
| Новых модулей | 3 (module_interface.py, compose_profiles.py или в platform_config, llm_paths.py) |
| Зависимости | A3 ← C7, A5 ← C9, B8 ← C5 |

## $END

Открытые вопросы:
1. **C11** — изменение poller-таймаутов (30/10→60/3) меняет поведение деплоя; проверить влияние на существующие тесты/ожидания перед фиксацией.
2. **C7** — объём перевода потребителей на deploy_paths может быть большим; если >5 файлов — ограничить топ-3 и задокументировать остаток (DEBT).
