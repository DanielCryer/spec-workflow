# Установка

Инструкция для агента OpenCode: получатель просит «прочитай INSTALL.md и примени разделы 2, 3A, 5» — и агент делает это у себя на машине. Разделы независимы, каждый заканчивается проверкой.

## 0. Границы

Делай только то, что написано в запрошенных разделах.

- Не переписывай `opencode.jsonc` целиком: правь точечно и сохраняй комментарии.
- Не трогай `service.json`, креды, авторизацию провайдеров, базы сессий.
- Не перезаписывай чужие скиллы с теми же id — если такой уже стоит, скажи об этом и остановись.
- Не создавай коммиты, не делай push, не правь чужие репозитории.
- Если что-то не сходится (нет каталога, другая структура, нет прав) — остановись и напиши, что именно не сходится, вместо догадок.

## 1. Предусловия

Проверь и покажи результат:

```sh
git --version
python3 --version    # на Windows: py --version
```

OpenCode у получателя уже стоит. Целевой каталог конфигурации: `~/.config/opencode` (Windows: `%USERPROFILE%\.config\opencode`).

## 2. Обязательный минимум

Клонировать библиотеку и подключить её как источник скиллов.

```sh
git clone <repo-url> ~/src/spec-workflow
```

Windows (PowerShell): `git clone <repo-url> "$env:USERPROFILE\src\spec-workflow"`.

Затем в `~/.config/opencode/opencode.jsonc` добавить запись — слить с существующим объектом, не заменять файл:

```jsonc
{
  "skills": ["~/src/spec-workflow/skills"],
}
```

Если массив `skills` уже есть — дописать строку в него. `~/` раскрывается от домашнего каталога, поэтому запись одинакова на Windows и на Linux.

Проверка: каталог `~/src/spec-workflow/skills` существует, в нём три подкаталога с `SKILL.md` (`spec-driven-development`, `controlled-work`, `spec-review`), конфиг остался валидным JSONC.

## 3. Разделы

Каждый раздел ставится отдельно. Порядок в таблице — порядок зависимостей.

| Раздел | Что даёт | Нужно до него |
|---|---|---|
| 3A | скилл `spec-driven-development` + гейт `spec_check.py` | раздел 2 |
| 3B | скилл `controlled-work` (режимы quick/step/run) | раздел 2 |
| 3C | ревьюер: скилл `spec-review` + субагент `spec-reviewer` | 3B |
| 3D | слэш-команды `/quick`, `/step`, `/run` | 3B |
| 3E | шаблон репозитория спек + `SPECS_ROOT` | 3A |

### 3A. Спеки

Ставится разделом 2 — отдельных действий нет. Проверка: запусти гейт на шаблоне.

```sh
python3 ~/src/spec-workflow/skills/spec-driven-development/scripts/spec_check.py \
  --spec ~/src/spec-workflow/templates/specs-repo/tasks/_example --no-tests
```

Ожидаемо: `0 error(s)` и код возврата 0. На Windows: `py` вместо `python3`.

### 3B. Режимы

Ставится разделом 2. Проверка: скилл виден в списке скиллов сессии и загружается по id `controlled-work`.

### 3C. Ревьюер

```sh
mkdir -p ~/.config/opencode/agents
cp ~/src/spec-workflow/agents/spec-reviewer.md ~/.config/opencode/agents/
```

Windows: `mkdir` создаст каталог, копирование — `Copy-Item`.

Проверка: субагент `spec-reviewer` запускается и умеет загрузить скилл `spec-review` при запрете на запись. Если субагент не видит скилл — не выдумывай обход, напиши об этом: значит, версия OpenCode не разрешает `skill` в правах агента.

### 3D. Команды

```sh
mkdir -p ~/.config/opencode/commands
cp ~/src/spec-workflow/commands/quick.md ~/src/spec-workflow/commands/step.md \
   ~/src/spec-workflow/commands/run.md ~/.config/opencode/commands/
```

Не перезаписывай одноимённые файлы, которых нет в библиотеке, и не удаляй чужие команды.

Проверка: `/step` есть в списке команд.

### 3E. Репозиторий спек

Если репозитория спек ещё нет:

```sh
cp -r ~/src/spec-workflow/templates/specs-repo ~/projects/specs
cd ~/projects/specs && git init && git add -A && git commit -m "init specs"
rm -rf tasks/_example
```

Если репозиторий уже есть — ничего не копируй. Возьми из шаблона только `README.md`/`AGENTS.md`, если в них нет правил структуры, и не перезаписывай существующие файлы задач.

Корень репозитория спек задаётся переменной `SPECS_ROOT`:

- Windows: `setx SPECS_ROOT "D:\projects\specs"` (для текущей сессии — ещё и `$env:SPECS_ROOT="D:\projects\specs"`).
- Linux/macOS: строка `export SPECS_ROOT="$HOME/projects/specs"` в `~/.bashrc` или `~/.zshrc`.

Репозитории кода, на которые ссылаются спеки, лежат рядом (`<PROJECTS_ROOT>/<name>`): по умолчанию `PROJECTS_ROOT` = родитель `SPECS_ROOT`. Нужно иначе — задай `PROJECTS_ROOT` отдельно.

Проверка: `echo $SPECS_ROOT` (Windows: `$env:SPECS_ROOT`) выдаёт путь, гейт из 3A работает без `--no-tests`.

## 4. Отчёт

В конце перечисли: что применено, что пропущено и почему, какие проверки прошли, какая версия библиотеки (`git -C ~/src/spec-workflow rev-parse --short HEAD`). Не пиши «готово», если проверка не запускалась.

## 5. Обновление и откат

Обновление: `git -C ~/src/spec-workflow pull`, затем перечитать нужные разделы и применить заново — разделы идемпотентны, копирование перезаписывает файлы, запись в конфиге не дублируется.

Откат любого раздела: удалить скопированные файлы (скиллы уезжают вместе с клоном), убрать строку `skills` из `opencode.jsonc` при полном удалении. `SPECS_ROOT` можно оставить: без библиотеки он ни на что не влияет.
