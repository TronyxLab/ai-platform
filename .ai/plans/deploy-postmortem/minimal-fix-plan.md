# Minimal Fix Plan — что реально нужно, чтобы «голый сервер → одна команда → рабочая система» стало воспроизводимым

Без большого рефакторинга. Только то, что разрывает цикл «deploy → ошибка → fix».

---

## A. Что реально сломано (5–10 пунктов)

1. **Fail-soft на критических путях** — overlay clone (WARN+return 1), required-секреты (autogen-only при отсутствии enc-файла), серты («converged» при None). Нода репортит success при отсутствующем контексте.
2. **Converge/чекпойнт не верифицирует желаемое состояние** — дрейф (stub→0 vhosts, absent module→no action, cert не восстановлен) невидим до фейла следующего этапа.
3. **Readiness подменён healthcheck** — холодный старт зависимых (langfuse↔clickhouse, litellm, Loki, hermes) недетерминирован.
4. **Нет обязательного clean-server гейта** — красный CI 2.5 недели ничего не блокировал.
5. **Node-side overlay deploy-key + SSH-алиас — ручной шаг на сервере**, не провижинится фазой.
6. **CI-канал — параллельная реализация** локального канала (дрейф F-05/06/07).
7. **AGE-key транспорт** — три канала, конфликт приоритетов (сейчас fail-loud, остаточный риск).

## B. Корневая причина (3–7 пунктов)

1. **Успех заявляется, а не проверяется** — нет пост-условий/readiness; «healthy» и «фаза done» — liveness/чекпойнт, а нужен «serving/desired-state verified».
2. **Bootstrap — чекпойнт-skip, а не реконсиляция** — идемпотентность как «done → skip», не «проверь и действуй».
3. **Нет required clean-server acceptance-гейта** — дрейф контрактов копится невидимо.
4. **Fail-soft вместо fail-loud** на нарушении контракта.
5. **Культура симптом-локальных фиксов** — фикс не проверяется против инвариантов «2-й запуск = no-op» и «local ≠ node/CI path» → фикс A ломает этап N+1.

## C. Что является просто следствием (симптомами)

Десятки багфиксов сводятся к двум-трём первопричинам:

- **Все 13 «silent-success» фиксов** + **8 «readiness/order» фиксов** + **3 «drift-detection»** + **5 «idempotency»** = один корень **«успех = заявка, не проверка»** (~30 фиксов).
- **6-коммитная φ4-сага** + SSH_OPTS = один корень **«нет единого typed transport для секретов»**.
- **F-05/06/07** + channel-pin-чейн = один корень **«CI ≠ локальный канал»**.
- **F-01/02/09/10** + strict-init = один корень **«чекпойнт-skip маскирует дрейф»**.

То есть ~73 fix-коммита → ~4 реальных первопричины, а не 73 независимых проблем.

## D. Минимальный план исправления (5 шагов, P0)

### D-1. Fail-loud вместо silent-success на 3 критических точках
- **Problem:** нода говорит «READY» при отсутствующем overlay/секретах/сертах.
- **Why:** `context_overlay.py:285-295` WARN+return 1; `helpers/secrets.py:224` гейтит пост-чек наличием enc-файла; `ssl_provision` мапил None в «converged».
- **Minimal fix:** (a) clone-фейл → hard error (или явный статус `missing`, выводимый в вердикт); (b) пост-чек required∧sops выполнять **безусловно** (enc-файл отсутствует → hard error, не autogen-деградация); (c) `ssl_provision` уже получил честный статус-маппинг (`848576a`) — распространить fail-closed на «extractor None».
- **Verify:** bootstrap на ноде без overlay-ключа / без enc-файла должен падать с явной ошибкой, а не репортить success.

### D-2. Converge = verify-desired-state, не чекпойнт-skip
- **Problem:** дрейф (F-01 stub, F-02 серт, F-09 absent module) невидим.
- **Why:** skip-путь проверяет `status+hash`, не существование артефакта.
- **Minimal fix:** добавить пост-условие на критические юниты (vhost-render: rendered-count == exposed-projects; R-ssl: серт существует; R9: absent-but-enabled = дрейф — уже сделано `33a633d`). Унифицировать: «done» = артефакт существует И hash совпал.
- **Verify:** удалить live-серт / vhost / контейнер → повторный converge восстанавливает (не «no action»).

### D-3. Readiness-гейт на деплой-пути
- **Problem:** холодный старт зависимых недетерминирован.
- **Why:** единственный сигнал — Docker health status.
- **Minimal fix:** в деплой-оркестраторе ждать «зависимый сервис обслуживает операцию» (для langfuse — clickhouse миграции прошли; для litellm — /health отдаёт ok; для loki — /ready 200), а не только `service_healthy`. Один помощник `wait_ready(service, probe)` переиспользуется всеми.
- **Verify:** destroy + cold bootstrap N раз подряд — langfuse/litellm/loki стартуют без ручных ретраев.

### D-4. Обязательный clean-server гейт
- **Problem:** красный CI 2.5 недели не блокировал промоуты.
- **Why:** нет required-check на main; нет real-from-zero дрилла.
- **Minimal fix:** (a) required `platform-test` (+ `platform-gate-fast`) на push в main; (b) добавить дрилл «destroy → `make bootstrap-node` → verify» (на test-VPS, он же закрывает release-checklist item 1).
- **Verify:** коммит, ломающий smoke-контракт, блокирует merge; clean-дрилл в CI зелёный.

### D-5. Overlay deploy-key — в фазу, не в руки
- **Problem:** node-side `~/.ssh/id_ed25519_github_overlay` + `github.com-overlay` алиас ставятся человеком (`context_initializer.py:665-672` печатает шаги).
- **Why:** ключ приватного overlay не провижинится пайплайном.
- **Minimal fix:** минимальный — фаза/`make new-context` устанавливает ключ+алиас через core-канал (SCP/rsync), либо отсутствие → hard error (см. D-1). Не оставлять WARN.
- **Verify:** bootstrap на чистой ноде клонирует приватный overlay без ручного scp/chmod/ssh-config.

## E. Acceptance Test (DONE-критерий)

1. Новый чистый сервер (свежий Ubuntu, доступен только root SSH — единственный предусловие оператора).
2. У оператора есть канонические входы: `node-configs/<NODE>/node.yaml`, `<NODE>.enc.yaml`, AGE-ключ, проекты `~/projects/<ctx>/<p>/` (это «код/конфиг», не серверная подготовка).
3. **Одна команда:** `make bootstrap-node NODE=<node>`.
4. Команда **exit 0** и система READY **с первой попытки** (не после фикса на этой же ноде).
5. Все enabled-модули **healthy И обслуживают** (readiness подтверждён, не только health status).
6. **Все** проекты контекста live (deployed + healthy + reachable по HTTPS) — без `skipped=no_local_source` на проектах с доступным source.
7. Healthchecks pass; e2e-verify зелёный.
8. **Ни одного ручного действия на сервере** (никаких scp ключей, правки ssh-конфига, ручного клона overlay).
9. Повторный запуск **exit 0 и no-op** (converge не ре-мутирует, node-update не `done_with_warnings`).
10. Тот же результат на **втором чистом сервере** (или destroy→re-bootstrap той же ноды) — воспроизводимость, не удача.

**DONE** = пункты 1–10 выполнены на двух независимых чистых серверах без единого ручного шага на сервере.
