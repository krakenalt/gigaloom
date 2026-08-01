# ADR: Per-run current-state storage

Статус: решение по хранению session/run принято 2026-07-29.

## Контекст

Session runs ранее хранились в одном authoritative `runs.jsonl` для каждой
session. Обновление одного run разбирало и переписывало все retained runs,
поэтому стоимость одной update росла вместе с session history, хотя менялся
bounded state одного run.

Замена должна сохранить прозрачные recoverable files, append order, redaction,
per-session serialization, Windows-safe replacement, legacy state и
rebuildable SQLite projections. Она не должна ослаблять durability barrier или
делать derived index authoritative.

## Варианты

### Append-only versioned run-state log

Каждый patch мог бы добавлять новую version, а latest-offset projection
разрешала бы current state. Writes остались бы последовательными, но retained
history росла бы с каждым patch, compaction стала бы отдельной migration, а
append-order pages пришлось бы различать run identity и state-version order.

### Per-run current-state files

Каждый run может иметь один atomically replaced current-state file с его
стабильной append position. Небольшой append-order file остаётся rebuildable
projection. Тогда update одного run читает и заменяет один bounded file.

## Решение

Использовать per-run current-state files.

- `runs.jsonl` остаётся immutable migration input для legacy sessions.
- Authoritative current records находятся в `run_records/`. Имена файлов —
  SHA-256 digests run IDs, поэтому caller-provided IDs не могут выйти из
  session directory.
- Каждый record хранит schema version, immutable append position и redacted run
  payload.
- `run_order.jsonl` и `run_revision.json` являются derived. Их можно удалить
  или перестроить сканированием current-state files с сортировкой по position.
- Migration сериализуется тем же per-session lock, что append и patch. Durable
  marker записывается до materialization; revision публикуется только после
  готовности всех current-state files и order projection. Restart с marker
  детерминированно повторяет migration из `runs.jsonl`.
- Patch читает и atomically replaces только выбранный current-state file.
  Temporary и destination files находятся в одной directory, handles закрыты
  до `os.replace`, а authoritative file flushed и synced до replacement.
- Concurrent patches берут один per-session process/thread lock и перечитывают
  current state внутри lock, поэтому сохраняют поля предыдущего patch.
- Существующий SQLite lookup остаётся derived и перестраивается из current
  files и append positions.

Storage не описывается как transactional. Будущий bounded write-batch contract
может координировать несколько файлов с явными recovery markers, но не должен
заявлять crash atomicity, которой это решение не предоставляет.

## Последствия

Read/write работа update одного run не зависит от числа retained runs. Full
export и legacy `list_runs` остаются явными O(N) operations. Во время migration
нужно дополнительное место, потому что `runs.jsonl` сохраняется как recoverable
input; cleanup требует отдельного compatibility decision.
