# GREP_SUMMARY: template context org platform
# STRUCTURE: context.yaml → modules/hermes-agent/ → config.yaml → projects/

# Template Context

<!-- GREP_SUMMARY: template context usage directory-structure org platform -->
<!-- STRUCTURE: context.yaml → modules/hermes-agent/ → config.yaml, templates/profiles/, skills/ → projects/ -->

Этот шаблон используется для создания нового org-контекста.

## Использование

```bash
# Скопировать шаблон
cp -r templates/template-context ~/projects/<org>/platform

# Заменить плейсхолдеры
sed -i 's/__ORG_NAME__/<org>/g' ~/projects/<org>/platform/context.yaml
sed -i 's/__CONTEXT__/<context>/g' ~/projects/<org>/platform/context.yaml
sed -i 's/__NODE_NAME__/<node>/g' ~/projects/<org>/platform/context.yaml
```

## Структура

```
platform/
├── context.yaml              # Контекст (org, default_node)
├── modules/
│   └── hermes-agent/
│       ├── config.yaml       # Конфиг-оверлей модуля
│       ├── templates/profiles/ # Профили агента (override)
│       └── skills/            # Кастомные скиллы
└── projects/                  # L2-оверрайды мониторинга (создать при необходимости)
```
