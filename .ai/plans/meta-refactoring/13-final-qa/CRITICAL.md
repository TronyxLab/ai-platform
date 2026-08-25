<!-- GREP_SUMMARY: final-qa critical launch-blockers WIP stale-pin L1-bypass llm-provisioner age-argv DR-inert -->
<!-- STRUCTURE: ▶ C1 dirty-tree → ⚡ C2-C6 fix-forward блокеры → ⎋ условия снятия -->

# CRITICAL — блокеры запуска (final QA, независимо)

Дата: 2026-08-25 · Аудит диапазона `38699a9..HEAD` (волны 0–3, план 11) · Метод: 7 параллельных
субагент-аудитов + собственная верификация deploy-core/monitoring срезов. Канал субагентов упал по
инфра-причине (баланс/сертификат) на 3 срезах — они закрыты лидом вручную (см. FINAL-VERDICT §Coverage).

Каждая находка верифицирована чтением актуального кода (не со слов предыдущих агентов).

---

## C1 · Рабочее дерево НЕ чистое: незакоммиченный WIP вне аудированного диапазона

**SEV=CRITICAL (release-process)** · `git status` → 29 файлов Modified, +882/−165.

Затронуты: `core/platform-infra.yaml`, `core/internal/shared/platform_ports.py` (+placement),
`core/internal/bootstrap/deploy/deploy_orchestrator.py`, `lifecycle/phases/{docker,system}.py`,
`firewall.py`, `modules/nginx/*`, `modules/service-exporters/*`, `log-collector/module.yaml`,
новые тесты (`test_shared_placement.py`, `test_gen_env_platform.py`, `test_firewall_peer.py` +116…122).

Почему блокер:
- Ни один гейт волн (make check / agent-check / check-manifests) на ЭТО состояние не прогонялся;
  леджер (12-StatusReport) фиксирует чистоту на коммитном дереве, а не на текущем.
- `check-manifests` на рабочем дереве **RED** (platform-env.yaml расходится: порт экспортера
  9113→9121 и др.) — acceptance-критерий AC-7.2 плана сейчас невыполним.
- WIP меняет те же файлы, что аудировались (deploy_orchestrator, phases) — выводы этого отчёта
  могут быть неприменимы к WIP-слою.

Снятие: закоммитить WIP отдельной волной с полным гейт-циклом ИЛИ откатить; перегенерировать
манифесты; добиться зелёных `check-manifests` + `make check`.

## C2 · Шаблонные проекты получают деплой-канал БЕЗ всего харденинга (stale SHA-pin)

**SEV=HIGH** · `templates/template-{backend,frontend}/.github/workflows/deploy.yml:81`

Пин reusable workflow = snapshot `4425ce0` — коммит от **2026-08-18**, т.е. ДО всех волн
(комментарий «main snapshot 2026-08-24» ложен). В этой версии отсутствуют: top-level
`permissions`, `concurrency`, SHA-pin'ы actions (`actions/checkout@v7` — тег!), gitleaks
sha256-verify, hoisted SSH-флаги. Гейт `test_gate_workflow_sha_pins` проверяет форму (`@<40hex> # vX`),
но не свежесть — stale-ping проходит.

Каждый `make new-project` наследует канал без REF-0012/0011-харденинга — цель «канал рождается
запиненным» (TRAP[DECISION] DevPlan §5/В0) не достигнута. Fix-forward: перепинить на HEAD
deploy-project.yml, обновить комментарий; гейт дополнить сверкой «пин ≥ даты последнего изменения файла».

## C3 · Обход L1-deny-set через top-level volumes (docker.sock достижим)

**SEV=HIGH (security)** · `core/internal/deploy/verify_contracts.py:817`

`_check_dangerous_volumes` сканирует только `services[].volumes`. Верхнеуровневая секция compose
не читается нигде:

```yaml
volumes:
  sock:
    driver: local
    driver_opts: {type: none, o: bind, device: /var/run/docker.sock}
services:
  app:
    volumes: ["sock:/var/run/docker.sock"]   # проходит все L1-проверки сегодня
```

Источник сервиса = named volume (regex `[A-Za-z0-9_.-]*` — pass), а bind прячется в driver_opts.
Вектор C1 (docker.sock → root ноды), который REF-0006 объявляет закрытым безусловно, остаётся
рабочим. Дополнительно deny-set неполон против собственного TRAP-обещания: нет `ipc: host`,
`security_opt: [seccomp/apparmor/systempaths=unconfined]`, `volumes_from:`, `uts: host`.

Fix-forward: читать top-level `volumes` (deny `driver_opts.o=bind`/device-сокеты), расширить
host-mode keys, R5-негативы на точные входы выше.

## C4 · REF-0104 реализован частично: provisioner падает на transient, fetch-once отсутствует

**SEV=HIGH** · `core/internal/llm/key_provisioner.py:717,748,680`; `admin_client.py:63`

1. `except (OSError, ConnectionError, TimeoutError)` физически не может поймать
   `LiteLLMTransportError(Exception)` — заявленная семантика «transport-failure → WARN + failed++,
   фаза продолжается» недостижима: любой сетевой сбой LiteLLM абортит ВСЮ фазу provision-llm
   посреди цикла через generic-handler main(). Это регрессия надёжности node-update против цели среза.
2. «list_keys() once за прогон» (PERF-081) не реализован: `get_key_by_metadata` внутри цикла →
   N полных пагинаций за прогон.
3. Спящая мина: ветка «update failed → falling through to generate» создаст ВТОРОЙ ключ с тем же
   metadata при живом первом (сейчас ветка мертва из-за п.1; первое же «очевидное» исправление её
   активирует). Нужен re-lookup после неудачного update либо failed++ без generate.

Fix-forward: добавить `LiteLLMTransportError` в кортежи + re-lookup вместо fall-through +
fetch-once; довести контракты/STRUCTURE до соответствия или наоборот.

## C5 · AGE-мастер-ключ в локальном argv (fallback-deliver) — противоречит инварианту REF-0007

**SEV=HIGH** · `core/entrypoints/core-deliver.sh:103-107`

```bash
python3 -m core.internal.bootstrap.core_deliverer fallback-deliver \
    ... --age-secret-key "${detected_age_key}"
```

stdin-транспорт закрывает только remote-ногу; `/proc/<pid>/cmdline` читаем любым локальным
аккаунтом всё время процесса (deliver+provision+node-update, минуты). Инвариант модуля «ключи вне
argv» выполняется только для remote-стороны. Fix: читать ключ внутри Python из env/file
(AGE_SECRET_KEY_FILE уже экспортируется фасадом), argv-флаг удалить.

## C6 · Off-site DR-цепочка неактивна по умолчанию: AGE_RECIPIENT не имеет канона провижининга

**SEV=HIGH (operational readiness)** · `core/modules/backup-cron/docker-compose.base.yml:90`,
отсутствие в `core/secret-definitions.yaml` / `platform-infra.yaml`

`AGE_RECIPIENT` не входит в матрицу секретов (нарушение grep-гейта «новый секрет обязан попасть в
матрицу») и некому доставляться на ноду. На реальной ноде переменная пуста → nightly upload
ПОСТОЯННО SKIP (fail-closed работает корректно, plaintext не уходит — хорошо), но off-site копий
нет вообще: RPO 24ч из core/AGENTS.md фиктивен до ручного шага оператора, который нигде не
запротоколирован как precondition запуска. Сигналы есть (CRITICAL-лог, BackupUploadFailure), т.е.
слепоты нет — но готовность DR зависит от незадокументированной операции.

Снятие: внести AGE_RECIPIENT в secret-definitions + канал доставки + шаг в release-checklist
(рядом с drill'ами В4).

---

## Условия снятия блокера

| # | Действие | Закрывает |
|---|----------|-----------|
| 1 | WIP закоммитить/откатить + полный гейт-цикл + зелёный check-manifests | C1 |
| 2 | Перепин шаблонных workflows на HEAD + freshness-проверка в гейте | C2 |
| 3 | verify_contracts: top-level volumes + ipc/security_opt/volumes_from/uts + R5-негативы | C3 |
| 4 | key_provisioner: TransportError в кортежи, re-lookup, fetch-once, тесты (см. TEST-GAPS G2) | C4 |
| 5 | core-deliver.sh: убрать --age-secret-key из argv | C5 |
| 6 | AGE_RECIPIENT: матрица + доставка + release-checklist шаг | C6 |
