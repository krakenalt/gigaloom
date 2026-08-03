# ADR: Локальный content-free product evidence

Статус: принято 2026-08-04 для foundation GigaLoom 0.9.

## Контекст

Design-partner beta требует фактов о достижении успешной и recoverable работы.
Telemetry или вывод intent из filesystem timestamps нарушает local boundary и
создаёт слабые метрики. GigaLoom уже владеет run, action, approval, recovery,
route, process и evidence facts.

## Решение

### Owner и команда

Существующий evidence owner создаёт `ProductEvidenceReportV1` только по явной
локальной команде
`giga evidence product-beta --project <id> --output <path>`. Export opt-in,
project-scoped, bounded, deterministic и local-only. Нет upload, network
export, account lookup, outreach, dependency execution или provider call.

### Контракт report

При наличии сильных owner facts вычисляются durations/outcomes: first project
до successful run и accepted change, first-run outcome/blocker, approval
latency/abandonment, intervention/cancellation/recovery/resume, review до
accepted change, repeated Work/Inbox/Automations/Library use, Relay adoption/
rejection, gateway preflight/unsupported rate и managed sidecar cold/warm attach.

Каждая metric связывает schema/version, pseudonymous project identity, bounded
window, source revisions/digests, numerator/denominator или count, outcome и
omission/unknown reasons. `unknown` остаётся unknown: install time, intent,
success и adoption не выводятся из weak filesystem proxies.

### Privacy и compatibility

Evidence content-free: без prompts, responses, transcript, code, diffs,
unrestricted paths, raw arguments, credentials, accounts, attachment bodies и
provider payloads. Допустимы enumerated outcomes, counts, bounded durations,
redacted classes и уже owned digests.

Новый event/session/process/route/approval/evidence store не создаётся.
Unknown/stale source revision делает metric `unknown` с omission, а не берёт
факты у другого owner. Export success не означает успех dependency/route/run.

Отдельный dated pilot contract определяет opt-in cohort, privacy/support
boundary, metrics, stop criteria и решение `continue`, `narrow`,
`internal_only`, `defer` или `stop`. Outreach и чужие данные всегда требуют
отдельной авторизации.

## Миграция и откат

Report — derived versioned artifact над immutable facts. Migration пересчитывает
новую версию без придуманных событий. Rollback удаляет только выбранные
пользователем report files и не меняет source evidence; remote collector нет.

## Последствия

Beta оценивается честными local facts при сохранении evidence owner, privacy и
отдельной authority для любого sharing.
