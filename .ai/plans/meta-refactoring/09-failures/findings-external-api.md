# FAIL-findings · S1 External API timeout / S2 Malformed external response

$ARTIFACT_CONTRACT
## @purpose Pre-launch audit: отказы внешних API (ACME, S3, ghcr/Docker Hub, GitHub, Telegram, LLM-провайдеры через litellm) — research-only, код не менялся
## @scope core/internal/bootstrap/{cert_orchestrator,issue_cert,install_acme,cert_expiry_check,s3_ssl_cache,cron_installer,docker_registry_auth}, core/internal/llm/{admin_client,key_provisioner,config_renderer}, core/internal/shared/{notifications,telegram_notifier,docker_compose,retry,timeouts}, core/modules/{litellm,monitoring}, .github/workflows
## @rationale максимум снижения риска / минимум churn: config > runbook > точечный код
## ACCEPTANCE_CRITERIA каждый finding: file:symbol-цитата, 9 ответов, confidence, action
## IMPLEMENTS pre-launch audit wave 09-failures (сценарии S1/S2 external API)
## IMPACTS launch-blockers candidates (FAIL-0300, FAIL-0301, FAIL-0303)

## Карта внешних вызовов платформы (факт)

| Внешняя система | Точка вызова | Таймаут | Retry |
|---|---|---|---|
| Let's Encrypt (acme.sh) | issue_cert.py `_acme_issue_with_retry` | 300s/попытка (`ACME_CMD_TIMEOUT`) | 2 попытки, без backoff |
| webnames/reg.ru DNS API | внутри acme.sh dnsapi (внешний код) | не контролируется платформой | acme.sh internal |
| GitHub (git clone acme.sh/dnsapi) | install_acme.py `_clone_acme_github` | 300s / 120s | clone→merge-fallback (1 повтор) |
| S3 (timeweb) | s3_ssl_cache (boto3) | botocore standard | max_attempts=3 |
| ghcr.io / docker.io | shared/docker_compose.py `retry_pull` | `PULL_TIMEOUT`/попытка | 4 попытки, backoff [5,10,20] |
| Telegram Bot API | shared/notifications.py `send_telegram` | `SSH_CONNECT_TIMEOUT` (30s) | 0 (non-blocking by design) |
| LiteLLM Admin API | llm/admin_client.py (httpx) | 30s (`_DEFAULT_TIMEOUT`) | 0 |
| LLM-провайдеры | litellm proxy (runtime) | НЕ задан в конфиге | num_retries=3 + fallbacks |
| GitHub API (mirror) | .github/workflows/mirror.yml | — | sha-resolve 10×10s; push 1× |

---

### FAIL-0300 · CRITICAL · S3-restored сертификаты вне renewal И вне expiry-мониторинга → гарантированное тихое истечение TLS
- scenario: bootstrap новой/пересозданной ноды — cert_orchestrator restore-first берёт сертификат из S3 и кладёт в /etc/letsencrypt/live; acme.sh о нём не знает (нет conf в /root/.acme.sh) → cron renewal его НЕ продлевает; cert_expiry_check сканирует только acme.sh-каталог → алерта тоже нет. Через ≤90 дней сертификат истекает = полный HTTPS-outage ingress без единого предупреждения.
- evidence: cert_orchestrator.py `_try_s3_restore`/`_plw_body__try_s3_restore` — `cache.download_cert(domain, cert_dir, ...)` где `cert_dir = vpath` = `/etc/letsencrypt/live` (`CERT_VALIDITY_PATH = str(letsencrypt_live())`); регистрации в acme.sh нет. cert_expiry_check.py:48 `ACME_CERT_DIR = "/root/.acme.sh"` + :60 `CERT_FILENAMES: tuple[str, ...] = ("fullchain.cer",)` — /etc/letsencrypt/live/**fullchain.pem** не сканируется ни по пути, ни по имени. cron_installer ставит renewal только для доменов acme.sh.
- 9 ответов: 1) тихое истечение restored-сертификата; 2) cert_orchestrator.py::_try_s3_restore + cert_expiry_check.py::scan_expiring (слепая зона); 3) auto-recovery НЕТ; 4) broken state ДА (истёкший cert в nginx, ServiceDown не сработает — nginx жив, отдаёт expired); 5) retry=renew вручную безопасен; 6) user impact: полный отказ HTTPS всех доменов ноды (browser errors); 7) alert НЕТ (двойной промах: не в скане, не в Prometheus — ssl-expiry правил нет); 8) восстановление: `acme.sh --install-cert -d <domain> ... --reloadcmd` (пере-регистрация в acme.sh) или пере-issue; 9) минимальный фикс: (а) systemd-юниту platform-reboot.service добавить `--cert-dir /etc/letsencrypt/live`, (б) в CERT_FILENAMES добавить `fullchain.pem`, (в) runbook-шаг после restore: `acme.sh --install-cert` re-registration.
- confidence: HIGH (пути и имена файлов подтверждены кодом; сценарий restore-first — канон cert_orchestrator invariant 1)
- action: launch-blocker candidate. Фикс = точечный код (2 строки) + 1 аргумент юнита.

### FAIL-0301 · HIGH · Self-signed fallback не алертится: тихая TLS-деградация до ~76 дней
- scenario: ACME/DNS API недоступен при первичном выпуске → cert_orchestrator генерирует self-signed (90 дней) → nginx стартует, но все браузеры дают security warning; ни одного алерта: source="self_signed" никуда не репортируется, а cert_expiry_check увидит проблему только когда self-signed сам войдёт в <14 дней (день ~76).
- evidence: cert_orchestrator.py `_generate_self_signed`: `"[IMP:7]... SELF-SIGNED cert generated (browsers will warn)"` + комментарий `"Monitoring should alert on self_signed source"` — но в core/modules/monitoring/config/{platform-alerts.yml,alert-rules.yml,alerting/alert-rules.yml} 0 вхождений self_signed/ssl_expiry/cert (grep). Self-signed валиден 90 дней (`"-days", "90"`) > threshold 14d (cert_expiry_check.py:57 `DEFAULT_THRESHOLD_DAYS = 14`).
- 9 ответов: 1) тихий downgrade LE→self-signed на весь срок 90д; 2) cert_orchestrator.py::_generate_self_signed (генерация) + monitoring/config (отсутствие правила); 3) auto-recovery частично: следующий bootstrap/node-update повторит issue (φ7/φ12), но по расписанию никто не запускает; 4) broken state ДА; 5) retry безопасен (issue идемпотентен, LE-issuer guard); 6) user impact: browser-предупреждения на всех проектных доменах, API-клиенты с verify TLS падают; 7) alert НЕТ; 8) восстановление: устранить причину (креды/DNS API) → `make node-update` (φ12 ssl-provision); 9) минимальный фикс: TG-notify из cert_orchestrator при source=self_signed (1 вызов notifications.notify, событие уже канонно для notification-catalog) ИЛИ Prometheus-правило по audit-логу.
- confidence: HIGH
- action: launch-blocker candidate (в связке с FAIL-0300 закрывается одним TG-каналом + сканом live-каталога).

### FAIL-0302 · HIGH · ACME issue/renew: retry 2× без backoff; провал renewal cron не алертится напрямую
- scenario: webnames/reg.ru API или Let's Encrypt недоступны в момент issue/renew. Issue: `_acme_issue_with_retry` делает 2 попытки подряд (0s пауза) → провал → self-signed (FAIL-0301). Renewal: acme.sh cron (daily) молча фейлится в собственном логе; единственный детектор — cert_expiry_check за 14 дней до истечения, и тот только TG (канал — FAIL-0303). LE rate-limit 50 certs/domain/week: агрессивные повторы выпуска его усугубляют (TRAP в bootstrap/AGENTS.md — прежний отказ был именно rate-limit).
- evidence: issue_cert.py:95 `ISSUE_MAX_ATTEMPTS: int = 2` + `_acme_issue_with_retry`: `for attempt in range(1, ctx.max_attempts + 1): result = ctx.runner.run(acme_args, timeout=ACME_CMD_TIMEOUT, check=False)` — между попытками sleep отсутствует. cron_installer.py ставит `--install-cronjob` + renew-hook; провал cron нигде не парсится.
- 9 ответов: 1) недоступность ACME → 2 быстрые попытки → fallback; renewal-провал → тишина; 2) issue_cert.py::_acme_issue_with_retry; cron_installer.py::install_acme_cron; 3) auto-recovery: cron повторяется ежедневно (для доменов acme.sh) — да, медленный; 4) broken state: см. FAIL-0300/0301; 5) retry безопасен, но жжёт rate-limit — нужен backoff ≥1-5 мин и ≤3 попыток/сутки на домен; 6) impact: затяжная TLS-деградация; 7) alert: только косвенный (TG за 14д, канал уязвим — FAIL-0303); 8) восстановление: ручной `acme.sh --renew -d <domain> --force` после починки DNS API; 9) фикс: (а) sleep между attempt (retry.py `exponential_backoff` уже есть в shared), (б) runbook: еженедельная проверка `/root/.acme.sh/acme.sh.log` на renew-ошибки ИЛИ grep-проба в cert_expiry_check.
- confidence: HIGH (код), MED (оценка частоты webnames-сбоев)
- action: точечный код (backoff) + runbook-пункт.

### FAIL-0303 · HIGH · Единый alert-канал Tor→Privoxy: смерть tor = тишина всех уведомлений (Python + Grafana)
- scenario: tor-proxy на хосте падает/блокируется → api.telegram.org недостижим → (а) все Python-уведомления (watchdog, cert expiry, deploy, reboot) получают DELIVERY FAILED → audit-fallback в локальный jsonl; (б) Grafana contact points (через host.docker.internal:8118) тоже молчат. Платформа слепа именно в момент широкого инцидента. tor_proxy_check.py существует (3-stage), но его результат никуда не эскалируется вне ноды (он сам зависит от того же канала для notify).
- evidence: notifications.py invariant 5: `"Транспорт из ноды — ТОЛЬКО через Tor/Privoxy... прямой HTTPS на ноде ЗАПРЕЩЁН (TRAP[BUG] 141 — утечка IP)"`; monitoring/docker-compose.base.yml:206-212 TRAP: `"api.telegram.org НЕДОСТУПЕН напрямую с ноды (000)"`, grafana proxy = `host.docker.internal:8118`; notifications.py invariant 7: провал → `write_audit_entry(tag="notify:failed", status="ERROR")` — только локальный audit.
- 9 ответов: 1) tor down → 100% потеря уведомлений; 2) tor (systemd, install_tor_proxy) + notifications.py::send_telegram + grafana contact-points; 3) auto-recovery: watchdog рестартует ТОЛЬКО docker-контейнеры (watchdog.py скан `docker ps`), tor — host-процесс, вне его радиуса; 4) broken state: нет данных, но канал мёртв; 5) retry: доставка ретраится сама при восстановлении tor (кроме throttle-подавленных — FAIL-0307); 6) impact: все инциденты проходят без оповещения; 7) alert: рекурсивная проблема — алерт о смерти алерт-канала не доставляем; 8) восстановление: `systemctl restart tor privoxy` (имена юнитов — install-tor-proxy), проверка `make healthcheck`; 9) минимальный фикс: runbook + внешний heartbeat (healthchecks.io/cron-monitor или второй бот на CI-стороне через notify-ci — прямой HTTPS из CI уже канонен, notifications.py CLI notify-ci).
- confidence: HIGH (архитектура канала), MED (вероятность отказа tor за неделю)
- action: runbook-пункт «проверка tor после любых сетевых изменений» + внешний heartbeat (config-level).

### FAIL-0304 · MED · LLM-провайдеры через litellm: request_timeout не задан → зависший провайдер держит запрос минутами
- scenario: провайдер (deepseek и т.п.) принимает TCP, но не отвечает → litellm ждёт дефолтный timeout (HYPOTHESIS: 6000s в litellm-proxy) на каждую попытку; num_retries=3 умножает ожидание; alias-fallback срабатывает только после финальной ошибки первой ноги.
- evidence: litellm-config.yml.j2:54-56 — в `litellm_settings` только `num_retries: {{ settings.num_retries }}`, `drop_params`; в `litellm_params` (строки 36-40) нет `timeout`/`request_timeout`; config_renderer.py:277 `"num_retries": 3`. Ни одного вхождения `timeout` в сгенерированном litellm-config.yml.
- 9 ответов: 1) hang вместо ошибки → длинные зависания проектных запросов; 2) core/modules/litellm/config/litellm-config.yml.j2 (генерация) — отсутствие параметра; 3) auto-recovery: retry×3 + fallback-алиас — да, но ПОСЛЕ таймаута; 4) broken state: нет; 5) retry безопасен (LLM-вызовы идемпотентны с точки зрения прокси; биллинг дубликатов — HYPOTHESIS); 6) impact: p99 проектов деградирует до минут при сбоях провайдера; 7) alert: Nginx5xxErrors (Grafana, Loki) поймает только явные 5xx, не hang; langfuse-трейсы покажут post-factum; 8) восстановление: `make -C core/modules/litellm restart` / починка провайдера; 9) минимальный фикс: одна строка в policy/template — `request_timeout` (напр. 120s) в litellm_settings или litellm_params (config > code).
- confidence: MED (отсутствие параметра — факт; значение дефолта litellm — HYPOTHESIS, требует проверки версии образа)
- action: config-фикс (1 строка) + verify на staging.

### FAIL-0305 · MED · provision-llm: 0 retries, провал = WARNING и деплой продолжается → проект остаётся без LLM-ключа
- scenario: litellm недоступен/медленный в момент φ11 llm-keys или deploy-hook → generate/update ключа фейлится с WARNING, provisioning продолжается, фаза закрывается done_with_warnings → проект деплоится с отсутствующим/устаревшим виртуальным ключом → runtime 401/403 у проекта уже в проде, без алерта.
- evidence: key_provisioner.py:657-662 `except (OSError, ConnectionError, TimeoutError) as e: logger.log(logging.WARNING, "...Generate failed for '%s'...")` — цикл продолжается; admin_client.py:38 `_DEFAULT_TIMEOUT: float = 30.0`, retry-обёрток нет (grep retry по llm/ — 0); key_provisioner.py:372-377 битый JSON persist-store → `"overwriting"` WARNING (теря всех сохранённых ключей стораджа при одном битом файле).
- 9 ответов: 1) недоступность litellm при provision → тихий пропуск ключа; 2) llm/key_provisioner.py::provision_all + llm/admin_client.py (транспорт); 3) auto-recovery: нет (следующий node-update повторит — раз в день максимум); 4) broken state ДА: ключ отсутствует в persist-store и в .env.platform проекта; 5) retry безопасен и идемпотентен (`metadata.project` — контракт admin_client invariant); 6) impact: LLM-функциональность проекта лежит, деплой «зелёный»; 7) alert НЕТ (deploy burn-rate считает деплой успешным); 8) восстановление: `make provision-llm` повторно; 9) фикс: (а) retry×3 из shared/retry.py вокруг admin_client-вызовов (точечный код), (б) fail-fast опция: отсутствие ключа при llm.enabled=true → блок деплоя (config).
- confidence: HIGH (код-пути), MED (реальная частота)
- action: точечный код (retry) + опция strict-режима.

### FAIL-0306 · MED · ghcr/Docker Hub pull при деплое: retry есть, но first-deploy fail → exit 10 (ручное вмешательство), burn-rate только warning
- scenario: registry (ghcr.io) недоступен/429 в момент деплоя → retry_pull исчерпывает 4 попытки (backoff 5/10/20 clamp) → существующий деплой откатывается к previous image (ок), но ПЕРВЫЙ деплой проекта → PlatformFatalError exit 10, сервиса нет вовсе; CI красный — единственный сигнал.
- evidence: deploy/engine/engine.py::deploy — `if not pull_images(project_dir, service, ref): handle_first_deploy(project, service, ref, "Pull failed after 5 attempts")` → lifecycle.py invariant 3: `handle_first_deploy ВСЕГДА raise PlatformFatalError (exit 10, нет rollback)`; shared/docker_compose.py::retry_pull `max_attempts: int = RETRY_COUNT + 1`, `backoff_seconds = RETRY_BACKOFF_SECONDS` ([5,10,20]); алерт PlatformDeployBurnRate — severity "warning", окно 24h (platform-alerts.yml:43-56).
- 9 ответов: 1) registry outage → деплой падает; 2) deploy/engine/flow.py::pull_images → shared/docker_compose.py::retry_pull; 3) auto-recovery: повторный git push/re-run CI — ручной; 4) broken state: нет (previous image / nothing); 5) retry безопасен: save_previous_image ДО pull (invariant 1), receive идемпотентен по sha; 6) impact: первый деплой проекта отложен до ручного re-run; существующие — откат, пользователи на старой версии; 7) alert: CI-red + PlatformDeployBurnRate (warning, 24h-окно — свежий единичный фейл может не дотянуть до <75%); 8) восстановление: re-run workflow / `make deploy-project`; 9) фикс: не требуется сверх наблюдения (runbook: «первый деплой при registry-инциденте — повторить после восстановления»).
- confidence: HIGH
- action: runbook-пункт; опционально поднять burn-rate до critical при first_deploy_failed в audit.

### FAIL-0307 · LOW · Telegram API ошибки/malformed: доставка non-blocking, но throttle глушит повторы на 1 час
- scenario: затяжной сбой Telegram/Tor (>1ч) → каждое событие шлётся 1 раз, DELIVERY FAILED, audit-fallback; повторные вхождения того же (event, fingerprint) в течение часа подавляются throttle ДО попытки отправки → после восстановления канала «хвост» уведомлений не приходит.
- evidence: notifications.py invariant 6: `"Throttle/dedup: реестр {(event, fingerprint): ts}; окно... DEFAULT_THROTTLE_SECONDS (3600). Подавление → IMP:8, return True"`; invariant 7: провал → audit-fallback; send_telegram ловит ВСЕ исключения → False (telegram_notifier invariant 7); malformed JSON ответа — `json.JSONDecodeError` в except-списке get_me (telegram_notifier.py:178).
- 9 ответов: 1) сбой доставки >throttle-окна → потеря промежуточных событий; 2) shared/notifications.py::notify_event (throttle-проверка перед send); 3) auto-recovery: следующее событие с новым fingerprint пройдёт; 4) broken state: нет; 5) retry: повтор вручную безопасен; 6) impact: пропущенные critical-уведомления во время инцидента; 7) alert: рекурсия (см. FAIL-0303); 8) восстановление: audit.jsonl `tag="notify:failed"` — реконструкция вручную; 9) фикс: не throttling-ать при предыдущем DELIVERY FAILED (точечный код, 3-5 строк) ИЛИ не поднимать до launch (audit-fallback уже даёт реконструируемость).
- confidence: HIGH (механика), LOW (частота)
- action: backlog после launch.

### FAIL-0308 · LOW · GitHub API/mirror (CI-only): retry каноничен, прод не затронут
- scenario: GitHub API eventual-consistency/сбои при mirror push → sha-resolve 10×10s, push 1 повтор с re-sync; фейл = красный mirror-workflow, прод-нода не зависит.
- evidence: .github/workflows/mirror.yml:200-208 `for attempt in 1 2 3 ... 10; do MIRROR_HEAD=$(git ls-remote mirror ... || true)... sleep 10`; :177-179 push fail → re-sync + retry.
- 9 ответов: 1) CI-mirror рассинхрон; 2) mirror.yml steps; 3) retry встроен; 4) broken state: рассинхрон mirror до следующего прогона; 5) retry безопасен; 6) impact: нет прод-эффекта (context-promote тянет из source); 7) alert: notify-telegram action при fail (hermes-nightly:104-106 паттерн); 8) восстановление: re-run workflow; 9) фикс: не требуется.
- confidence: HIGH
- action: none.

### FAIL-0309 · MED · S3 cert cache: upload-провал после успешного issue = тихая дыра в DR-кэше
- scenario: сертификат выпущен (LE ok), но S3 upload упал (сеть/креды) → WARN в bootstrap-логе и всё; в S3 лежит старый/отсутствующий cert → при пересоздании ноды restore-first вытянет устаревший серт (который сам скоро истечёт) вместо свежего.
- evidence: cert_orchestrator.py `_upload_to_s3`: `"Non-fatal: failure logs WARN, returns False"`; s3_ssl_cache.py:17 `max_attempts=3, mode='standard'` (boto3 retry — есть); вызов после issue: `_process_single_domain` — upload без проверки результата при status="issued" (результат `_upload_to_s3` игнорируется в ветке issued: строки 476-478 — вызов, не проверка).
- 9 ответов: 1) тихое расхождение live↔S3; 2) cert_orchestrator.py::_upload_to_s3 + точка вызова в _process_single_domain; 3) auto-recovery: следующий skip-путь (valid cert on disk) снова делает upload — да, при следующем node-update; 4) broken state: DR-кэш устарел (не data loss — live-серт валиден); 5) retry безопасен (upload идемпотентен); 6) impact: проявится только при DR — restore старого серта + FAIL-0300 слепая зона; 7) alert НЕТ; 8) восстановление: ручной re-upload `python3 s3_ssl_cache.py upload <domain>`; 9) фикс: runbook-пункт в DR-drill: сверка `S3 list vs /etc/letsencrypt/live` (квартальный drill уже канон — runbook §7).
- confidence: HIGH
- action: runbook (добавить сверку в DR-drill чек-лист).

### FAIL-0310 · LOW · GitHub git clone acme.sh/dnsapi при bootstrap φ7: провал = фаза падает, идемпотентный re-run
- scenario: GitHub недоступен при bootstrap → clone + merge-fallback оба fail → install_acme False → EXIT_GENERIC → φ7 certificates fail → bootstrap останавливается; dnsapi-clone non-fatal (WARN, webnames не работает — задокументировано).
- evidence: install_acme.py `_clone_acme_github`: `if fallback.returncode == 0: ... return True; _log_step("acme", "FAIL", ...); return False`; `_clone_dnsapi_ext`: `"WARN", "Failed to clone regtime-ltd/dnsapi — webnames TLS will not work"` (non-fatal); GIT_CLONE_TIMEOUT=300, DNSAPI_CLONE_TIMEOUT=120.
- 9 ответов: 1) bootstrap прерывается на φ7; 2) install_acme.py::_clone_acme_github; 3) auto-recovery: нет, но re-run `make bootstrap-node` идемпотентен (done-фазы SKIP, merge-fallback для непустого ACME_HOME — TRAP 017e1c1); 4) broken state: нет; 5) retry безопасен; 6) impact: задержка bootstrap до восстановления GitHub; 7) alert: нет (интерактивная операция оператора — вывод в терминале); 8) восстановление: re-run bootstrap; 9) фикс: не требуется.
- confidence: HIGH
- action: none (поведение корректное).

### FAIL-0311 · LOW · webnames `zone_manager_unavailable` — malformed-ответ, провоцирующий ложный диагноз (уже TRAP)
- scenario: webnames.ru API отвечает `{"result":"ERROR","details":"zone_manager_unavailable"}` на domains_list при РАБОЧИХ add/delete TXT → оператор/агент решает «DNS-01 сломан», отключает DNS-01 → потеря wildcard → каскад к HTTP-01 individual certs.
- evidence: bootstrap/AGENTS.md TRAP[BUG] P0 FALSE DIAGNOSIS: `"Reality: TXT record add/delete WORK... Root of prior failure: LE rate-limit (50/domain/week)"`; issue_cert.py:471-474 WARN о формате ключа (leading `*`).
- 9 ответов: 1) ложный диагноз → неверный runbook-шаг; 2) внешний API (webnames) + интерпретация оператором; 3) auto-recovery: нет (человеческий фактор); 4) broken state: возможна потеря wildcard-покрытия; 5) retry безопасен; 6) impact: потенциальный self-inflicted outage; 7) alert: n/a; 8) восстановление: revert на DNS-01; 9) фикс: уже закрыто документацией (TRAP + test-curl в AGENTS.md) — поддерживать в актуальном состоянии.
- confidence: HIGH (инцидент задокументирован)
- action: none (runbook уже есть).

### FAIL-0312 · LOW · cert_expiry_check парсинг openssl-вывода: непарсимая строка = серт молча пропущен
- scenario: смена формата `openssl x509 -enddate` (версия/locale) или битый .cer → parse_enddate None → серт исключён из отчёта (WARN) → истечение не попадёт в уведомление.
- evidence: cert_expiry_check.py:61 `_ENDDATE_RE = re.compile(r"notAfter=([A-Za-z]{3}\s+\d+\s+...)\s+GMT")`; :113-114 `read_enddate` invariant: `"Ошибки... → WARN + None — сертификат пропускается"`; scan_expiring собирает только распарсенные.
- 9 ответов: 1) слепое пятно мониторинга; 2) cert_expiry_check.py::read_enddate/parse_enddate; 3) auto-recovery: нет; 4) broken state: нет (check read-only); 5) retry: n/a; 6) impact: скрыт до реального истечения; 7) alert: нет (и не может — пропуск тихий); 8) восстановление: ручной openssl-пробой; 9) фикс: считать «0 распарсенных при N>0 найденных файлов» аномалией → TG (точечный код, ~5 строк); низкий приоритет (формат openssl стабилен).
- confidence: HIGH (механика), LOW (вероятность)
- action: backlog после launch.

## Сводка приоритетов (минимальный churn до launch)

| # | Фикс | Тип | Закрывает |
|---|------|-----|-----------|
| 1 | `--cert-dir /etc/letsencrypt/live` в platform-reboot.service + `fullchain.pem` в CERT_FILENAMES | точечный код (2 строки) + unit-arg | FAIL-0300 |
| 2 | TG-notify при source=self_signed в cert_orchestrator | точечный код (~10 строк) | FAIL-0301 |
| 3 | sleep между acme attempts (shared/retry.exponential_backoff) | точечный код (~3 строки) | FAIL-0302 |
| 4 | `request_timeout` в litellm policy/template | config (1 строка) | FAIL-0304 |
| 5 | retry×3 вокруг admin_client (shared/retry) | точечный код | FAIL-0305 |
| 6 | Внешний heartbeat + DR-drill сверка S3↔live | runbook/config | FAIL-0303, FAIL-0309 |

Launch-blockers: FAIL-0300 (гарантированный будущий outage без алерта), FAIL-0301 (тихая деградация без алерта), FAIL-0303 (слепота всех каналов оповещения).
