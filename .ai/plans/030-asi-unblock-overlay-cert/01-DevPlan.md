$START_DEVPLAN

# DevPlan 030 — Разблокировка asi-team-vps: overlay-миграция (F15), wildcard-aware final-verify (F14), identity = `roadmap`

## $ARTIFACT_CONTRACT

| Поле | Значение |
|------|----------|
| PURPOSE | Разблокировать `asi-team-vps` (контекст `asi-group`): φ-final-verify assertion (a) падает на серте `roadmap.asiteam.ru` (F14), а проект roadmap не деплоится и отдаёт HTTP 502 (F15). Дополнительно свести контейнерную/репо-идентичность к ЕДИНСТВЕННОМУ `roadmap` (владелец: «только один контейнер с подобным именем, верное — roadmap»). |
| DESCRIPTION | (1) **Код:** сделать assertion (a) φ-final-verify wildcard-aware — `roadmap.asiteam.ru` покрыт wildcard `*.asiteam.ru` (`/etc/letsencrypt/live/asiteam.ru/`), что уже понимают `cert_orchestrator._log_post_issue_coverage` и `vhost_renderer`; (2) **Код/конфиг:** закоммитить уже сделанные фиксы F6/F10/F12 (uncommitted) + новый wildcard-фикс + свести `node.yaml#repo` `roadmap2`→`roadmap`; (3) **Оператор+GH:** миграция asi-group на канонический overlay-layout (создать `asi-group/asi-group-overlay`, snapshot, `repos.core` → overlay); (4) **Верификация:** повторный bootstrap → φ-final-verify PASS + roadmap live. |
| RATIONALE | F14 — ложный FAIL assertion (a): серт реально выпущен (wildcard `*.asiteam.ru` покрывает `roadmap.asiteam.ru`), но проверка ищет только direct-каталог `live/{domain}/`. F15 — задокументированный TRAP[DEBT] (root AGENTS.md §«Почему одна папка», DevPlan 022 TASK-1): `repos.core` указывает на full-source mirror `asi-group/ai-platform`, а не на overlay — VPS-клон `/opt/asi-group/platform/` не содержит `context.yaml`/канонических `projects/`/overlay-`node-configs/`. Identity — GH-репо переименован `roadmap2`→`roadmap`, но `node.yaml#repo` остался на старом имени. |
| ACCEPTANCE_CRITERIA | (1) `make check` зелёный + `make agent-check` exit 0; (2) unit-тест: `roadmap.asiteam.ru` при наличии `live/asiteam.ru/fullchain.pem` с CN=`*.asiteam.ru` → assertion (a) PASS; при реальном отсутствии coverage → exit 10; (3) `grep -rn "roadmap2" node-configs/` = пусто (кроме исторических комментариев); (4) создан `asi-group/asi-group-overlay` с `context.yaml` + `node-configs/asi-team-vps/` + `projects/roadmap.yaml` + `modules/hermes-agent/` + `.github/workflows/deploy.yml`; (5) `node.yaml#repos.core = git@github.com-overlay:asi-group/asi-group-overlay.git`; (6) повторный `make bootstrap-node NODE=asi-team-vps` → `φ-final-verify PASS` и `roadmap.asiteam.ru` → HTTP 200 + валидный TLS (`make e2e-verify`). |
| IMPLEMENTS | Закрытие TRAP[DEBT] миграции asi-group (root AGENTS.md §«Почему одна папка» + §«Две роли репозиториев контекста»), F14/F15 из 029-deploy-integrity/06-overnight-report. |
| IMPACTS | `core/internal/shared/ssl_certs.py` (новый helper), `core/internal/bootstrap/lifecycle/helpers/domains.py`, `core/internal/bootstrap/cert_orchestrator.py` (опц. дедуп), `node-configs/asi-team-vps/node.yaml`, `tests/unit/test_ssl_certs.py` + `tests/unit/test_final_verify.py`, новый GH-репо `asi-group/asi-group-overlay`. |
| REQUIRES | gh-доступ к org `asi-group` (доступен: аккаунт Tronyx161, member + token `admin:org` — подтверждено `gh auth status`/орг-листингом); сетевая доступность VPS `77.233.221.129` для повторного bootstrap. |

---

## 1. Requirements Analysis

### 1.1 Верифицированные факты (сверено с кодом/git/gh, 2026-09-02)

| # | Факт | Источник |
|---|------|----------|
| F1 | gh аутентифицирован как `Tronyx161`, состоит в org `asi-group` (scope `admin:org`); репо `asi-group/ai-platform` (public mirror) + `asi-group/roadmap` (private). GH-орг-шаги НЕ являются жёстким блокером. | `gh auth status`, `gh repo list asi-group` |
| F2 | GH-репо `roadmap2` **переименован в `roadmap`** (`gh repo view asi-group/roadmap2` → `"name":"roadmap"`, redirect). `node.yaml#projects[roadmap].repo = asi-group/roadmap2` — устаревшая ссылка на старое имя (единственный residual `roadmap2`). | `gh repo view`, `node-configs/asi-team-vps/node.yaml:39` |
| F3 | Identity уже сходится на `roadmap`: local git remote = `asi-group/roadmap`; `docker-compose.yml` service=`roadmap` + alias `roadmap`; image = `ghcr.io/asi-group/roadmap`; `ai-platform.yaml` name=`roadmap`, type=`frontend`, expose=true, domain=`roadmap.asiteam.ru`. | `~/projects/asi-group/roadmap/*`, `git remote -v` |
| F4 | F14: vhost `roadmap.asiteam.ru.conf` использует `ssl_certificate /etc/letsencrypt/live/asiteam.ru/fullchain.pem` (wildcard `*.asiteam.ru`, SAN asiteam.ru + \*.asiteam.ru). `cert_orchestrator._log_post_issue_coverage` (cert_orchestrator.py:1148) и `vhost_renderer` понимают wildcard-покрытие; `domains._certs_converged_on_disk` (domains.py:360, канон assertion (a)) — НЕТ (только `live/{domain}/fullchain.pem`). | vhost conf, cert_orchestrator.py, domains.py |
| F5 | F15: `node.yaml#repos.core = https://github.com/asi-group/ai-platform.git` (full-source mirror) → VPS-клон `/opt/asi-group/platform/` = полный source-клон (есть `core/`, `Makefile`, `pyproject.toml`), БЕЗ `context.yaml`/overlay-`projects/`/канонической `node-configs/`. Документировано как TRAP[DEBT] (root AGENTS.md). | node.yaml:19, `~/projects/asi-group/platform/` (source-клон), root AGENTS.md |
| F6 | F6/F10/F12 уже исправлены в working tree (uncommitted): `preflight.py` (F6 probe_sops_enc_file), `context_overlay.py` (F10 HTTP/2 401 → `http.version=HTTP/1.1`), `final_verify.py` (F12 GHCR fallback → secrets.env) + тесты. | `git status`: M context_overlay.py, final_verify.py, preflight.py + 3 теста |
| F7 | Cert-примитивы уже в shared: `ssl_certs.cert_get_subject` (ssl_certs.py:228) + `cert_subject_matches_domain` (ssl_certs.py:251, матчит `CN = *.parent`). | core/internal/shared/ssl_certs.py |

### 1.2 Корневая причина

1. **F14** — assertion (a) не знает про wildcard-родителя. Серт выпускается как `*.asiteam.ru` (DNS-01 regru), `roadmap.asiteam.ru` SKIP'ается как покрытый, но `_certs_converged_on_disk` проверяет только direct-каталог → «no certificate on disk» → exit 10.
2. **F15** — layout-долг: `repos.core` = full-source mirror, а не `<org>/<ctx>-overlay`. VPS-клон не несёт контекстного overlay (`context.yaml` + `projects/` + overlay-`node-configs/`), поэтому deploy-context не находит roadmap-проектный конфиг → контейнер не поднимается → 502.
3. **Identity** — GH-репо переименован, но `node.yaml#repo` не обновлён; `roadmap2` остался как «призрак» в конфиге (контейнер/образ/сервис уже `roadmap`).

### 1.3 Решения (superposition, auto-collapsed)

**D1. Cert-политика roadmap — Option A: wildcard остаётся, assertion (a) становится wildcard-aware.**
- ✅ Выбрано: `roadmap.asiteam.ru` покрыт `*.asiteam.ru` (vhost уже ссылается на `live/asiteam.ru/`; `_log_post_issue_coverage` и `vhost_renderer` уже так считают). Меняем только `_certs_converged_on_disk` (и тесты). Ноль инфра-правок.
- Rejected: per-project direct-серт `live/roadmap.asiteam.ru/` — требует (а) запретить skip поддоменов wildcard'а в cert_orchestrator, (б) перерендерить vhost на `live/roadmap.asiteam.ru/`, (в) перевыпуск/миграцию серта на ноде. Дороже без выгоды (wildcard уже покрывает apex+все поддомены).
- Rev: если появится требование разнесённых по сертам SAN (напр. отдельный серт для поддомена с другим провайдером) — пересмотреть.

**D2. Identity = `roadmap` (единственный контейнер/репо/образ).**
- ✅ Выбрано: канон `roadmap`. `node.yaml#repo` → `asi-group/roadmap` (репо переименован). Контейнер/сервис/образ уже `roadmap`. Лишний GH-репо `roadmap2` физически НЕ существует (redirect) — удалять нечего; убираем только устаревшую ссылку.
- Rejected: сведение к `roadmap2` — противоречит git remote (`roadmap`), образу (`ghcr.io/asi-group/roadmap`), vhost upstream (`http://roadmap:80`) и прямому указанию владельца.

**D3. Overlay-миграция = TASK-4 (оператор+gh, доступен агенту с approval).**
- ✅ Выбрано: создать `asi-group/asi-group-overlay` (private), snapshot канонического layout (см. tronyx-lab эталон), `repos.core` → SSH-алиасный URL. По root AGENTS.md §«Почему одна папка» / §«VPS-доступ к приватному overlay».
- Rejected: продолжить жить на full-mirror — воспроизводит F15 на каждом следующем деплое контекста.

---

## 2. Draft Code Graph

```
φ-final-verify assertion (a)                                  (TASK-1)
  phase_final_verify → _assert_certs_on_disk → domains.ssl_certs_converged_on_disk
    → _certs_converged_on_disk(core_dir, node_yaml)
        ▶ NEW: ssl_certs.cert_covers_domain(le_live, domain) -> bool
             ┌ direct:  live/{domain}/fullchain.pem  + subject covers domain ┐
             └ wildcard: live/{parent}/fullchain.pem + subject covers *.parent┘
        → все домены покрыты → True; есть непокрытый → False; экстрактор недоступен → None
  (дедуп: cert_orchestrator._log_post_issue_coverage переиспользует тот же helper — опц.)

node.yaml#projects[roadmap].repo: asi-group/roadmap2 → asi-group/roadmap   (TASK-2)

Overlay-миграция                                                     (TASK-4)
  gh repo create asi-group/asi-group-overlay --private
  → snapshot: context.yaml + node-configs/asi-team-vps/{node.yaml,overlays/,secrets/}
    + projects/roadmap.yaml (L2-override) + modules/hermes-agent/ + .github/workflows/deploy.yml
  → gh repo deploy-key add (read-only, title "vps-asi-group-readonly")  [или make new-context provision_deploy_key]
  → node.yaml#repos.core = git@github.com-overlay:asi-group/asi-group-overlay.git
  → push; node-side key+SSH-алиас (runbook core/internal/bootstrap/AGENTS.md §VPS-доступ) (TASK-5)
```

---

## $TASKS

### TASK-1 — Wildcard-aware assertion (a) (код, Coder)
**Сложность:** 4/10 · **Файлы:** `core/internal/shared/ssl_certs.py`, `core/internal/bootstrap/lifecycle/helpers/domains.py`, `core/internal/bootstrap/cert_orchestrator.py` (опц.), `tests/unit/test_ssl_certs.py`, `tests/unit/test_final_verify.py`

- В `ssl_certs.py` добавить `cert_covers_domain(le_live: Path, domain: str) -> bool` — единый канон on-disk покрытия: direct (`live/{domain}/fullchain.pem` + `cert_subject_matches_domain(subject, domain)`) ИЛИ wildcard-родитель (для `i in range(1, len(labels)-1)`: `parent = labels[i:]`, `live/{parent}/fullchain.pem` + `cert_subject_matches_domain(subject, f"*.{parent}")`). Переиспользовать существующие `cert_get_subject`/`cert_subject_matches_domain`. (Обоснование для shared/: дедупликация ≥2 реализаций — см. ниже.)
- Переписать `domains._certs_converged_on_disk`: `missing = [d for d in domains if not cert_covers_domain(le_live, d)]`. Пустой список доменов → True; `extract_domains_for_context is None` → None (fail-closed, семантика сохранена).
- (Опц. дедуп) `cert_orchestrator._log_post_issue_coverage` — заменить дублирующую ветку прямого+wildcard-поиска на вызов `cert_covers_domain`, сохранив строку-вердикт (`direct`|`wildcard:{parent}`|`none`) и лог-семантику (не обязательное условие AC; если задевает больше 3 строк — отложить отдельной микро-задачей).
- Обновить MODULE_CONTRACT/GREP_SUMMARY/STRUCTURE затрагиваемых файлов (@changes 2026-09-02 · DevPlan 030 TASK-1).

**$TEST_SPEC** (unit, tmp_path, 0 subprocess):
1. `test_cert_covers_domain_direct` — `live/app.example.com/fullchain.pem` с `CN = app.example.com` → True.
2. `test_cert_covers_domain_wildcard_parent` — `live/example.com/fullchain.pem` с `CN = *.example.com`, домен `roadmap.example.com` → True. *Падает на старом коде (assertion (a) wildcard не знает).*
3. `test_cert_covers_domain_none` — нет ни direct, ни wildcard → False.
4. `test_assert_a_accepts_wildcard` — `phase_final_verify` с реальной `ssl_certs_converged_on_disk` на tmp `live/asiteam.ru/` покрывает `roadmap.asiteam.ru` → не падает (monkeypatch `letsencrypt_live`).

**Acceptance:** AC1, AC2.

---

### TASK-2 — Identity convergence: `roadmap` (конфиг, Coder)
**Сложность:** 1/10 · **Файлы:** `node-configs/asi-team-vps/node.yaml`

- `projects[roadmap].repo: asi-group/roadmap2` → `asi-group/roadmap` (F2: репо переименован).
- `grep -rn "roadmap2" node-configs/ core/ tests/` → единственные оставшиеся вхождения — только исторические комментарии (если есть живые ссылки — свести к `roadmap`).
- Не трогать `repos.core` здесь (меняется в TASK-4) и не трогать `type/expose/domain` (уже корректны).

**Acceptance:** AC3.

---

### TASK-3 — Коммит фиксов (код, Lead/Coder)
**Сложность:** 2/10

- Закоммитить уже сделанные F6/F10/F12 (`preflight.py`, `context_overlay.py`, `final_verify.py` + `tests/unit/test_{preflight,context_overlay,final_verify}.py`) вместе с TASK-1/TASK-2.
- Коммиты (≤3): `fix(030): F14 wildcard-aware final-verify assertion (a)` + `fix(030): roadmap identity — node.yaml repo roadmap2→roadmap` + `fix(029): F6/F10/F12 deploy-integrity fixes + tests` (если F6/F10/F12 ещё не закоммичены отдельно).
- `make check` до чистоты + `make agent-check` exit 0.

**Acceptance:** AC1.

---

### TASK-4 — Overlay-миграция asi-group (оператор + gh; доступен агенту с approval)
**Сложность:** 6/10 · **Артефакт:** новый GH-репо `asi-group/asi-group-overlay` (private)

**Owner-action (approval):** создание репо в org asi-group + read-only deploy key — мутирующие GH-операции.

1. Создать `asi-group/asi-group-overlay` (private).
2. Snapshot канонического layout (эталон — `~/projects/tronyx-lab/platform/`):
   - `context.yaml` — `name: asi-group`, `org: asi-group`, `default_node: asi-team-vps`, `platform_image`/`context_image` → ghcr.io/asi-group/hermes-agent-{platform,context}:latest.
   - `node-configs/asi-team-vps/node.yaml` — копия текущего (с `repos.core → git@github.com-overlay:asi-group/asi-group-overlay.git`, `repo → asi-group/roadmap`) + `node-configs/asi-team-vps/overlays/nginx/*.conf` + `node-configs/secrets/asi-team-vps.enc.yaml` (sops; секрет НЕ в git — `.gitignore`, enc.yaml доставляется SCP — TRAP[DECISION] dual delivery).
   - `projects/roadmap.yaml` — L2-monitoring override (формат `~/projects/tronyx-lab/platform/projects/tronyx-site.yaml`; type=frontend, metrics=false, logs_retention=7d, dashboard=false, alerting=false).
   - `modules/hermes-agent/` — контекстные кастомизации (если есть; иначе пустой placeholder по scaffold-канону).
   - `.github/workflows/deploy.yml` — контекстный CI-деплой (копия из tronyx-lab overlay, адаптировать org=asi-group).
3. Deploy key: `gh repo deploy-key add --repo asi-group/asi-group-overlay` (read-only, БЕЗ `--allow-write`; title `vps-asi-group-readonly`); приватный ключ → `~/projects/asi-group/.secrets/` (0600, вне `platform/`).
4. Push overlay → `main`.
5. Обновить канонический `node.yaml` (source-фикстура `node-configs/asi-team-vps/node.yaml`) — `repos.core` → SSH-алиасный URL (чтобы bootstrap с dev-машины резолвил overlay, а не mirror).

**Acceptance:** AC4, AC5.

---

### TASK-5 — Node-side: deploy key + повторный bootstrap (оператор/runbook)
**Сложность:** 4/10 · **Требует:** доступ по SSH к `asi-team-vps`

1. Установить на ноде key + SSH-алиас `github.com-overlay` (runbook `core/internal/bootstrap/AGENTS.md` §«VPS-доступ к приватному overlay (deploy key)»; или auto-step `context_initializer install-node-deploy-key` при bootstrap).
2. Верификация с ноды: `git ls-remote git@github.com-overlay:asi-group/asi-group-overlay.git`.
3. Повторный `make bootstrap-node NODE=asi-team-vps` (идемпотентен: done-фазы skip; φ8.5/φf перевыполнятся по hash-инвалидации) → `φ-final-verify PASS`.
4. Деплой roadmap: `make deploy-project PROJECT=~/projects/asi-group/roadmap NODE=asi-team-vps` (direct-канал; CI receive-канал остаётся отдельным блокером — см. §Residual).

**Acceptance:** AC6 (roadmap.asiteam.ru → HTTP 200 + TLS valid).

---

### TASK-6 — Финальная верификация (Lead)
**Сложность:** 2/10

1. `make check` зелёный, `make agent-check` exit 0.
2. `make e2e-verify NODE=asi-team-vps` — `roadmap.asiteam.ru` HTTP 200 + TLS (wildcard `*.asiteam.ru`).
3. Повторный bootstrap = no-op (delivered=0, φf no-op).
4. Журнал `.ai/logs/runs.jsonl`.

**Acceptance:** AC1, AC6.

---

## 4. Риски

| # | Риск | Митигация |
|---|------|-----------|
| R1 | Переименование/миграция overlay-репо конфликтует с ещё живым `roadmap2`-redirect на GH | F2: `roadmap2` — redirect на `roadmap`; overlay создаётся с НОВЫМ именем `asi-group-overlay`, конфликтов нет |
| R2 | Секреты (`secrets/*.enc.yaml`) попадут в overlay-git | Следовать канону dual delivery: enc.yaml НЕ коммитится (SCP-канал); `.gitignore` в `node-configs/secrets/` |
| R3 | Wildcard-aware assertion (a) маскирует реальный missing-cert (fail-open) | Helper возвращает `False` при отсутствии ЛЮБОГО покрытия (direct И wildcard); `None`-семантика fail-closed сохранена; negative-тест test 3 |
| R4 | CI receive-канал roadmap остаётся сломан (reusable workflow + GHCR read_package) — кажется «недодеплоем» | Явно задокументировать как отдельный Residual (ниже); bootstrap/deploy-project direct-канал НЕ зависит от CI |
| R5 | Bootstrap перевыполнит φ8.5/φf и упадёт на другом assertion (b/c/d) | Assertions b/c/d уже зелёные на tronyx-vps (029 report); F12 GHCR-fallback входит в коммит TASK-3 |

## 5. Non-goals

- **CI receive-канал roadmap** (reusable workflow permission в org Actions + GHCR `read_package` для `asi-group/roadmap`) — отдельная задача на уровне GH-настроек org/package; не блокирует bootstrap и прямой деплой.
- **Переименование/архив GH-репо** — репо уже `roadmap` (F2); ничего удалять не нужно.
- **Миграция tronyx-lab** — уже канонична (эталон).
- **Multi-node/placement** — вне скоупа (asi-group single-node).

## 6. Residual (фиксируем, не чиним в этом плане)

| # | Суть | Где | Кто |
|---|------|-----|-----|
| CI-1 | Reusable workflow вызовы из приватного `asi-group/roadmap` инстанцируются с мгновенным failure (0s, jobs=[]) — Org Settings → Actions → Workflow permissions / «Allow reusable workflows» | GitHub org asi-group | владелец org |
| CI-2 | CI push в `ghcr.io/asi-group/roadmap` блокирован `read_package` — пакет привязан к старому имени репо | GHCR package settings | владелец org |
| Naming | `repo`-поле `roadmap2` в исторических логах/планах — не правится задним числом | logs/, .kilo/plans/ | — |

## 7. Next Steps

```
Wave 1 (параллельно, код — без VPS/GH): TASK-1 (wildcard-aware assertion a) + TASK-2 (repo→roadmap)
Wave 2 (последовательно): TASK-3 (коммит F6/F10/F12 + W1) → TASK-4 (overlay-миграция, approval)
Wave 3: TASK-5 (node-side key + bootstrap + deploy roadmap) → TASK-6 (make check / agent-check / e2e-verify)
```

$END_DEVPLAN
