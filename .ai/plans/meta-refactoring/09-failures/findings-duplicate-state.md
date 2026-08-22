# Findings: duplicate request (S1) — pre-launch audit

$ARTIFACT_CONTRACT
- PURPOSE: Аудит failure-modes повторных/параллельных деплоев (S1) перед launch
- SCOPE: research-only; ID-диапазон FAIL-0700–0799; evidence = file:symbol + цитата
- REQUIRES: сценарий S1 — два push подряд, CI retry, deploy-project поверх CI, двойной forced-command
- ACCEPTANCE: каждый finding отвечает на 9 вопросов аудит-протокола; severity по 00-scope.md

## Сводка S1

Канал: `git push` → deploy-project.yml (reusable) → `tar | ssh ci-deploy@ "receive <project> <sha>"`
→ authorized_keys command= → orchestrator_cli dispatch → ReceiveFlow.run → copy → DeployOrchestrator.deploy.
Единственная защита от параллельности — per-project flock ВНУТРИ orchestrator.deploy; фаза копирования
payload'а и сам CI-канал замком не покрыты. Итог S1: 0 CRITICAL / 3 HIGH / 1 MED / 2 LOW.

### FAIL-0700 · HIGH · CI deploy-канал без concurrency group — два push дают гарантированные параллельные receive
- scenario: два push в main проекта подряд (или push во время ручного re-run) → два job → два `receive` одного проекта параллельно
- evidence: `.github/workflows/deploy-project.yml` — ключа `concurrency:` НЕТ (grep concurrency по .github/workflows/: только push-gate.yml:48, mirror.yml:90, platform-gate-fast.yml:44, platform-test.yml:58, core-deploy.yml:56); caller-шаблон `templates/template-backend/.github/workflows/deploy.yml:9-13` — `on(push:main)` тоже без concurrency
- Q1: оба job проходят quality/gitleaks и одновременно открывают SSH-receive одной ноды
- Q2: точка отказа — отсутствие concurrency group в reusable workflow И в caller'е
- Q3: авто-recovery НЕТ; единственный барьер — flock внутри orchestrator.deploy (orchestrator.py:295), который не покрывает копирование (FAIL-0701) и деградирует (FAIL-0702)
- Q4: broken state — возможен смешанный payload (см. FAIL-0701)
- Q5: retry (re-run проигравшего job) безопасен после завершения победителя
- Q6: проигравший CI красный «Concurrent deploy blocked» (orchestrator.py:308-314); победитель зелёный, но возможно с чужими файлами
- Q7: alert — CI failure + Telegram critical у проигравшего (deploy-project.yml:381-398)
- Q8: перезапустить проигравший run после завершения победителя
- Q9 (fix, 1 строка): `concurrency: { group: deploy-${{ inputs.project_name }}, cancel-in-progress: false }` в deploy-project.yml (+ шаблоны проектов)
- confidence: high · action: добавить до launch (дешёвле и эффективнее всех остальных фиксов S1)

### FAIL-0701 · HIGH · Копирование payload выполняется ВНЕ per-project lock — гонка двух receive даёт смешанный payload при зелёном CI
- scenario: receive A и receive B одного проекта перекрываются; оба успевают скопировать файлы до того, как кто-то взял deploy-lock
- evidence: `core/internal/deploy/receive_flow.py:423-462` (ReceiveFlow.deploy: makedirs → backup → staging_copy → per-file os.replace в target_dir) выполняется ДО вызова orchestrator.deploy (:472); lock берётся только внутри `DeployOrchestrator.deploy` Step 0 (`core/internal/deploy/orchestrator.py:292-297`). Reentrant-реестр `_REENTRANT` действует только внутри процесса (`core/internal/shared/file_lock.py:62`), а каждый forced-command = новый процесс (`command="...orchestrator_cli dispatch"`, core/internal/bootstrap/security/deploy_channel_posture.py:14)
- Q1: A и B интерливят os.replace одних и тех же файлов → target_dir = смесь (docker-compose.yml от A, .env.platform от B, practices.lock от кого повезло)
- Q2: точка отказа — lock-секция начинается позже критической секции копирования
- Q3: само не восстановится: победитель поднимает compose из смеси; проигравший получает FAILED locked
- Q4: broken state ДА: history/snapshot/CI записывают version=sha победителя, фактические файлы — смесь двух sha; payload-бэкап содержит состояние «до последнего копирования», не до деплоя
- Q5: retry чистым деплоем последней версии — безопасен и чинит файлы
- Q6: тихое расхождение «задеплоенный sha» ↔ реальный конфиг; сервис обычно стартует (смесь валидных файлов), но контракт нарушен
- Q7: alert НЕТ (CI зелёный, notify info)
- Q8: операторского триггера нет; обнаружение — только сверка файлов ноды с git
- Q9 (fix, маленький): взять тот же flock в ReceiveFlow.deploy ПЕРЕД backup/copy (FileLock reentrant — вложенный acquire оркестратора станет depth+1 без дедлока, file_lock.py:190-193)
- confidence: high · action: кандидат launch-blocker (вместе с 0700/0702 образует единый кластер «lock не покрывает критическую секцию»)

### FAIL-0702 · HIGH · FileLock молча деградирует в no-lock при cross-user EACCES — после root-деплоя CI-receive идут без замка постоянно
- scenario: bootstrap φ8/φ12 (root) деплоит проект через тот же DeployOrchestrator и создаёт root-owned lock-файл; все последующие CI-receive под ci-deploy теряют защиту НАВСЕГДА
- evidence: `core/internal/shared/file_lock.py:164-180` — PermissionError/OSError в `_open_fd` → WARN «running WITHOUT lock» → acquire() no-op; lock создаётся с mode 0644 владельцем-процессом (file_lock.py:167), путь `/var/lock/platform-deploy-{project}.lock` (file_lock.py:307-310); root-деплой проектов существует: `core/internal/bootstrap/deploy/context_deployer.py:311-332` (DeployOrchestrator под root); chown-фикс в DeployHistory чинит ТОЛЬКО .deploy-snapshots, не lock (`core/internal/deploy/audit/history.py:187-190`)
- Q1: ci-deploy делает os.open(O_RDWR) на root:root 644 файл → EACCES → замок физически не берётся ни одним CI-деплоем этого проекта
- Q2: точка отказа — политика degrade-to-no-lock в _open_fd, рассчитанная на dev-машину, но срабатывающая на ноде при cross-user файле
- Q3: авто-recovery нет (файл персистентен)
- Q4: broken state ДА: системное отсутствие защиты от двойного деплоя (усиливает FAIL-0700/0701 до безбарьерного режима)
- Q5: retry безопасен не более чем обычный деплой — параллельность уже разрешена
- Q6: пользовательский impact отсутствует до первой гонки; затем — как FAIL-0701
- Q7: alert НЕТ (WARN только в journal ноды)
- Q8: вручную chown/chmod lock-файла или удалить его
- Q9 (fix, config): при создании lock под root — chmod 0o666 (flock-семантика не требует rw на файл у конкурента? требует open) ЛУЧШЕ: chown ci-deploy:ci-deploy по аналогии с history.py:188, либо общий group writable 0664 + общая группа
- confidence: high (код-путь подтверждён; сценарий root-deploy → CI-deploy — штатный bootstrap) · action: кандидат launch-blocker, чинится 2 строками

### FAIL-0703 · MED · Авто-retry ForcedCommandChannel после таймаута перезапускает receive поверх брошенного полпути деплоя
- scenario: make deploy-project / deliver: ssh-таймаут (PLATFORM_DEPLOY_TIMEOUT=900s, timeouts.py:130) убивает клиентский ssh, удалённый receive умирает в произвольной фазе; через [5,10]s канал шлёт payload заново
- evidence: `core/internal/deploy/channels/base.py:96-135` (_retry_deliver: attempts=3, retryable=«not success» — таймаут НЕ исключён), `core/internal/deploy/channels/forced.py:116-123` (TimeoutExpired → success=False, exit 124); удалённая сторона при обрыве получает SIGHUP в произвольной точке (copy/compose up), дочерний docker compose может дорабатывать осиротевшим
- Q1: второй receive стартует, пока первый не завершил (или завершился насильно) — та же гонка копирования, что FAIL-0701
- Q2: точка отказа — retryable-предикат канала не различает «честный отказ» и «исход неизвестен» (timeout)
- Q3: авто-recovery нет для брошенного экземпляра (rollback не запустится — процесс мёртв)
- Q4: broken state возможен (смешанный payload; осиротевший compose-up завершится сам)
- Q5: retry НЕ полностью безопасен (в отличие от повторного receive после чистого FAILED)
- Q6: CI/оператор видит timeout → красный; финальное состояние ноды недетерминировано
- Q7: alert — CI red + Telegram critical (deliver-ветка deploy-project)
- Q8: проверить статус проекта (`make project-status`) и перезапустить деплой
- Q9 (fix, точечный): retryable = not success AND exit_code != 124 — таймаут = «исход неизвестен», решение за оператором
- confidence: medium-high · action: фикс вместе с кластером lock

### FAIL-0704 · LOW · Окно remove→replace при перезаписи payload-файла (нарушение собственного инварианта T9.8 «старый ИЛИ новый»)
- scenario: читатель (compose up параллельного процесса — см. 0701/0702) обращается к файлу точно между os.remove и Path.replace
- evidence: `core/internal/deploy/receive_flow.py:452-460` — `if os.path.lexists(dest): os.remove(dest)` … `Path(item).replace(dest)`; тот же паттерн в `core/internal/deploy/orchestrator.py:1105-1112` (_restore_payload_files). Между remove и replace dest отсутствует → ENOENT у читателя
- Q2: точка отказа — предварительный remove нужен только для root-owned стаба (D11), но выполняется ВСЕГДА
- Q4: transient (окно микросекунды-миллисекунды), но при активной гонке реалистично
- Q5: retry безопасен
- Q7: alert нет; проявился бы как разовый compose-fail с rollback
- Q9 (fix, минимальный): сначала Path.replace(dest), remove — только в except OSError (EACCES) ветке
- confidence: high · action: opportunistic fix рядом с FAIL-0701

### FAIL-0705 · LOW · Мусорные tmpdir внутри project dir при kill процесса receive
- scenario: kill -9/SIGHUP во время receive → finally-блоки не выполняются → payload-backup-*/payload-stage-* остаются навсегда
- evidence: `core/internal/deploy/receive_flow.py:427` (mkdtemp «payload-backup-», dir=target_dir), :441 («payload-stage-», dir=target_dir); cleanup только в finally (:461-462, :485-486); ни один следующий деплой префиксы не зачищает
- Q4: не влияет на compose (имена вне whitelist payload), но накапливает копии старых .env.platform в /opt/projects/<p>
- Q7: alert нет
- Q9 (fix): вынести mkdtemp в /tmp (вне target_dir) или чистить префиксы в начале следующего ReceiveFlow.deploy
- confidence: high · action: hygiene, после launch

## Синтез S1 → launch-blockers

Кластер двойного деплоя = FAIL-0700 + FAIL-0701 + FAIL-0702: concurrency group (1 строка YAML) +
flock перед копированием (поднять существующий lock выше по flow) + chown/chmod lock-файла (2 строки).
Суммарно <20 строк кода закрывают единственный путь к тихой порче задеплоенного состояния.
