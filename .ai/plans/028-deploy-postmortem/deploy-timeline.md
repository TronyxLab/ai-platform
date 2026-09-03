# Deploy Timeline — эволюция проблем 2026-08-25 → 09-02

Источники: git log (154 коммита), планы 011/014/017/018/020/022 восстановлены из git (удалены 09-01), живые планы 012/013/015/016/026/027, .ai/logs/runs.jsonl (1533 записи, 530 падений), 558 evidence-логов /tmp/*.log.

## Хронология попыток

| # | Дата | Кампания | Нода | Главные ошибки → фиксы | Результат |
|---|---|---|---|---|---|
| 1 | 08-26 ночь | 011 launch-validation | tronyx-vps (пересоздана SC2) | φ8 sops-матрица required (F-014); NGINX_OVERLAY_DIR только для nginx + **exit 0 при 3 failed** (F-015); S3-кеш мёртв — boto3 (F-019); **reboot = полный отказ** — platform-secrets.service без PYTHONPATH (F-037, P0) | bootstrap rc=0 после 2 workarounds; 25/25 healthy; reboot долечен drop-in'ом на ноде; CI billing-block |
| 2 | 08-26 вечер | 014 re-validation | tronyx-vps (пересоздана) | secrets-unlock exit 10 — fail-loud без source=sops (F-05); φ8 dry-run isolated `-f` → «undefined volume» (F-07); S3-кеш мёртв снова (F-08) | rc=0 после инлайн-фиксов (fde3fe8); вердикт PARTIAL |
| 3 | 08-27 | 017 launch-validation | tronyx-vps (пересоздана, 3-й раз) | mass-pull docker.io без retry (F-03); **delivered=0 при 4 проектах** — cold-delivery отсутствовал (F-04); nginx hook `docker exec -T` (F-05); healthcheck без NODE_NAME (F-16); restore 5 дефектов (F-19) | 13 fix-коммитов (волны A–F: 76a95e3, e921910, 5e34401…); **критерий доказан**; промоут отложен |
| 4 | 08-31 | 018 closeout | — | F-21a/b/c watchdog/OOM/node-targets (4 слоя); F-22 NODE_NAME-утечка из тестов; **F-23: nightly-бэкапы не работали с 08-26** (/run/lock, flock ENOENT, rc=0 маскировка) | PASS_WITH_CONDITIONS |
| 5 | 08-31…09-01 | 020 acceptance + launch-validation (2 воркспейса) | tronyx-vps + asi-team-vps | langfuse cold-start гонка → depends_on fix **revert через 15 мин** (86987a9); vhost «rendered» безусловно (F-06); **restore black-hole P0** — drill 017 был false-green (env-форма U=postgres); **pydantic-chain на python 3.12 → φ7 ложно «provisioned»** (379fd01); **re-exec argv потерял script path → lifecycle умер после φ1 на свежей ноде** (e0d0e09); **AGE-сага: zshrc перекрыл, multi-line ломал prelude** — 5 диагностических коммитов + d1337ab; нода пересоздана владельцем посреди валидации | PROVEN + промоут разрешён; SUCCESS на asi после 4 P0 |
| 6 | 09-02 | 027 acceptance-validation | tronyx-vps (пересоздана, 5-й раз) | **F-01 vhost-render 0/3** (stub ai-platform.yaml = expose:false, курица-яйцо с payload delivery); **CI D5: 3 P0 подряд** — gitleaks rename → SSH_OPTS job-env литерал → dispatch shlex; F-02/F-03/F-10 ssl-контур; F-11 chaos; F-12 runner disk; F-13: platform-test красен **с 08-17** | **Критерий ВЫПОЛНЕН**: rc=0 → 3 проекта live → повтор no-op 66s → дриллы G1-G4 → промоут tronyx-lab; CI main зелёный впервые с 08-17 |
| CI | 08-26, 09-02 | deploy-project.yml канал | GitHub Actions | 08-26 billing-block; 09-02 чейн gitleaks→SSH→shlex → GREEN (run 33592708886) | канал E2E подтверждён 1 полным прогоном |

Фактически запусков bootstrap-node в окне — ~30 (logs/make/ + /tmp/bootstrap_*.log), каждый успех — после 2–8 итераций фиксов.

## Порочный цикл фиксов (цепочки A→B→C→A2)

| Цепочка | Проблема | Фиксы по кругу | Настоящая причина | Что закрыло бы класс |
|---|---|---|---|---|
| **Пины deploy-канала** | гейт stale-pin честно сигналил при каждом push | 64c2090 → fa30c22 → 688055c | shallow `--depth=1` делал HEAD «last-touch» каждого файла → пин всегда stale; лечили пере-пином, не снятием shallow | fetch-depth 0 везде + тест runner-контекста |
| **job-level env** | workflow parse error (2419325) → заменили на литерал → литерал не раскрывается (acf4b97) → откат на step-level. Полный круг за 2 дня | 2419325 → acf4b97 | семантика env-раскрытия GH не была формализована и не воспроизводима локально | runner-контекст в arena-сьюте |
| **depends_on** | фикс локального `make up` отвергнут нодой через 15 минут | 64fe57d → 86987a9 (revert) | per-module dry-run не знает cross-module сервисов; канон module.yaml#depends_on | dry-run-паритет локального и нодового пути в тесте |
| **re-exec** | механизм re-exec для pydantic (379fd01) сломал fresh node на следующий день — argv потерял script path | 379fd01 → e0d0e09 | механизм вводился без cold-прогона | тот же arena cold-test |
| **ssl_provision статус** | F-02 добавил R-ssl self-heal → через час F-10: «provisioned» на каждом прогоне, node-update перестал быть идемпотентным | 7da4914 → 848576a → 396cd4d | статус-слово не отражало выполненной работы (CertResult) | честный статус-контракт + тесты all-skipped/issued/restored/failed (сделано) |
| **fail-loud secrets** | семантика дофикшивалась 3 раза, каждый фикс = следующий путь отказа | fde3fe8 (source=sops) → 9b8a6af (module-aware) → b3b3100 (systemd auto-detect) | fail-loud без учёта контекста исполнения сам становился источником отказа | один транспортный контракт + таблица-матрица в тесте |
| **S3/boto3 кеш сертов** | 3 итерации за 3 дня на трёх нодах | 011 F-019 → 014 F-08 → 015 lazy-import → 017 fail-loud | python-deps канал не был верифицируемо идемпотентным; маскирующий skip | final-verify: boto3 import-probe как post-condition (сделано в φ1 self-heal — держится) |

## Среда возникновения классов

- **Только чистая нода**: cold-φ8 (dry-run/isolated -f), pydantic python 3.12, re-exec argv, AGE prelude-транспорт, systemd reboot-путь (PYTHONPATH/NODE_NAME/autogen), prometheus wiring (umask 0700, file_sd — 4 слоя), /run/lock, mass-pull, TLS restore-first, stub-expose.
- **Только CI**: billing, gitleaks rename, job-level env ×2, dispatch shlex, shallow ×2, runner disk, smoke-дрейф.
- **Только тесты/dev**: xdist NODE_NAME-утечка, reset_state, benchmark flake, pyright orphan.
- **Сквозной**: silent rc=0 (vhost на ноде, gitleaks в CI, restore в DR) и env-коллизии (zshrc→нода, тест-env→xdist).

**Тренд прогрессии:** φ8-интерполяции (08-26) → каналы доставки (08-27) → env-транспорт/AGE (09-01) → честность статусов (09-01) → гигиена P2/P3 (09-02). Циклы сходились; их не могла закрыть среда исполнения — детектора чистого прогона не существовало до 027-B.