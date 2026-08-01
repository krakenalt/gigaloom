# ADR: Операционное доверие, headless-выполнение и release identity

Статус: принято 1 августа 2026 года как операционная основа GigaLoom 0.8.

## Контекст

В GigaLoom 0.7 появился Native Agent Gateway: coding agents владеют своими
реальными терминальными процессами, а управляемое структурированное выполнение
запускается только через явную поверхность GigaLoom. Для следующего
операционного уровня нужны стабильные термины и машинные контракты, прежде чем
установка из registry, восстановление, обновления, визуальная оценка и
headless-выполнение смогут развиваться независимо.

Нужно устранить пять неоднозначностей:

- `/api/agents` уже описывает создаваемые в проектах automation definitions и
  не может одновременно обозначать установленные среды coding agents;
- запись registry может описывать структурированный ACP route, не владея
  нативной командой для человеческого терминала;
- проверенные версии executable полезны как evidence, но слишком изменчивы,
  чтобы быть единственным источником authority для structured compatibility;
- машинному runner нужны детерминированные потоки и значения exit code, а не
  правила терминального представления;
- release identity повторяется в Python, npm, manifests, changelogs и именах
  workflow artifacts без единственного редактируемого вручную источника.

Решение расширяет существующих владельцев authority, approval, project, run,
route, evidence, capsule и Agent Profile. Оно не создаёт параллельных
владельцев состояния.

## Решение

### Продуктовая терминология и владение API

Следующие термины обозначают разные публичные понятия:

| Понятие | Продуктовое имя | Владелец API |
| --- | --- | --- |
| Нативные coding CLI, установленные из registry ACP agents и пользовательские structured routes | **Coding Agents** | `/api/agent-runtimes/...` |
| Официальный удалённый каталог совместимых ACP agents | **ACP Registry** | `/api/agent-runtimes/registry/...` |
| Управляемые изолированные скачанные или пакетные artifacts | **Installed Agents** | `/api/agent-runtimes/installations/...` |
| Создаваемые в проекте переиспользуемые workflow agents | **Automation Agents** | существующий `/api/agents/...` |
| ACP, app-server, event streams и подобные машинные transports | **Routes** | под одним Coding Agent |

`registry_id`, `local_agent_id`, `native_agent_id`, `route_id` и
`managed_install_id` остаются разными типизированными идентификаторами.
Значение registry id можно предложить как local id только после проверки
коллизий. Display name никогда не создаёт alias. Связывание установленного ACP
route с существующей нативной идентичностью продукта — отдельное явное
действие.

Метаданные registry являются evidence для discovery, а не authority для
выполнения. Search, refresh и installation preview не устанавливают пакеты, не
аутентифицируют, не выполняют код, не открывают browser, не изменяют native
home и не выдают workspace или network authority.

### Владение нативным суффиксом

Когда первый токен разрешён в Coding Agent с native launch contract, каждый
оставшийся токен принадлежит нативному агенту. GigaLoom передаёт суффикс без
разбора, перевода, добавления, удаления, сохранения или content hashing.
Нативные stdout, stderr, terminal topology, signals и exit status остаются во
владении провайдера по существующему прямому или управляемому контракту
процесса.

Core commands всегда имеют приоритет при разрешении namespace. В частности,
`giga agent add ...` — installer registry во владении GigaLoom, а
`giga <native-agent> add ...` — непрозрачный синтаксис нативного агента.
Registry id не может затенить core command, native id или alias.

ACP installation без нативного контракта не становится terminal command. Она
используется через явного structured owner, например
`giga run --agent <id>`, или через Web Workbench. Наличие id в registry не даёт
GigaLoom права запускать ACP stdio server прямо в человеческий терминал.

### Protocol-first совместимость structured routes

Native launch не зависит от версии, если executable можно безопасно разрешить
и запустить. Structured route допускается по non-mutating observation, которое
содержит executable identity, protocol negotiation, framing, обязательные
capabilities, security invariants и проверенное evidence.

Проверенный диапазон версий влияет на confidence и warnings, но сам по себе не
является allowlist. Более новый или неизвестный executable, прошедший
обязательный protocol/capability probe, получает состояние
`compatible_unverified`; structured route не исчезает только из-за версии вне
последнего проверенного диапазона.

Structured admission работает fail-closed при:

- несовместимом major protocol;
- отсутствии обязательной capability или session behavior;
- malformed или unsafe framing;
- нарушении security invariant;
- явном known-bad rule, связанном с immutable evidence.

Ошибка probe, drift или несовместимость structured route не лишают допустимости
безопасный нативный вызов `giga <agent>`. Version strings, manifests,
signatures, package integrity, discovery и download success остаются evidence
и не дают authority на установку, выполнение, аутентификацию, credentials,
network или filesystem.

### Headless stdout, stderr, events и exit codes

`giga run --headless` — единственный публичный владелец headless execution.
Команда принимает ровно один явный источник prompt, требует семантику
`--no-input`, не зависит от TTY и раздельно проверяет workspace и result
directory.

В режиме `jsonl-v1`:

- stdout содержит ровно один канонический JSON object на строку и ничего
  больше;
- stderr содержит только ограниченную человеческую диагностику;
- оба потока не содержат ANSI control sequences;
- sequence numbers монотонны внутри одного run;
- signals и timeouts переходят в ограниченную cancellation и создают terminal
  receipt;
- terminal event выдаётся ровно один раз;
- terminal event содержит ссылку на result или capsule и явный список
  omissions.

Обязательные события: `run_started`, `agent_resolved`, `route_observed`,
`turn_started`, `tool_activity`, `approval_required`, `usage`, `artifact`,
`warning`, `run_succeeded`, `run_failed` и `run_canceled`. Успех требует и
успешного процесса, и обязательного structured terminal receipt.

Exit codes стабильны:

| Код | Значение |
| ---: | --- |
| 0 | Успех |
| 2 | Ошибка CLI usage или детерминированный admission failure |
| 10 | Требуется или истекла аутентификация |
| 20 | Отказ policy или authority |
| 30 | Ошибка Coding Agent или structured transport |
| 40 | Cancellation или timeout |
| 50 | Ошибка state, evidence или integrity |
| 70 | Нарушение внутреннего invariant |

### Владение release identity

`release/version.toml` — единственная редактируемая вручную release identity.
Команда подготовки релиза владеет детерминированными projections в Python
metadata, npm metadata, `release/release.json`, Git tag, сгенерированные
разделы changelog, сгенерированную package documentation и artifact manifest.

Подготовка атомарна и идемпотентна. Verification завершается ошибкой при drift
любой projection, а no-op preparation байт-в-байт ничего не меняет. Build hooks
могут проверять tracked inputs, но не имеют права молча их изменять.

Release evidence использует role-based имена: `external-evidence.json`,
`candidate-report.md` и `artifact-set.toml`. Версия находится внутри
digest-bound payload, а не в имени файла. Tagging, registry publication и
создание GitHub Release остаются отдельными действиями владельца и не следуют
автоматически из подготовки.

### Производительность и приватность

Производительность является частью каждого application contract. Shell
Settings и лёгкие локальные defaults должны отображаться без ожидания provider
account probing, MCP inventory, diagnostics и всех harness projections.
Registry refresh и install progress не входят в CLI startup или
`giga --version`.

Content-free baseline до изменений сохранён в
[`benchmarks/gigaloom_performance/baselines/2026-08-01-operational-foundation.json`](https://github.com/krakenalt/gigaloom/blob/main/benchmarks/gigaloom_performance/baselines/2026-08-01-operational-foundation.json).
Он фиксирует cold CLI, API, Settings, source inventory и release-bump facts на
принятом source commit. Абсолютные timings являются evidence конкретного host,
а не переносимыми budgets.

Baseline и compatibility observation не содержат secret, prompt, response,
credential value, raw native argument или private path. Credential material
остаётся reference-only до последней ответственной injection boundary и не
становится model-visible или persisted content.

## Последствия

Страницы, routes и state Coding Agents и Automation Agents могут развиваться
без перегрузки одного термина или API namespace. Нативные команды переживают
drift structured adapters, а structured transports по-прежнему fail-closed при
ошибках protocol, capability, integrity и security.

Headless consumers получают один ANSI-free machine contract вместо парсинга
терминального вывода. Подготовка release сможет заменить ручные изменения
нескольких файлов, не получая authority на публикацию.

ADR не изменяет runtime dispatch, API response, persisted state, release
metadata, provider process, native home или external system. Реализация и
миграция требуют отдельных проверенных изменений у названных владельцев.
