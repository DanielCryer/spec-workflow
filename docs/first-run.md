# Первый прогон: 20 минут

Цель — своими руками увидеть, как гейт ловит расхождение с кодом, и получить один `BLOCKED` от ревьюера. Ничего реального не трогаем: демо-репозиторий во временном каталоге.

Нужны: OpenCode с установленными разделами 2–3C из `INSTALL.md`, `git`, Python 3 с `pytest`.

## 1. Демо-репозиторий кода

```sh
mkdir -p ~/projects/demo-calc/src ~/projects/demo-calc/tests
cd ~/projects/demo-calc
git init
```

`src/calc.py`:

```python
class Calculator:
    def add(self, a: int, b: int) -> int:
        return a + b
```

`tests/test_calc.py`:

```python
from src.calc import Calculator


def test_add():
    assert Calculator().add(2, 3) == 5
```

`pytest.ini` (по его наличию гейт понимает, что тесты репозитория надо запускать):

```ini
[pytest]
```

```sh
git add -A && git commit -m "demo: calculator"
git rev-parse --short HEAD
```

Хеш из последней строки понадобится ниже — здесь он записан как `<hash>`.

## 2. Репозиторий спек

```sh
cp -r ~/src/spec-workflow/templates/specs-repo ~/projects/specs
cd ~/projects/specs && git init && git add -A && git commit -m "init specs"
rm -rf tasks/_example
mkdir -p tasks/demo-1
```

Windows: `setx SPECS_ROOT "$env:USERPROFILE\projects\specs"`, затем **перезапустить приложение** — сервис читает окружение при старте. Linux/macOS: `export SPECS_ROOT="$HOME/projects/specs"` в `~/.bashrc`/`~/.zshrc` и новый терминал.

## 3. Папка задачи

`tasks/demo-1/index.md`:

```markdown
---
id: DEMO-1
kind: problem
status: active
mode: step
repos: [demo-calc]
---

# Демо: сложение

## Скоуп

Проверить, что гейт сходится с кодом.
```

`tasks/demo-1/decisions.md`:

```markdown
# Решения

**Последняя ревизия:** 2026-11-03

## Действующие решения

- **D1** — арифметика в одном классе.

## D1. Арифметика в `Calculator`

- **Контекст:** демо, чтобы увидеть работу гейта.
- **Решение:** методы сложения живут в `Calculator`.
- **Альтернативы:** —
- **Последствия:** новые операции добавляются туда же.
- **Статус:** действует
- **Ссылки:** demo-calc@<hash>, `src/calc.py:1`
```

`tasks/demo-1/plan.md`:

```markdown
# План

## Этап 1

- [x] Сложение в `Calculator`, тест `tests/test_calc.py`, коммит demo-calc@<hash>.
- [ ] Умножение в `Calculator`.
```

## 4. Гейт зелёный

```sh
python3 ~/src/spec-workflow/skills/spec-driven-development/scripts/spec_check.py \
  --spec ~/projects/specs/tasks/demo-1
```

Ожидаемо: `0 error(s), 0 warning(s)`. Если `pytest` не установлен — сначала `pip install pytest` (или добавьте `--no-tests`, чтобы пропустить прогон тестов).

## 5. Гейт красный

Поменяйте в `plan.md` имя класса на несуществующее:

```markdown
- [x] Сложение в `Calculatr`, тест `tests/test_calc.py`, коммит demo-calc@<hash>.
```

Тот же запуск теперь даёт:

```text
ERROR  symbols    plan.md:5  checked item names `Calculatr`, which is not in the code

1 error(s), 0 warning(s)
```

Это и есть смысл гейта: отмеченный пункт ссылается на то, чего в коде нет. Верните `Calculator` — снова ноль.

## 6. Один `BLOCKED` от ревьюера

Попросите агента:

> Найди субагента `spec-reviewer` и проверь такой дифф по скиллу `spec-review`. Спека требует, чтобы тест проверял сложение.
>
> ```diff
> +def test_add():
> +    assert Calculator().add(2, 3) == Calculator().add(2, 3)
> ```

Ожидаемая реакция — `BLOCKED` с находкой «тест проходит при пустой реализации». Это второй механизм: форма у гейта, смысл у ревьюера.

Если ревьюер ответил `PASS` или не смог загрузить скилл — установка раздела 3C неполная, вернитесь к `INSTALL.md`.

## 7. Дальше

- Сквозной пример настоящей задачи: [`walkthrough.md`](walkthrough.md).
- Почему всё так устроено: [`concept.md`](concept.md).
- Свой репозиторий спек: `templates/specs-repo/README.md`.
- Убрать за собой: `rm -rf ~/projects/demo-calc ~/projects/specs`.
