# Контракты durability, recovery и производительности

Этот документ фиксирует операционные контракты, которые остаются стабильными
при организации пакета GigaLoom по bounded contexts. Он дополняет
[ADR структуры пакета](package-layout.md) и
[ADR per-run storage](session-run-storage-adr.md).

## Финальная структура пакета и владение

Production-поведение принадлежит одному именованному контексту:

```text
gigaloom/
  attachments/  automation/  cli/          contracts/   core/
  diagnostics/  execution/   harnesses/    integrations/ native/
  projects/     providers/   review/       runtime/      sessions/
  skills/       tools/       ui/
```

Domain contexts владеют state и policy. `execution` координирует их публичные
facades. CLI, Web и diagnostics адаптируют эти application APIs, но не
становятся альтернативными владельцами state. Root Python-модули вне этого
дерева являются только временными compatibility shims. Versioned architecture
manifest — источник истины для каждого оставшегося shim, owner и removal gate.

## Authoritative и derived state

| State | Authority | Правило rebuild |
| --- | --- | --- |
| Session metadata, messages, events, attachments и per-run current records | Redacted-файлы в настроенном Harness data directory | Нельзя удалять ради починки индекса |
| Durable jobs, attempts, approvals, workers, leases, outbox и side-effect records | Runtime SQLite database | Мигрировать вперёд по runtime schema; не восстанавливать из UI projections |
| Session catalog, lookup tables, run order, revisions и SQLite read models | Derived indexes или projections | Можно удалить и перестроить из authoritative session records |
| Browser query caches, SSE pending queues, generated frontend assets и benchmark reports | Derived или exported artifacts | Повторно загрузить, сгенерировать или создать; это не recovery source |
| Project configuration и project-local `.giga/` state | Файлы project workspace | Сохранять или version-control отдельно от Harness user-data archive |

Настроенный user-data directory обычно равен `~/.gigaloom` и
переопределяется через `GIGALOOM_DATA_DIR`. Redaction выполняется до
authoritative persistence и до API/UI serialization. Support export не
содержит content; state backup является приватными user data и не redacted.

## Migration и rebuild

- Legacy `runs.jsonl` остаётся immutable migration input. Authoritative
  per-run records материализуются под durable marker; после прерывания reopen
  детерминированно повторяет migration.
- `run_order.jsonl`, run revisions, session catalog и session SQLite
  projections являются rebuildable. Stale или отсутствующая projection не
  может перекрыть завершённую authoritative write.
- Runtime SQLite schema migrations являются forward-only и применяются к
  authoritative coordination database. Перед сменой package version
  остановите UI и workers и создайте проверенный backup.
- Структурные Python-переносы не меняют persisted schemas, `fsync` barriers,
  locks, leases, cancellation, reconciliation, API bodies, SSE events или CLI
  exit codes.
- Packaged Cockpit assets являются производными от точного source commit.
  Пересоберите их перед editable sync или package validation; sealed wheel и
  sdist используют проверенный asset manifest без запуска Node.

## Классы durability событий

Retained events проходят redaction до append и записываются bounded-группами не
более 64 records.

| Класс | Примеры | Flush-контракт |
| --- | --- | --- |
| `critical_control` | Approval, warning, recovery и неизвестные control events | Консервативно завершает и сохраняет текущую группу до продолжения control flow |
| `final_state` | `run_finished`, `run_canceled`, `error`, завершённые tool/command/message events | Завершает и сохраняет текущую группу до публикации terminal state |
| `presentation_delta` | Message, stdout/stderr, reasoning и tool-call deltas | Может входить в bounded-группу; retained event никогда не становится in-memory-only authority |

`append_event` сохраняет legacy synchronous guarantee: retained record
persisted до возврата вызова. Classification управляет batching и flush
boundaries, а не тем, durable ли retained control или terminal event. При SSE
overflow клиент получает требование resnapshot из durable state, а не
утверждение, что unbounded in-memory stream полон.

## Worker maintenance cadence

Каждый worker ведёт независимые monotonic deadlines:

| Задача | Default cadence |
| --- | ---: |
| Worker и owned-attempt heartbeat | 2 секунды |
| Schedule trigger scan | 5 секунд |
| Retry requeue | 5 секунд |
| Expired-attempt recovery | 5 секунд |
| Runtime/session reconciliation | 30 секунд |

Явный request может приблизить один deadline. Завершение задачи продвигает
только её deadline, поэтому частый heartbeat не может вытеснить recovery или
reconciliation. Idle polling по умолчанию увеличивается от 0,25 до максимум
1 секунды, ограничивается ближайшим database или maintenance deadline и может
быть прерван best-effort content-free loopback wake signal. Если loopback
недоступен, fallback остаётся bounded polling.

## Benchmark profiles

Запускайте content-free diagnostics так:

```bash
giga benchmark performance --profile ci-smoke --samples 10
giga benchmark performance --profile local-detail --samples 10
giga benchmark performance --profile runtime-detail --samples 10
```

| Profile | Назначение | Gate semantics |
| --- | --- | --- |
| `ci-smoke` | Быстрые deterministic projections и local primitives | Единственный профиль с blocking CI budgets |
| `local-detail` | Детали filesystem, SQLite, session/history, CLI, worker и Web | Reference evidence; machine-sensitive budgets не blocking |
| `runtime-detail` | Queue claims, leases, worker cadence/wakeup, recovery, SQLite contention, revisions и application reads | Evidence runtime regression и scaling |

CLI по умолчанию использует пять samples; release и refactor gates задают
sample count в своём плане. `--output PATH` записывает canonical private JSON с
mode `0600`. Reports content-free, имеют size bounds и retention metadata
(7 дней для `ci-smoke`, 14 дней для detail profiles) и не обещают одинаковую
absolute latency на разных машинах.

## Legacy compatibility и migration imports

Новый first-party code импортирует публичные границы контекстов, например:

```python
from gigaloom.runtime.api import RuntimeCoordinationStore
from gigaloom.projects.api import resolve_project
from gigaloom.integrations.api import IntegrationCatalogStore
from gigaloom.diagnostics.performance.api import run_performance_baseline
```

Reviewed root paths, например `gigaloom.doctor`,
`gigaloom.product_inventory` и
`gigaloom.performance_baseline`, остаются compatibility shims на время
опубликованного migration window. Они не содержат business implementation.
Удаляйте shim только после доказанной parity в production, tests, examples,
docs, entry points и installed wheel/sdist; иначе сохраняйте его с явными owner
и removal gate.

Исторический combined-prerelease namespace `gpt2giga.harness.*` отличается:
standalone distribution не восстанавливает его. Out-of-tree adapters должны
использовать `gigaloom.*`, публичные SDK/contracts и существующую
entry-point group `gigaloom.harnesses.v1`. Проверяйте dynamic imports и entry
points из installed artifact, а не только из source checkout.

## Backup и rollback

Перед upgrade или rollback остановите Cockpit processes, durable workers и
active runs выбранного data directory:

```bash
giga state backup --output ../gigaloom-state.zip
giga state verify ../gigaloom-state.zip --json
giga state restore ../gigaloom-state.zip --replace --json
```

Создавайте архив вне state directory и храните приватно. Backup versioned и
content-addressed, делает consistent SQLite snapshots и исключает transient
locks, WAL/SHM и temporary files. Для project-local `.giga/` directories нужна
отдельная backup policy.

Каждый structural commit независимо revertible, пока сохраняется его
compatibility shim. Reverse state migrations не поддерживаются: package
rollback требует проверенный pre-upgrade archive для этой версии. Derived
indexes можно перестроить после restore; authoritative files и runtime database
нельзя удалять, чтобы заставить старый package запуститься.
