# GREP_SUMMARY: DevPlan 147-02, critical-debt-closeout, CI-secrets-restore, R15, R17, AGE-DR-drill, chaos-window, deploy.sh-removal, operator-windows
# STRUCTURE: ┌контекст+сверка фактов (5 пунктов, 2 расхождения с брифом)┐ → ◇ TRAP[DECISION] (5) → ┌код-граф XML┐ → ┌волны W1-W5┐ → ◇ acceptance criteria → ⎋ verification + операторские зависимости

$START_DEVPLAN

# DevPlan 147(02) — Закрытие критичного технического долга: секреты CI, DR-drill AGE, chaos-окно, deploy.sh

$ARTIFACT_CONTRACT
PURPOSE:               Закрыть 5 критичных пунктов реестра долга 145 v2.0 (бриф 147 §2):
                       2 блокера CI-канала (R15 — утраченный ci-deploy ключ, R17 — Docker Hub
                       rate-limit), 2 операционных окна (DR-drill AGE до 2026-08-31, chaos T6-T11
                       до 2026-09-15) и верификацию/удаление legacy deploy.sh (до 2026-11-01).
                       План выбирает и фиксирует способы закрытия (за брифом решения не было).
DESCRIPTION:           W1 — восстановление CI-канала: CI_DEPLOY_KEY установлен агентом из живой
                       локальной пары во все 5 репозиториев + верификация forced-command канала
                       (ci-deploy@, pong) — ВЫПОЛНЕНО 2026-08-11 (решение оператора «делай всё сам»;
                       новый ключ platform_personal не создаётся, cicd-ключ — только CI/CD);
                       остаток W1 — DOCKER_HUB_USERNAME/TOKEN (токен оператора). W2 — код:
                       pre-window фиксы chaos-харнесса T6-T10 (причины RED из VR 142 §6) +
                       автоматизация `make age-key-backup`; W3 — операционные окна: DR-drill AGE
                       (≤2026-08-31), chaos-прогон T1-T11 + fresh ACME DNS-01 (≤2026-09-15),
                       аудит deploy.sh (≤2026-11-01); W4 — финализация: удаление deploy.sh,
                       обновление manifest/AGENTS.md, реестр → v2.1, архивация evidence (D-J4).
RATIONALE:             Сверка фактов 2026-08-11 (повторная, бриф §5): локальная пара
                       ~/.ssh/platform_personal_cicd СУЩЕСТВУЕТ (создана 2026-08-07); канал на ноде
                       УЖЕ настроен — /home/ci-deploy/.ssh/authorized_keys содержит forced-command
                       dispatch с идентичным pub (ping → pong); ранняя проверка tronyx@ была
                       неверным пользователем (канон φ2 = ci-deploy). Не хватало только секрета
                       CI_DEPLOY_KEY — установлен агентом во все 5 репозиториев (решение оператора
                       «делай всё сам»; отдельный platform_personal не создаётся, ssh-add основного
                       ключа выполнен). Остаток W1: DOCKER_HUB_* (токен оператора). Chaos-харнесс
                       (tests/e2e/test_chaos_resilience.py, 1365 LOC, T1-T11) жив, последний фикс —
                       142 B36. Для всех 5 пунктов суперпозиция раскрыта и автоколлапснута к Option A.
ACCEPTANCE_CRITERIA:   AC1: `gh secret list` = 17 секретов (CI_DEPLOY_KEY, DOCKER_HUB_USERNAME,
                       DOCKER_HUB_TOKEN); `ssh -i ~/.ssh/platform_personal_cicd` → pong; CI-деплой
                       реального проекта (TronyxLab/*) → DEPLOYED healthy; platform-test GREEN
                       (R15/R17/B6 закрыты). AC2: make check GREEN; chaos-харнесс T6-T10 фиксы с
                       unit-тестами; `make age-key-backup` работает и зарегистрирован в manifest.
                       AC3: evidence drill'а (≤2026-08-31) и chaos-окна (≤2026-09-15) в
                       .ai/plans/147-critical-debt-closeout/evidence/; финальные статусы T1-T11 +
                       fresh ACME в реестре. AC4: при 0 вызовах deploy.sh в audit-логах —
                       deploy.sh удалён, manifest/core-AGENTS.md/root-AGENTS.md keep-таблица
                       обновлены (≤2026-11-01); реестр 145 → v2.1, 0 критичных OPEN;
                       evidence-папки 126/141 заархивированы (D-J4).
IMPLEMENTS:            Бриф 147 §2.1-2.5 (проблемы), реестр 145 v2.0 (00-TECHNICAL-DEBT-REGISTRY.md,
                       категории B/C/D), VR 142 §4.4/§6 (R15/R17, chaos T6-T10 причины), Debt 126/136
                       (D-5, T9-T11, S-13, B6/B7), Debt 139 T-1 (deploy.sh), docs/age-master-key-dr.md
                       §2-3 (процедуры drill'а), docs/ci-secrets-rotation.md (runbook регенерации).
IMPACTS:               gh-секреты tronyx161/ai-platform + TronyxLab/* (восстановление);
                       tronyx-vps (authorized_keys ci-deploy, chaos-прогон); test-e2e (DR-drill
                       restore-first); CI (platform-test, Build Platform Agent — разблокировка);
                       tests/e2e/test_chaos_resilience.py + chaos_audit.py (фиксы маркеров);
                       Makefile + entrypoint-manifest.yaml (make age-key-backup, deploy.sh);
                       core/AGENTS.md + root AGENTS.md (keep-таблица); реестр 145 (v2.1);
                       .ai/plans/126-chaos-resilience/files/ + 141-server-recovery/evidence/
                       (архивация D-J4).
REQUIRES:              Оператор: (а) read-only Docker Hub токен (W1, R17); (б) окна: DR-drill до
                       2026-08-31, chaos до 2026-09-15, аудит deploy.sh (2 SSH-проверки) — W3;
                       (в) подтверждение судьбы deploy.sh по результатам аудита (W4).
                       W1 (CI_DEPLOY_KEY + канал) — ВЫПОЛНЕНО агентом 2026-08-11 (авторизация
                       оператора «делай всё сам»; ssh-add основного ключа выполнен).
$END_ARTIFACT_CONTRACT

---

## 1. Контекст и диагноз — сверка фактов 2026-08-11 (повторная)

> Метод: live-проверки (ls ~/.ssh/, ssh -i, gh secret list, gh run list, git log) +
> чтение харнесса/манифестов. Сверка обязательна по брифу §5 («состояние может измениться»).

### 1.1 Расхождения с брифом (сверка 2026-08-11, 21:50 MSK)

| Пункт | Бриф 147 §2 | Live-факт | Следствие для плана |
|-------|-------------|-----------|---------------------|
| R15 (2.1) | «локальная пара ~/.ssh/platform_personal_cicd НЕ существует» | **Пара СУЩЕСТВУЕТ** (Aug 7 23:05, 419B priv + 111B pub); ранний `ssh -i ... tronyx@` → Permission denied — **неверный пользователь**: канон = `ci-deploy@` | gh secret set из локальной пары (путь 145 W1 жив); авторизация НЕ требуется — см. ниже |
| R15 (2.1) | CI_DEPLOY_KEY отсутствует | **ЗАКРЫТО W1.1 (2026-08-11 22:06 MSK):** CI_DEPLOY_KEY установлен во все 5 репозиториев (tronyx161/ai-platform + TronyxLab/*); `gh secret list` = 15 секретов | Секрет верифицирован; канал — ниже |
| R15 (2.1) | «путь восстановления из локальной пары невозможен» (145 W1 TRAP) | **Канал УЖЕ НАСТРОЕН:** `/home/ci-deploy/.ssh/authorized_keys` содержит `command="cd /opt/platform && PYTHONPATH=/opt/platform python3 -m core.internal.deploy.orchestrator_cli dispatch",restrict ssh-ed25519 <KEY> tronyx@platform_personal_cicd` — pub **идентичен** локальному; `ssh -i ~/.ssh/platform_personal_cicd ci-deploy@tronyx-vps ping` → **`pong`** (dispatch). Пользователя `tronyx` на ноде нет (getent) — канон φ2 пишет ключ пользователю `ci-deploy` (phases/system.py:332-334) | Шаг авторизации pub ИСКЛЮЧЁН из W1 (не требуется); R15 закрыт секретом + верификацией канала |
| R15 (модель ключей) | — (решение оператора 2026-08-11 22:05) | `platform_personal_cicd` — **ТОЛЬКО CI/CD** (forced-command, пользователь ci-deploy, интерактивного входа нет); ручной операторский доступ — **основной ключ** (ssh-add `Tronyx (ED25519)` выполнен, root-доступ работает); отдельный ключ `platform_personal` **НЕ создаётся** | W1 выполняется агентом самостоятельно (авторизация оператора «делай всё сам»); зависимости оператора сокращаются до DOCKER_HUB_TOKEN |
| R17 (2.2) | DOCKER_HUB_* отсутствуют | Подтверждено (в списке 14 секретов нет); CI: platform-test failure 2026-08-10, Build Platform Agent failure 2026-08-09 | W1.3 без изменений |
| S-13 (2.3) | drill не выполнен | Подтверждено (нет следов backup/restore в репозитории и evidence) | W3.1 |
| Chaos (2.4) | T6-T10 RED, T9-T11 не запускались | Подтверждено; харнесс жив (последний коммит 606d2d1d — 142 B36); T7 уже содержит «allocator внутри clickhouse, лимит 1GiB» — маркер victim_named остаётся причиной RED | W2.1 (pre-window фиксы), W3.2 |
| deploy.sh (2.5) | верификация не проведена | Подтверждено; deploy.sh 175 LOC; manifest:68 ссылается (delegates_to make deploy, legacy-local-entrypoint); manifest:885 — orchestrator_cli dispatch канон | W3.3 аудит, W4.1 удаление |

### 1.2 Подтверждённые причины RED chaos T6-T10 (VR 142 §6, для W2)

| Тест | Причина RED (VR 142) | Pre-window фикс |
|------|----------------------|-----------------|
| T6 postgres-sigkill | маркеры `docker:postgres-interrupted`/`postgres-ready` count=0 — «postgres не был убит?» | диагностика инъекции: kill -9 PID внутри контейнера + валидация маркеров |
| T7 oom-clickhouse | «OOM victim not named: 3» — oom_report не матчится на имя clickhouse | уточнить grep (Killed process … cgroup clickhouse), диагностика аллокатора |
| T8 disk-pressure | ENOSPC-доказательство отсутствует: бэкап УСПЕЛ; spool-fill 99% не отработал (пустой stdout) | фикс spool-fill (маркер fs-pressure), наполнение до ENOSPC |
| T9 cert/secrets-corruption | age на sops-файле вернул «unexpected intro» с rc=0 (ожидался чёткий fail) | уточнить критерий теста: fail по stderr-паттерну, не по rc |
| T10 restore-drill | «T10 extract FAIL: (пусто)» — restore-канал вернул пустой вывод | фикс extract-канала (stdout/поток) |
| T11 reboot | self-heal GREEN по существу; формально RED (cross-boot audit при изолированном rerun) | в полном прогоне окна T11 идёт последним — кросс-бут маркеры T1-T10 накопятся |

### 1.3 Каналы, подтверждённые кодом

- **docker/login-action**: platform-test.yml:171-174, гейт `DOCKER_HUB_AUTH` (C-11, TRAP[BUG] 141 — условия через env, не secrets) — механизм готов, секретов нет.
- **dispatch-канал**: authorized_keys на нодах пишутся φ2 `users.py::add_ssh_key` (команда `python3 -m core.internal.deploy.orchestrator_cli dispatch`, единственный писатель — DevPlan 116 B1); deploy.sh — переходный фасад (0 inline python3, keep 119 D8).
- **AGE-детекция**: `node_detect.py::detect_age_key()` — env-цепочка (AGE_SECRET_KEY → SOPS_AGE_KEY → AGE_SECRET_KEY_FILE → ~/.config/age/keys.txt → /etc/age/key.txt fallback); канон — env → tmpfs decrypt-only (140 W4); AGE_SECRET_KEY присутствует в gh (2026-08-06).
- **DR-процедуры**: docs/age-master-key-dr.md §2 (off-node encrypted backup: sops encrypt → приватный bucket → sha256) и §3 (restore-first: bootstrap до φ4 → доставка .enc → расшифровка на ноде → secrets-unlock → сверка).

---

## 2. TRAP[DECISION]

⚠️ TRAP[DECISION] · 2026-08-11 · HI · R15: канал ci-deploy УЖЕ настроен — W1 сводится к секрету (выполнено агентом)
· Rejected: (а) «выгрузить из gh-секрета» — секрета не было; (в) root-dispatch — неканон;
  (г) регенерация пары по runbook — не нужна (ключ совпадает с authorized_keys ноды);
  (д) создание нового ключа platform_personal — отклонено оператором («не надо его добавлять»).
· Reason: суперпозиция 147 (Option A, score 9/10) + решение оператора 2026-08-11 22:05 («делай всё сам,
  я сделал ssh-add с основным ключом доступа»). Live-проверка: /home/ci-deploy/.ssh/authorized_keys
  содержит forced-command dispatcher с pub, идентичным локальному (~/.ssh/platform_personal_cicd.pub);
  `ssh -i ~/.ssh/platform_personal_cicd ci-deploy@tronyx-vps ping` → `pong`. Модель ключей:
  platform_personal_cicd — ТОЛЬКО CI/CD (пользователь ci-deploy, forced-command, без интерактива);
  ручной доступ — основной ключ оператора (root). W1.1 выполнен агентом: CI_DEPLOY_KEY установлен
  в tronyx161/ai-platform + TronyxLab/botanika, dance-site, roadmap, tronyx-site (15 секретов).
· Rev: если CI-деплой проекта после W1 всё же не пройдёт — проверить deploy-project.yml:286-289
  (формат PEM/base64) и логи receive; регенерация пары — только при компрометации.

⚠️ TRAP[DECISION] · 2026-08-11 · MED · R17: read-only Docker Hub токен от оператора — единственный путь
· Rejected: (а) анонимные пуллы + retry (корень не устраняется, CI остаётся хрупким);
  (в) ghcr-прокси/зеркала (архитектурный сдвиг без пропорциональной ценности).
· Reason: суперпозиция 147 (Option A, score 9/10). Механизм верифицирован (C-11, TRAP[BUG] 141);
  нужен только токен. `gh secret set DOCKER_HUB_USERNAME/TOKEN -R tronyx161/ai-platform`; код не меняется.
· Rev: если токен не предоставлен до 2026-08-25 — пересмотреть Option B (retry-логика) как interim.

⚠️ TRAP[DECISION] · 2026-08-11 · HI · S-13: полный DR-drill по канону + автоматизация make age-key-backup
· Rejected: (а) backup-only без пересоздания ноды (не тестирует restore-first, формально не
  закрывает T12.12); (в) ручная процедура без make-таргета (невоспроизводима); (г) сокращённый
  drill на живой ноде (не воспроизводит «голую» ноду).
· Reason: суперпозиция 147 (Option A, score 9/10). Канон docs/age-master-key-dr.md §2-3 готов;
  test-e2e доступна для пересоздания (инвариант 9). Автоматизация (W2.2) делает процедуру
  воспроизводимой и проверяемой: Python CLI + Makefile таргет + manifest-запись.
· Rev: если sops CLI недоступен на dev-машине — fallback на age-native encrypt (age-keygen
  реципиент), процедура §2 остаётся каноном.

⚠️ TRAP[DECISION] · 2026-08-11 · MED · Chaos: единое окно T1-T11 + ACME на provisioned tronyx-vps
· Rejected: (а) точечные прогоны только RED (T9-T11 и B7 остаются открытыми — долг мигрирует);
  (в) окно на test-e2e (B7 требует реальных LE-доменов tronyx-vps, production-след не
  проверяется); (г) два окна (двойная стоимость операторского времени).
· Reason: суперпозиция 147 (Option A, score 9/10). Харнесс покрывает T1-T11 целиком; причины
  RED T6-T10 зафиксированы в VR 142 §6 и закрываются pre-window фиксами (W2.1) + диагностикой
  на окне. Fresh ACME DNS-01 (B7) — в том же окне. Дедлайн 2026-09-15.
· Rev: если окно невозможно до 2026-09-15 — T9-T11/B7 переоформляются с новой Rev и
  операторским решением.

⚠️ TRAP[DECISION] · 2026-08-11 · HI · D-139-T1: аудит → удаление deploy.sh → обновление manifest
· Rejected: (а) удаление без аудита (неверифицируемо, риск legacy-нод); (в) оставить как
  fallback (поверхность остаётся, противоречит Rev-условию 117/119); (г) только аудит без
  удаления (не закрывает долг).
· Reason: суперпозиция 147 (Option A, score 9/10). Оба узла пересозданы ≤1 мес (141, 2026-08-06),
  φ2 пишет authorized_keys с orchestrator_cli dispatch — legacy-записей быть не должно. Аудит:
  audit-лог ноды (0 вызовов deploy.sh как forced-command) + authorized_keys обеих нод.
  При 0 вызовов — удаление + manifest/core-AGENTS.md/root-AGENTS.md keep-таблица (строка
  «deploy.sh 175 LOC» удаляется, D7-исключение снимается). Дедлайн 2026-11-01.
· Rev: если аудит найдёт вызовы — deploy.sh остаётся, верификация повторяется после
  следующего деплоя, новая Rev-дата.

---

## 3. Код-граф (XML)

```xml
<devplan number="147-02" slug="critical-debt-closeout">
  <prerequisite>
    <artifact id="01-Brief" status="DONE"/>
    <registry id="145-v2.0" status="DONE" note="10 OPEN + 3 partially-done; 5 критичных"/>
    <verification evidence="2026-08-11 live: пара ЕСТЬ (не авторизована), 14 секретов, CI красный, харнесс жив"/>
  </prerequisite>
  <wave id="W1" name="restore-ci-deploy-channel" effort="S" blocking="true" operator="true">
    <task action="gh-secret-set" secret="CI_DEPLOY_KEY" value="~/.ssh/platform_personal_cicd" status="DONE 2026-08-11"
          repos="tronyx161/ai-platform, TronyxLab/botanika, TronyxLab/dance-site, TronyxLab/roadmap, TronyxLab/tronyx-site"/>
    <task action="verify-channel" status="DONE 2026-08-11">
      <change>authorized_keys /home/ci-deploy/.ssh/ содержит forced-command dispatcher, pub идентичен
             локальному; ssh -i ~/.ssh/platform_personal_cicd ci-deploy@tronyx-vps ping → pong</change>
    </task>
    <task action="gh-secret-set" secret="DOCKER_HUB_USERNAME" operator_token="true" repo="tronyx161/ai-platform"/>
    <task action="gh-secret-set" secret="DOCKER_HUB_TOKEN" operator_token="true" repo="tronyx161/ai-platform"/>
    <task action="verify" cmd="gh secret list -R tronyx161/ai-platform"/>
    <task action="verify-ci-deploy" note="push в TronyxLab/* → deploy-project.yml → receive → DEPLOYED"/>
    <task closes="D-142-R15, D-142-R17, D-136-B6"/>
  </wave>
  <wave id="W2" name="pre-window-code" effort="M" requires="W1(optional)">
    <task action="edit" path="tests/e2e/test_chaos_resilience.py" closes="D-126-D5, D-126-T9-T11, D-142-Chaos-T6-T10">
      <change>T6: инъекция kill -9 PID postgres внутри контейнера + валидация маркеров interrupted/ready</change>
      <change>T7: уточнить grep OOM-victim (Killed process … cgroup clickhouse); диагностика аллокатора</change>
      <change>T8: фикс spool-fill (маркер fs-pressure), наполнение до ENOSPC; убрать пустой stdout</change>
      <change>T9: критерий fail по stderr-паттерну sops/age (не по rc)</change>
      <change>T10: фикс extract-канала restore (пустой вывод → диагностика)</change>
    </task>
    <task action="add" path="core/internal/deploy/age_key_backup.py" new="true" closes="D-136-W10-S-13" effort="S">
      <change>Python CLI: AGE-ключ (env-цепочка node_detect) → sops encrypt → S3 (приватный bucket,
             S3_ENDPOINT_URL) → sha256-сверка; dry-run; ИМП:9 логи; 0 секретов в выводе</change>
    </task>
    <task action="add" path="Makefile" new="true" closes="D-136-W10-S-13">
      <change>make age-key-backup [AGE_RECIPIENT=...] — фасад python3 -m core.internal.deploy.age_key_backup</change>
    </task>
    <task action="edit" path="core/entrypoint-manifest.yaml" closes="D-136-W10-S-13">
      <change>запись age-key-backup в allowed_verbs + delegates_to (генератор манифеста)</change>
    </task>
    <task action="edit" path="tests/unit/test_age_key_backup.py" new="true">
      <change>unit-тесты: encrypt→upload→sha256 (mock sops/S3), dry-run, отсутствие секретов в stdout</change>
    </task>
    <task action="verify" cmd="make check"/>
  </wave>
  <wave id="W3" name="operational-windows" effort="L+XL" operator="true" requires="W1, W2">
    <task action="drill" closes="D-136-W10-S-13" deadline="2026-08-31">
      <change>DR-drill AGE по docs/age-master-key-dr.md §2-3: make age-key-backup → off-node encrypted
             backup в приватный bucket → restore-first на пересозданной test-e2e (bootstrap до φ4 →
             доставка .enc → расшифровка на ноде tmpfs → secrets-unlock → сверка известного значения);
             evidence в .ai/plans/147-critical-debt-closeout/evidence/</change>
    </task>
    <task action="chaos-window" closes="D-126-D5, D-126-T9-T11, D-136-B7, D-142-Chaos-T6-T10" deadline="2026-09-15">
      <change>полный прогон T1-T11 на provisioned tronyx-vps (pytest tests/e2e/test_chaos_resilience.py -m requires_node);
             T7 OOM victim=clickhouse, T8 ENOSPC, T9 cert/secrets corruption, T10 restore-drill,
             T11 reboot + cross-boot; fresh ACME DNS-01 (acme.sh --issue, B7); regression T4/T5/T6;
             финальные статусы T1-T11 → реестр 145</change>
    </task>
    <task action="audit" closes="D-139-T1" deadline="2026-11-01">
      <change>SSH на ноды (tronyx-vps + test-e2e): audit-лог — 0 вызовов deploy.sh как forced-command;
             authorized_keys — все записи = orchestrator_cli dispatch; результат → W4</change>
    </task>
  </wave>
  <wave id="W4" name="finalize" effort="S+M" requires="W3(audit)">
    <task action="delete" path="core/entrypoints/deploy.sh" closes="D-139-T1" condition="0 вызовов в аудите">
      <change>удалить файл; обновить entrypoint-manifest.yaml (deploy delegates_to — убрать
             legacy-local-entrypoint), core/AGENTS.md (цепочка make deploy), root AGENTS.md
             keep-таблица (снять строку deploy.sh 175 LOC / D7)</change>
    </task>
    <task action="edit" path=".ai/plans/145-repo-cleanup-debt-registry/00-TECHNICAL-DEBT-REGISTRY.md">
      <change>v2.1: D-142-R15, D-142-R17, D-136-B6, D-136-W10-S-13-drill, D-126-D5, D-126-T9-T11,
             D-142-Chaos-T6-T10, D-136-B7, D-139-T1, D-J4 → [CLOSED] с evidence-ссылками;
             метрики пересчитаны</change>
    </task>
    <task action="archive" closes="D-J4">
      <change>126-chaos-resilience/files/ (6.0M) + 141-server-recovery/evidence/ (828K) →
             .tar.gz в .ai/plans/_archive/; git rm --cached + .gitignore</change>
    </task>
    <task action="edit" path="docs/age-master-key-dr.md">
      <change>W12 completion: drill-статусы [x] off-node backup, [x] restore-first; дата evidence</change>
    </task>
    <task action="verify" cmd="make check && make check-manifests"/>
  </wave>
  <verification>
    <task action="gh-secret-list" expect="CI_DEPLOY_KEY + DOCKER_HUB_* present (17)"/>
    <task action="ssh-ping" expect="pong через ci-deploy ключ"/>
    <task action="gh-run" expect="platform-test GREEN"/>
    <task action="ls" expect="evidence/147: drill + chaos + audit"/>
    <task action="rg" expect="deploy.sh вне .ai/plans = 0 (при удалении)"/>
    <task action="registry" expect="v2.1, 0 критичных OPEN"/>
  </verification>
</devplan>
```

---

## 4. Волны

### W1 — Восстановление CI-канала деплоя (BLOCKING, оператор, ~30 мин)

**Цель:** разблокировать `make deploy-project` / e2e MODE=remote (R15) и CI-джобы (R17).

**Контекст (сверка 2026-08-11):** forced-command канал на ноде **уже настроен** —
`/home/ci-deploy/.ssh/authorized_keys` содержит запись `command="cd /opt/platform && PYTHONPATH=/opt/platform
python3 -m core.internal.deploy.orchestrator_cli dispatch",restrict` с pub, **идентичным** локальному
`~/.ssh/platform_personal_cicd.pub`; `ssh -i ~/.ssh/platform_personal_cicd ci-deploy@tronyx-vps ping` → `pong`.
Пользователя `tronyx` на ноде нет (канон φ2: ci-deploy ключ пишется пользователю `ci-deploy`,
phases/system.py:332-334; tronyx@ — ошибочный пользователь в ранней проверке). CI_DEPLOY_KEY в gh
отсутствовал (14 секретов); CI красный (platform-test 08-10, Build Platform Agent 08-09).

**Модель ключей (решение оператора 2026-08-11 22:05):**
- `~/.ssh/platform_personal_cicd` — **ТОЛЬКО CI/CD**: forced-command dispatcher, пользователь `ci-deploy`,
  интерактивный вход невозможен (restrict). Не используется для ручного доступа.
- Ручной операторский доступ — **основной ключ** (ssh-add `Tronyx (ED25519)` выполнен, root-доступ работает).
- Отдельный ключ `platform_personal` **НЕ создаётся** (отклонено оператором).

**Шаги (исполняет агент; авторизация оператора — «делай всё сам»):**
1. ✅ **DONE (2026-08-11 22:06 MSK):** `gh secret set CI_DEPLOY_KEY` из `~/.ssh/platform_personal_cicd`
   (плоский PEM; workflow принимает PEM и base64 — deploy-project.yml:286-289) во все 5 репозиториев:
   `tronyx161/ai-platform`, `TronyxLab/botanika`, `TronyxLab/dance-site`, `TronyxLab/roadmap`, `TronyxLab/tronyx-site`.
   Верифицировано: `gh secret list` = 15 секретов; CI_DEPLOY_KEY присутствует во всех 5 репо.
2. ✅ **DONE:** верификация канала — `ssh -i ~/.ssh/platform_personal_cicd ci-deploy@tronyx-vps ping` → `pong`
   (dispatch: `[IMP:9][parse_ssh_command] Parsed: verb=ping`).
3. **R17 (осталось):** оператор создаёт read-only токен Docker Hub → `gh secret set DOCKER_HUB_USERNAME -R tronyx161/ai-platform` и `gh secret set DOCKER_HUB_TOKEN -R tronyx161/ai-platform` (интерактивный ввод, без вывода значения). Единственная оставшаяся операторская зависимость W1.
4. Верификация после R17:
   - `gh secret list -R tronyx161/ai-platform` → 17 секретов;
   - `gh run list` → platform-test GREEN (закрывает R17);
   - push в любой проект TronyxLab → deploy-project.yml → receive → `DEPLOYED healthy` (закрывает B6).

**Приёмка:** CI-деплой реального проекта проходит; e2e MODE=remote работает; D-142-R15/R17/B6 закрыты.

### W2 — Pre-window код: chaos-харнесс T6-T10 + make age-key-backup (M)

**Цель:** устранить известные причины RED T6-T10 (VR 142 §6) до chaos-окна; автоматизировать
off-node backup AGE-ключа для drill'а.

| Задача | Файл | Действие | Закрывает |
|--------|------|----------|-----------|
| T6 fix | `tests/e2e/test_chaos_resilience.py::test_t06_postgres_sigkill_under_load` | диагностика инъекции: kill -9 PID postgres внутри контейнера; валидация маркеров `docker:postgres-interrupted`/`postgres-ready` (не 0) | D-142-Chaos-T6-T10 |
| T7 fix | `test_t07_oom_kill_clickhouse` | уточнить grep OOM-victim: `journalctl -k` → `Killed process … (…clickhouse…)` / cgroup-имя; диагностика аллокатора (bash внутри clickhouse, лимит 1GiB уже есть) | D-126-D5 |
| T8 fix | `test_t08_disk_pressure_92` | spool-fill: маркер fs-pressure вместо пустого stdout; наполнение до реального ENOSPC (не 92%) | D-126-T9-T11 (маркеры), D-142-Chaos |
| T9 fix | `test_t09_cert_and_secrets_corruption` | критерий: fail по stderr-паттерну sops/age («unexpected intro»), не по rc | D-142-Chaos-T6-T10 |
| T10 fix | `test_t10_restore_drill_drop_db` | extract-канал restore: диагностика пустого вывода (stdout/поток) | D-142-Chaos-T6-T10 |
| age-key-backup | `core/internal/deploy/age_key_backup.py` (новый) | Python CLI: ключ по env-цепочке node_detect → sops encrypt (AGE_RECIPIENT) → S3 приватный bucket (S3_ENDPOINT_URL) → sha256-сверка; dry-run; IMP:9; 0 секретов в stdout | D-136-W10-S-13 (автоматизация) |
| age-key-backup | `Makefile` + `core/entrypoint-manifest.yaml` | `make age-key-backup` — фасад; запись в allowed_verbs (через генератор манифеста) | D-136-W10-S-13 |
| unit | `tests/unit/test_age_key_backup.py` (новый) | encrypt→upload→sha256 (mock sops/S3), dry-run, отсутствие секретов в выводе | — |
| verify | — | `make check` зелёный; харнесс-фиксы не ломают статические тесты | — |

**Приёмка:** make check GREEN; `make age-key-backup --dry-run` корректен; manifest с записью
age-key-backup; T6-T10 причины RED закрыты фиксами или задокументированы как diagnostic-остаток
для окна.

### W3 — Операционные окна (L/XL, оператор, дедлайны из реестра)

| Окно | Дедлайн | Закрывает | Содержание |
|------|---------|-----------|------------|
| **DR-drill AGE** | **2026-08-31** | D-136-W10-S-13 | (1) `make age-key-backup` → sops-encrypted backup мастер-ключа в приватный bucket (sha256-сверка до удаления локального plaintext); (2) restore-first на пересозданной test-e2e: bootstrap до φ4 (ожидаемая остановка на secrets-provision) → доставка `.enc` по защищённому каналу → расшифровка НА ноде (tmpfs /dev/shm, 0600, dd-wipe) → `make secrets-unlock` → сверка известного значения (напр. POSTGRES_PASSWORD); (3) evidence: sha256-вывод, log bootstrap, secrets-unlock log → `.ai/plans/147-critical-debt-closeout/evidence/` |
| **Chaos-окно** | **2026-09-15** | D-126-D5, D-126-T9-T11, D-136-B7, D-142-Chaos-T6-T10 | Полный прогон T1-T11 на provisioned tronyx-vps: `pytest tests/e2e/test_chaos_resilience.py -m requires_node` (харнесс + chaos_audit.py); T7 — OOM жертва = clickhouse (маркер victim_named); T8 — ENOSPC-доказательство; T9 — cert/secrets corruption (критерий по stderr); T10 — restore-drill; T11 — reboot + cross-boot audit (последним); fresh ACME DNS-01: `acme.sh --issue` на 1 домене (B7, rate-limit-осторожно); regression T4/T5/T6; финальные статусы T1-T11 → реестр 145 |
| **Аудит deploy.sh** | **2026-11-01** | D-139-T1 | 2 SSH-проверки: (1) audit-лог нод (tronyx-vps, test-e2e) — 0 вызовов deploy.sh как forced-command после деплоя; (2) authorized_keys — все записи = `orchestrator_cli dispatch`. Результат → W4 |

**Приёмка:** evidence drill'а (≤2026-08-31), chaos-прогона (≤2026-09-15) и аудита (≤2026-11-01)
в `.ai/plans/147-critical-debt-closeout/evidence/`; статусы в реестре 145.

### W4 — Финализация (S+M)

| Задача | Файл | Действие | Условие |
|--------|------|----------|---------|
| Удаление deploy.sh | `core/entrypoints/deploy.sh` | удалить (175 LOC) | **0 вызовов в audit-логах** (W3.3) |
| Manifest | `core/entrypoint-manifest.yaml` | deploy delegates_to: убрать legacy-local-entrypoint → только CI-цепочка receive | то же |
| core-AGENTS.md | `core/AGENTS.md` | цепочка `make deploy` — без deploy.sh | то же |
| root-AGENTS.md | `AGENTS.md` | keep-таблица: снять строку «deploy.sh 175 LOC, D7»; мигрированный список | то же |
| Реестр v2.1 | `00-TECHNICAL-DEBT-REGISTRY.md` | 10 пунктов → [CLOSED] с evidence; категории B/C/D итоги; метрики пересчитаны; топ-5 → L3/L4/hermes-500 | — |
| Evidence-архивация | `126-chaos-resilience/files/` + `141-server-recovery/evidence/` | `.tar.gz` → `.ai/plans/_archive/`; git rm --cached + .gitignore (D-J4) | после chaos-закрытия |
| DR-документ | `docs/age-master-key-dr.md` | W12 completion: drill-статусы [x]; evidence-ссылки | после drill'а |

**Приёмка:** `make check` + `make check-manifests` зелёные; `rg "deploy.sh" core/ tests/` = 0
(вне .ai/plans); реестр v2.1.

---

## 5. Acceptance Criteria (контрольный лист)

- [ ] **AC1 (W1):** ✅ CI_DEPLOY_KEY установлен во все 5 репозиториев (15 секретов, 2026-08-11); ✅ `ssh -i ~/.ssh/platform_personal_cicd ci-deploy@tronyx-vps ping` → `pong` (канал настроен). Остаток: DOCKER_HUB_USERNAME/DOCKER_HUB_TOKEN в gh (17 секретов) → platform-test GREEN; push в TronyxLab/* → CI-деплой → `DEPLOYED healthy`. (R15 ✅, R17, B6)
- [ ] **AC2 (W2):** `make check` зелёный; фиксы T6-T10 в харнессе (или задокументированный diagnostic-остаток); `make age-key-backup --dry-run` работает; запись age-key-backup в manifest.
- [ ] **AC3 (W3):** evidence drill'а (≤2026-08-31) и chaos-окна (≤2026-09-15) в `.ai/plans/147-critical-debt-closeout/evidence/`; финальные статусы T1-T11 + fresh ACME (B7) в реестре; аудит deploy.sh проведён.
- [ ] **AC4 (W4):** deploy.sh удалён при 0 вызовах + manifest/AGENTS.md обновлены (≤2026-11-01); реестр v2.1, 0 критичных OPEN; evidence 126/141 заархивированы (D-J4).

---

## 6. Verification (post-execution)

```bash
# 1. Secrets + канал (W1)
gh secret list -R tronyx161/ai-platform | rg "CI_DEPLOY_KEY|DOCKER_HUB"
ssh -i ~/.ssh/platform_personal_cicd -o ConnectTimeout=10 ci-deploy@tronyx-vps ping
# Expected: "pong" (DONE 2026-08-11)

# 2. CI (W1)
gh run list -R tronyx161/ai-platform -L 3 --json name,conclusion
# Expected: platform-test success

# 3. Code (W2)
make check                                              # Expected: green
make age-key-backup --dry-run                           # Expected: rc=0, 0 секретов в stdout
rg -n "age-key-backup" core/entrypoint-manifest.yaml    # Expected: запись в allowed_verbs

# 4. Окна (W3) — evidence-файлы:
ls .ai/plans/147-critical-debt-closeout/evidence/       # drill + chaos + audit

# 5. Finalize (W4)
rg -rn "deploy.sh" core/ tests/ --glob '!*.pyc'         # Expected: 0 (вне .ai/plans)
rg -n "Версия реестра" .ai/plans/145-repo-cleanup-debt-registry/00-TECHNICAL-DEBT-REGISTRY.md
# Expected: 2.1
make check-manifests                                    # Expected: green
```

---

## 7. Зависимости от оператора (вне этого девплана)

1. **W1 (остаток):** read-only Docker Hub токен (личный кабинет) → `gh secret set DOCKER_HUB_USERNAME/DOCKER_HUB_TOKEN -R tronyx161/ai-platform`. CI_DEPLOY_KEY и авторизация канала — **выполнены агентом** (решение оператора 2026-08-11 22:05: «делай всё сам, ssh-add основного ключа сделан»).
2. **W3:** окна 2026-08-31 (DR-drill, пересоздание test-e2e) и 2026-09-15 (chaos, tronyx-vps) — операторские; sops CLI на dev-машине (или fallback age-native).
3. **W4:** подтверждение удаления deploy.sh по результатам аудита (при 0 вызовов — удалять; при вызовах — оставить + новая Rev).
4. **W2:** фиксы T6-T10 — код (не требуется оператор), но прогон окна — после W1 (provisioned нода).

$END_DEVPLAN
