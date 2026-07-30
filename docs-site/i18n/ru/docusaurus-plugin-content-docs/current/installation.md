# Установка

GigaLoom поддерживает Python 3.11–3.14. Отдельно установите хотя бы один
provider-native CLI и завершите собственный flow аутентификации провайдера.

## Установка preview

Через `uv`:

```sh
uv tool install --prerelease allow 'gigaloom==0.5.1a2'
```

Или в изолированном Python-окружении:

```sh
python -m pip install --pre 'gigaloom==0.5.1a2'
```

Проверьте установленный артефакт:

```sh
giga --version
giga doctor
```

`doctor` сообщает состояние возможностей и конфигурации, не читая содержимое
prompts и не обращаясь к провайдерам.

## Миграция с `gpt2giga-harness`

Имя PyPI-проекта изменилось до первого самостоятельного target release.
Удалите исторический дистрибутив и установите `gigaloom`, не удаляя
существующие каталоги состояния:

```sh
uv tool uninstall gpt2giga-harness
uv tool install --prerelease allow 'gigaloom==0.5.1a2'
```

Standalone-дистрибутив предоставляет Python namespace `gigaloom` и единственную
публичную команду `giga`. Legacy namespace и command shim не публикуются.

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

## Необязательный gateway preset

Базовый пакет не требует gpt2giga. Установите extra только для Direct Chat или
legacy preset локального gateway:

```sh
uv tool install --prerelease allow 'gigaloom[gpt2giga]==0.5.1a2'
```

Устанавливается закреплённый публичный дистрибутив gateway. Репозиторий gateway,
sibling checkout, editable dependency или submodule не нужны. См.
[Интеграцию с gateway](gateway-integration.md).

## Обновление и удаление

```sh
uv tool upgrade --prerelease allow gigaloom
uv tool uninstall gigaloom
```

Удаление пакета не удаляет пользовательское состояние в
`~/.gigaloom`. Сначала прочитайте [Операции](operations.md).
