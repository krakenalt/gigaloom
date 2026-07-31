# Установка

GigaLoom 0.6 — ломающий clean cut альфа-линии. Поддерживается Python 3.11–3.14.
Отдельно установите хотя бы один provider-native CLI и завершите собственный
flow аутентификации провайдера.

Для managed provider terminals также нужны POSIX-система и доступный `tmux` с
корректным выводом `tmux -V`. На Linux или macOS установите `tmux` через
системный package manager. Windows и POSIX-системы без рабочего `tmux`
сохраняют provider-native passthrough; GigaLoom не подменяет его эмуляцией
терминала. Для самого TUI `giga` достаточно поддерживаемого интерактивного
терминала.

## Установка preview

Через `uv`:

```sh
uv tool install --prerelease allow 'gigaloom==0.6.0a1'
```

Или в изолированном Python-окружении:

```sh
python -m pip install --pre 'gigaloom==0.6.0a1'
```

Проверьте установленный артефакт:

```sh
giga --version
giga doctor
```

`doctor` сообщает состояние возможностей и конфигурации, не читая содержимое
prompts и не обращаясь к провайдерам.

## Обновление до 0.6

Точное ограничение `uv tool install` остаётся закреплённым при
`uv tool upgrade`. Для перехода с предыдущего preview пересоздайте tool
environment с новой точной версией:

```sh
uv tool install --force --prerelease allow 'gigaloom==0.6.0a1'
```

Если использовался optional gateway extra, сохраните его явно:

```sh
uv tool install --force --prerelease allow 'gigaloom[gpt2giga]==0.6.0a1'
```

Перед обновлением остановите все процессы GigaLoom и сохраните
`~/.gigaloom`, исторический root `~/.gpt2giga/harness`, если он существует, и
`.giga/` каждого зарегистрированного проекта.

## Миграция namespace и команд

Имя PyPI-проекта изменилось до первого самостоятельного target release.
Удалите исторический дистрибутив и установите `gigaloom`, не удаляя
существующие каталоги состояния:

```sh
uv tool uninstall gpt2giga-harness
uv tool install --prerelease allow 'gigaloom==0.6.0a1'
```

Обновите extensions, imports, scripts и frontend consumers как единый clean
cut:

| Историческая поверхность | Поверхность 0.6 |
|---|---|
| PyPI `gpt2giga-harness` | PyPI `gigaloom` |
| Python `gpt2giga_harness.*` | Python `gigaloom.*` |
| команда `gpt2giga-harness` | команда `giga` |
| entry-point group `gpt2giga.harnesses` | `gigaloom.harness_adapters.v1` |
| npm `@gpt2giga/harness-cockpit-v2` | npm `@gigaloom/web` |
| top-level UI aliases, например `/work` и `/workflows` | canonical routes `/web/**` |

Внешние adapters должны быть перевыпущены для новой entry-point group;
runtime bridge для старой group отсутствует. Standalone-дистрибутив не
публикует legacy Python namespace, команду, entry-point group, npm package или
Web route aliases.

## Миграция локального состояния

Перед первым запуском 0.6 остановите существующие Harness-процессы, уберите
устаревший override `GPT2GIGA_HARNESS_DATA_DIR` и выполните:

```sh
giga state migrate --json
```

Тот же preflight автоматически запускается перед обычными командами с default
root. Old-only state сначала сохраняется в проверенный backup под
`~/.gigaloom-migration`, затем переносится через staging и атомарно публикуется
в `~/.gigaloom`. Legacy root и все project `.giga/` остаются нетронутыми. Если
оба root уже существуют без завершённого migration journal, GigaLoom
останавливается и печатает явные команды `mv`, не выбирая authoritative root
самостоятельно. `giga state rollback` восстанавливает проверенный backup в
legacy root и сохраняет `~/.gigaloom` для диагностики.

Для собственного canonical root задайте `GIGALOOM_DATA_DIR`. При таком
override default roots автоматически не мигрируются.

## Откат обновления

До миграции state остановите GigaLoom и переустановите точную предыдущую
версию. Если миграция уже началась, сначала оставьте 0.6 установленным и
восстановите проверенный backup:

```sh
giga state rollback
uv tool install --force --prerelease allow 'gigaloom==0.5.1a2'
```

`giga state rollback` восстанавливает исторический root из
`~/.gigaloom-migration` и сохраняет `~/.gigaloom` для диагностики. Не
направляйте старый executable на canonical root 0.6, не объединяйте два root
вручную и не удаляйте их до проверки rollback. Project `.giga/` и
provider-owned homes эта команда не мигрирует и не откатывает.

Downgrade пакета отделён от rollback релиза: опубликованные registry versions
и их теги `v...` неизменяемы, их нельзя перезаписывать или перемещать.

## Необязательный gateway preset

Базовый пакет не требует gpt2giga. Установите extra только для Direct Chat или
legacy preset локального gateway:

```sh
uv tool install --prerelease allow 'gigaloom[gpt2giga]==0.6.0a1'
```

Устанавливается закреплённый публичный дистрибутив gateway. Репозиторий gateway,
sibling checkout, editable dependency или submodule не нужны. См.
[Интеграцию с gateway](gateway-integration.md).

## Использование Web package без Python

Сервис, самостоятельно публикующий проверенные static Cockpit assets, может
установить соответствующий npm release:

```sh
npm install --save-exact @gigaloom/web@0.6.0-alpha.1
```

Смонтируйте `dist/` package по `/web/assets/` и обслуживайте
`dist/index.html` для operator routes. Сохраняйте hashed filenames и проверяйте
вложенный content manifest. Это не React component library и не Python server;
Python wheel содержит те же Web bytes.

## Удаление

```sh
uv tool uninstall gigaloom
```

Удаление пакета не удаляет пользовательское состояние в
`~/.gigaloom`. Сначала прочитайте [Операции](operations.md).
