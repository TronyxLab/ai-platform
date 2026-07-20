# $START_STATUS_REPORT

## $ARTIFACT_CONTRACT

| Поле | Значение |
|------|---------|
| **PURPOSE** | Диагностика проблемы: дефолтный nginx vhost отдаёт Grafana вместо `return 444` (закрытие соединения) для незарегистрированных субдоменов `*.tronyx.ru` |
| **DESCRIPTION** | Batch-диагностика nginx-конфигурации на tronyx-vps, проверка HTTPS всех субдоменов tronyx.ru, анализ SSL-сертификатов, определение корневой причины |
| **RATIONALE** | Пользователь сообщил: дефолтный vhost показывает Grafana. Требуется сбор информации без мутаций (read-only диагностика) |
| **ACCEPTANCE_CRITERIA** | Корневая причина идентифицирована, все субдомены проверены, задокументирована цепочка причинно-следственных связей |
| **IMPLEMENTS** | Sysadmin §WORKFLOW: Step 5 BATCH_DIAGNOSE |
| **IMPACTS** | core/modules/nginx/config/ — пустая директория `platform-default.conf` вместо файла-шаблона |
| **REQUIRES** | SSH-доступ к tronyx-vps |

---

## 1. Diagnostic Summary

| Параметр | Значение |
|----------|---------|
| **Target host** | tronyx-vps (103.88.243.151) |
| **OS** | Ubuntu 24.04.4 LTS (Noble Numbat), kernel 6.8.0-136-generic |
| **Platform domain** | tronyx.ru |
| **Core version** | 1.0.0 (SCP/rsync delivery, not git) |
| **Nginx delivery** | Docker container (image: nginx:1.28.3), envsubst-templates rendering |
| **SSL certificate** | Wildcard `*.tronyx.ru` + apex `tronyx.ru`, Let's Encrypt via acme.sh DNS-01 |
| **Date of diagnostic** | 2026-07-20 21:03 MSK |

### Issues Found

| # | Severity | Issue |
|---|----------|-------|
| I1 | **CRITICAL** | `platform-default.conf.template` в контейнере — **директория**, а не файл. Nginx entrypoint не рендерит default_server vhost → отсутствует `return 444` для незарегистрированных субдоменов |
| I2 | **HIGH** | Без явного `default_server` на порту 443, nginx делает `grafana-vhost.conf` имплицитным дефолтным (первый по алфавиту среди файлов с `listen 443 ssl`) |
| I3 | **MEDIUM** | Пустая директория `/opt/platform/core/modules/nginx/config/platform-default.conf` (без `.template`) создана с owner `root:root`, mtime `2026-07-20 12:14` — не совпадает с owner остальных файлов (`501:staff`) |
| I4 | **LOW** | `overlay/conf.d/tronyx.ru.conf` — мёртвый конфиг (не включается через `include overlay/*.conf`, т.к. лежит в поддиректории `overlay/conf.d/`); использует self-signed сертификаты |

---

## 2. Root Cause Chain

```
Пустая директория platform-default.conf (вместо файла-шаблона)
  │ создана 2026-07-20 12:14, owner root:root
  │
  ▼
Docker bind-mount: /opt/.../platform-default.conf (директория)
  → /etc/nginx/templates/platform-default.conf.template (тоже директория)
  │
  ▼
Nginx entrypoint: for template in /etc/nginx/templates/*.template
  │ ★ platform-default.conf.template — директория, не обрабатывается
  │
  ▼
/etc/nginx/conf.d/platform-default.conf — НЕ СОЗДАН
  │ ★ Отсутствует server-блок с `listen 443 ssl default_server; return 444;`
  │
  ▼
Нет явного default_server на порту 443 ни в одном конфиге
  │
  ▼
Nginx выбирает имплицитный default: первый по алфавиту server-блок с `listen 443`
  │ ★ Алфавитный порядок: default.conf(80) < grafana-vhost.conf(443) ← ВЫБРАН
  │
  ▼
grafana-vhost.conf становится default_server де-факто
  │ ★ Любой неизвестный Host-заголовок → Grafana (302 → /login)
```

### Почему зарегистрированные субдомены работают корректно

Nginx выбирает server-блок по правилам приоритета:
1. **Точное совпадение `server_name`** → приоритет выше, чем `default_server`
2. `default_server` (явный или имплицитный) → только когда нет точного совпадения

Поэтому явно сконфигурированные субдомены обслуживаются правильно:
| Субдомен | Конфиг | Статус |
|----------|--------|--------|
| `tronyx.ru` | `overlay/nginx.conf` (server_name tronyx.ru) | ✅ Корректно → tronyx-site |
| `www.tronyx.ru` | `overlay/www.tronyx.ru.conf` + `platform-default` www→apex redirect | ✅ Корректно → tronyx-site |
| `grafana.tronyx.ru` | `grafana-vhost.conf` | ✅ Корректно → Grafana |
| `langfuse.tronyx.ru` | `langfuse-vhost.conf` | ✅ Корректно → Langfuse |
| `prometheus.tronyx.ru` | `prometheus-vhost.conf` | ✅ Корректно (401 Basic Auth) |
| `loki.tronyx.ru` | `loki-vhost.conf` | ✅ Корректно (401 Basic Auth) |
| `botanika.tronyx.ru` | `overlay/botanika.tronyx.ru.conf` | ✅ Корректно |
| `hermes.tronyx.ru` | `hermes-dashboard.conf` | ✅ Корректно |

### Субдомены, попадающие на Grafana (имплицитный default_server)

| Субдомен | HTTP-ответ | Контент |
|----------|-----------|---------|
| `api.tronyx.ru` | 302 → `/login` | **Grafana** `<title>Grafana</title>` |
| `auth.tronyx.ru` | 302 → `/login` | **Grafana** |
| `clickhouse.tronyx.ru` | 302 → `/login` | **Grafana** |
| `minio.tronyx.ru` | 302 → `/login` | **Grafana** |
| `test.tronyx.ru` | 302 → `/login` | **Grafana** |
| `unknown123.tronyx.ru` | 302 → `/login` | **Grafana** |
| Любой другой `*.tronyx.ru` | 302 → `/login` | **Grafana** |

---

## 3. Actions Taken

| # | Действие | Результат |
|---|---------|----------|
| 1 | Preflight: SSH connectivity check | ✅ root@tronyx-vps, kernel 6.8.0-136 |
| 2 | Read node.yaml | Domain: tronyx.ru, modules: nginx/monitoring/logging + projects: tronyx-site/botanika/dance-site |
| 3 | Collect nginx vhost templates + overlay configs | Обнаружено: 8 template-файлов, 4 overlay-конфига, 1 мёртвый конфиг в `overlay/conf.d/` |
| 4 | Inspect rendered configs in Docker container | **Critical find:** `platform-default.conf.template` — директория, а не файл |
| 5 | Test HTTPS all subdomains (curl -skL) | 12 субдоменов проверено; 6 показывают Grafana (нежелательно) |
| 6 | Verify SSL cert SAN | `DNS:*.tronyx.ru, DNS:tronyx.ru` — wildcard корректен |
| 7 | Check source file on host | `/opt/.../config/platform-default.conf` — пустая директория (root:root, Jul 20 12:14) |
| 8 | Verify nginx error log | Чисто — нет ошибок рендеринга (entrypoint молча пропускает директорию) |
| 9 | Check core version | 1.0.0, NOT git repo (SCP/rsync invariant PASS) |

### Мутации

**Не выполнялись** (read-only диагностика).

---

## 4. TRAP Annotations

### TRAP[DECISION] — пустая директория platform-default.conf

```nginx
# 🧐 TRAP[DECISION] · 2026-07-20 · HI · Нет default_server на 443 — grafana-vhost.conf стал имплицитным дефолтным
# · Observed: все неизвестные subdomain *.tronyx.ru отдают Grafana (302 → /login)
# · Root: /opt/platform/core/modules/nginx/config/platform-default.conf — ПУСТАЯ ДИРЕКТОРИЯ,
# ·   создана 2026-07-20 12:14 (root:root), вместо файла-шаблона с return 444 default_server.
# ·   Docker монтирует директорию в /etc/nginx/templates/platform-default.conf.template,
# ·   nginx entrypoint не может обработать директорию как шаблон → platform-default.conf не рендерится.
# · Fix candidate: удалить пустую директорию, убедиться что platform-default.conf — файл (не директория),
# ·   force-recreate nginx контейнер. Либо переименовать platform-default.conf.template →
# ·   platform-default.conf (без .template), если схема именования это предполагает.
# · Rev: при следующем деплое core — проверить, не создаётся ли директория снова.
```

---

## 5. Legalization Tasks

Не требуется — мутаций на VPS не производилось.

---

## 6. Overall Verdict

**PARTIAL** — корневая причина идентифицирована с высокой уверенностью (CRITICAL: I1). Система функционирует (зарегистрированные субдомены работают корректно), но дефолтный vhost раскрывает Grafana вместо закрытия соединения (`return 444`), что является information disclosure для незарегистрированных субдоменов.

---

## 7. Next Steps (предложения)

### Fix (не выполнялся в рамках данной сессии)

1. **Удалить пустую директорию** на хосте:
   ```bash
   ssh tronyx-vps "rmdir /opt/platform/core/modules/nginx/config/platform-default.conf"
   ```

2. **Убедиться, что platform-default.conf — файл-шаблон.** Сейчас реальный шаблон лежит как `platform-default.conf.template` — нужно либо переименовать его в `platform-default.conf` (без `.template`), либо поправить docker-compose volume mount на правильный source.

3. **Пересоздать nginx-контейнер:**
   ```bash
   ssh tronyx-vps "cd /opt/platform && docker compose up -d --force-recreate nginx"
   ```

4. **Верифицировать:** `curl -skL https://api.tronyx.ru/` должен вернуть пустой ответ (444 — nginx закрывает соединение), а не Grafana.

### Агент для исправления

```
Sysadmin: исправь проблему с дефолтным vhost на tronyx-vps —
пустая директория platform-default.conf вместо файла,
из-за чего grafana-vhost.conf стал имплицитным default_server.
См. .ai/plans/015-nginx-vhost-default/01-StatusReport.md
```

# $END_STATUS_REPORT
