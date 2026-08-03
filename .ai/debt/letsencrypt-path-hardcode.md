# Letsencrypt path hardcode — DEBT (DevPlan 119 C6)

> Создан: 2026-08-02 | DevPlan 119 C6 (AUDIT-4 T7)

## Суть

`/etc/letsencrypt/live/<domain>/` хардкодится в нескольких файлах, хотя
`core/internal/shared/deploy_paths.py::letsencrypt_live()` (DevPlan 118 C7) — единый
резолвер пути. `cert_orchestrator.py` уже мигрирован (C7, 118); остаются:

- `core/internal/scaffold/vhost_renderer.py` (~L336-337) — `ssl_certificate /etc/letsencrypt/live/{cert_domain}/...`
- `core/internal/scaffold/nginx_harness.py` (~L187/192) — regex `r"/etc/letsencrypt/live/[^/]*/(fullchain|privkey)\.pem"`

## Наблюдение (Observed)

grep 2026-08-02: `letsencrypt/live` встречается в vhost_renderer.py (2 места) и
nginx_harness.py (2 места) как литерал пути, минуя `letsencrypt_live()`.

## Гипотеза (Suspected)

Миграция 118 C7 покрыла топ-потребителей (cert_orchestrator, s3_ssl_cache и др.),
но scaffold-генераторы vhost-конфигов остались на литералах.

## Влияние (Impact)

При смене корня letsencrypt (например, тестовые пути, контейнеризация nginx) —
тихий рассинхрон путей; единый резолвер деградирует до «ещё одной копии».

## Действие

TRAP[DEBT] добавлен на оба файла. Миграция на `letsencrypt_live()` — при касании
этих файлов (или плановая волна path-unification).

| Status | Rev |
|--------|-----|
| OPEN | При касании vhost_renderer.py / nginx_harness.py |

## FIXED (RC-сессия 2026-08-03, долг 119 C6)

Оба файла мигрированы на letsencrypt_live() (shared/deploy_paths):
- `vhost_renderer.py::generate_vhost_body` — ssl_certificate/ssl_certificate_key через le_live
- `nginx_harness.py` — regex swap через re.escape(str(letsencrypt_live()))
Прод-дефолт не меняется (LETSENCRYPT_LIVE не задан); 48 unit-тестов PASS.
Status: FIXED | Rev: 2026-08-03
