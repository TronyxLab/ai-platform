$START_BRIEF
# Brief 094 — Template Engine Python Native

## $ARTIFACT_CONTRACT
- **PURPOSE:** Заменить `template-engine.sh` (238 LOC) на нативный Python-рендерер, устранить 25 subprocess-вызовов из Python-ядра к shell-скрипту. Ликвидировать shell-зависимость из Python-домена.
- **DESCRIPTION:** Создать `core/internal/template_engine.py` (нативный Python, без subprocess) с тем же контрактом: `{{UPPER_SNAKE}}` strict regex для nginx vhost, `render()`/`render_dir()` API. Удалить `template-engine.sh`. Обновить 25 вызовов в Python-модулях.
- **RATIONALE:** `template-engine.sh` — последний крупный shell-компонент, вызываемый **из Python** через `subprocess.run`. Это инверсия зависимости (Infrastructure → Python через shell), нарушает AI-First Architecture (domain не должен зависеть от infra). AGENTS.md: «3 механизма шаблонизации», template-engine.sh — один из них.
- **ACCEPTANCE_CRITERIA:** Все шаблоны рендерятся идентично (nginx vhosts, monitoring configs, sudoers); `template-engine.sh` удалён; 0 subprocess-вызовов к нему; `{{UPPER_SNAKE}}` strict regex сохранён (не матчить Go/Prometheus `{{$labels.x}}`); unit-тесты.
- **IMPLEMENTS:** Устранение GAP-2 (из 2-й экспертизы) + закрытие «template-engine.sh не покрыт планом» (из 3-й экспертизы).
- **IMPACTS:** `core/internal/template-engine.sh` (DELETE), `core/internal/template_engine.py` (существует, расширить). 25 call sites: `monitoring_config_renderer.py`, `sudoers_generator.py`, `add-project.sh` (после 092), cert_orchestrator, deploy.
- **REQUIRES:** DevPlan 092 Wave 4 (add-project мигрирован — иначе двойная работа по перезаписи вызовов). Блокируется 092.

## Current Status (Audit 2026-07-30)
- **template-engine.sh:** 238 LOC, sed-фолбэк. Контракт: `{{UPPER_SNAKE}}` strict regex (только UPPER_SNAKE, чувствителен к регистру — НЕ матчить Go/Prometheus `{{...}}`).
- **template_engine.py:** уже существует (ядро Python). Нужно убедиться, что API покрывает все use-cases shell-версии.
- **Call sites:** 25 grep-совпадений `template-engine.sh` в `.py` файлах. Главные: `monitoring_config_renderer.py` (строка 68: `TEMPLATE_ENGINE_SCRIPT`), `sudoers_generator.py` (строка 89).

## Key Findings (verificated)
- 3 механизма шаблонизации (AGENTS.md): template-engine (UPPER_SNAKE), Jinja2 (LiteLLM/status-page), `${VAR}` (compose). Этот план касается только UPPER_SNAKE.
- **Критично:** strict regex НЕ должен матчить `{{$labels.alertname}}`, `{{instance}}` (Prometheus/Go). Это документированная причина существования strict regex.
- `render_dir()` — рендерит все файлы в директории (используется add-project для копирования шаблона проекта).

## Required Actions

### Wave 1: verify Python parity
1. Прочитать `template-engine.sh` — извлечь точное regex для `{{UPPER_SNAKE}}`.
2. Сравнить с regex в `template_engine.py`. Если не совпадает — выровнять (strict, case-sensitive, UPPER_SNAKE only).
3. Проверить `render_dir()` эквивалент в Python (если нет — добавить).

### Wave 2: migrate call sites
4. `monitoring_config_renderer.py`: заменить subprocess на прямой import `template_engine.render()`.
5. `sudoers_generator.py`: то же.
6. cert_orchestrator, deploy modules: то же.
7. add-project (после 092): использовать Python API напрямую.

### Wave 3: delete shell
8. Удалить `core/internal/template-engine.sh`.
9. Обновить AGENTS.md §Template Mechanisms — отметить UPPER_SNAKE как Python-native.
10. Gate test: регрессия рендеринга — сравнить вывод Python vs сохранённые expected-файлы.

## Verification
- `grep -rn "template-engine.sh" core/ --include="*.py"` → 0.
- `grep -rn "template-engine.sh" core/` → 0 (или только в комментариях/AGENTS.md как historical).
- Тест: рендер nginx vhost с `{{DOMAIN}}`, `{{UPSTREAM}}` → корректный вывод.
- Тест: НЕ-матчинг `{{$labels.alertname}}` проходит как literal.
- `make templates-render` (если есть) — зелёный.

## Anti-Loop Note
Не объединять UPPER_SNAKE regex с Jinja2 (это 2 разных механизма по дизайну — AGENTS.md §rationale). Только нативизация вызова, не смена механизма.

$END_BRIEF
