# spec-workflow

Skills for OpenCode that turn a large task into a worked spec and a controlled run: a spec repository with decision and problem journals, three work modes (quick / step / run) with an executor and a read-only reviewer, and a deterministic gate that checks the form of a spec folder against the code.

Три независимых юнита — можно поставить только нужные:

| Юнит | Что даёт | Зависит от |
|---|---|---|
| `spec-driven-development` | где живут спеки, что читать, что писать, гейт `spec_check.py` | — |
| `controlled-work` | режимы `quick`/`step`/`run`, роли координатора/исполнителя/ревьюера | — |
| `spec-review` | ревью задачи против кода, `PASS`/`BLOCKED` | `controlled-work` |

Вместе с ними: команды `/quick`, `/step`, `/run`, агент `spec-reviewer` и шаблон репозитория спек.

## Установка

Один шаг руками — клонировать репозиторий; дальше всё делает агент.

```sh
git clone https://github.com/DanielCryer/spec-workflow.git ~/src/spec-workflow
```

Затем попроси своего агента: «прочитай `~/src/spec-workflow/INSTALL.md` и примени разделы 2 и 3A» (или любые другие). `INSTALL.md` написан как инструкция агенту: границы, предусловия, независимые разделы, проверка, откат.

Требования: OpenCode, `git`, Python 3 (для гейта).

## Как это работает

- Спека — отдельный репозиторий (`SPECS_ROOT`), артефакты не смешиваются с кодом.
- Пункт работы проходит цикл: исполнитель → гейт → ревьюер → карточка. Паузы — после каждого пункта (`step`), только на серьёзном (`run`) или в конце (`quick`).
- Гейт проверяет форму, а не смысл: битые ссылки на коммиты и файлы, повтор номеров `D`/`P`, `shortcut:` без предела и условия возврата, красные тесты.
- Ревьюер не видит обоснований исполнителя и не имеет права писать — только читать.

## Состав

```
skills/spec-driven-development/   SKILL.md, references/formats.md, scripts/spec_check.py, tests
skills/controlled-work/           SKILL.md, references/briefs.md
skills/spec-review/               SKILL.md (не показывается модели, грузится ревьюером)
agents/spec-reviewer.md           обёртка: deny на запись, грузит spec-review
commands/                         /quick, /step, /run
templates/specs-repo/             скелет репозитория спек
INSTALL.md                        инструкция для агента-установщика
```

## Границы

- Только OpenCode. Другие клиенты не поддерживаются и не тестировались.
- `commands/` и `agents/` — установленные копии: канон здесь, обновляются повторным применением `INSTALL.md`.
- Скиллы не знают путей: корень спек — `SPECS_ROOT`, корень проектов — `PROJECTS_ROOT` (по умолчанию родитель `SPECS_ROOT`).

## Проверка репозитория

```sh
python3 -m pytest skills/spec-driven-development/scripts -q
sh scripts/check-portability.sh
```

## Лицензия

MIT, см. `LICENSE`.
