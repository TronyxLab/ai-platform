# 141-server-recovery — certs-r2.md (2-й цикл)

$START_CERTS_R2

> Дата проверки: 2026-08-06 13:45 MSK (10:45Z). Метод: openssl s_client (TLS на 443, SNI) + grep фазового лога бутстрапа (logs/make/20260806-114034-test-node.log).

## 1. Сертификаты на ноде (все LE, НЕ self-signed)

| Домен | subject | issuer | notBefore | notAfter | дней осталось |
|-------|---------|--------|-----------|----------|---------------|
| tronyx.ru | CN=tronyx.ru | Let's Encrypt YE2 | 2026-08-03 10:04 GMT | 2026-11-01 10:04 GMT | 86 |
| sexydancerostov.ru | CN=sexydancerostov.ru | Let's Encrypt YE2 | 2026-07-22 11:04 GMT | 2026-10-20 11:04 GMT | 75 |
| botanika.tronyx.ru | **CN=tronyx.ru (wildcard)** | Let's Encrypt YE2 | 2026-08-03 | 2026-11-01 | 86 |
| roadmap.tronyx.ru | **CN=tronyx.ru (wildcard)** | Let's Encrypt YE2 | 2026-08-03 | 2026-11-01 | 86 |

Issuer = Let's Encrypt (YE2, актуальный промежуточный), ни один не self-signed (subject ≠ issuer). SAN tronyx.ru: `*.tronyx.ru, tronyx.ru` — покрывает botanika/roadmap (подтверждено e2e-verify san_ok=True).

## 2. «Из кеша» — подтверждение (0 новых issued через acme)

Трейс фазы certificates (test-node-r2.log → make-лог бутстрапа):

- `[IMP:9][cert_orchestrator] tronyx.ru — cert restored from S3` (строка 546)
- `[IMP:9][cert_orchestrator] sexydancerostov.ru — cert restored from S3` (576)
- `[IMP:9][cert_orchestrator] botanika.tronyx.ru — cert restored from S3` (606)
- `[IMP:9][cert_orchestrator] roadmap.tronyx.ru — cert restored from S3` (636)
- Для каждого: `s3_ssl_cache` → `Checking S3 cache for <domain>` → `Downloaded fullchain/privkey/chain/account.tar.gz` → `Cert validated OK (LE, domain match, expiry OK)` → `Cert download complete`.
- acme.sh: только `install_acme` + cronjob (450-451, 637-639); **вызовов `acme.sh --issue` НЕТ** → выпуск не происходил, все 4 домена + account.tar.gz восстановлены из S3-кеша (ключи s3-cert-keys-r2.txt: 4 домена × fullchain/chain/privkey + account.tar.gz).

## 3. Наблюдение (не баг): overlay ссылается на wildcard tronyx.ru

- node-configs/tronyx-vps/overlays/nginx/botanika.tronyx.ru.conf и roadmap.tronyx.ru.conf → `ssl_certificate /etc/letsencrypt/live/tronyx.ru/fullchain.pem` (wildcard).
- Собственные серты botanika/roadmap (выпущены 1-м циклом 06.08, загружены в S3) восстановлены в `/etc/letsencrypt/live/<domain>/`, но nginx их не использует.
- Последствие: e2e-verify TLS ok (wildcard SAN покрывает), дни считаются от серта tronyx.ru (86).
- Рекомендация: если нужны выделенные серты поддоменов — поправить overlay (или оставить wildcard как есть; функционально валидно).

$END_CERTS_R2
