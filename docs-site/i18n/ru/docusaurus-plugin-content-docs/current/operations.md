# Операции

GigaLoom работает local-first. Runtime state хранится в
`~/.gigaloom`, project-scoped state — в `.giga/` зарегистрированного
проекта.

## Запуск и проверка

```sh
giga doctor
giga ui
```

Browser UI по умолчанию слушает `127.0.0.1:8091`. Не открывайте его в
недоверенную сеть без явно описанного remote identity profile.

## Требования managed terminal

Provider-native passthrough доступен везде, где может работать сам provider
CLI. Managed terminal kernel — отдельная capability:

- Linux и macOS требуют `tmux` в `PATH` и корректный результат `tmux -V`;
- Windows сообщает managed tmux как unsupported и сохраняет native passthrough;
- для local attach нужен интерактивный terminal;
- каждый managed terminal использует private owner-bound tmux instance, а не
  default tmux server пользователя.

Перед managed session проверьте provider CLI и terminal capability:

```sh
tmux -V
giga doctor
```

Если tmux отсутствует, некорректен или позже отключён, GigaLoom должен честно
сообщить unavailable capability. Он не возвращает GigaLoom-owned provider
terminal UI и не заявляет structured resume. Существующие content-free
terminal lifecycle records остаются читаемыми; cleanup и recovery ограничены
точным managed instance.

## Резервное копирование

Остановите процессы GigaLoom и скопируйте целиком `~/.gigaloom` и
нужные project `.giga/`, чтобы SQLite, JSON/JSONL, evidence и metadata остались
согласованными.

Удаление пакета не удаляет состояние. Восстанавливайте его в те же пути только
при остановленном GigaLoom, затем запустите `giga doctor`.

Для одностороннего cutover root в 0.6 используйте
`giga state migrate --json`. Migration evidence и проверенный private backup
хранятся отдельно в `~/.gigaloom-migration`; отчёт содержит counts, версии схем
и digests, но не содержимое state. `giga state rollback` восстанавливает этот
backup в legacy root и намеренно оставляет canonical root для диагностики.

Запускайте state rollback исполняемым файлом 0.6 до переустановки старого
пакета. Не заставляйте старый executable читать `~/.gigaloom` вместо
восстановления проверенного historical root. Точный порядок приведён в
[Установке](installation.md#откат-обновления).

Для upgrade Native Agent Gateway 0.6→0.7 остановите всех владельцев state и
запустите `giga state upgrade --backup <outside-data-dir>.zip --json`. Эта
операция отличается от прежнего root cutover: она создаёт полный проверенный
archive, мигрирует legacy project bindings sessions, исключает Textual-only
preferences без преобразования в Web settings и записывает content-free
ordered receipt. Прерванный запуск возобновляется с тем же backup path. Для
recovery остановите GigaLoom, проверьте archive и выполните
`giga state restore <archive> --replace --json` до переустановки 0.6.

## Диагностика

- Нет провайдера: установите нативный CLI и используйте его login/status.
- Нет managed terminal: установите tmux на POSIX-системе или используйте
  provider-native passthrough, который показывает `giga doctor`.
- Действие отклонено: проверьте scope; не обходите policy или approval.
- Устарели browser assets: переустановите релизный пакет.
- Нет optional gateway: проверьте extra `gpt2giga`; checkout исходников не нужен.

## Базовая линия качества

Репозиторий владеет отдельным badge покрытия GigaLoom. Split baseline —
**84.59%**, измеренный 2026-07-29 non-live standalone gate. Это зафиксированная
база, а не утверждение о непроверенном remote run. Quality gate требует не
менее 80% и исключает opt-in live provider tests.
