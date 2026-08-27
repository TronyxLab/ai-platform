#!/usr/bin/env bash
# GREP_SUMMARY: restore-psql decrypt-filter-feed backup-cron round-trip F-19 017 age gzip
# STRUCTURE: ▶ ┌DUMP_FILE ┐ → ◇ .age→decrypt(stdout) / *.gz→gunzip → ⊕ self-role filter → ⚡ psql -1 ON_ERROR_STOP -d postgres → ⎋ rc {0|3|1}
# region MODULE_CONTRACT
## @purpose  Единый заливочный канал DR-restore postgres (F-19 серия, 017): расшифровка
##           .age В STDOUT (plaintext не касается диска), фильтр self-role дропа
##           (pg_dumpall --clean содержит DROP ROLE <session-user> — psql запретит),
##           приём в psql на АДМИН-БД (-d postgres — DROP DATABASE «currently open» устранён).
##           Вызывается ИЗ core/modules/postgres/Makefile restore-цели; работает как из make,
##           так и напрямую оператором (ранбук DATA-504).
## @scope    Только восстановление дампов вида *.sql[.gz|.age|.gz.age] кластера платформы.
## @io       ⇥ argv[1]=DUMP_FILE (обязателен); env: AGE_SECRET_KEY|AGE_IDENTITY_FILE,
##           POSTGRES_USER (из secrets.env ноды); ⎋ exit 0 ok | 1 usage/env | (psql rc passthrough)
## @invariants
##   - Ключ/пароли НЕ в argv и не логируются; identity tmp 0600 + wipe (age_decrypt_stdout).
##   - Секреты source-ятся здесь (set -a secrets.env) — вызывающий контекст не обязан их иметь.
##   - ON_ERROR_STOP=1 сохранён; частичное состояние — громкий rc3 (DATA-504).
## @rationale Ручной проверенный пайплайн обернут одним скриптом: устраняет класс
##            make-контекстных расхождений env/expansion, найденных в drill'е 017.
## @changes 2026-08-27 | DevPlan 017 F-19 — created (round-trip GREEN baseline)
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

FILTER="awk -v U=\"\$POSTGRES_USER\" '(/^CREATE ROLE / || /^DROP ROLE /) && index(\$0, U \";\") != 0 {skip=1; next} /^ALTER ROLE / && index(\$0, U \";\") != 0 {instmt=1} {if(!skip) print} /^\\);[ ]*$/{if(instmt){instmt=0;next}}'"

case "$DUMP_FILE" in
    *.gz.age|*.age.gz)
        python3 "$SCRIPT_DIR/age_decrypt_stdout.py" "$DUMP_FILE" \
            | gunzip -c \
            | eval "$FILTER" \
            | docker exec -i postgres sh -c 'exec psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres < /dev/stdin'
        ;;
    *.age)
        python3 "$SCRIPT_DIR/age_decrypt_stdout.py" "$DUMP_FILE" \
            | eval "$FILTER" \
            | docker exec -i postgres sh -c 'exec psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres < /dev/stdin'
        ;;
    *.gz)
        gunzip -c "$DUMP_FILE" \
            | eval "$FILTER" \
            | docker exec -i postgres sh -c 'exec psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres < /dev/stdin'
        ;;
    *)
        docker exec -i postgres sh -c 'exec psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres < /dev/stdin' \
            < <(eval "$FILTER" < "$DUMP_FILE")
        ;;
esac
