# Структура пакета и границы модулей

Статус: принято 29 июля 2026 года; окончательный контракт структуры пакета
утверждён 30 июля 2026 года.

## Контекст

GigaLoom — модульный монолит с долговечным локальным состоянием, двумя
пользовательскими интерфейсами, встроенными адаптерами coding agents, слоями
совместимости провайдеров и инструментами расширения. Существующий Python-пакет
вырос преимущественно как плоское пространство имён. Несколько корневых модулей
сочетают хранение, application policy, транспорт провайдеров и presentation, а
крупнейшие composition- и facade-файлы содержат тысячи строк.

Одномоментный перенос создаст ненужный риск для совместимости и восстановления.
Если оставить форму пакета неявной, параллельные рефакторинги выберут
конфликтующие имена и направления зависимостей. Поэтому репозиторий фиксирует
целевое дерево и ratcheting-проверки: новый долг запрещается сразу, но legacy
дерево не обязано стать полностью совместимым за один коммит.

Машиночитаемая политика хранится в
`architecture/module-budgets.json`. Она привязана к
замороженной архитектурной ревизии и содержит текущие исключения корневых модулей,
ограничения размера, правила импортов, владельцев и removal gates.

## Решение

Python-дистрибутив организуется как модульный монолит по ограниченным
контекстам. Имена пакетов верхнего уровня описывают продуктовый домен или
адаптер интерфейса. Бизнес-поведение не размещается в глобальном generic
service, model, helper или utility package.

В финальном корне `gigaloom` остаются только:

```text
__init__.py
entrypoint.py
py.typed
```

Остальная реализация принадлежит одному из замороженных контекстов:

```text
attachments/  automation/  cli/          contracts/   core/
diagnostics/  execution/   harnesses/    integrations/ native/
projects/     providers/   review/       runtime/      sessions/
skills/       tools/       ui/
```

Директория появляется только вместе с реальным поведением и focused tests.
Пустые пакеты-заглушки для внешнего сходства с целевым деревом не создаются.

### Ответственность контекстов

| Контекст | Ответственность | Публичная граница |
| --- | --- | --- |
| `core` | Технические primitives: configuration, IDs, clocks, paths, redaction, serialization, instrumentation и concurrency | Связные именованные модули; без импортов продуктовых контекстов |
| `contracts` | Стабильные provider-neutral DTO, protocols, enums, events, permissions и serialization contracts | `contracts/*`; импортирует только `core`, stdlib и typing |
| `sessions` | Conversation state, messages, runs, event streams, exports, titles, authoritative filesystem storage и derived read models | `sessions/api.py` и явные contracts |
| `runtime` | Durable jobs, workers, leases, approvals, outbox, side effects, revisions и reconciliation | `runtime/api.py` и явные contracts |
| `execution` | Provider-neutral admission, preparation, invocation, persistence, continuation и finalization запусков | `execution/api.py` |
| `harnesses` | Adapter SDK, registry, conformance, plugins и встроенные agent adapters | `harnesses/api.py` и SDK contracts |
| `native` | Общий lifecycle native-процессов, discovery, snapshots, stores и provider connectors | `native/contracts.py` и явные lifecycle ports |
| `providers` | Provider registry, profiles, settings, accounts, authentication, normalized protocols, compatibility transports и gateway configuration | `providers/api.py` |
| `projects` | Project configuration, memory, backup, workspace resolution, worktrees и environment actions | `projects/api.py` |
| `attachments` | Attachment models, limits, MIME handling, rendering и storage | `attachments/api.py` |
| `integrations` | Catalogs, installable packages, lifecycle, flows, groups и integration SDK | `integrations/api.py` |
| `tools` | Tool contracts, profiles, policy, secrets и lifecycle managed/external MCP | Package exports и `tools/mcp/api.py` |
| `skills` | Built-in, external и portable skills, library, authoring и catalog proxy | `skills/api.py` |
| `automation` | Agents, workflows, schedules, evaluations, arena и attention services | Contracts и application services подконтекстов |
| `review` | Provenance, reviewed evidence, artifacts, replay, promotions, handoffs и support exports | Явные read и reviewed-mutation contracts |
| `diagnostics` | Doctor checks, compatibility evidence, inventory и performance tooling | Diagnostic report и export contracts |
| `cli` | Ленивая обработка командной строки, dispatch, errors и адаптация output | `cli/main.py`; не владеет бизнес-логикой |
| `ui` | FastAPI composition, dependencies, routers, projections, streaming и доставка Cockpit | Application services и transport schemas; не владеет storage |

На время опубликованного migration window root files или directories вне
контекстов выше являются compatibility shims, а не владельцами реализации. Их
точные paths, owners и removal gates записаны в manifest. Временное имя не
разрешает добавлять business behavior или создавать рядом ещё один package.

### Целевая форма backend

Внутри контекстов используется следующая форма. Имена файлов можно уточнить
через ADR, но границы владения и направление зависимостей не меняются неявно.

```text
core/
  config.py  errors.py  ids.py  clock.py  json_codec.py  paths.py
  redaction.py  security.py  instrumentation.py  concurrency.py

contracts/
  harness.py  execution.py  events.py  permissions.py  providers.py
  plugins.py  serialization.py

sessions/
  api.py  models.py  commands.py  queries.py  conversation.py
  titles.py  exports.py
  events/{models,broker,cursors,projections}.py
  storage/
    protocol.py
    filesystem/{catalog,manifests,messages,runs,events,raw_records,atomic,migrations}.py
    read_model/{sqlite,schema,revisions,rebuild}.py

runtime/
  api.py  models.py
  db/{connection,transactions,migrations,schema,diagnostics}.py
  jobs/{repository,claims,leases,retries,cancellation,payloads}.py
  workers/{service,repository,maintenance,heartbeat,wakeup}.py
  approvals/{policy,authority,repository,audit,presentation}.py
  outbox/{repository,dispatcher}.py
  side_effects/{models,repository,executor}.py
  revisions/repository.py
  reconciliation/service.py

execution/
  api.py  context.py  options.py  admission.py  preparation.py
  continuation.py  invocation.py  persistence.py  finalization.py
  preflight.py  readiness.py  routing.py
  structured/{sessions,processes,supervision}.py

harnesses/
  api.py  registry.py  plugins.py
  sdk/{contracts,conformance,scaffold,capability_matrix}.py
  builtins/
    echo.py
    direct_chat/{adapter,transport}.py
    codex/{adapter,workbench,plugin_target,mcp_target}.py
    codex/app_server/{client,protocol,process,events,session}.py
    claude/{adapter,workbench,agent_sdk,handoff,plugin_target,mcp_target}.py
    gemini/{adapter,workbench,acp,extension_target,mcp_target}.py

native/
  contracts.py  models.py  discovery.py  registry.py  snapshots.py  store.py
  process/{manager,pty,output,recovery}.py
  connectors/{codex,claude,gemini}.py

providers/
  api.py  registry.py  profiles.py  settings.py  migration.py
  accounts/{models,sessions,broker}.py
  authentication/{models,resolution,capabilities}.py
  protocols/{normalized,openai,anthropic,gemini,gigachat}/
  gateway/{proxy,preset}.py

projects/
  api.py  config.py  memory.py  bootstrap.py  preferences.py  backup.py
  workspace/{resolver,tree,files,worktrees}.py
  environment/{models,capture,commit,push,pull_request,github,editor}.py

integrations/
  api.py  models.py
  catalog/{local,federated,sync}.py
  packages/{models,installer,lifecycle,runtime}.py
  flows/{models,service,repository}.py
  groups/{models,service}.py
  sdk/{contracts,conformance,scaffold}.py

tools/
  api.py  models.py  profiles.py  policy.py  secrets.py
  mcp/{contracts,external,managed,inventory,authoring,targets}/

skills/
  api.py  builtin.py  external.py  portable.py  library.py  authoring.py
  catalog_proxy/{server,client}.py

automation/
  agents/  workflows/  schedules/  evaluations/  arena/  attention/

review/
  provenance.py  evidence.py  artifacts.py  replay.py  promotions.py
  handoffs.py  support.py

diagnostics/
  doctor/  compatibility/  inventory/  performance/

cli/
  main.py  parser.py  registry.py  context.py  errors.py  output.py
  completion.py  commands/

ui/
  app.py  container.py  dependencies.py
  security/  schemas/  services/  streaming/  routers/  web/
```

`sessions.storage` остаётся authoritative и прозрачным. SQLite read model
является derived, удаляемым и перестраиваемым. Такое же разделение
authoritative и derived применяется к индексам и projections других контекстов.

### Направление зависимостей

First-party imports направлены так:

```text
cli ui diagnostics     -> public application APIs
automation review      -> execution runtime sessions projects
execution              -> sessions runtime harnesses projects attachments
harnesses              -> providers native contracts
domain contexts        -> contracts core
contracts              -> core
core                   -> stdlib and approved technical dependencies
```

Это верхняя граница зависимостей, а не требование импортировать каждый
доступный слой.

Правила:

1. `core` не импортирует продуктовые контексты.
2. `contracts` импортирует только `core`, stdlib и typing.
3. Runtime и доменные контексты не импортируют `cli` или `ui`.
4. Для импортов между подсистемами используются `api.py`, `contracts.py` или
   другой явно объявленный публичный интерфейс целевого модуля. Обращаться
   напрямую к внутренним репозиториям, хранилищам, маршрутам и компонентам
   интерфейса нельзя.
5. `harnesses` зависит только от нейтральных контрактов и публичных интерфейсов
   провайдеров и нативных CLI. Модули провайдеров не импортируют компоненты
   пользовательского интерфейса.
6. `execution` координирует публичные API сессий, среды выполнения, адаптеров,
   проектов и вложений, но не управляет их постоянным состоянием.
7. `automation` и `review` используют публичные API выполнения, среды,
   сессий и проектов.
8. CLI и веб-интерфейс преобразуют ввод и вывод для прикладных API, не
   дублируют политики и не обращаются напрямую к файловым и SQLite-хранилищам.
9. Файлы `__init__.py` не должны загружать тяжёлые зависимости или создавать
   циклические цепочки повторного экспорта.

Архитектурная проверка разбирает исходный код через AST стандартной библиотеки
Python и не импортирует модули приложения. Существующие нарушения границ
перечислены как временные исключения с ответственным и условием удаления.
Просроченное исключение приводит к ошибке проверки.

### Целевая форма frontend

Cockpit использует компактную feature-oriented структуру:

```text
frontend/src/
  app/{App,router,providers,queryClient,shell}/
  shared/{api,streaming,ui,hooks,lib,styles}/
  entities/{session,run,approval,environment,provider,attachment,integration}/
  features/{workbench,runs-center,integrations,settings,automation,evaluation,arena,inbox}/
  widgets/{inspector,navigation,drawers}/
  i18n/{keys,en,ru}/
  main.tsx
```

Направление импортов frontend:

```text
app      -> features, widgets, entities, shared
widgets  -> entities, shared
features -> entities, shared
entities -> shared
shared   -> third-party and browser APIs only
```

Feature не импортирует internal-файл другой feature. Общая логика переносится
в именованную entity или действительно reusable shared module со своими
тестами. Barrel files не скрывают feature-to-feature dependencies и cycles.

### Целевая форма тестов

Тесты следуют архитектуре владельца и типу evidence:

```text
tests/harness/
  architecture/
  unit/{sessions,runtime,execution,providers,harnesses,integrations,automation,projects,cli,ui}/
  contract/{adapters,api,cli,sse,storage}/
  integration/{durable_runtime,session_execution,application_surfaces}/
  migrations/{sessions,runtime}/
  performance/
  e2e/
  live/
```

Существующие тесты перемещаются только вместе со своим production context.
Массовая перестановка test tree не является prerequisite, а `live` остаётся
явным opt-in.

## Ограничения размера модулей

Новый executable Python-модуль без ADR содержит не более 600 физических строк;
предпочтительный диапазон — 150–400. Для composition или compatibility facade
hard limit равен 350 строкам, нормальный диапазон — 50–250.

Дополнительные ограничения для проверки кода:

| Единица | Рекомендуемый размер | Предельный размер без отдельного решения |
| --- | ---: | ---: |
| Функция или метод Python | 10–50 строк | 100 строк |
| Класс Python | 50–250 строк | 400 строк |
| Маршрутизатор FastAPI | Не более 12 маршрутов | 400 строк |
| Страница React | 150–300 строк | 400 строк |
| Компонент React | 50–200 строк | 300 строк |
| Хук или контроллер React | 50–180 строк | 250 строк |
| Модель TypeScript или файл API | 100–300 строк | 450 строк |
| Файл CSS | 100–300 строк | 450 строк |

Для каждого Python-модуля, который на момент принятия решения превышал
600 строк, в манифесте записан текущий верхний предел. Такой файл можно
уменьшить или удалить, но нельзя увеличивать. Каждое структурное изменение
должно сокращать соответствующий крупный устаревший модуль не менее чем на
15 процентов, пока он превышает установленный предел. После изменения предел
в манифесте снижается. Повысить его можно только отдельным архитектурным
решением с измеримым обоснованием.

Сформированные схемы и ресурсы, неизменяемые тестовые данные и SQL миграций
получают исключение только через явную запись в манифесте. Исключение не может
содержать исполняемую бизнес-логику.

Новые общие файлы `utils.py`, `helpers.py`, `common.py`, `misc.py`, глобальный
`services.py` и единый для всего продукта `models.py` запрещены. Простое
разбиение крупного файла на несколько частей без разделения ответственности
не решает архитектурную проблему.

## Протокол миграции

За один раз переносится один модуль или одна связная область ответственности:

1. Добавить целевой модуль или подпакет вместе с публичным контрактом и
   профильными тестами.
2. Перенести поведение без одновременного изменения архитектуры или алгоритма.
3. Временно сохранить старый путь импорта через прослойку объёмом не более
   30 строк. Более крупные фасады должны иметь отдельный лимит в манифесте.
4. Запретить новым внутренним потребителям использовать старый путь импорта.
5. Перевести существующих потребителей на публичный интерфейс целевого модуля
   отдельными небольшими изменениями.
6. Проверить точки входа, динамические импорты, цели плагинов, сериализованные
   имена типов и совместимость сохранённого состояния.
7. Перед удалением прослойки найти все обращения в рабочем коде, тестах,
   примерах и документации; собрать устанавливаемый пакет; проверить старый и
   новый пути импорта на всём поддерживаемом диапазоне версий.
8. Удалять прослойку отдельным проверенным изменением. Если проверка неполна,
   оставить прослойку и явно указать ответственного и условие удаления.

Миграции хранилища должны безопасно повторяться после прерывания. Перенос
модулей сохраняет атомарную запись, блокировки, аренды, отмену, согласование
состояния, идемпотентность, редактирование чувствительных данных и основные
записи JSON/JSONL. Он не меняет неявно поведение `fsync`, надёжность SQLite,
публичные маршруты, события SSE, вывод CLI, сквозную передачу параметров
провайдеру и правила подтверждения операций.

## Фазы рефакторинга

| Этап | Результат |
| --- | --- |
| Зафиксировать границы | Описать пространство имён, направление импортов, ответственность модулей, временные исключения и ограничения размера без переноса рабочего кода |
| Разделить крупные файлы | Выделить компоненты за совместимыми публичными фасадами |
| Перенести модули | Переместить корневые модули в соответствующие подсистемы |
| Перевести потребителей | Использовать публичные фасады и запретить новые импорты по старым путям |
| Удалить прослойки | Удалить только проверенные прослойки совместимости и закрыть либо документировать оставшиеся исключения |

Выделение `core` и `contracts` согласуется отдельно. Оно начинается только
после подтверждения команды интеграции, что ответственные за критические пути
перестали менять общие модули. До этого `types.py`, `config.py`, публичный
интерфейс `execution/__init__.py`, `safe_paths.py` и `instrumentation.py`
остаются неизменными.

## Владение во время миграции

| Область | Владелец |
| --- | --- |
| Хранилище сессий и запросы | Команда сессий |
| База данных и публичный фасад среды выполнения | Команда среды выполнения |
| Очередь заданий | Команда очереди среды выполнения после завершения базовой структуры |
| Рабочие процессы и пробуждение заданий | Команда рабочих процессов после завершения базовой структуры |
| Исполнитель и новые модули `execution` | Команда выполнения |
| Компоновка FastAPI, сервисы, потоковая передача и маршруты | Команда UI и серверной части |
| CLI и точка входа | Команда CLI |
| API веб-интерфейса и контракты запросов | Команда Web API |
| Workbench | Команда Workbench |
| Потоковая передача и ограниченный рендеринг | Команда веб-интерфейса |
| Архитектурный манифест, тесты и этот документ | Команда архитектуры |
| Выделение `core` и `contracts` | Ответственные за `core` и `contracts` после явного согласования |
| Интеграции, инструменты и навыки | Команда интеграций |
| Провайдеры и встроенные адаптеры | Команда провайдеров и адаптеров |
| Автоматизация и проверка результатов | Команда автоматизации и проверки |
| Projects, workspace и environments | Project maintainers |
| Cross-cutting import migration, diagnostics, docs и final cleanup | Architecture maintainers |

Замороженные shared contracts, включая registry и runtime policy/model
surfaces, изменяются только через integration owner. Тред, которому нужен файл
другого владельца, передаёт interface request, а не редактирует файл.

## Совместимость и rollback

Решение меняет организацию пакета, но не поведение продукта. CLI commands,
flags, output, exit codes, REST paths, response shapes, SSE events/cursors,
plugin entry points, provider-native passthrough, persisted state и Web
semantics остаются compatibility contracts.

Каждый structural commit можно независимо откатить. Compatibility shims и
authoritative state formats образуют rollback boundary. Derived indexes можно
удалить и перестроить; authoritative user state нельзя удалить ради упрощения
переноса пакета.

## Последствия

Параллельные рефакторинги используют замороженные имена контекстов и ownership,
не создавая новых namespace conflicts. Новые модули ограничиваются сразу, а
legacy god-files и internal imports могут только оставаться на месте или
сокращаться.

Цена решения — явный facade design, временные shims, поддерживаемый exception
manifest и дополнительные installed-artifact checks перед cleanup. Это
предпочтительнее скрытой cross-context coupling, широкого consumer churn или
рискованного big-bang rewrite.
