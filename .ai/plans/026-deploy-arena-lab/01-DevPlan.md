<!-- GREP_SUMMARY: deploy-arena lab multinode scenario dsl providers multipass hcloud placement validate-topology on-demand teardown S2 data-apart platform-node-configs four-path-resolve headless dns-flag ssh-plumbing -->
# GREP_SUMMARY: deploy-arena, lab, multinode, scenario-dsl, providers, multipass, hcloud, placement, validate-topology, on-demand, teardown, data-apart, platform-node-configs, headless, dns-flag, ssh-plumbing
# STRUCTURE: ┌scenario.yaml SoT┐ → ⚡ render → ┌platform/node-configs flat (Path 2 glob)┐ → ⊕ make-verbs unchanged → ⚡ providers(multipass|hcloud)+keys/cloud-init → ⎋ journal

<!-- $START_DEVPLAN -->

## $ARTIFACT_CONTRACT

- **PURPOSE:** Спроектировать standalone-тулинг `deploy-arena` — стоящую, повторяемую, одноразовую лабораторию для прогона multi-node сценариев развёртывания платформы по мере надобности (up → verify → down). DevPlan 010 closeout становится сценарием №1 библиотеки.
- **DESCRIPTION:** Гибридный субстрат (локальные VM Multipass — 0 ₽, мгновенный teardown; on-demand cloud Hetzner через Terraform — €/день для network-truth и resource-honesty). Сценарий = YAML-файл (SoT); контекст платформы = `arena-<сценарий>`; глаголы платформы переиспользуются **без правок ai-platform**: GENERATED node-configs рендерятся в `~/projects/deploy-arena/platform/node-configs/` и находятся каноническим 4-path резолвом платформы (Path 2: `~/projects/*/platform/node-configs/`), поверх — env-оверрайды `NODE_CONFIGS_DIR`/`PLATFORM_ROOT` для NODE_CONFIGS_DIR-потребителей. Локальная фаза — headless (exposed+sweep — через опциональный `dns:`-флаг сценария или в M2). SSH-сантехника (keypair'ы, cloud-init root-SSH, managed `~/.ssh/config`) — явная задача.
- **RATIONALE:** Текущий план 1788237709751 — разовая 4–6-дневная кампания (3 постоянных VPS, один placement.yaml правкой между фазами, ручной runbook) — не даёт «запускать по мере надобности и удалять». Разделение топологии/субстрата/оркестрации + библиотека сценариев превращает валидацию в повторяемую capability.
- **ACCEPTANCE_CRITERIA:** (1) `arena up/verify/down SCENARIO=data-apart` — полный headless-цикл на локальных VM: lab-api (backend+БД на data-ноде) и lab-web (frontend, expose:false) задеплоены и healthy; check-security PASS; healthcheck PASS; e2e-verify локально зелёный (0 exposed endpoints — F-034 семантика). (1b, отложено в dns-режим/M2) Сценарий с `dns:`-секцией: полный e2e-verify sweep по exposed-доменам (LE wildcard DNS-01 + managed /etc/hosts). (2) Повторный `up` идемпотентен. (3) `down` уничтожает субстрат целиком (multipass `delete --purge`). (4) Рендер в `platform/node-configs/` проходит платформенный `validate_topology` (полнота записей, RFC1918-хосты, contexts[0].name==placement.context). (5) Cloud-бэкенд (M2) воспроизводит network-truth: ufw peer-rules на Tailscale 100.64/10, порт-скан не-пира, DNS-steering.
- **IMPLEMENTS:** Заменяет одноразовый план валидации мульти-ноды на standing lab-capability; closeout DevPlan 010 = сценарии библиотеки + VerificationReport.
- **IMPACTS:** Не трогает ai-platform (код/схемы/контракты/глоссарий). Читает платформу через существующие глаголы и публичные CLI. Создаёт новый git-репозиторий `~/projects/deploy-arena/` (исключение корневого контракта ~/projects/ — класс инструментов, как ai-instructions/ai-project).
- **REQUIRES:** Python 3.14+, Multipass (macOS), Terraform/OpenTofu (только cloud-бэкенд), ai-platform checkout (`PLATFORM_ROOT`), lab AGE-ключ (отдельный от production), scenario-SSH-keypair'ы (root + ci_deploy, генерируются ареною).

**Ревизия 2 (2026-09-01)** — после код-верификации и супер-позиций с владельцем: (R1) layout
GENERATED → `platform/node-configs/` (канонический Path 2 glob; NODE_CONFIGS_DIR-оверрайд
не участвует в резолве node.yaml — D3 исправлен); (R2) DNS/TLS — гибрид: headless по умолчанию,
опциональная `dns:`-секция (SP2); (R3) lab-проекты — фикстуры в репо арены + доставка через
`orchestrator_cli deliver --project-dir --key-file` (SP3); (R4) модульный сет — расширенный минус
приватные ghcr-образы (SP4); (R5) + SSH-сантехника как задача T4 (владелец); (R6) + TRAP[DECISION]
на каждое отклонённое альтернативное решение.

---

## 1. Требования и критерии успеха

Запрос владельца (дословно): «запускать по мере надобности тестовые сервера и прогонять на них разные сценарии развёртывания на нескольких нодах. Потом — удалять/останавливать их». Зафиксированные решения владельца (3 раунда опроса): **capability + 010 как сценарий №1 · гибридный субстрат · standalone-тулинг вне репозитория · старт с S2 · тема `arena` · семантические имена сценариев · бинарь → Makefile** *(ревизия 2)*: **layout `platform/node-configs/` · DNS-гибрид (headless сначала, `dns:`-флагом полный sweep) · фикстуры проектов в репо арены · расширенный сет минус приватные образы · SSH-сантехника явной задачей**.

Критерии успеха:
1. **On-demand:** `arena up` поднимает N нод заданной топологии; `arena down` стирает их подчистую. Между прогонами — ноль затрат.
2. **Повторяемость:** сценарий = один YAML; прогон воспроизводим с нуля; повторный `up` идемпотентен (bootstrap no-op).
3. **Покрытие форм:** все 4 формы placement ({node}/all-nodes/{nodes[]}/off) + single-node no-op валидируются на живом контуре.
4. **Честность сетевого слоя:** локально — логика placement/drift/env-хостов + ufw peer-rules на multipass (RFC1918); на cloud-фазе — Tailscale 100.64/10, порт-скан не-пира, DNS-steering, полный e2e-verify sweep.
5. **Изоляция:** production-контексты физически вне lab-дерева; lab AGE-ключ, lab SSH-keypair'ы и prod `~/.ssh/ci_deploy_key` не пересекаются.

---

## 2. Архитектура и Draft Code Graph

Три раздельных слоя, соединённых CLI `arena`:

```
┌ scenario.yaml (SoT) ─────────────────────────────────────┐
│  context(авто), nodes(+host/size/modules), modules,      │
│  projects, resources, keys, dns(опц.), plan_ref          │
└──────────────────────┬───────────────────────────────────┘
                       │ render.py (генератор + off-автодополнение)
                       ▼
┌ platform/node-configs/ (GENERATED, flat, gitignored) ────┐
│  <context>/placement.yaml + <node>/node.yaml ×N          │
└──────────────────────┬───────────────────────────────────┘
                       │ 4-path резолв Path 2: ~/projects/*/platform/node-configs/
                       │ (+ NODE_CONFIGS_DIR env для NODE_CONFIGS_DIR-потребителей)
                       ▼
┌ существующие глаголы ai-platform (read-only) ─────────────┐
│  bootstrap-node → deploy-context → orchestrator_cli     │
│  deliver → e2e-verify/check-security/converge           │
└──────────────────────┬───────────────────────────────────┘
        ┌──────────────┴───────────────┐
        ▼                              ▼
  providers/multipass.py           providers/hcloud/ (Terraform)
  cloud-init(root SSH, keypairs)   pay-per-hour, Tailscale/WireGuard
  RFC1918 из коробки, 0 ₽
```

### Draft Code Graph (репозиторий `~/projects/deploy-arena/`)

| Модуль | Ответственность |
|---|---|
| `arena` | Thin bash facade (argpassthrough → `make`) |
| `Makefile` | Рецепты `up/down/verify/status/list` → `python3 -m src.arena.*` |
| `src/arena/cli.py` | argparse-подкоманды, парсинг `SCENARIO=`, проброс env |
| `src/arena/schema.py` | Валидация scenario.yaml (JSON-Schema draft-07): nodes+resources, modules, projects, keys, `dns:` (optional), plan_ref |
| `src/arena/keys.py` | SSH-сантехника: генерация per-scenario keypair'ов (root_ssh, ci_deploy), managed-блок `~/.ssh/config` (Host <VM-IP> → IdentityFile, маркер-паттерн), private keys в `secrets/` 0600 |
| `src/arena/render.py` | scenario.yaml → `platform/node-configs/` (flat), off-автодополнение из `core/modules/` (ls), RFC1918-проверка, vpn_enforced: true, guard приватных образов, публичные ключи из keys.py в node.yaml (owner_key/ci_deploy_key) |
| `src/arena/providers/base.py` | Provider Protocol: `provision(nodes) → hosts`, `destroy()` |
| `src/arena/providers/multipass.py` | Backend A: `multipass launch --cloud-init` (root authorized_keys из keys.py, ресурсы из scenario), `delete --purge`; hosts → RFC1918 |
| `src/arena/providers/hcloud.py` | Backend B: tfvars → `terraform apply/destroy`, Tailscale/WireGuard overlay (M2) |
| `src/arena/orchestrate.py` | `make -C <ai-platform> bootstrap-node NODE=...` (unchanged; резолв через Path 2); `NODE_CONFIGS_DIR` env для потребителей; пост-бутстрап доставка фикстур: `python3 -m core.internal.deploy.orchestrator_cli deliver --project X --project-dir <arena>/projects/<ctx>/<x> --host <VM-IP> --key-file <arena>/secrets/ci_deploy/ci_deploy_key` |
| `src/arena/verify.py` | headless: `check-security` + `healthcheck` + e2e-verify (0 endpoints — тривиально зелёный) + health-probe проектов; dns-режим (M2): `arena hosts apply` → полный sweep |
| `src/arena/hosts.py` | (dns-режим, M2) managed-блок /etc/hosts (sudo, паттерн dev_hosts.py): `<project>.<dns.domain>` → IP main-VM; `clear` на teardown |
| `src/arena/teardown.py` | `down`/`down-volumes` (по SSH) + provider.destroy + ssh-config block cleanup + (dns) hosts clear + archive node-configs |
| `scenarios/*.yaml` | Библиотека сценариев (SoT) |
| `projects/<ctx>/{lab-api,lab-web}/` | Фикстуры lab-проектов (docker-compose.yml + ai-platform.yaml) |
| `providers/hcloud/{main.tf,variables.tf}` | Terraform-модуль cloud-бэкенда (M2) |
| `secrets/`, `logs/` | lab AGE-ключ + keypair'ы + per-node secrets.env; журнал прогонов (gitignored) |

---

## 3. Ключевые решения (архитектура)

### D1 — Плоская раскладка node-configs внутри `platform/` (Path 2 канон)

Платформа резолвит placement **относительно node.yaml**: `placement_node_relative_path` = `Path(node.yaml).parent.parent / <context> / "placement.yaml"` — раскладка плоская: `<context>/placement.yaml` и `<node>/node.yaml` — siblings. Ревизия 2: GENERATED-дерево живёт в `~/projects/deploy-arena/platform/node-configs/` — node.yaml попадает в **канонический Path 2** 4-path резолва (`~/projects/*/platform/node-configs/<node>/node.yaml`, resolve.py:114-120). Имена нод глобально уникальны (префикс `<сценарий>-`).

```
~/projects/deploy-arena/
├── scenario'и, src/, Makefile, arena
├── platform/                        # GENERATED (gitignored) — НЕ overlay-репо
│   └── node-configs/
│       ├── arena-data-apart/placement.yaml
│       ├── data-apart-data/node.yaml
│       └── data-apart-main/node.yaml
├── projects/arena-data-apart/{lab-api,lab-web}/   # фикстуры
└── secrets/                         # age + keypair'ы (gitignored)
```

# 🧐 TRAP[DECISION] · 2026-09-01 · — · GENERATED node-configs в platform/ подпапке (Path 2 канон) · Rejected: (a) корневой node-configs/ — legacy Path 3 glob с IMP:7 WARN, slated for removal после миграции asi-group (TRAP в resolve.py) — арена сломалась бы по расписанию чужого плана; (b) прямой вызов entrypoints с PLATFORM_ROOT=<arena> — дублирует make-рецепты в orchestrate.py, `make bootstrap-node` жёстко перезаписывает PLATFORM_ROOT (bootstrap.mk:30) · Reason: Path 2 — канон overlay-layout (DevPlan 024 TASK-1), удаление не планируется; `make bootstrap-node` работает без правок · Rev: если platform/ subdir внутри чужого репо начнёт конфликтовать с glob-потребителями (сейчас — только при явном имени ноды арены) — переезжать наPLATFORM_ROOT-путь

### D2 — Контекст = сценарий (параллельные топологии)

Каждый сценарий получает свой контекст `arena-<slug>` и свои ноды. Production-контекст не задет: placement резолвится по имени контекста из физического пути, а lab-дерево живёт в отдельном GENERATED-каталоге. Обобщение D1 исходного плана (один `test-lab` + правка placement) до библиотеки сценариев с параллельными топологиями.

### D3 — Интеграция: 4-path Path 2 резолв + env-оверрайды для NODE_CONFIGS_DIR-потребителей (исправлено, ревизия 2)

Верифицированная карта резолвов (не единый механизм!):

| Механизм резолва node.yaml | Читает NODE_CONFIGS_DIR? | Как работает в арене |
|---|---|---|
| `bootstrap_resolver` → `NodeYaml.resolve` (bootstrap/check-security/converge/deploy.mk) | **НЕТ** — 4-path: {PLATFORM_ROOT}/node-configs → `~/projects/*/platform/node-configs/` → `~/projects/*/node-configs/` (legacy WARN) → /opt | Path 2 по физическому пути `platform/node-configs/`; `make bootstrap-node` перезаписывает PLATFORM_ROOT (bootstrap.mk:30) — не мешает, Path 2 глобальный |
| `project_lister._resolve_scan_root`, `enabled_modules`, `decrypt_secrets`, `platform_export_metrics`, `dev_hosts`, `context_deployer._step_vhosts` | **ДА** (env) | арена экспортирует `NODE_CONFIGS_DIR=<arena>/platform/node-configs` |

Итого: `arena up` гоняет канонический `make -C <ai-platform> bootstrap-node NODE=data-apart-data` **без env-манипуляций ради резолва** (Path 2 находит), плюс экспортирует `NODE_CONFIGS_DIR` для остальных потребителей. Ноль правок ai-platform.

# 🧐 TRAP[DECISION] · 2026-09-01 · — · Резолв через Path 2 glob, а не NODE_CONFIGS_DIR · Rejected: env-оверрайд NODE_CONFIGS_DIR как универсальный механизм (исходный D3) · Reason: верификация показала — bootstrap-путь игнорирует NODE_CONFIGS_DIR (4-path в resolve.py:109-142), make жёстко задаёт PLATFORM_ROOT; NODE_CONFIGS_DIR покрывает только 6 из ~10 потребителей · Rev: если платформа добавит NODE_CONFIGS_DIR в NodeYaml.resolve — можно упростить, но Path 2 останется корректным

### D4 — off-автодополнение из фактического инвентаря

`validate_topology` требует **полноту** записей (каждый модуль `core/modules/` обязан иметь форму, включая `off`). Генератор `render.py` дочитывает инвентарь фактическим `ls core/modules/` и заполняет отсутствующие в сценарии модули формой `{mode: off}` — сценарий автор пишет только интересные модули, инвариант полноты держит генератор.

### D5 — Семантические имена сценариев + `plan_ref`

`scenarios/data-apart.yaml` (а не `s2.yaml`). Привязка к DevPlan 010 — полем `plan_ref: "010 §8 S2"` внутри файла, не в имени. Сценарии: `data-apart` (S2), `data-agent-apps` (S3), `canary-multi-ingress` (S2b), `single-node` (regression).

### D6 — Тема `arena`, бинарь → Makefile, исключение корневого контракта

Папка `~/projects/deploy-arena/`, CLI `arena`, глаголы `arena up|down|verify|status|list`. `up/down` консистентны с платформенными `make up/down`. Бинарь — тонкий фасад → Makefile-рецепты → Python (Strangler-стиль платформы). Префикс `tronyx-` убран (коллизия с production-контекстом `tronyx-lab`). Репо содержит подпапку `platform/` — визуально совпадает с overlay-контейнером канона ~/projects/, но это **инструмент**, не контекст: исключение класса ai-instructions/ai-project (вне lifecycle, реестр не ведётся), фиксируется в README арены.

### D7 — SSH-сантехника (новое, ревизия 2)

Верифицированные факты: (a) `node.schema.json` требует `node.owner_key` (SSH pub — платформенный пользователь, φ2) и опционально `ci_deploy_key` (ci-deploy пользователь, forced-command S7); (b) bootstrap SSH — `root@<host>` (build-ssh-cmd.sh:115), `SSH_OPTS` не содержит IdentityFile (ssh_opts.py:40-49) → identity выбирает штатный ssh (config/agent); (c) Multipass VM не даёт root SSH из коробки. Решение — модуль `keys.py`:

- Per-scenario keypair'ы в `secrets/`: `root_ssh` (cloud-init → `/root/.ssh/authorized_keys`; Ubuntu 24.04 default PermitRootLogin prohibit-password — достаточно) и `ci_deploy` (pub — в node.yaml#node.ci_deploy_key; private — в `--key-file` доставки).
- Managed-блок `~/.ssh/config` (маркер-паттерн dev_hosts.py, идемпотентный): `Host <VM-IP> → IdentityFile <arena>/secrets/root_ssh/id_ed25519` — покрывает bootstrap/check-security/deploy-context/converge (все ходят root@IP через SSH_OPTS).
- Доставка проектов — **прямой** `orchestrator_cli deliver --key-file <arena>/secrets/ci_deploy/ci_deploy_key` (флаги --host/--user/--key-file/--project-dir существуют, orchestrator_cli.py:174-180): prod `~/.ssh/ci_deploy_key` не затрагивается.
- health-предпробка bootstrap-фазы для lab-проектов не выполняется: локальных источников `~/projects/arena-<ctx>/<p>/` нет → все проекты `skipped(no_local_source)` (project_payload_delivery.py:45-46, не failure) — канал ci-deploy при bootstrap не задействован.

# 🧐 TRAP[DECISION] · 2026-09-01 · — · Прямой orchestrator_cli deliver с --key-file вместо make deploy-project · Rejected: канонический `make deploy-project` (deploy.mk не пробрасывает --key-file/--project-dir → упал бы на дефолтном ~/.ssh/ci_deploy_key = prod-ключ) и подмена ~/.ssh/ci_deploy_key на время прогона (глобальная мутация машины оператора) · Reason: orchestrator_cli deliver — канонический операторский путь (DevPlan 116 T5), публичный CLI с нужными флагами; изоляция prod-ключей сохранена · Rev: если deploy.mk начнёт пробрасывать KEY_FILE/PROJECT_DIR — вернуть make-путь

### D8 — DNS/TLS: headless по умолчанию, `dns:`-флаг для полного sweep (гибрид, ревизия 2)

Верифицированные факты: (a) NAT'ные multipass VM не публично достижимы → HTTP-01 невозможен; (b) `cert_is_valid()` требует **LE-issuer** (ssl_certs.py: parseable+LE+domain+expiry) → mkcert/self-signed/собственный CA платформа считает невалидными (self-signed fallback φ7 перезапишет их при каждом bootstrap); (c) `http_probe` — curl без `-k`; (d) e2e-verify local-mode включает только exposed-домены (F-034).

Следствие — двухрежимность:
- **Headless (default, Wave 1–5):** node.yaml без `domain`; lab-web с `expose: false`; e2e-verify локально — 0 endpoints (тривиально зелёный, слабый сигнал — честно документировано); реальная валидация локальной фазы = деплой+healthcheck+check-security+кросс-нодовая БД.
- **dns-режим (опциональная секция `dns: {domain, provider}` в сценарии; включается позже или в M2):** render пишет `domain` + `acme_dns_plugin` в node.yaml; единственный lab-секрет — DNS-провайдер-кред (WEBNAMES_API_KEY) в lab sops; φ7 выпускает LE wildcard DNS-01 (публичная достижимость VM не нужна, исходящий интернет через NAT есть); модуль `hosts.py` маппит FQDN → VM IP managed-блоком /etc/hosts (sudo) → полный честный e2e-verify локально. DNS-steering canary-сценария — переключением hosts-блока между нодами.

# 🧐 TRAP[DECISION] · 2026-09-01 · — · Гибрид headless/dns-флаг вместо DNS-01 с первого дня · Rejected: (a) DNS-01 сразу (копия prod DNS-креда в lab + sudo-автоматизация в критическом пути M1); (b) mkcert/self-signed (архитектурно невозможно — cert_is_valid требует LE-issuer, φ7 перезапишет); (c) правка verify_sweep (-k/--resolve) — ломает инвариант «ноль правок ai-platform» и ослабляет сигнал (TLS не валидируется) · Reason: 80% логики (placement/drift/env/кросс-нодовая БД) валидируется headless за 0 ₽ и 0 секретов; полный sweep остаётся достижимым без правок кода арены · Rev: если dns-сценарии понадобятся до M2 — включить секцию (код hosts.py добавить отдельной волной)

### D9 — Модульный сет: расширенный минус приватные образы (SP4)

Локальный S2 гоняет расширенный сет из публичных образов: `nginx, postgres, redis, litellm, langfuse, minio, clickhouse, monitoring, logging, status-page` (2 VM × 2 vCPU/4GB — суммарно ~5-6GB, влезает). `hermes-agent` (и всё на `ghcr.io/tronyxlab/hermes-agent-context`) **исключён** — приватный ghcr, тянет GHCR-креды. `render.py` держит константу приватных модулей: модуль из списка в сценарии локального бэкенда → ConfigValidationError с подсказкой (dns/GHCR-креды или cloud). Placement несёт полный инвентарь — исключённые модули `off` (политику полноты держит D4).

### D10 — Lab-проекты: фикстуры в репо арены (SP3)

`lab-api` (backend, `needs.database`, headless) и `lab-web` (frontend, `expose: false` локально / true в dns-режиме) — фикстуры в `projects/arena-data-apart/` внутри репо арены. Доставка — пост-бутстрап `orchestrator_cli deliver --project-dir ...` (D7); payload-канал тот же, что CI-deploy (receive forced-command). `projects/` в арене — единственное место; `~/projects/arena-*` не создаются (корневой контракт не размывается).

## @rationale

- Q: почему standalone вне ai-platform? A: выбор владельца; не трогает инвариант глоссария глаголов и гейты платформы; быстрее итерации. При необходимости позже промоутить в verb.
- Q: почему гибридный субстрат? A: 80% сценариев (логика placement/drift/env) валидируются локально за 0 ₽; 20% (network-truth, resource-honesty) требуют реального сетевого слоя/RAM — дешевле гонять по API за €/день, чем держать постоянные VPS.
- Q: почему контекст = сценарий, а не один контекст с правкой placement? A: параллельные топологии + библиотека; прошлая топология не уничтожается правкой.
- Q: почему резолв через Path 2, а не env-оверрайд? A: верификация кода показала, что bootstrap-путь игнорирует NODE_CONFIGS_DIR; Path 2 — канон без WARN и без правок платформы (D3).
- Q: почему headless локально? A: cert_is_valid требует LE-issuer — не-LE сертификаты платформа отвергает архитектурно; DNS-01 возможен, но тянет prod-кред и sudo в критический путь M1 (D8).

---

## 4. Данные (Data Flow, шаг за шагом)

1. `arena up SCENARIO=data-apart` → cli.py парсит сценарий, schema.py валидирует (resources, keys, опц. dns).
2. keys.py: генерирует/переиспользует per-scenario keypair'ы (root_ssh, ci_deploy) в `secrets/`; идемпотентно обновляет managed-блок `~/.ssh/config` (Host <VM-IP> → IdentityFile).
3. render.py: генерирует `platform/node-configs/arena-data-apart/placement.yaml` + `platform/node-configs/data-apart-{data,main}/node.yaml` — off-автодополнение, `vpn_enforced: true`, `node.owner_key`/`node.ci_deploy_key` = pub-ключи из keys.py, guard приватных образов; экспортирует `NODE_CONFIGS_DIR` для потребителей.
4. providers/multipass.py: `multipass launch --cpus --mem --disk --cloud-init` ×2 (Ubuntu 24.04; cloud-init пишет root authorized_keys из keys.py), ждёт IP → RFC1918 → дописывает `node.host` в node.yaml + Host-строки в ssh-config block.
5. orchestrate.py: `make -C <ai-platform> bootstrap-node NODE=data-apart-data` → resolver Path 2 находит node.yaml → SCP core + `platform/node-configs/` → SSH root@VM init (AGE-контент через stdin prelude из `AGE_SECRET_KEY_FILE=<arena>/secrets/age/lab-age-key.txt`); φ4 autogen-креды (enc-файл отсутствует → step_skip → autogen); φ7 без домена — skip; повтор `NODE=data-apart-main`.
6. orchestrate.py: `make deploy-context NODE=...` (on-node; ensure_context_repo без repos.core → WARN-но-op) → доставка фикстур: `orchestrator_cli deliver --project lab-api --project-dir <arena>/projects/arena-data-apart/lab-api --host <data?main> --key-file ...` (lab-api → data-ноде: БД на data; lab-web → main).
7. verify.py: `make check-security NODE=<обе>` + `make healthcheck NODE=<main>` + e2e-verify (0 endpoints) + health-probe lab-api/lab-web (verb `health`, ci-deploy канал с lab ключом) → журнал `logs/<run>.jsonl`.
8. `arena down SCENARIO=data-apart` → teardown.py: `make down`/`down-volumes` по SSH → `multipass delete --purge` → managed ssh-config block cleanup → (dns) hosts clear → (опц.) archive node-configs.

---

## 5. $TASKS

Каждая задача: один артефакт, один владелец (Coder), измеримые AC, зависимости, сложность 1–10.

| ID | Задача | Артефакт | AC | Deps | Сложность |
|---|---|---|---|---|---|
| T1 | Scaffold репозитория `deploy-arena` + Makefile + thin facade `arena`; layout c `platform/`, `projects/`, `secrets/`; README с исключением корневого контракта (D6) | `arena`, `Makefile`, структура папок | `arena list` возвращает сценарии; `make -n up` показывает цепочку рецептов без ошибок | — | 2 |
| T2 | schema.py: валидация scenario.yaml (nodes+resources, modules, projects, keys, `dns:` optional, plan_ref) | `src/arena/schema.py` + tests | валидный/невалидный YAML → pass/fail; отсутствие обязательных полей → ConfigValidationError; `dns` без provider → ошибка | — | 3 |
| T3 | render.py: scenario → `platform/node-configs/` (flat) + off-автодополнение + RFC1918 + vpn_enforced + guard приватных образов (D9) + pub-ключи в node.yaml | `src/arena/render.py` + tests | рендер проходит платформенный `validate_topology`; каждый модуль инвентаря имеет запись; host RFC1918; hermes-agent в локальном сете → ConfigValidationError | T2, T4 | 6 |
| T4 | SSH-сантехника (D7): keys.py — per-scenario keypair'ы, cloud-init user-data (root authorized_keys), managed ~/.ssh/config block | `src/arena/keys.py` + tests | keypair'ы 0600 в secrets/; повторный вызов no-op; ssh-config block идемпотентен (двойной apply = 1 блок); приватные ключи не в git | T1 | 4 |
| T5 | providers/base.py + multipass.py (cloud-init из T4, ресурсы из scenario) | `src/arena/providers/{base,multipass}.py` + tests | `provision` возвращает hosts; `destroy` удаляет VM (mock-проверка); cloud-init user-data содержит root pubkey | T4 | 4 |
| T6 | orchestrate.py: `make -C <ai-platform>` с env-оверрайдами + пост-бутстрап `orchestrator_cli deliver --key-file` (D3/D7) | `src/arena/orchestrate.py` | bootstrap обеих нод S2 завершается exit 0 (резолв Path 2, без WARN); идемпотентный повторный прогон; lab-api/lab-web доставлены на свои ноды | T1,T3,T5 | 6 |
| T7 | verify.py: check-security + healthcheck + e2e-verify (headless 0-endpoints) + health-probe проектов + журнал | `src/arena/verify.py` | журнал пишется; verify зелёный на headless data-apart | T1,T6 | 3 |
| T8 | teardown.py: down + destroy + ssh-config cleanup + archive | `src/arena/teardown.py` | `down` уничтожает субстрат; повторный `down` no-op; ssh-config block удалён | T1,T5 | 3 |
| T9 | Фикстуры lab-api/lab-web + сценарий `data-apart` + unit/integration-тесты | `projects/arena-data-apart/*`, `scenarios/data-apart.yaml` + tests | полный цикл up→verify→down зелёный локально (критерий 1: БД lab-api на data-ноде) | T2–T8 | 7 |
| T10 | hcloud-бэкенд (Terraform) + Tailscale overlay (M2) | `providers/hcloud/*`, `providers/hcloud.py` | 3 ноды по API; ufw peer-rules на 100.64/10; порт-скан не-пира closed; полный e2e-verify по реальным доменам | T5 | 6 |
| T11 | Сценарии `data-agent-apps`, `canary-multi-ingress`, `single-node` | `scenarios/*.yaml` | покрытие форм {nodes[]}, off, all-nodes, no-op | T9 | 4 |
| T12 | dns-режим: hosts.py (managed /etc/hosts) + `dns:`-секция в render + dns-вариант сценария | `src/arena/hosts.py` + tests | (M1.5/M2) полный e2e-verify sweep локально: LE wildcard DNS-01, hosts-маппинг, `arena down` чистит hosts | T9 | 4 |

Критический путь: T2 → T3 → T6 → T9 (→ T11). T1, T4 параллельны старту; T12 — отдельная волна после T9 (или M2).

---

## 6. $PARALLEL_GROUPS

### Wave 1 (независимы, нет общих файлов)
- Задачи: T1, T2, T4
- Команда: `coder Read .ai/plans/026-deploy-arena-lab/01-DevPlan.md, implement Wave 1: T1, T2, T4`

### Wave 2 (зависимости из Wave 1)
- Задачи: T3 (T2,T4), T5 (T4)
- Команда: `coder ... implement Wave 2: T3, T5`

### Wave 3
- Задачи: T6 (T1,T3,T5)
- Команда: `coder ... implement Wave 3: T6`

### Wave 4 (параллельны, разные файлы)
- Задачи: T7 (T1,T6), T8 (T1,T5)
- Команда: `coder ... implement Wave 4: T7, T8`

### Wave 5 (интеграция)
- Задачи: T9 (T2–T8) → затем T11 (T9)
- Команда: `coder ... implement Wave 5: T9`, затем `... T11`

### Wave 6 (опционально, dns-режим)
- Задачи: T12 (T9)
- Команда: `coder ... implement Wave 6: T12`

---

## 7. Acceptance Criteria (сводная)

| # | Критерий | Проверка |
|---|---|---|
| AC1 | Headless-цикл S2: `arena up/verify/down SCENARIO=data-apart` зелёный | check-security PASS + healthcheck PASS + lab-api (backend, БД через pgbouncer на data-ноде) и lab-web (expose:false) healthy; e2e-verify локально — 0 endpoints, зелёный |
| AC1b | (dns-режим, T12/M2) Полный sweep по exposed-доменам | e2e-verify PASS: LE wildcard DNS-01, managed /etc/hosts, curl без -k |
| AC2 | Идемпотентность | повторный `up` — no-op дрейфа (bootstrap/converge) |
| AC3 | Teardown | `down` → `multipass delete --purge`; повторный `down` no-op; ssh-config block и (dns) hosts вычищены |
| AC4 | Корректность рендера | `platform/node-configs/` проходит платформенный `validate_topology` (полнота, RFC1918, contexts[0].name); резолв Path 2 без legacy WARN |
| AC5 | Изоляция | production-контексты, lab AGE-ключ и prod `~/.ssh/ci_deploy_key` не затронуты; приватные ключи арены только в secrets/ (0600, gitignored) |
| AC6 | Cloud network-truth (M2) | Tailscale 100.64/10 в ufw, не-пир порт-скан closed, DNS-steering, полный e2e-verify по публичным доменам |

---

## 8. File Manifest

Репозиторий `~/projects/deploy-arena/` (новый, вне ai-platform):

```
arena                          # thin bash facade
Makefile                       # up/down/verify/status/list
src/arena/{cli,schema,render,keys,orchestrate,verify,teardown}.py
src/arena/hosts.py             # dns-режим (T12)
src/arena/providers/{base,multipass,hcloud}.py
scenarios/{data-apart,data-agent-apps,canary-multi-ingress,single-node}.yaml
projects/arena-data-apart/{lab-api,lab-web}/   # фикстуры: docker-compose.yml + ai-platform.yaml
providers/hcloud/{main.tf,variables.tf}        # M2
platform/node-configs/         # GENERATED, gitignored (Path 2 layout)
secrets/                       # lab AGE-ключ + keypair'ы + secrets.env (gitignored, 0600)
logs/                          # журнал прогонов
tests/                         # pytest: test_{render,schema,keys,providers,cli}.py
README.md                      # назначение + исключение корневого контракта ~/projects/
```

План в ai-platform: `.ai/plans/026-deploy-arena-lab/01-DevPlan.md` (этот файл).

---

## 9. $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|---|---|---|---|
| tests/test_render.py | test_render_flat_platform_layout | placement.yaml в `<ctx>/placement.yaml`, node.yaml в `<node>/node.yaml` внутри `platform/node-configs/`; `placement_node_relative_path` резолвит точно | render.py |
| tests/test_render.py | test_off_autocompletion | каждый модуль core/modules/ получает запись (включая off) | render.py |
| tests/test_render.py | test_host_rfc1918 | node.host только RFC1918/CGNAT, иначе ошибка | render.py |
| tests/test_render.py | test_private_image_guard | hermes-agent в локальном сете → ConfigValidationError | render.py (D9) |
| tests/test_render.py | test_node_yaml_carries_keys | owner_key/ci_deploy_key = pub из keys.py, vpn_enforced: true | render.py (D7) |
| tests/test_render.py | test_placement_passes_validate_topology | рендер проходит платформенный validate_topology | render.py (интеграция) |
| tests/test_schema.py | test_scenario_schema_validation | валидный/невалидный scenario.yaml; `dns` без provider → ошибка | schema.py |
| tests/test_keys.py | test_keypair_generation_idempotent | повторный вызов = те же ключи, 0600, вне git | keys.py |
| tests/test_keys.py | test_ssh_config_block_idempotent | двойной apply → ровно один managed-блок; cleanup удаляет | keys.py |
| tests/test_providers.py | test_multipass_provision_destroy | provision→hosts, destroy→удаление, cloud-init содержит root pubkey (mock) | providers/multipass.py |
| tests/test_orchestrate.py | test_delivery_uses_lab_key_and_project_dir | deliver-команда содержит --project-dir (вне ~/projects) и --key-file (lab), НЕ подменяет ~/.ssh/ci_deploy_key | orchestrate.py |
| tests/test_cli.py | test_cli_dispatch | подкоманды route в рецепты | cli.py |

---

## 10. Честные границы (не подтвердит — и не должна)

Лаба подтверждает формы размещения, переходы, сетевые швы, ufw peer-логику (локально — на RFC1918 multipass-сети) и blast-radius изоляцию, но **не** HA-failover (SPOF каждого singleton, RTO часы) и **не** улучшает RPO 24ч (backup-cron покрывает только postgres). Локальный e2e-verify в headless-режиме — тривиально зелёный (0 exposed endpoints, F-034): полный sweep = dns-режим (T12) или M2. Tailscale CGNAT, WAN-экспозиция, DNS-steering на реальных A-записях — только cloud-фаза. Self-signed/mkcert-пути исключены платформой (`cert_is_valid` требует LE-issuer) — это ограничение платформы, не арены. Теперь это свойства библиотеки сценариев: будущий вопрос (новый модуль/форма/миграция) = новый `.yaml`, а не 5-дневный проект.

## Next Steps

### Wave 1
Используй роль Coder, прочитай `.ai/plans/026-deploy-arena-lab/01-DevPlan.md`, реализуй Wave 1: T1, T2, T4. Верификация — `make check` в репозитории `deploy-arena` (до чистоты).

### Wave 2–6
Аналогично по секции §6, волны последовательно; после каждой — `make check` до чистоты; финал — AC1 полный headless-цикл S2 на локальных VM. Wave 6 (T12, dns-режим) — по запросу владельца или вместе с M2.

<!-- $END_DEVPLAN -->
