# Minimal Fix Plan — минимальный путь к «голый сервер → одна команда → DONE»

Без большого рефакторинга. Приоритет: P0 = блокирует воспроизводимость цели; P1 = серьёзная нестабильность; P2 = позже; IGNORE = не влияет на цель (не перечисляется).

## P0

### 1. Deploy-arena минимальный срез (T1–T9 из плана 026, single-node headless)

```text
Problem    Full cold-bootstrap проверяется только вручную на проде; детектор cold-only регрессий отсутствует (RC1/RC3).
Why        Требует чистой VM; тесты requires_node ручные; в CI cold-bootstrap отсутствует; каждая ручная валидация = часы и новые находки.
Minimal    ~/projects/deploy-arena/: scenario single-node (multipass VM), arena up → make bootstrap-node → healthcheck → arena down.
           Обязательный шаг сценария: повторный bootstrap = no-op; converge rc=0; node-update rc=0 (идемпотентность).
Verify     arena up/verify/down зелёный на чистой VM; повторный up — no-op дрейфа; запуск дважды подряд.
```

### 2. CI-required: arena-cold как required-check на main

```text
Problem    platform-test красен 08-17→09-02, промоуты не блокировались; cold-bootstrap регрессии доезжали до прод-нод (RC5/RC7).
Why        Нет блокирующего сигнала: CI зелёность не была обязательной; runner-контекст не реплицирован локально.
Minimal    Nightly (или per-push при стоимости <15 мин) arena-cold в GitHub Actions на self-hosted/multipass runner + branch protection: required checks = push-gate, platform-test, arena-cold.
Verify     Внесение регрессии в bootstrap-контур → arena-cold красный → merge блокирован.
```

### 3. Pre-flight верб: validate-node-input до SCP-фазы

```text
Problem    Входной контракт (AGE-ключ канонической формы, sops-файлы, DNS-разрешимость, SSH-ключи, node-configs) не проверяется; падения приходят на φ4/φ8 после SCP и φ1–φ3 (RC4/RC6).
Why        Диагностика глубины фаз затратна (φ4 AGE = 5 диагностических коммитов); внешние блокеры маскировались под продукт.
Minimal    `make validate-node-input NODE=<n>`: single-line AGE-ключ (env/file, приоритет), наличие <node>.enc.yaml и required sops-ключей, DNS A-записи доменов node.yaml, SSH-доступность, ci_deploy_key/owner_key форма. Вызывать первым шагом bootstrap.sh (fail-loud, 0 remote-действий).
Verify     Кривой AGE-ключ/отсутствующий sops/DNS без A-записи → exit 1 с указанием причины ДО любого SSH.
```

### 4. Final-verify фаза bootstrap: end-state assertions до exit 0

```text
Problem    Успех определялся rc фаз, а не фактом (RC2): серты «provisioned» пустые, vhost 0/3, pull-токен skip.
Why        Компоненты репортят «отработал», не «цель достигнута»; dependency-gate принимает done_with_warnings.
Minimal    Лёгкая фаза после φ8.5 (в state machine, с checkpoint): (a) серты всех exposed-доменов на диске; (b) secrets.env полный (verify_required_sops_secrets re-run); (c) exposed проекты healthy ИЛИ awaiting-CI с отрендеренным vhost; (d) приватные образы достижимы (GHCR токен ≠ skip). FAIL → exit 10.
Verify     Провал любой из 4 assertion'ов на чистой ноде ловится в фазе, а не на следующем слое; повторный bootstrap — no-op этой фазы.
```

## P1

### 5. Env-hermeticity guard для тестов

```text
Problem    NODE_NAME-класс утечек ×4; NODE_NAME-утечка дал ложный зелёный DR-restore на сутки (RC7).
Why        Ручные os.environ-snapshot'ы в фикстурах; системного garde нет.
Minimal    Session-scoped fixture: snapshot/clean os.environ для платформенных ключей + grep-гейт «тест не пишет NODE_NAME/AGE_SECRET_KEY в env». Включить в check-suite.
Verify     Внесение polluter-теста → check красный с указанием утечки.
```

### 6. Runner-контекст-тесты в arena (CI-класс)

```text
Problem    job-level env ×2 (взаимный откат), shallow ×2, gitleaks rename — каждый подкласс повторился (RC5).
Why        Deploy-канал и гейты верифицируются только живым прогоном.
Minimal    В arena/CI: (a) shallow-clone smoke → pin-freshness gate поведение; (b) тест setup-gitleaks на upstream-ассет-контракт (версия-префиксный fallback уже внесён — гейтизировать); (c) локальная реплика dispatch-парсинга (shlex уже внесён — оставить negative-тесты).
Verify     Имитация runner-контекста в CI job → гейты ловят оба класса до push.
```

### 7. honesty-гейты в чек-сьют

```text
Problem    Silent-точки закрыты точечно, класс может вернуться на новом компоненте (RC2).
Why        Нет гейта, ловящего новые silent-точки.
Minimal    Конвенция-гейт: для модулей deploy/converge — тест post-condition (успех обязан верифицировать результат); известные паттерны (безусловный success-лог, rc без post-check) — статическая проверка.
Verify     Новый «успех без проверки результата» в core/internal → гейт RED.
```

## P2 (позже, не блокирует)

- Hardcode-probe слой: версии docker/kernels/upstream-ассеты — пин + probe-шаг с честным degrade (8 инстансов недели).
- R9 cooldown → честный «drift-masked» статус вместо молчаливого skip.
- Doxygen/docs-дрейф — уже гейтится, не трогать.

## Acceptance Test (DONE)

```text
1. Чистая VM (multipass или свежий VPS), подготовлены только: sops-секреты в node-configs, AGE-ключ канонической формы, DNS A-записи, SSH-ключи в node.yaml — ничего проектно-специфичного вручную.
2. make validate-node-input NODE=<n> → PASS (pre-flight).
3. make bootstrap-node NODE=<n> → exit 0 за предсказуемое время; strict-INIT; 0 ручных вмешательств.
4. Все enabled-модули healthy; exposed-проекты live (или awaiting-CI с vhost и stub-семантикой); e2e-verify PASS.
5. Повторный make bootstrap-node → no-op (rc=0, delivered=0); converge → FULLY CONVERGED; node-update → rc=0.
6. Reboot ноды → все контейнеры Up, healthcheck ALL MODULES HEALTHY без ручных шагов.
7. CI-канал: git push проекта → build → receive → deploy → healthy; CI main зелёный на required-check'ах.
8. Весь сценарий (2–5) исполняется автоматически командой arena up/verify/down — не руками.
9. Тот же сценарий на второй чистой VM → тот же результат.
10. Понижение: удаление серта/одного контейнера/секрета → converge самолечит (F-02/F-09 контракт) или fail-loud с указанием причины — ни одно состояние не маскируется rc=0.
```

DONE = пункты 1–9 пройдены автоматически (арена/CI), а не оператором; пункт 10 — контракт честности.
Текущее состояние по этой шкале: 1–7 выполнены вручную в 027-B; 8 (автоматизация) и 2 (required-check) — не начаты; это и есть минимальный остаток пути.