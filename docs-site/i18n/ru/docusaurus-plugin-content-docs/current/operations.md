# Операции

GigaLoom работает local-first. Runtime state хранится в
`~/.gigaloom`, project-scoped state — в `.giga/` зарегистрированного
проекта.

## Запуск и проверка

```sh
giga doctor
giga ui
giga tui
```

Browser UI по умолчанию слушает `127.0.0.1:8091`. Не открывайте его в
недоверенную сеть без явно описанного remote identity profile.

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

## Диагностика

- Нет провайдера: установите нативный CLI и используйте его login/status.
- Действие отклонено: проверьте scope; не обходите policy или approval.
- Устарели browser assets: переустановите релизный пакет.
- Нет optional gateway: проверьте extra `gpt2giga`; checkout исходников не нужен.

## Базовая линия качества

Репозиторий владеет отдельным badge покрытия GigaLoom. Split baseline —
**84.59%**, измеренный 2026-07-29 non-live standalone gate. Это зафиксированная
база, а не утверждение о непроверенном remote run. Quality gate требует не
менее 80% и исключает opt-in live provider tests.
