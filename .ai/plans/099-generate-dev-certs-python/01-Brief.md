# $ARTIFACT_CONTRACT
## @PURPOSE Миграция generate-dev-certs.sh (295 LOC) → Python-модуль + тонкий shell-фасад (~50 LOC)
## @DESCRIPTION
Скрипт `core/modules/nginx/generate-dev-certs.sh` (295 строк) — полностью самодостаточный домен:
5 изолированных функций без зависимостей от других shell-скриптов. Идеальный кандидат для Strangler-Fig.

Функции для миграции:
- `required_sans()` — построение required SAN set
- `get_cert_sans()` — парсинг SAN из PEM-сертификата (openssl x509)
- `cert_is_current()` — идемпотентность: literal SAN ⊇ required AND not expiring
- `generate_mkcert()` — генерация через mkcert
- `generate_openssl()` — генерация через openssl (heredoc config → tempfile)
- `verify_san()` — пост-генерационная верификация SAN
- `main()` — оркестрация: check idempotency → select backend → generate → verify

Shell-фасад оставляет: env-var defaults, вызов python3 + exit code.
## @RATIONALE
- Максимальный эффект (295→50 LOC, −83%)
- Самодостаточный домен — минимальный риск регрессии
- Закрывает последний «толстый» скрипт в `core/modules/`
- Без зависимостей от lib/ скриптов (только openssl/mkcert как external dependencies)
## @ACCEPTANCE_CRITERIA
- AC1: Python-модуль `core/modules/nginx/dev_cert_generator.py` с 6 функциями + main()
- AC2: Shell-фасад `generate-dev-certs.sh` ≤ 50 LOC (env vars + вызов python3)
- AC3: `make dev-certs` проходит без изменений в поведении
- AC4: Unit-тесты на cert_is_current (SAN match/mismatch, expiry), verify_san, required_sans
- AC5: Интеграционный тест: generate → verify → idempotent no-op
- AC6: Все существующие GREP_SUMMARY/STRUCTURE/TRAP сохранены
- AC7: `make gate MODE=fast` зелёный
## @IMPLEMENTS Brief 099
## @IMPACTS core/modules/nginx/generate-dev-certs.sh, core/modules/nginx/dev_cert_generator.py (NEW), tests/unit/test_dev_cert_generator.py (NEW), core/entrypoint-manifest.yaml
## @REQUIRES Ничего — полностью самодостаточный домен
