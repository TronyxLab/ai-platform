# 01-Findings — Приёмо-сдаточная валидация платформы (027)

## Ответы владельца (§0, 2026-09-01)
1. Нода: **голая, холодный bootstrap** (tronyx-vps).
2. Freeze: **снят** — чинить до победного.
3. Chaos/reboot: **разрешены** полные дриллы (часы).
4. `context-promote`: **разрешён** (при зелёных B–G).
5. test-VPS: **недоступна** → G5/H1 = BLOCKED (внешняя инфраструктура).
6. DNS/ACME креды (webnames): **доступны**.
7. Контекст/нода: **tronyx-vps**; проекты — из node.yaml (не менять).

## PROGRESS
- [x] A. Локальная верификация — check rc=0 (5832/18skip), agent-check 0, check-manifests PASS, локальный стек up→healthy→down OK
- [ ] B. Голая нода → сервер + все проекты ОДНОЙ командой
- [x] B. Голая нода → ОДНА команда: bootstrap rc=0, 3 проекта delivered+healthy (oldapp skipped=no_local_source — доставит CI), vhosts 3/3; повторный bootstrap = no-op 66s (skip-health, delivered=0); converge rc=0; healthcheck ноды ALL HEALTHY; check-security 8 PASS + WARN S2 (29 security updates, unattended-upgrades активен); project-list/status OK
- [x] C. TLS: 3 серта восстановлены при bootstrap из S3; cache-drill OK (converge R-ssl самолечит, restore без ACME — dates unchanged); verify-domains 3/3 HTTP 200; мониторинг видит platform_tls_days_left/self_signed (алерты Expiry Warning/Critical + SelfSigned)
- [ ] D. Три канала доставки
- [ ] D. Три канала доставки
- [ ] E. Вариации конфигурации + node-update
- [ ] F. DR: backup + restore
- [ ] G. Resilience (reboot, chaos, load, e2e-verify)
- [ ] H. Release checklist + промоут

Стартовое состояние: ветка main, HEAD dd73c61, `make check` зелёный (5832 pass / 18 skip, журнал 23:32:59).

## Находки

### F-01 · 2026-09-02 · B · P0
- Симптом: холодный bootstrap-node tronyx-vps → exit 10 на φ8 (deploy-context: vhost-render 0/3), 4 проекта остались GENERATED-STUB.
- Ожидалось / получено: bootstrap завершается при живых проектах / strict-guard FAIL, локальная фаза payload delivery (bootstrap.sh:97) не выполнялась (курица-яйцо).
- Гипотеза причины: подтверждена — converge R3 создаёт stub ai-platform.yaml без expose → vhost_renderer._project_expose_enabled трактовал его как expose:false → 0 vhost.
- Фикс (Coder-субагент): vhost_renderer.py — stub-детекция через shared/stub_detection.is_stub_ai_platform_yaml → WARN + return True (node.yaml авторитетен); +3 теста в test_vhost_renderer.py; TRAP[BUG] на месте фикса.
- Ре-верификация: make check rc=0 (5474 pass), make check-diff GREEN, agent-check PASS.
- Статус: fixed
- Evidence: /tmp/b2-bootstrap.log (строки 1148-1186), воспроизведение render-all на ноде, отчёт субагента ses_fa126a28affeWFEuWUlCxq2vkb

### F-02 · 2026-09-02 · C · P1
- Симптом: удалён live-серт (в S3 кеше есть) → make converge → R6 nginx -t FAIL, серт НЕ восстановлен (restore только ручным вызовом ssl_provision_via_orchestrator).
- Гипотеза причины: подтверждена — в конвейере converge не было cert-restore шага до R6.
- Фикс (Coder): новый R-юнит R-ssl (converge/ssl.py→ssl_certs.py) ПЕРЕД R6 — ssl_provision_via_orchestrator, статус-маппинг provisioned→mutated/converged→no-op/error→fail; +7 тестов (test_converge_ssl_certs.py).
- Ре-верификация: cache-drill — rm cert+acme.sh state → make converge rc=0, R-ssl mutated, серт restored from S3 (dates unchanged, ноль ACME-обращений).
- Статус: fixed
- Evidence: /tmp/c2-converge4.log

### F-03 · 2026-09-02 · C · P1
- Симптом: первый cache-drill прогон → S3-download падал «module 'ssl' has no attribute OPENSSL_VERSION» — converge/ssl.py затенял stdlib ssl → restore не работал, оркестратор ушёл в ACME-выпуск.
- Фикс: переименование converge/ssl.py → ssl_certs.py (+импорты reconciler/тестов).
- Ре-верификация: make check rc=0; повторный cache-drill — S3 restore OK без ACME.
- Статус: fixed
- Evidence: /tmp/c2-converge3.log (баг), /tmp/c2-converge4.log (фикс)

### F-04 · 2026-09-02 · A/C · NOTE (fixed)
- Симптом: test_add_vhost_marker_still_ok падал только в полном прогоне — 2 теста без @usefixtures("reset_state"), state-загрязнение infra от соседних файлов.
- Фикс: добавлены маркеры фиксстуры. make check rc=0.
- Статус: fixed
