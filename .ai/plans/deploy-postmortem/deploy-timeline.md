# Deploy Timeline — эволюция «deploy → ошибка → fix» (2026-08-25 → 09-02)

Агрегировано до 24 значимых шагов (не 145 коммитов). Якоря: находки F-01..F-14 (027), φ-фазы (020/022), P0/P1/P19.

## Таймлайн

| # | Попытка | Ошибка | Фикс | Следующая ошибка |
|---|---|---|---|---|
| 1 | 08-25/26 meta-refactoring wave-1 | 7 P0-инфра-багов (DOCKER-USER peer, placement, pg hook silent, provisioner guard, stdin-prelude, fetch-depth) | `ceedffb`, `4e623c1` | P19 CI git-state (`688055c`) |
| 2 | 08-26 011 cold bootstrap tronyx-vps | F-014 abort, F-015 half-stack=exit0, F-019 python deps, F-037 reboot | verdict READY_WITH_FIXES («bare metal не гарантирован») | — |
| 3 | 08-26 012 fast-bootstrap | strict-init exit 2, node-side dry-run φ8, parity-гейты | T1–T19 (`be3a940`, `58ee763`, `d48acae`, `08b0174`) | local≠node compose divergence |
| 4 | 08-27 013/015/016 | chaos >100 мин; post-launch fixes; techdebt | `9810697`, `a8ec907`, `032c375` | D5/G5 blocks держат tails неделями |
| 5 | 08-31 020 cold bootstrap #1 | F-03 langfuse cold-start (clickhouse не готов) | compose `depends_on {clickhouse,pgbouncer}` (`64fe57d`) | F-05 |
| 6 | 020 bootstrap φ8 | **F-05 P0: «service langfuse depends on undefined clickhouse»** — fix валиден в root-include, инвалиден в per-module | revert depends_on, `module.yaml#depends_on`+deploy order (`86987a9`) | F-06 |
| 7 | 020 converge | F-06 «Vhosts rendered» безусловно (rc игнорирован); R7 false-positive | rc+retry+strict φ8; R7 prefix (`5aa2ea1`, `6f08f9e`) | F-08 |
| 8 | 020 node-update | F-08 P0: владелец пересоздал ноду mid-validation | полный re-run cold #2 (A–D fixes уже в core) | — |
| 9 | 020 DR restore | **F-11 P0 restore black-hole** — rc=0 над пустым кластером | statement-terminator + expected-DB fail-closed (`308cbef`) | маскировалось 017 (env-form) |
| 10 | 08-31 020 asi-team-vps minimal cold | F-01 module-aware secrets fail-loud; F-02 φ7 «provisioned» при None (pydantic на py3.12) | `96b42c3`/`9b8a6af`; re-exec 3.14 + lazy import (`379fd01`) | F-03, F-04 |
| 11 | 020-asi reboot | F-03 platform-secrets.service нет NODE_NAME → exit 10; F-04 reboot пишет только 16 ключей | node auto-detect (`b3b3100`); `ExecStartPost ensure` (`9ef5db9`) | — |
| 12 | 09-01 022 cold #2 (fresh) | **F-03 P0: lifecycle умер после φ1** — re-exec потерял argv | `_reexec_argv()` (`e0d0e09`) | F-04 |
| 13 | 022 φ4 | F-04 sops «no identity» — env `AGE_SECRET_KEY=tronyx` бьёт файл | operator override (no code) | F-05 |
| 14 | 022 φ4 | **F-05 P0: multi-line AGE key сломал prelude** | `_canonical_age_key()` (`d1337ab`) | F-06 |
| 15 | 022 φ4 | F-06 tronyx-ключ доходит до CLI-entry | digest-трассировка ×5 (`41ddd6c`…`fc515c1`) | — |
| 16 | 022 φ7/φ8 | bootstrap completes; roadmap DEPLOYED+healthy | — | F-08 (D1) |
| 17 | 022 deploy-context | F-08 nginx-t harness un-pinned → 429 pull | digest-pin (`baa748d`) | F-09, F-12 |
| 18 | 022 sync-env/e2e | F-09 phantom PLATFORM_PROVIDES; F-12 phantom endpoints; F-13 zram reboot | provides∩enabled; comment-strip; zram probe+skip | verdict |
| 19 | 09-01 022 verdict | ПРОМОУТ РАЗРЕШЁН — но C2/D5/D7/F1/F2/F4/G5 BLOCKED | — | 023/024/025 closeout |
| 20 | 09-01/02 022 tails | resolver glob, scaffold deploy-key, runbook | `f9770f4`, `c87f02d`, `ade7ae8` | — |
| 21 | 09-02 027 cold bootstrap (final) | **F-01 P0: exit 10 φ8 — 0/3 vhosts, 4 проекта GENERATED-STUB** | stub-detect (`7da4914`) | F-02/F-03 |
| 22 | 027 converge cert | F-02 нет cert-restore до R6; F-03 `ssl.py` затеняет stdlib `ssl` | R-ssl unit; rename `ssl_certs.py` (`7da4914`) | F-10 |
| 23 | 027 CI (первый real-run) | F-05 gitleaks checksums rename → silent; F-06 SSH_OPTS literal; F-07 quoted args | checksum fallback; step-level runner.temp; shlex (`7da4914`, `acf4b97`, `b955149`) | F-12/F-13/F-14 |
| 24 | 027 CI green (первый с 08-17) | F-13 redis NOAUTH+Loki /ready; F-12 disk OOM; F-14 hermes reset | smoke contract; disk cleanup; retry | verdict |

---

## Порочные циклы фиксов

**VC-1 — service ordering / compose.** local `make up` (root-include) → depends_on (`64fe57d`) → node per-module broke → revert (`86987a9`). Причина: два compose-контекста без общей модели зависимостей.

**VC-2 — AGE-ключ (φ4).** `F-04`→`F-05`→`F-06`: три слоя одного пути (env-leak / multi-line / потерянный env) + 4 диагностических коммита. Причина: один секрет, три канала, конфликт приоритетов.

**VC-3 — re-exec py3.14.** pydantic на 3.12 → re-exec (`379fd01`) → argv потерян, смерть после φ1 (`e0d0e09`). Fix-A сломал следующий этап.

**VC-4 — secrets vs reboot.** module-aware fail-loud → NODE_NAME нет → decrypt-only (16 ключей). Причина: два писателя `secrets.env` (φ4 = decrypt+ensure; systemd = decrypt).

**VC-5 — converge R-ssl.** Новый юнит → три бага подряд: нет шага (F-02) → name-shadow `ssl` (F-03) → нечестный статус (F-10).

**VC-6 — CI-канал.** `F-05`→`F-06`→`F-07`: путь не тестировался неделями (billing), три независимых поломки легли друг на друга.

**VC-7 — vhost/render «silent/phantom/stub».** `F-06`(020)→`F-09`(022)→`F-12`(022)→`F-01`(027)→`F-09`(027). Один участок многократно латал различие «отрендерено, но неверно» vs «не отрендерено» vs «реальный модуль» vs «stub/phantom».

**VC-8 — restore black-hole.** `F-11` (020): self-role skip-latch выбросил post-role dump, rc=0. Маскировалось 017-дриллом под `U=postgres` (filter no-op).

---

## Честная оценка воспроизводимости

**«Чистый сервер → одна команда → рабочая система» пока НЕ является стабильным свойством.**

1. Финальный cold bootstrap (027) **упал с первой попытки** (F-01). Вердикт «ВЫПОЛНЕН» описывает состояние *после* F-01-фикса, не first-try.
2. `oldapp skipped=no_local_source` — не все проекты.
3. `make test-node` (E2E) — BLOCKED во всех валидациях 020→027 (documented deviation).
4. CI main был красным 2.5 недели и не гейтил промоуты (F-13: нет branch protection).
5. Продакшн-путь `git push → CI` сломан тремя багами (F-05/06/07), зелёный только в самом конце.
6. Идемпотентность node-update сломана до последнего дня (F-10).

Итог: ноды реально вытирались/пересоздавались (не «удача на тёплой ноде»), но свойство **перезавоёвывалось фикс за фиксом до последнего коммита** — это не «одна команда из нуля», а «одна команда + N фиксов, обнаруженных на этой же ноде».
