# Changelog

## 0.1.0 — 2026-11-03

Первая публикация после разделения на юниты.

- `spec-driven-development`: процесс спек вынесен из личного конфига; правила записей — `references/formats.md`; гейт `spec_check.py` переехал внутрь скилла (`scripts/`).
- `controlled-work`: режимы `quick`/`step`/`run`; задания ролям — `references/briefs.md`; добавлен вариант работы без папки спеки.
- `spec-review`: ревьюер стал скиллом с `opencode/autoinvoke: false`, обёртка — агент `spec-reviewer`.
- Портативность: `SPECS_ROOT`/`PROJECTS_ROOT` вместо жёстких путей, `sys.executable` вместо `py`, работает на Windows и Linux.
- Тираж: `INSTALL.md` для агента-установщика, шаблон репозитория спек, CI на портативность и тесты.
