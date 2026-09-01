#!/usr/bin/env bash
# GREP_SUMMARY: restore-psql decrypt-filter-feed backup-cron round-trip F-19 017 age gzip post-check DATA-504 black-hole
# STRUCTURE: ▶ ┌DUMP_FILE ┐ → ◇ .age→decrypt(stdout) / *.gz→gunzip → ⊕ self-role filter (+expected-DB side-out) → ⚡ psql -1 ON_ERROR_STOP -d postgres → ◇ post-check expected vs cluster → ⎋ rc {0|3}
# region MODULE_CONTRACT
## @purpose  Единый заливочный канал DR-restore postgres (F-19 серия, 017): расшифровка
##           .age В STDOUT (plaintext не касается диска), фильтр self-role дропа
##           (pg_dumpall --clean содержит DROP ROLE <session-user> — psql запретит),
##           приём в psql на АДМИН-БД (-d postgres — DROP DATABASE «currently open» устранён).
##           P0 (2026-09-01): второй уровень защиты — пост-проверка restore_db_check.py:
##           ожидаемые БД из дампа (restore_self_role_filter.awk side-output) vs кластер;
##           нехватка → IMP:10 + exit 3 (DATA-504: restore НЕ может молча недопримениться).
##           Вызывается ИЗ core/modules/postgres/Makefile restore-цели; работает как из make,
##           так и напрямую оператором (ранбук DATA-504).
## @scope    Только восстановление дампов вида *.sql[.gz|.age|.gz.age] кластера платформы.
## @io       ⇥ argv[1]=DUMP_FILE (обязателен); env: AGE_SECRET_KEY|AGE_IDENTITY_FILE,
##           POSTGRES_USER (из secrets.env ноды); ⎋ exit 0 ok | 1 usage/env |
##           3 DATA-504 (psql failure = partial state ИЛИ пост-проверка не прошла)
## @invariants
##   - Ключ/пароли НЕ в argv и не логируются; identity tmp 0600 + wipe (age_decrypt_stdout).
##   - Секреты source-ятся здесь (set -a secrets.env) — вызывающий контекст не обязан их иметь.
##   - ON_ERROR_STOP=1 сохранён; частичное состояние (psql rc!=0) — громкий rc3 (DATA-504).
##   - Ожидаемые имена БД пишутся в mktemp-файл (НЕ данные — только имена), trap-очистка;
##     пустой список ожидаемых → fail-closed rc3 (сбой сбора ≠ vacuous-pass).
## @rationale Ручной проверенный пайплайн обернут одним скриптом: устраняет класс
##            make-контекстных расхождений env/expansion, найденных в drill'е 017;
##            фильтр вынесен в restore_self_role_filter.awk (нативно тестируемый артефакт),
##            пост-проверка — в restore_db_check.py (Python-only канон для новой логики).
## @changes 2026-08-27 | DevPlan 017 F-19 — created (round-trip GREEN baseline)
##          2026-09-01 | P0: skip-latch фильтра (black-hole) → restore_self_role_filter.awk;
##                     пост-проверка БД (DATA-504) → restore_db_check.py; rc унифицирован к 3
# ⚠️ TRAP[BUG] · 2026-09-01 · P0 · restore молча уничтожает данные: skip-latch фильтра self-role
# · Symptom: restore round-trip на проде — rc=0 «restore complete» над ПУСТЫМ кластером;
# ·   langfuse/litellm/platform БД отсутствуют (DROP DATABASE выполнен, CREATE DATABASE — нет),
# ·   11k строк данных потеряны. Drill 017 был GREEN — там POSTGRES_USER=postgres и фильтр был no-op.
# · Root: pg_dumpall --clean --if-exists ставит `DROP ROLE IF EXISTS platform;` РАНЬШЕ секций
# ·   ролей и баз; старый inline-фильтр делал skip=1;next на нём и сбрасывал skip ТОЛЬКО через
# ·   instmt-ветку (терминатор /^\);/, которого в дампе нет) → skip защёлкивался НАВСЕГДА.
# · Fix: (1) restore_self_role_filter.awk — skip сбрасывается на ЛЮБОЙ `;`-терминаторной строке;
# ·   (2) restore_db_check.py — пост-проверка ожидаемых БД, exit 3 при нехватке.
# · Prevention: round-trip тест с НЕ-bootstrap суперпользователем (U=platform, env-форма как
# ·   на проде); Rev: любые правки фильтра гонять synthetic-dump тест с U=platform.
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $# -ne 1 ]]; then
    echo "[IMP:10][restore-psql] usage: restore_psql.sh <DUMP_FILE>" >&2
    exit 1
fi
DUMP_FILE="$1"
[[ -f "$DUMP_FILE" ]] || { echo "[IMP:10][restore-psql] file not found: $DUMP_FILE" >&2; exit 1; }

_SECRETS="${SECRETS_ENV_FILE:-/var/lib/platform/run/secrets.env}"
if [[ -f "$_SECRETS" ]]; then
    set -a; . "$_SECRETS"; set +a
fi
: "${POSTGRES_USER:?POSTGRES_USER required (secrets.env)}"

# DATA-504 пост-проверка: фильтр дописывает сюда ожидаемые CREATE DATABASE-имена
# (только имена, не данные — plaintext-инвариант дампа сохранён). mktemp + trap-очистка.
_EXPECTED_DBS_FILE="$(mktemp)"
trap 'rm -f "$_EXPECTED_DBS_FILE"' EXIT
export EXPECTED_DBS_FILE="$_EXPECTED_DBS_FILE"

# Единый self-role фильтр (вынесен в .awk-артефакт — нативно тестируется; P0 black-hole fix:
# skip сбрасывается на ЛЮБОЙ `;`-терминаторной строке, поток после self-role ролей жив).
FILTER_CMD=(awk -v U="$POSTGRES_USER" -f "$SCRIPT_DIR/restore_self_role_filter.awk")

case "$DUMP_FILE" in
    *.gz.age|*.age.gz)
        python3 "$SCRIPT_DIR/age_decrypt_stdout.py" "$DUMP_FILE" \
            | gunzip -c \
            | "${FILTER_CMD[@]}" \
            | docker exec -i postgres sh -c 'exec psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres < /dev/stdin'
        ;;
    *.age)
        python3 "$SCRIPT_DIR/age_decrypt_stdout.py" "$DUMP_FILE" \
            | "${FILTER_CMD[@]}" \
            | docker exec -i postgres sh -c 'exec psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres < /dev/stdin'
        ;;
    *.gz)
        gunzip -c "$DUMP_FILE" \
            | "${FILTER_CMD[@]}" \
            | docker exec -i postgres sh -c 'exec psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres < /dev/stdin'
        ;;
    *)
        docker exec -i postgres sh -c 'exec psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres < /dev/stdin' \
            < <("${FILTER_CMD[@]}" < "$DUMP_FILE")
        ;;
esac
_RESTORE_RC=$?
if [[ $_RESTORE_RC -ne 0 ]]; then
    echo "[IMP:10][restore-psql] psql restore failed rc=$_RESTORE_RC — cluster in PARTIAL state (DATA-504); pre_restore snapshot in backup-spool/pre-restore" >&2
    exit 3
fi

# Второй уровень защиты: «restore не может молча недопримениться» — ожидаемые БД vs кластер.
python3 "$SCRIPT_DIR/restore_db_check.py" --expected-file "$_EXPECTED_DBS_FILE"
_RC=$?
if [[ $_RC -ne 0 ]]; then
    echo "[IMP:10][restore-psql] post-restore DB verification FAILED rc=$_RC (DATA-504) — restore is NOT complete" >&2
    exit 3
fi
exit 0
