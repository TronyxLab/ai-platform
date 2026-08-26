<!-- GREP_SUMMARY: launch-validation verification report фазы A-H находки вердикт tronyx-vps план 014 -->
<!-- STRUCTURE: ▶ вердикт → ⊕ таблица фаз → ⚡ сводка находок P0/P1/P2 → ⎋ черновик DevPlan -->

# 02-VerificationReport — launch-validation tronyx-vps (план 014)

Дата: 2026-08-26 21:06–23:15 МСК · Нода: tronyx-vps (103.88.243.151, Ubuntu 24.04,
пересоздана SC2 оператором) · Ветка main @ 6fff904 → фикс-коммит `fde3fe8`.
Повторная приёмо-сдаточная валидация после прогона 011 (F-001..F-037) и фикс-планов
012/013/017.

## Вердикт

**PARTIAL — bootstrap green, остальные фазы не достигнуты из-за кластера регрессий plan 012.**

Главный результат: полный цикл «голое железо → bootstrap → converge» пройден ЗЕЛЁНЫМ
после исправления **5 блокирующих регрессий** плана 012 (см. таблицу фиксов). Все 5
зафиксированы одним коммитом `fde3fe8` (39+/13−, pre-commit green).

Фазы C–H НЕ пройдены: C2 (cache drill) заблокирован F-08 (S3-кеш сертификатов мёртв —
boto3 не доставлен), D5 (CI-канал) — GitHub Billing org TronyxLab (действие владельца,
прецедент 011), G5 (test-node) — test-VPS недоступна. Остальное — не хватило окна сессии.

## Таблица фаз

| Фаза | Проверка | Вердикт | Evidence |
|------|----------|---------|----------|
| A1 | `make check` до чистоты | **PARTIAL** | 3 отказа: F-01 (schema-drift, FIXED), F-02 (pyright timeout — инфра-флак), F-03 (benchmark xdist-флак, закрыт изоляцией) |
| A2 | `make agent-check` | **PASS** | blocking=0 advisory=0 |
| A3 | `check MARKER=check-manifests` | **PASS** | GREEN (all generated up to date) |
| A4 | Локальный стек status/healthcheck | **PASS** | reuse 26 контейнеров, ALL MODULES HEALTHY; `down` пропущен (общий стек) |
| B1 | `secrets-unlock NODE=tronyx-vps` | **PASS** | после F-05-fix: 58 ключей + 1 ci_default inject |
| B2 | Холодный bootstrap | **PASS** | rc=0 «All 9 init phases», 22 контейнера (21 healthy); после F-05+F-07-fix |
| B3 | Идемпотентность | **PASS** | третий прогон 22.5s, 8 фаз «already done — skipping» |
| B4 | converge + check-security | **PASS** | converge GREEN (R6 nginx -t, R7 volumes); check-security 8 PASS + 1 WARN (S2 apt-check) |
| B5 | project-list/status | **PARTIAL** | dev-сканер 0 node.yaml (F-11); node-side проекты reconciled (R3) |
| C | TLS + cache drill | **BLOCKED** | C2 блокирован F-08 (S3-кеш мёртв); wildcard *.tronyx.ru выпущен и валиден |
| D | deploy-context + каналы | **NOT RUN** | deploy_context: deployed=0 (5 проектов ждут CI payload); D5 — GH Billing |
| E | вариации модулей + node-update | **NOT RUN** | — |
| F | бэкап/restore/age-key | **NOT RUN** | — |
| G | reboot/chaos/load/e2e | **NOT RUN** | — |
| H | Release checklist | **NOT RUN** | п.3 локальный check PARTIAL (pyright флак); остальное не достигнуто |

## Фиксы сессии (коммит `fde3fe8`)

| # | Находка | Слой | Фикс |
|---|---------|------|------|
| F-01 | node.yaml dead-поле `postgres_init_databases` (schema-drift после plan 17) | node-configs (gitignored, локально+нода) | удалено из 2 node.yaml |
| F-05 | D3 fail-loud в `decrypt_secrets` без source-фильтра → блокировал secrets-unlock И φ4 | P0 | `source == "sops"` фильтр + R5-негатив |
| F-07 | `_interpolation_dryrun` isolated `-f <module>` → «undefined volume» → блокировал φ8 | P1 | `build_compose_args` (root-compose-first) |
| F-07-class | converge/volumes.py R7 — тот же isolated `-f` | P1 | `build_compose_args` |
| F-10 | vhost_renderer не резолвил platform_domain из node.yaml → direct-cert путь → nginx -t FAIL | P1 | fallback `NodeYaml.get("domain")` |

## Сводка открытых находок (вход для DevPlan)

### P1
| # | Находка | Fix-направление |
|---|---------|-----------------|
| F-08 | S3-кеш сертификатов мёртв на свежей ноде: `s3_ssl_cache module not loaded — S3 restore unavailable` (boto3 не доставлен; F-019 из 011 НЕ закрыт plan 012) | доставка boto3/python_deps в φ1–φ3 + marker-invalidation при missing-import; рантайм-проверка C2 после |

### P2
| # | Находка | Fix-направление |
|---|---------|-----------------|
| F-09 | status-page unhealthy (healthz 503, metrics 200) | диагностика readiness-коллектора |
| F-11 | project-list/status сканер ищет node.yaml под repo-root, а не NODE_CONFIGS_DIR | scan-root → NODE_CONFIGS_DIR (dev/bare-NODE) |
| F-06 | утёкший basedpyright-орфан (209 мин CPU) при timeout check-suite 120s | kill process-group по timeout / reaper орфанов |
| F-02 | pyright full-repo >120s timeout на dev (известный TRAP[DEBT]) | поднять timeout ИЛИ changed-files scope для gate |
| F-07-followup | стейл .pyc на живой ноде при инкрементальной доставке (rsync -t) | touch/invalidate .pyc при core-deliver |
| F-10-test | platform_domain-резолв не покрыт unit-тестом | тест CLI fallback node.yaml#domain |

### Инфраструктура владельца (BLOCKED, вне кода)
- **D5** CI-канал — GitHub Billing org TronyxLab (оплатить/поднять лимит, затем повторить).
- **G5/H1** test-node — test-VPS недоступна (повтор до прода).
- **G2** chaos FULL — выделенное ночное окно (техдолг из 011).

## Черновик DevPlan (фиксы следующей волны)

1. **F-08 (P1)** — S3-кеш: `python_deps.py` path-fix (requirements в корне core-dir),
   доставка boto3 в φ1–φ3, marker-invalidation при `ModuleNotFoundError` probe; тест
   «свежая нода → s3_ssl_cache доступен». После — rerun C2 cache drill.
2. **F-11** — project-list/status scan-root → NODE_CONFIGS_DIR (dev-режим).
3. **F-09** — status-page readiness: выяснить, какой коллектор роняет /healthz на ноде.
4. **F-06/F-02** — pyright: reaper орфанов + timeout/sсope пересмотр.
5. **F-07-followup** — .pyc инвалидация при core-deliver (rsync -t класс).
6. **F-10-test** — unit-тест platform_domain-резолва в vhost_cli.

После фиксов — rerun фаз C–H по процедуре release-checklist (с операторскими гейтами D5/G5).

## Согласованность ноды (рантайм-сверка открытых пунктов QA)

- **C1 (грязное дерево)** — на старте дерево чистое @ 6fff904; check-manifests GREEN.
- **C4 (provisioner transient)** — НЕ проверялся рантаймом в этой сессии (D7 не достигнут);
  рантайм-подтверждение из 011 (F-021) остаётся актуальным.
- **C6 (AGE_RECIPIENT)** — не перепроверялся (F1/F4 не достигнуты).
- **F-037 (reboot P0)** — НЕ перепроверялся (G1 не достигнут); код-фикс генератора юнита
  platform-secrets был в plan 012 wave 1 — верифицировать при G1.
