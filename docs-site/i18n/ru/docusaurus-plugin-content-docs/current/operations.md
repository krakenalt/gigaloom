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

## Обновление 0.8.1 до 0.9

Остановите владельцев GigaLoom и создайте проверенный backup до изменения
пакета. Данные 0.9 добавочны: существующие sessions не переписываются в Thread
Relay records, старые attachment records читаются без charset evidence, старые
клиенты Context Lens могут игнорировать новые поля, а `.giga` parsers остаются
runtime source of truth. Route overlays не становятся default автоматически,
provider-native homes не мигрируют.

После обновления запустите `giga doctor`, откройте существующий project/session,
проверьте preview старого attachment и Effective Instructions, затем выполните
gateway `--dry-run` до managed sidecar. Храните pre-upgrade archive, пока эти
проверки и required work journey не завершатся успешно.

Для отката остановите GigaLoom и owned managed sidecar lease, отключите gateway
profiles 0.9, установите 0.8.1 и восстанавливайте verified archive только через
существующего state restore owner. Старый executable может игнорировать или
карантинировать unknown additive records; не удаляйте immutable launch/delivery
receipts и не переписывайте вручную SQLite/JSON state. Удаление `gpt2giga 0.3`
явно отключает новые routes и не должно переназначать их на legacy gateway.

## Диагностика

- Нет провайдера: установите нативный CLI и используйте его login/status.
- Нет managed terminal: установите tmux на POSIX-системе или используйте
  provider-native passthrough, который показывает `giga doctor`.
- Действие отклонено: проверьте scope; не обходите policy или approval.
- Устарели browser assets: переустановите релизный пакет.
- Нет optional gateway: проверьте extra `gpt2giga`; checkout исходников не нужен.
- Gateway route заблокирован: проверьте точный compatibility/preflight reason;
  не форсируйте другой protocol, provider, model или agent как fallback.

## Базовая линия качества

Репозиторий владеет отдельным badge покрытия GigaLoom. Split baseline —
**84.59%**, измеренный 2026-07-29 non-live standalone gate. Это зафиксированная
база, а не утверждение о непроверенном remote run. Quality gate требует не
менее 80% и исключает opt-in live provider tests.
