# GREP_SUMMARY: e2e, bootstrap-pipeline, test-vps, requires-node, chaos-marker, node-preflight, NODE_PREBOOTSTRAPPED, devplan-095, devplan-136
# STRUCTURE: ┌test-VPS prep (SC2 operator recreate)┐ → ◇ env vars (NODE/AGE/SSH/NODE_PREBOOTSTRAPPED) → ◇ pre-flight «голоты» → ◇ run (make test-node, chaos отдельно) → ◇ troubleshooting → ⎋ coverage limits (B6/B7)
# E2E Bootstrap Pipeline Tests (DevPlan 095, канонизация харнесса — DevPlan 136 W6)

E2E-тесты полного bootstrap-pipeline на **пересоздаваемой test-VPS** (AGENTS.md инвариант 9).
Покрывают: cold-start bootstrap (9 INIT фаз) → node-update (5 UPDATE фаз) → converge →
deploy test-project через DeployOrchestrator → healthcheck → backup snapshot → restore
round-trip → idempotent rebootstrap + 2 failure-сценария (ssh timeout, forced-command
receive).

**Маркеры (DevPlan 136 W6 T6.1, B4):**
- `requires_node` — основной suite: 10 тестов (8 bootstrap pipeline + 2 failure scenarios).
  Ортогонален `e2e` (= HTTP-проверки `*.tronyx.ru`).
- `chaos` — resilience drills (DevPlan 013: 9 fast + 3 night сценария). **ИСКЛЮЧЁН из `make test-node`**
  (маркер-фикс B4): drills требуют ЗАБУТСТРАПЛЕННУЮ ноду (tronyx-vps), а не голую —
  на голой ноде они падают (нет docker/контейнеров). Запуск отдельно, см. «Running Tests».
- `night` — long-running drills (reboot/outbound-partition/docker-daemon-restart, DevPlan 013);
  подмножество chaos, отдельное операторское окно (~25 мин).

**Pre-flight «голоты» (DevPlan 136 W6 T6.2, B3+):** до suite conftest проверяет по SSH,
что нода достижима и «голая» (нет docker и /opt/platform). Если нода уже забутстраплена,
нужен `NODE_PREBOOTSTRAPPED=1` (явное подтверждение оператора SC2 — штатный сценарий
W6.5→W6.6); без env и при docker/platform present — suite FAIL с понятным сообщением.

**Запуск:** `make test-node NODE=<name>` — НЕ входит в `make check` и `make gate`.

---

## Test-VPS Preparation

Test-VPS пересоздаваема (инвариант 9) — cold-start only, backward-compat не нужна.
**Пересоздание — ОПЕРАТОРСКАЯ процедура SC2** (fresh Ubuntu → docker absent), НЕ автосброс
и НЕ функция тестов: `test_vps_fresh` сбрасывает только `state.json` (cold-start reset для
suite), ноду он не пересоздаёт. «Голоту» гарантирует pre-flight (см. выше).

```bash
# 1. Provision test-VPS (Ubuntu 22.04+/Debian 12, root SSH access, public IP)
ssh root@<test-vps-host> "uname -a && docker --version || echo 'docker not yet installed (bootstrap installs it)'"

# 2. Configure the node (repo-local config, Path 1 of NodeYaml.resolve)
#    node-configs/test-e2e/node.yaml:
#      node.host      → REAL test-VPS host/IP (placeholder 127.0.0.1 fails SSH — R4: FAIL)
#      node.owner_key → REAL operator SSH public key (authorized on the VPS)
#      domain         → НЕ задавать: ssl_provision (φ7) скипается детерминированно
#      modules        → [] (голая нода — только core + converge R-units)
#      projects       → [test-project] (тип backend; репозиторий placeholder)

# 3. Install platform core on the VPS (SCP, not git — Triple Delivery Model)
make bootstrap-node NODE=test-e2e          # первый холодный bootstrap (~10-30 мин)
```

> ⚠️ Первый ручной bootstrap нужен, чтобы SCP доставил core в `/opt/platform/`
> и нода стала управляемой. Дальше тесты делают это сами.

## Environment Variables

| Переменная | Обязательна | Описание |
|------------|-------------|----------|
| `NODE` | ✅ | Имя ноды (например `test-e2e`). Отсутствие → **FAIL** (Rule R4), не skip |
| `AGE_SECRET_KEY` / `AGE_SECRET_KEY_FILE` | ❌¹ | AGE-ключ для φ4 secrets_provision. Цепочка (node_detect): env → SOPS_AGE_KEY → файл из env → default-файл `~/.config/age/keys.txt` |
| `SSH_KEY` | ❌ | Путь к приватному SSH-ключу для доступа к VPS (`-i`), по умолчанию `~/.ssh` |
| `SSH_USER` | ❌ | SSH-пользователь, по умолчанию `root` |
| `NODE_PREBOOTSTRAPPED` | ❌² | Операторское подтверждение SC2 (DevPlan 136 W6 T6.2): `1` = нода пересоздана по SC2 (bootstrap W6.5 уже выполнен — docker present ожидаем). Без него docker/platform present на ноде → pre-flight **FAIL** («нода не пересоздана (инвариант 9) или забыт NODE_PREBOOTSTRAPPED=1»). Chaos-сессии (`-m chaos`) goloty-проверку пропускают |

¹ — не обязательна, если ключ доступен через `~/.config/age/keys.txt` (стандартная age CLI
локация; на dev-машине оператора — symlink на `~/.ssh/age-key-personal.txt`, автодетект
с 2026-08-02; README-пример с `AGE_SECRET_KEY_FILE` остаётся валидным для нестандартных путей).
² — см. «Pre-flight «голоты»» выше: для штатного цикла W6.5→W6.6 (пересоздание → bootstrap →
test-node) оператор ставит `export NODE_PREBOOTSTRAPPED=1` перед `make test-node`.

Пример:

```bash
export NODE=test-e2e
export AGE_SECRET_KEY_FILE=~/.config/age/keys/test-e2e.key   # опционально — default: ~/.ssh/age-key-personal.txt
export SSH_KEY=~/.ssh/test-e2e_ed25519
# Только для штатного цикла W6.5→W6.6 (нода уже забутстраплена после пересоздания SC2):
# export NODE_PREBOOTSTRAPPED=1
```

## Running Tests

```bash
make test-node NODE=test-e2e                          # основной suite: 10 requires_node (без chaos)
make test-node NODE=test-e2e -k "bootstrap_pipeline"  # только happy-path (8)
make test-node NODE=test-e2e -k "failure_scenarios"   # только failure (2)
```

Resilience drills (DevPlan 013, два тира) — **отдельный прогон** (маркер-фикс B4):

```bash
# На ЗАБУТСТРАПЛЕННОЙ ноде (tronyx-vps или test-VPS после bootstrap) — goloty pre-flight для chaos не применяется
# fast-тир: 9 drills, ≤30 мин wall-clock (каждый ≤6 мин)
PYTEST_NO_ESCALATION=1 python3 -m pytest tests/e2e/test_chaos_resilience.py -m "chaos and not night" -v --tb=short -rs
# night-тир: 3 drills (reboot / outbound-partition / docker-daemon-restart), отдельное окно ~25 мин
PYTEST_NO_ESCALATION=1 python3 -m pytest tests/e2e/test_chaos_resilience.py -m night -v --tb=short -rs
# Опционально -k <drill> для одного сценария (например -k "crash_postgres")
```

Ожидание основного suite: **8 PASSED** + **2 PASSED** (~1 час: 1 cold start ~10-30 мин +
инкрементальные). Resilience drills (DevPlan 013): fast-тир — 9 PASSED ≤30 мин суммарно;
night-тир — 3 PASSED в отдельном окне ~25 мин (reboot ~4-8 мин из них).

Gate-проверки (без VPS):

```bash
make gate MODE=fast        # зелёный — requires_node тесты исключены фильтром
make check MARKER=static_audit  # requires_node тесты не запускаются
```

## CI Preflight Checklist (B10 T9, DevPlan 116 D3)

Перед merge волны, затрагивающей bootstrap/e2e-контракты — локальный прогон E2E (без CI-джобы — решение пользователя 11-Brief AC11):

```bash
# 1. VPS доступен + AGE-ключ на месте (см. Environment Variables)
#    Для штатного цикла W6.5→W6.6 (после bootstrap-node на пересозданной ноде):
#    export NODE_PREBOOTSTRAPPED=1
make test-node NODE=test-e2e     # 10 requires_node тестов (без chaos) — все PASSED
# 2. Если VPS недоступен в момент прогона — зафиксировать в отчёте QA +
#    повтор после волны (manual-шаг, не блокирует merge)
```

## Troubleshooting

| Симптом | Причина | Решение |
|---------|---------|---------|
| `FAIL: NODE environment variable not set` | NODE не экспортирован | `export NODE=test-e2e` (Rule R4 — это FAIL, не skip) |
| `node.host missing in node-configs/test-e2e/node.yaml` | placeholder host не заменён | Вписать реальный host/IP тестовой VPS |
| SSH timeout / Connection refused | VPS недоступна, ключ не авторизован | Проверить `ssh root@<host>`, `SSH_KEY`, firewall 22 |
| Pre-flight FAIL: ... уже содержит docker/platform | Нода не пересоздана по SC2 (инвариант 9) или забыт env | Пересоздать VPS (SC2, оператор) → bootstrap W6.5 → `export NODE_PREBOOTSTRAPPED=1`; для chaos-прогона env не нужен (goloty skip) |
| φ4 precondition fail: AGE key required | AGE_SECRET_KEY не передан | `export AGE_SECRET_KEY_FILE=...` и повторить |
| `Kill window missed: φ7 completed before SIGKILL` | φ7 (install_acme) выполнился быстрее окна | Повторить тест — нода уже прогрета, окно стабильнее |
| `state.json` в неконсистентном состоянии после падения | Незавершённая фаза | Сброс: `ssh root@<host> "rm -f /var/lib/platform/.bootstrap/state.json"` (или следующий `make test-node` сделает это сам через `test_vps_fresh`) |
| Docker broken после T14 | Процессный kill (не docker) — docker не трогается | Если всё же docker повреждён: `ssh root@<host> "systemctl restart docker"` |
| Container test-project-web не поднимается | pull mirror.gcr.io недоступен (или L1-нарушение compose — DevPlan 176 A.2: receive исполняет pre-deploy L1-гейт) | Fixture использует mirror.gcr.io (публичный mirror без rate-limit — docker.io/library/nginx:alpine с datacenter-IP упирается в 429 Docker Hub); фикстура БЕЗ host-порта (L1 ports-published запрещает) — HTTP 200 проверяется docker exec внутри контейнера |

## Что НЕ покрывает

- **Production-деплои** — только пересоздаваемая test-VPS (инвариант 9)
- **B6 — CI-канал деплоя вне харнесса**: реальный `make deploy` (git push → CI →
  forced-command) в E2E-окружении отсутствует — CI не запускается против test-VPS.
  Харнесс эмулирует ровно то, что CI делает после push: прямой forced-command receive
  (`orchestrator_cli receive`). Сам канал git push → CI → deploy-project верифицируется
  отдельно (production-цикл, вне харнесса)
- **B7 — реальные ACME-сертификаты вне харнесса**: node.yaml без `domain` → φ7
  ssl_provision скипается (детерминированность); реальные Let's Encrypt-сертификаты
  (rate-limit, DNS-01/HTTP-01 провайдеры) не выпускаются в харнессе. staging-ACME можно
  включить, задав `domain` + `acme_dns_plugin`. Реальные LE-сертификаты верифицирует
  `make e2e-verify` на production-ноде (tronyx-vps)
- **`make backup/restore` (локальный backup-cron/postgres стек)** — backup-артефакт E2E =
  DeployHistory snapshot на VPS (`/opt/projects/<p>/.deploy-snapshots/`)
- **`make healthcheck NODE=`** — modules-healthcheck.sh локальный; здоровье контейнера
  проверяется docker inspect на VPS напрямую
- **Resilience drills (DevPlan 013)** — отдельный прогон (fast `-m "chaos and not night"` /
  night `-m night`), требует забутстрапленную ноду и операторское окно; артефакты пишутся
  в `/tmp/chaos-<date>` (не в .ai/plans/)
- **GNU `timeout` (macOS)** — ssh timeout в тестах Python-side (subprocess timeout),
  macOS-safe; lib/ssh.sh DRIFT-note не блокирует
