# ADR: Структура пакета по ограниченным контекстам

Статус: принято как архитектурная передача T15 Phase A для gate `G-ARCH`
2026-07-29 и финализировано как package contract T20 2026-07-30.

## Контекст

GigaLoom — модульный монолит с долговечным локальным состоянием, тремя
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
`packages/gpt2giga-harness/architecture/module-budgets.json`. Она привязана к
замороженной ревизии `G0` и содержит текущие исключения корневых модулей,
ограничения размера, правила импортов, владельцев и removal gates.

## Решение

Python-дистрибутив организуется как модульный монолит по ограниченным
контекстам. Имена пакетов верхнего уровня описывают продуктовый домен или
адаптер интерфейса. Бизнес-поведение не размещается в глобальном generic
service, model, helper или utility package.

В финальном корне `gpt2giga_harness` остаются только:

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
skills/       tools/       tui/          ui/
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
| `tools` | Tool contracts, profiles, policy, secrets и lifecycle managed/external MCP | `tools/api.py` |
| `skills` | Built-in, external и portable skills, library, authoring и catalog proxy | `skills/api.py` |
| `automation` | Agents, workflows, schedules, evaluations, arena и attention services | Contracts и application services подконтекстов |
| `review` | Provenance, reviewed evidence, artifacts, replay, promotions, handoffs и support exports | Явные read и reviewed-mutation contracts |
| `diagnostics` | Doctor checks, compatibility evidence, inventory и performance tooling | Diagnostic report и export contracts |
| `cli` | Ленивая обработка командной строки, dispatch, errors и адаптация output | `cli/main.py`; не владеет бизнес-логикой |
| `tui` | Textual state, clients, controllers, projections, widgets, screens и rendering | Application client protocols; не владеет storage |
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

tui/
  entrypoint.py  app.py  contracts.py  state.py  i18n.py
  commands/  clients/  controllers/  projections/  widgets/  screens/
  rendering/

ui/
  app.py  container.py  dependencies.py
  security/  schemas/  services/  streaming/  routers/  cockpit_v2/
```

`sessions.storage` остаётся authoritative и прозрачным. SQLite read model
является derived, удаляемым и перестраиваемым. Такое же разделение
authoritative и derived применяется к индексам и projections других контекстов.

### Направление зависимостей

First-party imports направлены так:

```text
cli tui ui diagnostics -> public application APIs
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
3. Runtime и доменные контексты не импортируют `cli`, `tui` или `ui`.
4. Межконтекстные импорты используют `api.py`, `contracts.py` целевого
   контекста или другой явно названный public port, но не repositories,
   storage, routers, widgets и другие internals.
5. `harnesses` зависит от нейтральных contracts и публичных provider/native
   ports; provider modules не импортируют presentation surfaces.
6. `execution` координирует публичные session, runtime, harness, project и
   attachment APIs, не присваивая их persistence.
7. `automation` и `review` используют bounded execution, runtime, session и
   project APIs.
8. CLI, TUI и Web адаптируют ввод и вывод к application APIs, не дублируют
   policy и не обращаются к concrete filesystem/SQLite repositories.
9. `__init__.py` остаются import-light и не создают circular re-export chains.

Architecture checker разбирает source через AST стандартной библиотеки Python
и не импортирует application modules. Существующие внутренние cross-context
imports — точные временные исключения с owner и removal gate. Устаревшее
исключение ломает проверку.

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
  unit/{sessions,runtime,execution,providers,harnesses,integrations,automation,projects,cli,tui,ui}/
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

Дополнительные review limits:

| Единица | Цель | Hard limit без ADR |
| --- | ---: | ---: |
| Python function или method | 10–50 строк | 100 строк |
| Python class | 50–250 строк | 400 строк |
| FastAPI router | Не более 12 routes | 400 строк |
| React page или surface | 150–300 строк | 400 строк |
| React component | 50–200 строк | 300 строк |
| React hook или controller | 50–180 строк | 250 строк |
| TypeScript model или API file | 100–300 строк | 450 строк |
| CSS file | 100–300 строк | 450 строк |

Точное число физических строк каждого Python-модуля, превышавшего 600 строк на
`G0`, записано как ceiling. Файл может уменьшиться или исчезнуть, но не вырасти.
Каждый structural slice уменьшает принадлежащий ему legacy god-file минимум на
15 процентов, пока файл остаётся выше hard limit. Manifest после slice
обновляется вниз; повышение ceiling требует отдельного ADR и измеримой причины.

Generated schemas/assets, frozen evidence fixtures и migration SQL получают
исключение только через явную запись manifest. Исключение не может содержать
executable business logic.

Новые generic `utils.py`, `helpers.py`, `common.py`, `misc.py`, глобальный
`services.py` или product-wide `models.py` запрещены. Разбиение одного god-file
на несколько файлов с прежним смешением responsibilities не считается
завершением.

## Протокол миграции

За один раз перемещается один модуль или связная responsibility:

1. Добавить destination context/subpackage вместе с публичным contract и
   focused tests.
2. Переместить или извлечь поведение без redesign и без алгоритмической
   оптимизации в том же коммите.
3. Сохранить старый import path через compatibility shim не более 30 строк.
   Более крупные composition/protocol facades используют только явно записанный
   budget.
4. Добавить или ужесточить architecture rule, чтобы новый first-party consumer
   не мог использовать legacy path.
5. Мигрировать consumers небольшими owner-specific коммитами через публичный
   facade назначения.
6. Проверить entry points, dynamic imports, plugin targets, serialized type
   names и совместимость persisted state.
7. Перед удалением shim найти все обращения в production, tests, examples и
   docs; собрать installed artifact; проверить старый и новый imports на всём
   одобренном compatibility window.
8. Удалять shim только отдельным reviewed commit. Если evidence неполон,
   сохранить shim с явными owner и removal gate.

Storage migrations повторяемы после interruption. Structural moves сохраняют
atomic writes, locks, leases, cancellation, reconciliation, idempotency,
redaction boundaries и authoritative JSON/JSONL state. Перенос не меняет
неявно `fsync`, SQLite durability, public routes, SSE events, CLI output,
provider passthrough или approval semantics.

## Фазы рефакторинга

| Фаза | Результат |
| --- | --- |
| R0 | Зафиксировать этот ADR, root namespace, import direction, ownership, временные исключения и budgets без перемещения production code |
| R1 | Разделить текущие choke-point files за compatibility facades |
| R2 | Перенести плоские root modules в bounded contexts через owner-specific commits |
| R3 | Мигрировать consumers на public facades и запретить новые legacy imports |
| R4 | Удалить только shims с installed-artifact evidence; закрыть или документировать оставшиеся исключения |

Извлечение `core` и `contracts` — отдельно согласуемая Phase B. Она начинается
только после подтверждения integration owner, что hot-path owners перестали
изменять shared modules. До gate заморожены `types.py`, `config.py`,
compatibility surface в `execution/__init__.py`, `safe_paths.py` и
`instrumentation.py`.

## Владение во время миграции

| Область | Владелец |
| --- | --- |
| Session storage и queries | T02 |
| Runtime DB и facade foundation | T03 |
| Runtime jobs | T04 после runtime-foundation gate |
| Runtime workers и wakeup | T05 после runtime-foundation gate |
| Runner и новые `execution` modules | T06 |
| FastAPI composition, services, streaming и routers | T07 |
| CLI и entrypoint | T08 |
| TUI clients, contracts и projections | T09 |
| TUI application и rendering | T10 |
| Frontend API и query contracts | T11 |
| Frontend Workbench | T12 |
| Frontend streaming и bounded rendering | T13 |
| Architecture manifest, tests и этот ADR | T15 |
| `core` и `contracts` Phase B | T15 или T00 после явного согласования |
| Integrations, tools и skills | T16 |
| Providers и built-in harnesses | T17 |
| Automation и review | T18 |
| Projects, workspace и environments | T19 |
| Cross-cutting import migration, diagnostics, docs и final cleanup | T20 |

Замороженные shared contracts, включая registry и runtime policy/model
surfaces, изменяются только через integration owner. Тред, которому нужен файл
другого владельца, передаёт interface request, а не редактирует файл.

## Совместимость и rollback

Решение меняет организацию пакета, но не поведение продукта. CLI commands,
flags, output, exit codes, REST paths, response shapes, SSE events/cursors,
plugin entry points, provider-native passthrough, persisted state и TUI/Web
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
