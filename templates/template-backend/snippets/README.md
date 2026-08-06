# GREP_SUMMARY: snippets README reference map services db metrics prometheus
# STRUCTURE: ┌snippets/┐ → ◇ таблица файл → сервис → как подключить → ⎋ note
# region MODULE_CONTRACT
## @purpose  Карта snippets/ — reference-файлы для подключения платформенных сервисов
##           (DevPlan 141 Q4). НЕ устанавливаются автоматически — копируются разработчиком
##           при необходимости (asyncpg/prometheus опциональны).
## @scope    template-backend проекты
## @invariants
##   - Содержимое snippets/ — reference, не импортируется приложением
##   - При копировании: раскомментировать зависимость в requirements.txt
# endregion MODULE_CONTRACT

# Snippets — reference-паттерны подключения сервисов

| Файл | Сервис | Как подключить |
|------|--------|----------------|
| `db.py` | PostgreSQL (`PLATFORM_POSTGRES_DSN`) | Скопировать в `src/db.py`; раскомментировать `asyncpg` в `src/requirements.txt`; `await get_pool()` в endpoint'ах (см. комментарии в файле) |
| `metrics_prometheus.py` | Prometheus (`/metrics`) | Базовый `/metrics` уже в `src/main.py` (prometheus-client обязателен). Файл — reference для кастомных метрик (Counter/Histogram) и mount-варианта |

## Примечания

- `PLATFORM_*` переменные приходят из `.env.platform` (генерируется `make sync-env`) — см. `src/config.py` (pydantic-settings).
- Полный список сервисов: `grep PLATFORM_ .env.example`.
