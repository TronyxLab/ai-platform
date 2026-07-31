# 02-Brief — B2: Генераторный контур и паритет-гейты

<!-- GREP_SUMMARY: generators parity-gates COMPOSE_PROFILES MINIO_PORT PLATFORM_DOMAIN scan_compose_ports sync_env_defaults -->
<!-- STRUCTURE: ┌scope┐ → ◇ фиксы генераторов → ◇ parity-гейты → ⊕ критерии → ⎋ зависимости -->
# region MODULE_CONTRACT
## @purpose  Волна B2: устранить слепые зоны генераторной цепочки (инвариант 11) и поставить parity-гейты на все ручные копии значений.
## @scope    U-01, U-02, U-16, U-17, U-33, U-43, U-44, U-47, U-59, U-68
## @invariants
##   - Генерируемые файлы не редактируются вручную (инвариант 11); parity-гейты RED-ят расхождения копий.
##   - «Комментарии 12 vs 13» устраняются генерацией, не ручной правкой.
# endregion MODULE_CONTRACT

$ARTIFACT_CONTRACT:
  PURPOSE: Починить генераторную цепочку env/профилей/шаблонов и сделать расхождения значений структурно невозможными.
  DESCRIPTION: Фикс scan_compose_ports (MINIO_PORT), консолидация COMPOSE_PROFILES в единственный SoT, устранение PLATFORM_DOMAIN/test.local противоречий, регенерация secrets-manifest, расширение generate-manifests, template-manifest покрытие, выравнивание discover_modules-предикатов.
  RATIONALE: Генераторная цепочка — лучший engineered-механизм платформы, но имеет слепые зоны: значения-хардкоды в генераторах и 3-8 ручных копий вне гейтов. Это эталонный источник дрейфа (U-01 — «gate зелёный, система врёт»).
  ACCEPTANCE_CRITERIA: (1) platform-env.yaml: port_mappings.MINIO_PORT == env_defaults.MINIO_PORT == 9000; (2) ровно один SoT COMPOSE_PROFILES, все копии генерируются; (3) PLATFORM_DOMAIN — одно определение, test.local/admin@test.local удалены; (4) secrets-manifest.yaml регенерирован и совпадает с secret-definitions.yaml (gate); (5) generate-manifests покрывает все генераторы (G1-G6), fix-gate чинит check-manifests; (6) template-manifest регистрирует все *.template (nginx, tor, dev-config); (7) discover_modules/module_discovery — один предикат; (8) комментарии «12 модулей» устранены.
  IMPLEMENTS: U-01 (MINIO_PORT), U-02 (COMPOSE_PROFILES), U-16 (PLATFORM_DOMAIN), U-17 (env-цепочка), U-33 (secrets-парсеры), U-43 (secrets stale), U-44 (generate-manifests), U-47 (template-manifest), U-59 (discover_modules), U-68 («12 модулей»)
  IMPACTS: core/internal/scripts/generate_platform_env.py, sync_env_defaults.py, core/templates/template-manifest.yaml, Makefile/makefiles, .env.example, tests/gates/*
  REQUIRES: Решения 01-Brief (гейты с allowlist); архитектор утверждает формат parity-гейтов

---

## Scope (U-проблемы)

| U | Проблема | Ключевые файлы |
|---|----------|----------------|
| U-01 | MINIO_PORT 9001/9000; баг scan_compose_ports (второй порт затирает первый при service==module) | generate_platform_env.py:227,278-288; minio/docker-compose.base.yml:36-37 |
| U-02 | COMPOSE_PROFILES ×8 копий, 1 расходится (12-item без status-page) | Makefile:30, helpers.mk:90, platform-infra.yaml:234, project_adopter.py:75, docker_orchestrator.py:514, sync_env_defaults.py:461 |
| U-16 | PLATFORM_DOMAIN ×4 + test.local + PLATFORM_MASTER_EMAIL=admin@test.local | sync_env_defaults.py:176, helpers.mk:40-41, smoke.py:95, .env.example:48-49,65 |
| U-17 | Env-цепочка: AWS-ключи хардкодом в генераторе; smoke.py статик перекрыт generated | sync_env_defaults.py:280-281, smoke.py:94-125 |
| U-33 | secrets-manifest: 3 парсера + hardcoded fallback | secrets_manager.py:201, secrets_validator.py:62,146 |
| U-43 | secrets-manifest не регенерирован (25.07 vs 30.07) | core/secrets-manifest.yaml, core/secret-definitions.yaml |
| U-44 | generate-manifests G2/G4/G5 не покрыты; fix-gate не чинит | makefiles/manifest.mk:34 |
| U-47 | template-manifest: nginx монтирует 9 шаблонов, зарегистрированы 2; tor/dev-config вне | template-manifest.yaml, nginx/docker-compose.base.yml, bootstrap/tor/*.template |
| U-59 | discover_modules: предикаты разошлись (substring vs exact YAML) | bootstrap/discover_modules.py:124-139, scripts/module_discovery.py:49-71 |
| U-68 | «12 docker modules» / «Все 12 профилей» при 13 | docker-compose.yml:2, sync_env_defaults.py:456, .env.example:254 |

## Ключевые артефакты

1. Фикс `scan_compose_ports`: счётчик портов инкрементируется для ПЕРВОГО порта сервиса (включая service==module); регенерация platform-env.yaml.
2. Единый SoT COMPOSE_PROFILES (platform-infra.yaml), остальные 7 мест — generated или вычисление runtime; parity-гейт `check-profiles-parity`.
3. Устранение PLATFORM_DOMAIN: smoke.py берёт из generated; удаление test.local-артефактов; гейт на паритет домена.
4. secrets-manifest: регенерация + гейт свежести (дата manifest >= дата defs); консолидация 3 парсеров в один shared.
5. generate-manifests: покрытие всех генераторов; fix-gate включает check-manifests.
6. template-manifest: регистрация nginx vhost/dev-config/tor шаблонов; templates-check покрывает их.
7. discover_modules: единый предикат (exact YAML + compose-check), оба потребителя используют его.

## Гейт самоверификации волны

- `check-manifests` + новые parity-гейты (profiles/domain/env-секции) зелёные на CI.
- Гейт «копий нет»: rg по профильной строке даёт ровно 1 SoT + generated.

## Зависимости

- От: 01-Brief (решение о гейтах с allowlist).
- К: все последующие волны (паритет-гейты делают их проверяемыми).
