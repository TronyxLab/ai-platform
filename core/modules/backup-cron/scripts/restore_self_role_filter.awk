# GREP_SUMMARY: restore-self-role-filter awk DR restore-psql backup-cron self-role expected-dbs DATA-504 black-hole
# STRUCTURE: ▶ ┌line┐ → ◇ stmt_role_name() → ◇ CREATE/DROP/ALTER ROLE U ? skip=1 → ⊕ side-out CREATE DATABASE names → ⊕ print unless skip → ◇ `;`-EOL → skip=0 → ⎋ filtered stream
# region MODULE_CONTRACT
## @purpose  Фильтр restore-канала postgres (F-19, restore_psql.sh): вырезает ТОЛЬКО
##           statements для self-role U (CREATE ROLE U / DROP ROLE [IF EXISTS] U /
##           ALTER ROLE U ...;) — psql запрещает пересоздание роли текущего сеансового
##           суперпользователя. Параллельно (опционально) собирает ожидаемые имена БД
##           из CREATE DATABASE — для пост-проверки restore (DATA-504).
##           P0-fix: прежний фильтр защёлкивал skip навсегда (DROP ROLE IF EXISTS U в
##           --clean-дампе идёт РАНЬШЕ секций ролей/баз) → весь остаток дампа
##           (CREATE DATABASE + данные) отфильтровывался → psql получал заголовок +
##           DROP DATABASE → выполнял их → rc=0 «restore complete» над пустым кластером.
## @scope    Только stdin→stdout поток pg_dumpall; вызывается restore_psql.sh.
## @io       ⇥ stdin = SQL-дамп; env: U (обязателен, -v U=...), EXPECTED_DBS_FILE
##           (опционально, export — имя файла для дописывания имён БД);
##           ⎋ stdout = отфильтрованный SQL
## @invariants
##   - Семантика сброса: skip сбрасывается на ЛЮБОЙ строке, оканчивающейся на `;` —
##     однострочный statement (DROP/CREATE ROLE U;) и многострочный ALTER ROLE U
##     (вплоть до `;` / `);`-финальной строки) вырезаются ЦЕЛИКОМ, поток после них жив.
##   - Роль сопоставляется по позиции имени (поле после ROLE, с учётом IF EXISTS,
##     кавычек и хвостовой `;`) — ЛОЖНЫЕ совпадения исключены: роль `myplatform`
##     при U=platform не режется; значение в SET/PASSWORD не режется.
##   - НЕ-role statements (DROP DATABASE, CREATE DATABASE, COPY, GRANT, \connect)
##     всегда проходят; CREATE DATABASE-имена дописываются в EXPECTED_DBS_FILE
##     (пусто/не задано → сбор отключён).
## @rationale awk — единственный интерпретатор фильтра (никакого Python-native пути);
##            вынесен в отдельный .awk-артефакт для нативного тестирования реального
##            фильтра (не копии) и единой точки правки restore_psql.sh.
## @changes 2026-09-01 | P0: skip-latch fix + expected-DB side-output (DATA-504 post-check)
# ⚠️ TRAP[BUG] · 2026-09-01 · P0 · restore молча уничтожает данные: skip-latch фильтра self-role
# · Symptom: restore round-trip на проде — rc=0 «restore complete» над ПУСТЫМ кластером;
# ·   langfuse/litellm/platform БД отсутствуют (DROP DATABASE выполнен, CREATE DATABASE — нет),
# ·   11k строк данных потеряны. Drill 017 был GREEN — там POSTGRES_USER=postgres и фильтр был no-op.
# · Root: pg_dumpall --clean --if-exists ставит `DROP ROLE IF EXISTS platform;` РАНЬШЕ секций
# ·   ролей и баз; старый фильтр делал skip=1;next на нём и сбрасывал skip ТОЛЬКО через
# ·   instmt-ветку (терминатор /^\);/, которого в дампе нет) → skip защёлкивался НАВСЕГДА.
# · Fix: (1) skip сбрасывается на ЛЮБОЙ `;`-терминаторной строке (одно-/многострочные
# ·   statements режутся целиком); (2) пост-проверка restore_db_check.py — ожидаемые БД
# ·   из дампа (этот side-output) vs фактический кластер, exit 3 при нехватке.
# · Prevention: round-trip тест с НЕ-bootstrap суперпользователем (U=platform, env-форма как
# ·   на проде); Rev: любые правки фильтра гонять synthetic-dump тест с U=platform.
# endregion MODULE_CONTRACT

# stmt_role_name(): имя роли из CREATE/DROP/ALTER ROLE (позиционное, без ложных совпадений).
# CREATE ROLE <n>;  /  DROP ROLE [IF EXISTS] <n>;  /  ALTER ROLE <n> ...
function stmt_role_name(   f) {
    if ($2 != "ROLE") return ""
    if ($1 == "DROP" && $3 == "IF" && $4 == "EXISTS") f = $5
    else f = $3
    gsub(/^"|"$|;/, "", f)
    return f
}

BEGIN { skip = 0 }

# Открывающие строки self-role statements для U
/^CREATE ROLE / || /^DROP ROLE / || /^ALTER ROLE / {
    if (stmt_role_name() == U) skip = 1
}

# Side-output: ожидаемые БД (для пост-проверки DATA-504)
/^CREATE DATABASE / {
    if (ENVIRON["EXPECTED_DBS_FILE"] != "") {
        n = $3
        gsub(/^"|"$|;/, "", n)
        print n > ENVIRON["EXPECTED_DBS_FILE"]
    }
}

# Пропуск self-role statements, пропуск всего остального
{ if (!skip) print }

# Терминатор statement: ЛЮБАЯ строка с `;` на конце (однострочный или финал многострочного;
# `);` тоже оканчивается на `;`). Сброс skip — ключ P0-фикса: поток после ролей жив.
/[;][ \t]*$/ { skip = 0 }
