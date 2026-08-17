# DevPlan 004 — SAN-aware валидация S3 SSL-кеша (устранение ложных cache-miss → пере-выпусков LE)

$ARTIFACT_CONTRACT:
  PURPOSE:       Устранить ложные cache-miss в S3 SSL-кеше: валидный LE-сертификат
                 отвергается на restore-пути (check_cert/download_cert), потому что
                 домен-матчинг смотрит на subject-CN, а современные LE-сертификаты —
                 SAN-only (subject пуст). Результат — ре-выпуск сертификатов при каждом
                 чистом бутстрапе вместо restore из кеша (risk: LE rate-limit 50/домен/нед).
  DESCRIPTION:   (1) SAN-aware домен-матчинг в shared/ssl_certs (SAN primary, CN fallback,
                 wildcard как в verify_sweep.tls_check); (2) покрытие формат-вариантов CN
                 RFC2253; (3) unit-тесты с РЕАЛЬНЫМИ openssl-сертификатами (SAN-only,
                 empty-subject, wildcard, RFC2253); (4) контрактный тест restore-first
                 «SAN-only cert из S3 → restored, issue НЕ вызывается»; (5) фильтр
                 доменного списка (wildcard-покрытие) как отдельная волна-2.
  RATIONALE:     Корень — в cert_is_valid(domain-match): CertResult-семантика «валиден»
                 консолидирована (C9) в один SoT, поэтому фикс точечный: один модуль,
                 один матчинг. Тесты на моках НЕ ловили класс (SAN-only subject-пустые
                 сертификаты в fixture'ах отсутствовали) — реальные openssl-сертификаты
                 в тестах обязательны (TRAP[TEST] ниже).
  ACCEPTANCE_CRITERIA:
    - AC1: openssl x509 SAN-only LE-style сертификат (subject пуст, SAN=domain) проходит
      cert_is_valid(expected_domains=domain) → True (unit, реальный openssl).
    - AC2: Wildcard SAN (*.example.com) покрывает example.com и app.example.com → True;
      посторонний домен → False (unit).
    - AC3: CN-fallback сохранён: CN=domain (все формат-варианты, включая RFC2253
      "CN=example.com" без пробела) → True; CN посторонний → False (unit).
    - AC4: Контракт restore-first: fake-S3 fullchain SAN-only + валидный →
      orchestrate_certs → status='restored', source='s3', issue-cert НЕ вызывался (unit).
    - AC5: Повторный bootstrap test-ноды (state.json сброшен, диск чист, S3-кеш заселён)
      → restored > 0, issued == 0; в логах НЕТ "failed validation"/"Cert_Unauthorized" (E2E).
    - AC6: make check (suite static_audit + component + gates) — зелёный; make agent-check — 0.
  IMPLEMENTS:    Диагноз 2026-08-16 (два тестовых бутстрапа: сертификаты выпускались заново
                 вместо взятия из S3-кеша).
  IMPACTS:       core/internal/shared/ssl_certs.py; tests/unit/test_ssl_certs.py;
                 tests/unit/test_cert_orchestrator_contract.py;
                 (W2, опционально) core/internal/bootstrap/lifecycle/helpers/domains.py,
                 deploy/context_deployer.extract_domains_for_context.
  REQUIRES:      Ничего внешнего. W1 не зависит от W2.

# region W0 · Подтверждение диагноза (gate, без правок кода)

**Цель:** подтвердить (или опровергнуть) primary-гипотезу на живом материале ДО фикса.
Ранее аналогичный сценарий уже приводил к ложной диагностике DNS-01 (TRAP в bootstrap/AGENTS.md) —
этот шаг обязателен.

T0.1 На ноде (любой из двух тестовых): для КАЖДОГО домена из node.yaml
    ```
    openssl x509 -in /etc/letsencrypt/live/<domain>/fullchain.pem -noout -subject -ext subjectAltName
    ```
    Ожидание при подтверждении: `subject=` (пусто) либо subject без CN домена, домен — в SAN.
T0.2 Лог бутстрапа (φ7/φ12 последнего тестового бутстрапа): grep
    `"failed validation"`, `"S3 cache miss"`, `"cert restored from S3"`, `"issuing via acme.sh"`.
    Подтверждение: «Cached cert ... failed validation» → «S3 miss, falling back to issue» → issue.
T0.3 (быстрая проверка гипотезы на dev-машине, без ноды): скачать fullchain из S3
    (aws s3 cp / boto3 head) и прогнать
    `python3 -c`-эквивалент cert_subject_matches_domain — убедиться, что матчинг False на реальном сертификате.

Критерий перехода к W1: подтверждён SAN-only/пустой-subject хотя бы для одного домена
ИЛИ (если неожиданно subject содержит CN) — T0.4: сравнить openssl-формат вывода на ноде
(`/etc/ssl/openssl.cnf` — имя_раздела/RFC2253 флаги) с тестами; зафиксировать фактический
формат вывода `openssl x509 -subject` и расширить паттерны по факту (fail-fast: не применять
фикс вслепую). Результат W0 зафиксировать в 02-VerificationReport.md (или обновить этот план).

# endregion W0

# region W1 · Фикс: SAN-aware домен-матчинг (SoT shared/ssl_certs.py)

**Файл:** `core/internal/shared/ssl_certs.py` (единый SoT — потребители
s3_ssl_cache/cert_orchestrator/context_deployer получают фикс автоматически; C9 не дублируется).

T1.1 **cert_get_san_list()** — новый примитив:
    `openssl x509 -in <cert> -noout -ext subjectAltName` → list[str] DNS-имён
    (нормализация: срезать `DNS:`, убрать trailing dot; IP-записи — отдельной веткой,
    референс парсинга — dev_cert_generator.get_cert_sans / verify_sweep.tls_check._extract_san_list).
    Non-fatal: ошибки/timeout → [] (канон модуля). timeout=DEFAULT_OPENSSL_TIMEOUT.

T1.2 **_cert_covers_domain(cert_path, domain, timeout)** — внутренний матчер:
    - SAN непуст → матч по SAN: точное совпадение ИЛИ wildcard `*.<parent>` покрывает domain
      (канон wildcard-матчинга — verify_sweep.tls_check._san_covers: минимально одна
      non-eTLD метка слева); SAN пуст → CN-fallback (T1.3). ⚠️ TRAP[DECISION]:
      SAN непуст + нет совпадения → False БЕЗ fallback на CN (RFC 6125: presence of SAN
      делает CN non-authoritative; CA/B Forum требует deprecate CN-матчинг при SAN).
      Rejected: CN-fallback всегда — держит баг-класс «CN совпал случайно».
      Rev: если появятся legacy-сертификаты с рассинхроном CN/SAN — пересмотреть.
    - Wildcard покрывает только ОДНУ метку слева (app.example.com да, a.b.example.com нет).

T1.3 **cert_subject_matches_domain** — расширить паттерны CN:
    существующие `CN = d`, `CN= d`, `CN=*.d`, `CN = *.d` + добавить `CN=d`, `CN=*.d`
    (RFC2253-формат без пробелов) + trailing-dot вариант. Поведение прежнее (pure string).

T1.4 **cert_is_valid** — шаг 3 (domain match) переключить на _cert_covers_domain
    (SAN primary / CN fallback). Контракт и сигнатура НЕ меняются — все DI-потребители
    (cert_validity_fn в оркестраторе) не затронуты.

T1.5 Doc-контракты: @invariants модуля + cert_is_valid docstring — отразить
    SAN-primary семантику; GREP_SUMMARY + `san`, `wildcard`.

# endregion W1

# region W2 · Тесты (реальные openssl-сертификаты, не моки)

TRAP[TEST] — моки subprocess не ловят этот класс: нужны настоящие PEM, созданные openssl
в tmp_path (Zero Hardcode). Фикстура-хелпер: генерация LE-подобного SAN-only сертификата
(`openssl req -x509 -subj "/" -addext "subjectAltName=DNS:<domain>"`) и CN-only
legacy (`-subj "/CN=<domain>"` без addext), issuer в обоих — валидировать как LE через
monkeypatch cert_get_issuer (существующий TRAP[DI-KEEP]-паттерн test_ssl_certs.py).

T2.1 tests/unit/test_ssl_certs.py — новые тесты (LDD, caplog, IMP:9):
    - test_cert_is_valid_san_only_cert: subject пуст, SAN=domain → True (AC1);
    - test_cert_is_valid_san_wildcard: SAN=*.example.com покрывает example.com и
      app.example.com, НЕ покрывает a.b.example.com и other.com (AC2);
    - test_cert_is_valid_cn_fallback: CN-only (без SAN) → True по всем форматам
      subject-вывода (AC3; параметризация строк, как сейчас — без subprocess);
    - test_cert_is_valid_san_present_no_cn_fallback: SAN=other.com + CN=example.com,
      проверяем example.com → False (TRAP[DECISION] T1.2);
    - негативный оригинальной формы (R5): кейс, который ловил баг — SAN-only →
      старый матчинг дал бы False, новый → True (assert истинного поведения нового).

T2.2 tests/unit/test_cert_orchestrator_contract.py — AC4:
    _FakeS3Cache.check_cert=True + download пишет SAN-only LE-подобный fullchain →
    orchestrate_certs → entry.status == 'restored', source == 's3', issue_script
    stub НЕ запускался (существующий паттерн fake-фактов path_isfile после download).

T2.3 Регресс-обзор: test_s3_ssl_cache.py (_validate_cert с expected_domains) —
    прогнать, зафиксировать зелёным; если fixture «fake pem content» перестал
    соответствовать контракту (мок _validate_cert=True) — НЕ трогать (мок выше матчинга).

# endregion W2

# region W3 · (Опционально, отдельный коммит) Wildcard-фильтр доменного списка

Проблема-компаньон (шум, не пере-выпуск): extract_domains_for_context отдаёт проектные
поддомены platform-domain; для них в S3 нет per-domain ключей (кеш пишется только под
platform_domain через reloadcmd upload + upload-on-skip) → каждый прогон:
S3 miss → issue_cert (внутри skip по wildcard) → upload_cert(False) → WARN-шум и
бесполезные вызовы acme.sh. Исправление — поверх W1: SAN-aware coverage-check.

T3.1 В cert_orchestrator._process_single_domain (или _issue_or_reuse): до Step 1 —
    если существующий сертификат родительского wildcard-домена (того же реестра доменов)
    покрывает domain (T1.2 матчер) → status='skipped', source='wildcard_covered',
    БЕЗ S3-обращения и issue. Инвариант: wildcard ищется только среди доменов того же
    node.yaml-списка (никакого угадывания родителя по строке).
    ⚠️ TRAP[DECISION]: семантика «covered» повторяет _log_post_issue_coverage (FL15) —
    переиспользовать тот же матчер, не дублировать.
T3.2 Тесты: два домена [example.com, app.example.com] + на диске валидный wildcard
    example.com → app.example.com skipped/wildcard_covered, issue не вызывался (unit).
T3.3 Не-цель: изменение схемы ключей S3 (per-subdomain) — не делаем.

# endregion W3

# region Волны/коммиты/верификация

| Волна | Скоуп | Коммит |
|-------|-------|--------|
| W0 | подтверждение диагноза (без правок) | — |
| W1+W2 | ssl_certs SAN-матчинг + тесты | feat(004): SAN-aware cert validation — S3 cache false-miss fix |
| W3 | wildcard-фильтр (опционально) | feat(004): wildcard-covered domain skip in cert orchestrator |

Верификация (Coder-цикл):
1. per-task: make check TEST_FILE=tests/unit/test_ssl_certs.py; make check TEST_FILE=tests/unit/test_cert_orchestrator_contract.py
2. фикс-цикл: make check (батч всех ошибок) до чистоты
3. финал: make agent-check; E2E-подтверждение AC5 — при следующем тестовом бутстрапе
   (release-checklist канона: make test-node NODE=<test> зелёный перед промоутом).

Rollback: W1/W3 — точечные правки, revert коммита не ломает совместимость
(SAN-матчинг строгий надмножество CN-матчинга для валидных LE-сертификатов).

Риски:
- R1: нестандартный openssl на ноде без -ext subjectAltName (старые LibreSSL) — маловероятно
  (Ubuntu 24.04, OpenSSL 3.x); матчер деградирует к CN-fallback при пустом SAN-выводе ошибок.
- R2: certs с SAN+CN-рассинхроном начнут отвергаться (это правильно, но заметно) —
  мониторинг verify-domains/e2e-verify после деплоя (release-checklist).
- R3: E2E AC5 требует LE rate-limit headroom — прогонять на test-ноде; выпуски на
  production-домене не трогать без необходимости.

# endregion
