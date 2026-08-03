# ADR: Read-only projection Effective Instructions

Статус: принято 2026-08-04 для foundation GigaLoom 0.9.

## Контекст

Пользователь должен понимать, какие project instructions могут влиять на run.
GigaLoom уже имеет owners Context Manifest и Context Lens. Синтез нового prompt
дублировал бы owner, создавал double injection и обещал непроверенную
compatibility.

## Решение

### Owner и discovery boundary

Effective Instructions расширяет Context Manifest/Lens как read-only
projection. Discovery ограничен project root, bounded tracked-file enumeration,
safe symlinks, ignores и size/count budgets. Допустимы root/nested `AGENTS.md`,
Agent Profile prompt files/selectors, GigaLoom rules и provider-native project
rules только при точной adapter capability без чтения private user homes.

Projection не объединяет автоматически `.cursor`, `.claude`, `.codex`,
`GEMINI.md`, `AGENTS.md` или GigaLoom rules, не materialize и не inject-ит их.
Provider-native homes/global config не читаются и не меняются.

### Контракт projection

Для каждого source записываются path/kind, digest, scope, precedence,
inclusion/omission reason, freshness, estimated tokens, conflicts,
uncertainties и materialization owner. Aggregate evidence содержит revisions,
digests, counts, bounds и omissions; prompt/rule content по умолчанию не
сохраняется.

Precedence приходит от owning adapter; GigaLoom не изобретает cross-provider
ordering. Conflicts — review facts, не merge decision. Unsupported source
отображается явно. Decision с unknown/stale revision fail closed и требует
revalidation; read-only display может показать stale facts только с revision и
omissions.

## Миграция и откат

Projection additive и не хранит replacement corpus. Migration может пересчитать
content-free digests, но не переписывает sources. Rollback удаляет только
derived cache существующего context owner; project files и native homes
остаются без изменений.

## Последствия

До запуска видны scope, precedence, drift, conflicts и omissions, при этом
GigaLoom не становится новым prompt owner и не меняет native agent loading.
