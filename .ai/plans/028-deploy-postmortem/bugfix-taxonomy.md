# Bugfix Taxonomy — каталог классов исправлений 2026-08-25 → 09-02

База: 154 non-merge коммита, ~90 fix-класса (72 `fix/*` + ~18 fix-несущих `feat/*`). Файлы 011/014/017/018/020/022 восстановлены из git. `*` = двойная атрибуция.

## Каталог

| # | Категория | Кол-во | Компоненты | Коммиты (примеры) | Повторялся | Критичность |
|---|---|---|---|---|---|---|
| 1 | **Silent failure / ложный успех / честность статусов** | 16 | vhost-render, ssl-provision, cert-orchestrator, restore, e2e-verify, s3-cache, strict-init, core-deliverer, http_probe | 6f08f9e, 8f315a4, 308cbef, 848576a, 5aa2ea1, ee73d74, 537d205, 61b942f, 7f1bc53 | **Да** — ложный успех ×3 в трёх компонентах трёх кампаний | **P0/P1** |
| 2 | **Тест-контракт дрейф** | 13 | redis/loki smoke, TLS-metrics, file_sd, chaos-предикаты, CertResult fakes | c4d1a9b, 34bb081, 7973d65, 4928285, 40c0966, a23e861 | **Да** — платформа менялась, смоук не догонял; красный platform-test 2.5 недели | P1/P2 |
| 3 | **CI workflow** | 10 | deploy-project.yml, platform-test, gitleaks, dispatch, runner-disk | 2419325, acf4b97, 64c2090, fa30c22, e5d76fa, 688055c, 34c9028, b955149 | **Да** — каждый подкласс ×2 (пины ×3) | **P0/P1** |
| 4 | **Secrets/AGE-цепочка** | 6+5 диагн. | decrypt_secrets, platform-secrets.service, node_detect, prelude | fde3fe8, 9b8a6af, 9ef5db9, d1337ab, b3b3100, 41ddd6c | **Да** — ×3 пути исполнения | **P0** |
| 5 | **Readiness/стартовые зависимости** | 6 | langfuse compose, module.yaml#depends_on, init-services, loki /ready, hermes warmup | 64fe57d, 86987a9, ecb6114, e1f5ee7, 34bb081, 7973d65 | Да — compose → module.yaml → probe-подкласс | P0/P1 |
| 6 | **Bootstrap-порядок / chicken-egg** | 6 | vhost_renderer ↔ payload-delivery, R-ssl↔R6, re-exec, python-deps | 7da4914, e0d0e09, 19b0949, 76a95e3, 379fd01 | **Да** — 017 F-04 → 027 F-01, тот же класс | **P0** |
| 7 | **Race/env-загрязнение тестов** | 8 | NODE_NAME-снапшот, tls-gauges, reset_state, AGE-утечка в логах | abeceb7, 01d0339, f510db5, baa748d | **Да** — ×4 сессии; системного garde нет | P2 |
| 8 | **Hardcoded внешняя реальность** | 8 | docker 29 API, OOM vs 3G, zram, apt-check Ubuntu 24.04, gitleaks asset, alloy flag | 0260235, c5b525e, 36d292e, be280b8, 728219b | Да — 8 инстансов, паттерн один | P1/P2 |
| 9 | **Parsing/protocol/CLI** | 6 | dispatch shlex, server_name comments, psql latch, ssl-shadow, --name | b955149, 3bebd02, 308cbef | Нет в пределах одного парсера; рассеянный | P0/P1 |
| 10 | **Env/context-проброс** (NODE_NAME-класс) | 5 | healthcheck, converge self-env, platform-secrets, provides-фильтр | e921910, b3b3100, 4236960 | **Да** — NODE_NAME ×4 места | P1 |
| 11 | **Volumes/permissions/ACL** | 5 | /run/lock, audit.jsonl ×2, umask 0700, R7 prefix | d8d885a, 7f3a829, d7174aa, bebd9fb | Да — audit ×2, umask ×2 | P1 |
| 12 | **Idempotency/converge-drift** | 6 | R9 ×2, honest no-op, init-recreate, rollback-контур | 33a633d, 848576a, 6094933, ecb6114, 269a30b | **Да** — R9 ×2 | P1 |
| 13 | **Restore/DR-пайплайн** | 3 | restore_psql.sh 5 дефектов, self-role latch | 5e34401, 308bef→308cbef, 16c4097 | Да — GREEN→P0 за сутки (false-green из env-формы) | **P0** |
| 14 | **Observability-инструментация** | 5 | AGE-digest phi4 (одно расследование) | 41ddd6c, 3358f98, 9852633, 081ffe6, fc515c1 | Нет | P1 |
| 15 | **Timeout/retry/флейк** | 4 | settle-retry, hermes 3×10s, best-of-3, атомарность | 244b351, 7973d65, 3273d5a, 7844a17 | Да | P2 |
| 16 | **Docs/contract-дрейф** | 4 | doxygen, REF-sync, сигнатуры | 9309753, c7dbc7b | Слабо | P2 |
| 17 | **Проактивные quality-волны** (не деплой) | 8 | ai-code-fixes T1-T19 | b0b883d…08b0174 | Нет | P2 |
| 18 | **Dev-инфра/эргономика** | 4 | pyright reaper, .pyc, scan-root, adopter | a8ec907, ad44991, e649263 | Частично | P2 |

## Топ-5 категорий

1. **Silent failure (16, повтор ×3+)** — единственный класс, проявлявшийся во всех средах (нода/CI/DR): каждый новый компонент изначально маскировал отказ; три независимых ложных успеха (cert 017 F-06 → vhost 020 F-06 → ssl 027 F-10).
2. **Тест-дрейф (13)** — единственная категория с длительным unnoticed-красным (platform-test 08-17→09-02).
3. **CI workflow (10)** — каждый подкласс повторился ровно дважды; runner-контекст невоспроизводим локально.
4. **Race/env-загрязнение (8)** — воспроизводимый xdist-класс, каждый фикс точечный.
5. **Hardcoded-допущения (8)** — тест/скрипт кодифицирует внешнюю реальность (версии/лимиты/kernel/ассеты), которая меняется под ним.

## Системные vs эпизодические

**Системные (≥3 инстансов или повтор после фикса):** silent failure, тест-дрейф, CI (все подклассы ×2), env-загрязнение, hardcoded, secrets/AGE (×3 пути), readiness (compose→module.yaml→probe), idempotency (R9 ×2), bootstrap-порядок (×2 кампании), env-проброс (NODE_NAME ×4), permissions (×2+×2).

**Эпизодические:** workflow parse error, /run/lock, alloy-флаг, R7 prefix FP, argv re-exec, phantom services, --name parity, ssl-shadow.

**Самые дорогие по ущербу (не по частоте):** Secrets/AGE (P0, ×3 повторов) · Restore false-green→P0 за сутки (308cbef) · Bootstrap chicken-egg между кампаниями (017 F-04 → 027 F-01) · CI-чейн D5 (3 P0 подряд убили весь канал).

## Инцидент-агрегация (правило §7: коммиты ≠ проблемы)

```text
~90 fix-коммитов
   ↓
~50 уникальных находок (7 холодных попыток × 2–14 находок)
   ↓
18 категорий ошибок
   ↓
6 системных причин (RC1–RC6, см. root-causes.md)
```

Примеры схлопывания: 16 «silent»-фиксов = 1 причина (отсутствие post-condition контракта) · 10 CI-фиксов = 2 причины (runner-контекст невоспроизводим + нет required-check) · 15 secrets-фиксов = 2 причины (форма входа не верифицируется + fail-loud без контекстной семантики).