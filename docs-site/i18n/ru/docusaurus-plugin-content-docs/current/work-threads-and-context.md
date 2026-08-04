# Рабочие задачи, потоки и контекст

GigaLoom 0.9 строит основной путь как
`Project -> Thread -> Run -> Evidence -> Action`. Экран Work объединяет
существующих владельцев project, session, runtime, evidence, approval и action,
но не создаёт второе хранилище transcript или execution.

## Начало работы в Work

```sh
giga ui
```

Откройте `http://127.0.0.1:8091/web/work`. Выберите проект и поток, затем до
отправки проверьте agent, поддержку route/model, workspace, сводку Effective
Instructions, authority mode и blockers. Запуск показывается как единая
причинная история. В Inbox находятся элементы, требующие внимания, в
Automations — schedules и workflows, а в Library — переиспользуемые определения
и ограниченный браузер Thread Relay.

## Thread Relay

Thread Relay умеет перечислять и читать допущенный поток GigaLoom, Codex
app-server или ACP и доставлять одно сообщение с ролью user в поддерживаемую
цель. Чтение ограничено и маскируется. Механизм не копирует transcript целиком,
не запрашивает hidden reasoning, не читает private homes провайдеров и не
создаёт автономный relay loop.

Перечислите и прочитайте поток локального проекта:

```sh
giga session threads --json
giga session read THREAD_ID --json
```

Проверьте доставку без сохранения receipt, запуска turn или вызова провайдера:

```sh
giga session send THREAD_ID --text "review failing tests" --dry-run --json
```

Короткий dry-run читает текущую ревизию цели и создаёт только краткоживущие
preview guards. Реальная доставка намеренно требует больше явных параметров:

```sh
giga session send THREAD_ID \
  --text "review failing tests" \
  --expected-revision REVISION \
  --idempotency-key UNIQUE_KEY \
  --expires-at RFC3339_TIMESTAMP \
  --json
```

Используйте `--source codex` или `--source acp`, только когда доступна
соответствующая pinned capability адаптера. Для steering также нужен точный id
активного turn. Перед изменением повторно проверяются actor/project binding,
revision, TTL, idempotency, relay depth и доступность attachments. Receipts
хранят content digests и ограниченные факты статуса, а не текст сообщения.

## Схемы редактора

В пакет входят JSON Schemas для `.giga` agent, workflow, schedule, evaluation и
других editor artifacts. Они помогают редактору проверять authored files;
обычные `.giga` parsers остаются runtime source of truth.

```sh
giga schema list --json
giga schema agent
```

Параметр `--output PATH` записывает выбранную схему. Экспорт не меняет
конфигурацию проекта и не выдаёт execution authority.

## Кодировки attachments

Текстовые attachments декодируются детерминированно, без replacement
characters. Допустимые evidence values: `utf-8`, `utf-8-sig`, UTF-16 LE/BE,
UTF-32 LE/BE, `windows-1251` и `koi8-r`. Preview показывает charset, confidence
class, наличие BOM, truncation, replacement count, failure reason и source
digest, но не повторяет исходный текст.

Malformed BOM, неоднозначный legacy text, binary signatures, избыток control
characters, size bombs и undecodable bytes отклоняются или пропускаются с явной
причиной. GigaLoom не подставляет молча `U+FFFD` и не меняет сохранённые source
bytes. Записи attachments до 0.9 остаются читаемыми; optional charset evidence
может отсутствовать.

## Effective Instructions

Перед запуском Work читает project-scoped projection
`GET /api/project/effective-instructions`. Она показывает обнаруженные sources,
scope, precedence от owning adapter, included/omitted counts, conflicts,
uncertainty, freshness, bounds и digest.

Это read-only представление. GigaLoom не объединяет и не инъецирует
`AGENTS.md`, `GEMINI.md`, `.claude`, `.codex`, Cursor или project rules и не
читает private provider homes. Unknown или stale capability facts остаются
видимыми и приводят к fail-closed, если от них зависит решение.

## Локальный beta evidence

Участник с явным согласием может экспортировать один ограниченный content-free
отчёт для catalog project:

```sh
giga evidence product-beta --project PROJECT_ID --output report.json
```

Команда записывает только выбранный локальный файл. Она не выполняет upload,
telemetry, provider call, account lookup или outreach. Prompts, responses,
содержимое messages/events, credentials, unrestricted paths и тела attachments
исключены. См. [условия пилотного внедрения](pilot-program.md).

## Обновление и откат

Projections и records 0.9 добавочны. Существующие sessions 0.8.1 не
переписываются в relay records; старые attachments остаются читаемыми; старые
клиенты Context Lens могут игнорировать новые instruction fields; gateway route
не становится default автоматически. Перед обновлением сохраните
`~/.gigaloom` и project state `.giga/`.

Для отката остановите GigaLoom, отключите gateway profiles 0.9, остановите
только managed sidecar lease и установите 0.8.1, восстановив проверенный
pre-upgrade archive, если нужен state restore. Immutable launch/delivery
receipts могут остаться неизвестными records для старого binary; native homes и
история sessions не переписываются. Удаление `gpt2giga 0.3` отключает его
routes, а не переназначает их на legacy fallback. Процедура backup/restore
приведена в [Операциях](operations.md).
