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
- [x] B. Голая нода → ОДНА команда: bootstrap rc=0, 3 проекта delivered+healthy (oldapp skipped=no_local_source — доставит CI), vhosts 3/3; повторный bootstrap = no-op 66s (skip-health, delivered=0); converge rc=0; healthcheck ноды ALL HEALTHY; check-security 8 PASS + WARN S2 (29 security updates, unattended-upgrades активен); project-list/status OK
- [x] C. TLS: 3 серта восстановлены при bootstrap из S3; cache-drill OK (converge R-ssl самолечит, restore без ACME — dates unchanged); verify-domains 3/3 HTTP 200; мониторинг видит platform_tls_days_left/self_signed (алерты Expiry Warning/Critical + SelfSigned)
- [x] D. Каналы доставки: deploy-context idempotent rc=0; vhosts+monitoring render OK; deploy-project DEPLOYED+аудит; CI-канал E2E GREEN (F-05/F-06/F-07 чейн пофикшен); sync-env rc=0 ×3 (F-08); provision-llm 1 key; rollback-контур через forced-command verb → healthy → re-deploy
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
### F-05 · 2026-09-02 · D · P0
- Симптом: CI-канал деплоя всех проектов сломан — шаг Gitleaks scan (L1) падал молча (0 строк вывода, exit 1 за 0.3s).
- Гипотеза причины: подтверждена — upstream gitleaks v8.30.1 переименовал checksums.txt → gitleaks_<ver>_checksums.txt; curl -sL (bash -e) сохранял «Not Found», grep пуст → abort до echo-диагностики.
- Фикс: deploy-project.yml + setup-gitleaks action — версия-префиксный ассет + fallback на legacy имя + curl --fail (явная диагностика).
- Ре-верификация: probe-раны — gitleaks L1 scan passed (v8.30.1), sha256 verified.
- Статус: fixed
- Evidence: /tmp/d5-job.log (баг), run 33587152773 (фикс gitleaks)

### F-06 · 2026-09-02 · D · P0
- Симптом: после gitleaks — SSH preflight «Identity file $RUNNER_TEMP/deploy_key not accessible» (ЛИТЕРАЛ в warning) → Permission denied.
- Гипотеза причины: подтверждена — job-level env SSH_OPTS содержал $RUNNER_TEMP; GitHub env-значения — литеральные строки, bash НЕ делает рекурсивного расширения при `ssh ${SSH_OPTS}` (TRAP[BUG] 2026-08-31 был неверен). Первый реальный CI-ран после биллинг-блока.
- Фикс: SSH_OPTS в step-level env каждого SSH-шага с ${{ runner.temp }} (контекст runner в job-level env недоступен). Секрет CI_DEPLOY_KEY в 3 проектных репо заменён на валидный (base64-форма, setup-ssh декодирует сам).
- Ре-верификация: preflight pong OK (run 33591414425).
- Статус: fixed
- Evidence: /tmp/d5-job3.log, run 33587937440

### F-07 · 2026-09-02 · D · P0
- Симптом: «Invalid or reserved project name: '"dance-site"'» — CI шлёт receive/verify с ручными кавычками, серверный dispatch парсил args наивным split().
- Гипотеза причины: подтверждена — _handle_* в orchestrator_cli.py не снимали кавычки (локальный канал shlex.quote кавычек не добавлял — потому bootstrap/deploy-project работали).
- Фикс (Coder): общий хелпер _parse_tokens (shlex.split + fallback split при unmatched quote) во всех verb-хендлерах + T9.7-блок dispatch; +7 тестов (инъекция-негатив сохранён); channel pin refresh (workflow-sha-pins gate).
- Ре-верификация: run 33592708886 — GREEN end-to-end (build→ghcr→receive→deploy→verify); на ноде dance-site healthy, аудит deploy:deploy DEPLOYED.
- Статус: fixed
- Evidence: /tmp/d5-job6.log (баг), run 33591414425 (до), run 33592708886 (после)
### F-08 · 2026-09-02 · D · P2
- Симптом: make project-sync-env NAME=<n> PROJECT_DIR=<dir> → exit 2 (unrecognized --name у gen_project_platform_md.py).
- Гипотеза причины: фасад scaffold.sh пробрасывает "$@" в оба генератора; md-генератор не принимал --name (канон AGENTS.md документирует NAME=).
- Фикс: gen_project_platform_md.py принимает --name и игнорирует (паритет gen_env_platform.py; имя выводится из project yaml).
- Ре-верификация: rc=0 для tronyx-site/dance-site/botanika; .env.platform + AI-PLATFORM.md обновлены (diff 2 файла).
- Статус: fixed
- Evidence: /tmp/d6a.log (баг), /tmp/d6a2.log (фикс)

### D-фаза примечания
- D1 deploy-context: rc=0, deployed=0 skipped=3 (канал ре-верифицирован, идемпотентен).
- D4 deploy-project: DEPLOYED healthy; аудит-след tag=deploy:deploy proc=orchestrator_cli (литерального маркера DEPLOY-DIRECT в коде нет — NOTE).
- D5 CI-канал: полное E2E (F-05/F-06/F-07 чейн) — git push → CI build → ghcr → forced-command receive → deploy → verify GREEN (run 33592708886).
- D7 provision-llm: локально rc=0, 1 key persisted (transient refusal при опущенном стеке обработан честно: exit 1, без дублей).
- D8 rollback-контур: forced-command `rollback botanika` → snapshot-rollback → health healthy → re-deploy через CI восстановил (botanika healthy, аудит OK).
