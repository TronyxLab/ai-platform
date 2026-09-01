# 01-Findings — launch-validation asi-team-vps (повторная полная приёмо-сдаточная)

$ARTIFACT_CONTRACT
@purpose: Полная приёмо-сдаточная валидация после крупного рефакторинга. Критерий: голая нода + `make bootstrap-node NODE=asi-team-vps` = сервер + ВСЕ проекты контекста одной командой.
@mode: чинить до победного; каждый фикс → ре-верификация → коммит → push.
@verdict_scope: PASS/FAIL/BLOCKED по фазам A–H + вердикт ПРОМОУТ (промоут выполняет основная модель).
@base: origin/main 2526b39 (merged 020/021: a9937d8, a823dc6, 19b0949, 0b9a485).
@worktree: /Users/tronyx/projects/ai-platform-worktrees/launch-validation-asi-team-vps, ветка launch-validation/asi-team-vps.
@prior: план 020 (критерий был PROVEN, PARTIAL из-за C2-cache/F/G-блокеров) + 021 merge-review (6/6 фиксов accepted). Эта сессия = повторный ХОЛОДНЫЙ прогон на пересозданной ноде + закрытие open-пунктов.

## 0a. Ответы владельца (2026-09-01)
1. Нода asi-team-vps: ГОЛАЯ → холодный bootstrap.
2. Freeze: СНЯТ — чиню свободно.
3. Chaos/reboot-дриллы: часы доступны (fast + night профили разрешены).
4. test-VPS: НЕДОСТУПНА → G5 = BLOCKED (внешняя инфраструктура).
5. DNS/ACME: креды regru ДОСТУПНЫ — wildcard DNS-01 разрешён.
6. Проекты: из node.yaml подтверждены (см. ниже).

## 0b. Контекст ноды (из node-configs/asi-team-vps/node.yaml, не из памяти)
- context: asi-group (1 нода = 1 контекст), node: asi-team-vps, host: 77.233.221.129
- domain: asiteam.ru, email: admin@asiteam.ru, acme_dns_plugin: regru (per-domain реестр)
- tor: off; timezone: Europe/Moscow; SSH-ключи изолированы (asi_owner/asi_cicd/asi_cicd_root)
- secrets: node-configs/asi-team-vps/secrets/asi-team-vps.enc.yaml; .sops.yaml контура asi (свой age-ключ)
- проекты: ровно 1 — roadmap (roadmap.asiteam.ru, repo asi-group/roadmap2, frontend, expose: true)
- модули: nginx (+config_overlay /opt/node-configs/asi-team-vps/overlays/nginx), platform-secrets, logging, status-page

## 0c. Открытые пункты прошлых валидаций (020/021) — предмет проверки рантаймом
- [ ] S3 SSL-кеш: `InvalidAccessKeyId` (s3.timeweb.cloud, bucket platform-asi-certs) — блокировал C2 cache drill в 020. Владелец подтвердил доступность кредов DNS; S3-креды проверить через secrets контура.
- [ ] F-06 (P2): converge R7 ложный drift-warning по volume `loki-data` (не учитывает compose project-prefix) — фикс опционален.
- [ ] F-10 (P2): apex https://asiteam.ru/ без default vhost (HTTP/2 PROTOCOL_ERROR).
- [ ] DR (020 F-09): postgres/backup-cron НЕ в контексте asi-group → F1/F2/F4 = BLOCKED by-design (внешняя конфигурация контекста, не баг платформы); F3 age-key-backup применим.
- [ ] make healthcheck NODE=... с операторской машины = fail-loud по контракту (F-016) → для ноды: converge + e2e-verify (не баг, зафиксировать в отчёте).

## PROGRESS-чеклист фаз
- [ ] §0  Ворктри/parity/hooks — DONE (симлинки node-configs/.venv/.env/hermes-env, pre-commit install)
- [ ] Фаза A: make check / agent-check / check-manifests / локальный стек / test_journal
- [ ] Фаза B: secrets-unlock → холодный bootstrap → идемпотентность → converge → check-security → sanity
- [ ] Фаза C: TLS wildcard → cache drill → verify-domains → мониторинг TLS
- [ ] Фаза D: deploy-context → render-vhosts/monitoring → project-list/status → deploy-project → CI-канал → sync-env → provision-llm → rollback
- [ ] Фаза E: все модули healthy → вкл/выкл → overlays → node-update → converge → сети
- [ ] Фаза F: DR (F1/F2/F4 by-design BLOCKED: нет postgres в контексте; F3 age-key-backup)
- [ ] Фаза G: reboot → chaos (fast+night) → load-smoke → e2e-verify → test-node BLOCKED
- [ ] Фаза H: Release checklist → ПРОМОУТ вербикт → 02-VerificationReport.md

## Находки (F-NN · дата · фаза · severity)
