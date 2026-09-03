# Overnight Report — 2026-09-03

## Verdict: DONE

Обе production-ноды зелёные и верифицированы end-state (не только exit 0): финальные e2e-сверки
3/3 на каждой, φ-final-verify 4/4, проекты live, повторные bootstrap — no-op, дрейф-дриллы —
heal-or-fail-loud. Один раунд вопросов владельца (все дефолты подтверждены), дальше — автономно.
Отклонение от матрицы — одно, задокументировано: в дереве 3 незакоммиченных fix-файла (+3 тест-файла)
ночной фикс-волны (прецедент легализации владельцем — 91ed43c за F6/F10/F12 вчера).

## Green matrix (§8, по факту)

### Локально
- [x] `make check` → **All checks PASS** (20/20; вчера́шние 7 failed не воспроизвелись — 0 failed)
- [x] `make agent-check` → **exit 0**, blocking=0 (11 advisory — pre-existing, вне ночных правок)
- [~] git tree → журналы/отчёты/archive + untracked планы **+ 3 ночных fix-файла (+3 тест-файла) незакоммичены**
      (`context_initializer.py`, `lifecycle/cli.py`, `converge/networks.py` + тесты) — ждут коммита владельца

### tronyx-vps (tronyx-lab)
- [x] validate-node-input PASS (0 remote)
- [x] bootstrap rc=0 (φ-final-verify: certs direct-or-wildcard 3/3 · secrets.env 59 entries · vhosts 3 · GHCR ≠ skip);
      converge **FULLY CONVERGED** (pre- и post-deploy, 0 drift warnings); node-update rc=0 (φ9–φ13)
- [x] healthcheck-эквивалент: `make healthcheck NODE=` — local-only по контракту (нода = e2e-verify + project-status);
      node-состояние верифицировано e2e + контейнерами
- [x] e2e-verify PASS: tronyx.ru 200 · sexydancerostov.ru 200 · botanika.tronyx.ru 200, TLS ok (58–82d)
- [x] tronyx-site, dance-site, botanika — DEPLOYED healthy (deploy-project, direct-канал); oldapp — явный stub
      («ai-platform.yaml is a GENERATED-STUB», контейнеров нет — adopted без домена, не деплоился)
- [x] повторный bootstrap no-op (10/10 фаз SKIP, delivered=0/skipped=4, final-verify no-op)
- [x] дрейф-дриллы: container → no-action конверга (FINDING-A) → heal deploy-project · cert → HEAL (S3 restore,
      restored=1) · vhost → FAIL-LOUD (exit 2) → restore → green. Финальный e2e после дриллов PASS 3/3

### asi-team-vps (asi-group)
- [x] validate-node-input PASS (WARN: env AGE_SECRET_KEY перекрывает файл — RC4 trap, компенсировано `env -u`)
- [x] bootstrap rc=0 (φ-final-verify 4/4: certs wildcard-aware · secrets.env 2/2 · vhosts 1 · GHCR ≠ skip);
      converge — R1–R11 converged (orphan-vhost warnings — статические vhost'ы apex/login, допустимо); node-update 5/5
- [x] e2e-verify PASS: asiteam.ru 301 · login.asiteam.ru 404 (by-design) · roadmap.asiteam.ru 200, TLS wildcard
      `*.asiteam.ru` (LE, 87d)
- [x] roadmap — DEPLOYED healthy (`roadmap-roadmap-1 Up (healthy)`)
- [x] повторный bootstrap no-op (φ1+final_verify SKIP; FINDING-p3-1: roadmap re-delivered=1 — liveness probe rc=1 →
      fail-safe re-delivery)
- [x] дрейф-дрилл vhost roadmap → FAIL-LOUD (exit 2, «roadmap.asiteam.ru.conf not found») → restore → nginx reload →
      green; container-дрилл → no-action (scope boundary) → heal deploy-project. Финальный e2e PASS 3/3

### CI
- [x] N/A — код проектов не менялся (деплой из существующих источников); правки ai-platform не пушены
      (авторизация на push не запрашивалась)

## Findings (severity, файл:строка, что сделано)

| # | Sev | Finding | Status |
|---|-----|---------|--------|
| F1 | **HIGH** | `install_overlay_deploy_key_node_side` не пиннит github.com в known_hosts → на ноде с потерянным known_hosts (wipe/re-provision) overlay-clone fail «Host key verification failed» (RC3-класс: clean≠warm). tronyx — подтверждено ssh read-only | **FIXED** — TOFU-pin (`ssh-keygen -F` guard + `ssh-keyscan`, fail-loud без `\|\| true`), `context_initializer.py`; +unit-тест (guard в conditional, порядок key→pin→alias) |
| F2 | **HIGH** | asi: dev-ключ overlay вне канона (`~/projects/asi-group/.secrets/` отсутствовал — ключ лежал в `.ai/plans/030-*/.secrets/`) → installer WARN-skip (exit 0) → алиас на ноде не установлен → clone fail «Could not resolve hostname» | **FIXED (config)** — ключ скопирован в канон 0600, fingerprint == GH `vps-asi-group-readonly`; bootstrap установил ключ+алиас+TOFU на ноду |
| F3 | **HIGH** | Post-bootstrap report перепечатывает stale error-records прошлых прогонов без маркеров («Failed: Phase deploy_update failed...» в успешном отчёте) → ~90 мин ложной форензики на tronyx + фантомная гипотеза ci-deploy на asi (опровергнута фактом: клон прошёл, `/opt/<ctx>/platform` @ HEAD) | **FIXED** — `lifecycle/cli.py`: stale-records вне scope текущего режима → секция «Stale previous-run records» с аннотацией `[STALE previous-run · phase status]`; JSON → parallel field; +2 теста (в т.ч. anti-survivorship) |
| F4 | **MED** | Converge R4: deployed-проект с 0 контейнеров → INFO-only, rc=0 «FULLY CONVERGED» над отсутствующим контейнером (silent rc=0 над пустым результатом; RC2-класс) — на обеих нодах дриллом | **FIXED** — `converge/networks.py`: report warn + logger.warning IMP:8 при deployed∧0-контейнеров (deployed-gate через `is_stub_ai_platform_yaml` — R3-консистентно; exit code не меняется — reconcile = deploy-project канал); TRAP[BUG] inline; +3 теста |
| F5 | MED | asi FINDING-p3-2: converge cert-unit каждый прогон churnит неиспользуемый self-signed roadmap.asiteam.ru (S3 ssl-cache держит invalid self-signed; vhost серверит wildcard asiteam.ru) — регенерация + acme-попытка + TG-алерт впустую | **RECORDED** (debt) — кандидат: почистить S3-запись roadmap.asiteam.ru / научить cert-unit wildcard-coverage (helper `cert_covers_domain` уже есть) |
| F6 | LOW | asi FINDING-p3-3: converge reloads nginx внутри cert-unit до R6 fail → mid-run vhost downtime window во время дрилла | **RECORDED** (debt) — reorder: reload после всех R-units |
| F7 | LOW | asi FINDING-p3-1: bootstrap no-op re-delivers roadmap (delivered=1) — liveness probe rc=1 трактуется как not-live | **RECORDED** (debt) — fail-safe направление; перепроверить probe-семантику |
| F8 | INFO | tronyx-vps за ночь потерял named volumes (postgres/grafana/loki data) + проектные payload'ы (все 4 = GENERATED-STUB) при живых bind-dirs — паттерн docker-level wipe/re-provision | **HANDLED** — канон восстановления (cold bootstrap + deploy-project ×3, как F8/F16 вчера); данных проектов нет (статические сайты), platform-DB пустая — как после вчера́шнего re-provision |
| F9 | INFO | `make status NODE=` — local-only, NODE молча игнорируется (makefiles/modules.mk) — пустая таблица ≠ состояние ноды | **RECORDED** — node-факт берётся из project-status NODE= / e2e-verify; кандидат на честный fail-loud или remote-режим |
| F10 | INFO | secrets.env на ноде: 2 malformed строки парсятся как ключ «data» (без утечки значений) | **RECORDED** — кандидат: parser warning |

## Blocked

Ни одного. (Планировавшийся блокер asi — F14/F15 legacy-layout из вчера — закрыт планом 030: overlay
`asi-group/asi-group-overlay` создан и склонирован на ноду, identity `roadmap` — подтверждено live.)

## Timeline (сжато, UTC)

- 12:22 старт; чтение источников (028/029/030, AGENTS.md, node.yaml, runs.jsonl) — план 030 уже реализован (кроме node-side)
- 12:35 Q-раунд: Q1–Q5 — все Recommended подтверждены
- 12:40 fresh journal (вчерашний → `files/run-2026-09-02/`); P0: git чист, `agent-check` 0, `make check` PASS
- 12:50 P1: validate-node-input PASS ×2; обнаружен RC4-trap (env AGE_SECRET_KEY) → все прогоны через `env -u`
- 13:10 P2 attempt 1 (2 параллельных субагента): **NOT GREEN** — обе ноды упали на overlay-clone (host-key / no-alias)
- 14:20 диагностика: F1 (known_hosts gap, код) + F2 (asi ключ вне канона) — верифицированы ssh read-only + сверкой GH fingerprints
- 15:00 фикс F2 (config) + F1 (coder: TOFU-pin, make check GREEN) → P2 attempt 2: **GREEN обе ноды** (final-verify 4/4,
  e2e 3/3 ×2, 4 проекта healthy)
- 16:00 P3: no-op bootstrap ×2 + дриллы (container/cert/vhost) — heal-or-fail-loud, nodes restored green; findings F4–F7
- 16:45 фикс-волна 2 (coder: F3 stale-records + F4 R4-warn) — make check GREEN 20/20, agent-check 0
- 17:20 финальная волна: node-update ×2 (доставка фиксов на ноды, 5/5 фаз) + e2e-verify PASS ×2 + agent-check 0

## Артефакты

- Журнал: `.ai/plans/029-deploy-integrity/execution-journal.md` (append-only, все шаги обеих нод)
- Машино-состояние: `.ai/plans/029-deploy-integrity/execution-state.json` (все фазы terminal)
- Вчерашний прогон: `.ai/plans/029-deploy-integrity/files/run-2026-09-02/`
- Незакоммиченные фиксы: `core/internal/scaffold/context_initializer.py` (F1) ·
  `core/internal/bootstrap/lifecycle/cli.py` (F3) · `core/internal/bootstrap/converge/networks.py` (F4) ·
  `tests/unit/test_{context_initializer,converge_networks,post_bootstrap_report}.py` — на нодах уже доставлены
  (node-update), в git ждут коммита владельца (`fix(029/030): overlay known_hosts TOFU + report stale-records + R4 container warn`)
