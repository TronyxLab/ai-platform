# GREP_SUMMARY: e2e, bootstrap-pipeline, test-vps, requires-node, devplan-095
# STRUCTURE: ┌test-VPS prep┐ → ◇ env vars (NODE/AGE/SSH) → ◇ run (make test-node) → ◇ troubleshooting → ⎋ coverage limits
# E2E Bootstrap Pipeline Tests (DevPlan 095)

E2E-тесты полного bootstrap-pipeline на **пересоздаваемой test-VPS** (AGENTS.md инвариант 9).
Покрывают: cold-start bootstrap (9 INIT фаз) → node-update (5 UPDATE фаз) → converge →
deploy test-project через DeployOrchestrator → healthcheck → backup snapshot → restore
round-trip → idempotent rebootstrap + 3 failure-сценария (mid-phase kill, ssh timeout,
forced-command receive).

**Маркер:** `requires_node` (ортогонален `e2e` = HTTP-проверки `*.tronyx.ru`).
**Запуск:** `make test-node NODE=<name>` — НЕ входит в `make test MARKER=all` и `make gate`.

---

## Test-VPS Preparation

Test-VPS пересоздаваема (инвариант 9) — cold-start only, backward-compat не нужна.

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

¹ — не обязательна, если ключ доступен через `~/.config/age/keys.txt` (стандартная age CLI
локация; на dev-машине оператора — symlink на `~/.ssh/age-key-personal.txt`, автодетект
с 2026-08-02; README-пример с `AGE_SECRET_KEY_FILE` остаётся валидным для нестандартных путей).

Пример:

```bash
export NODE=test-e2e
export AGE_SECRET_KEY_FILE=~/.config/age/keys/test-e2e.key   # опционально — default: ~/.ssh/age-key-personal.txt
export SSH_KEY=~/.ssh/test-e2e_ed25519
```

## Running Tests

```bash
make test-node NODE=test-e2e                          # все 11 тестов
make test-node NODE=test-e2e -k "bootstrap_pipeline"  # только happy-path (8)
make test-node NODE=test-e2e -k "failure_scenarios"   # только failure (3)
```

Ожидание: **8 PASSED** + **3 PASSED** (~1 час: 1 cold start ~10-30 мин + инкрементальные).

Gate-проверки (без VPS):

```bash
make gate MODE=fast        # зелёный — requires_node тесты исключены фильтром
make test MARKER=static    # requires_node тесты не запускаются
```

## CI Preflight Checklist (B10 T9, DevPlan 116 D3)

Перед merge волны, затрагивающей bootstrap/e2e-контракты — локальный прогон E2E (без CI-джобы — решение пользователя 11-Brief AC11):

```bash
# 1. VPS доступен + AGE-ключ на месте (см. Environment Variables)
make test-node NODE=test-e2e     # 11 requires_node тестов — все PASSED
# 2. Если VPS недоступен в момент прогона — зафиксировать в отчёте QA +
#    повтор после волны (manual-шаг, не блокирует merge)
```

## Troubleshooting

| Симптом | Причина | Решение |
|---------|---------|---------|
| `FAIL: NODE environment variable not set` | NODE не экспортирован | `export NODE=test-e2e` (Rule R4 — это FAIL, не skip) |
| `node.host missing in node-configs/test-e2e/node.yaml` | placeholder host не заменён | Вписать реальный host/IP тестовой VPS |
| SSH timeout / Connection refused | VPS недоступна, ключ не авторизован | Проверить `ssh root@<host>`, `SSH_KEY`, firewall 22 |
| φ4 precondition fail: AGE key required | AGE_SECRET_KEY не передан | `export AGE_SECRET_KEY_FILE=...` и повторить |
| `Kill window missed: φ7 completed before SIGKILL` | φ7 (install_acme) выполнился быстрее окна | Повторить тест — нода уже прогрета, окно стабильнее |
| `state.json` в неконсистентном состоянии после падения | Незавершённая фаза | Сброс: `ssh root@<host> "rm -f /var/lib/platform/.bootstrap/state.json"` (или следующий `make test-node` сделает это сам через `test_vps_fresh`) |
| Docker broken после T14 | Процессный kill (не docker) — docker не трогается | Если всё же docker повреждён: `ssh root@<host> "systemctl restart docker"` |
| Container test-project-web не поднимается | Порт 8080 занят, pull mirror.gcr.io недоступен | Освободить 8080; fixture использует mirror.gcr.io (публичный mirror без rate-limit — docker.io/library/nginx:alpine с datacenter-IP упирается в 429 Docker Hub) |

## Что НЕ покрывает

- **Production-деплои** — только пересоздаваемая test-VPS (инвариант 9)
- **Реальные ACME-сертификаты** — node.yaml без `domain` → φ7 ssl_provision скипается
  (детерминированность); staging-ACME можно включить, задав `domain` + `acme_dns_plugin`
- **`make deploy` (git push → CI)** — CI в E2E-окружении отсутствует; вместо этого
  forced-command receive (ровно то, что CI делает после push)
- **`make backup/restore` (локальный backup-cron/postgres стек)** — backup-артефакт E2E =
  DeployHistory snapshot на VPS (`/opt/projects/<p>/.deploy-snapshots/`)
- **`make healthcheck NODE=`** — modules-healthcheck.sh локальный; здоровье контейнера
  проверяется docker inspect на VPS напрямую
- **GNU `timeout` (macOS)** — ssh timeout в тестах Python-side (subprocess timeout),
  macOS-safe; lib/ssh.sh DRIFT-note не блокирует
