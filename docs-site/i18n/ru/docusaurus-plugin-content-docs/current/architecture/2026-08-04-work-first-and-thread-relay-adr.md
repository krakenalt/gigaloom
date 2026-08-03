# ADR: Work-first композиция и ограниченный Thread Relay

Статус: принято 2026-08-04 для foundation GigaLoom 0.9.

## Контекст

GigaLoom уже владеет проектами, структурированными сессиями, запусками,
evidence, actions, authority decisions и native processes. Версия 0.9 должна
собрать их в единый рабочий narrative и позволить пользователю читать или
адресовать другой поддерживаемый thread без второго transcript store и
автономной agent-to-agent сети.

## Решение

### Work-first композиция

Основной путь продукта: `Project -> Thread -> Run -> Evidence -> Action`.
Экран Work компонует существующих owners и имеет одно primary action —
отправить outcome пользователя в выбранный thread. До отправки видны
project/thread, agent, route/model status, workspace, Effective Instructions,
authority mode и blockers; после — единый причинный narrative запуска.

Это изменение vocabulary и projection. Записи project, session, run, evidence,
action, approval, route и process остаются у текущих owners.

### Контракты Thread Relay

`ThreadLocatorV1` содержит source kind (`gigaloom`, `codex`, `acp`), adapter,
project, thread, actor scope, capability revision и опциональные workspace/
provider refs. `ThreadReadProjectionV1` содержит ограниченные видимые сообщения,
title/status/time, active-turn и route/model facts, relationships, cursor,
omissions, redaction и unsupported facts.

`ThreadMessageEnvelopeV1` содержит locators, actor/project binding, фиксированную
роль `user`, режим `user_authored` или `agent_proposed_user_approved`, ссылки на
message/attachments, intent, ожидаемые target/turn revisions, idempotency key,
expiry и depth. `ThreadDeliveryReceiptV1` сохраняет identities, action/status,
timestamps, refs, capability revision, только content digest и ограниченную
причину failure/expiry/cancellation.

### Адаптеры и authority

GigaLoom sessions используют существующего durable owner. Codex ограничен
pinned app-server method/version capability, ACP — advertised session
capability. Opaque native terminals не открывают чужую history; input в уже
GigaLoom-owned terminal проходит через существующий authority path.

Agent surface содержит только `thread.list`, `thread.read`, `thread.send` и
`thread.status`. Settings, secrets, install/publication/recovery mutations, raw
history, system injection, hidden reasoning и provider-private state запрещены.

### Безопасность и границы

- Actor/project bindings обязательны; cross-project доступ запрещён по умолчанию.
- TTL и idempotency key обязательны; depth не больше 1, outstanding child
  deliveries не больше 4 на source run/thread.
- Mutating delivery проверяет target revision, steer — точный active-turn id.
- Доставляется только роль `user`; agent proposal требует user approval receipt.
- Reads/cursors ограничены; attachments остаются refs; receipts содержат digest,
  а не content.
- Failure не переписывает target history. Нет loops, whole-transcript copy,
  hidden-state portability или silent memory mining.
- Unknown/stale capability revision запрещает admission до revalidation и не
  выбирает fallback adapter.

## Миграция и откат

Четыре versioned contracts — additive projections над существующими owners.
Adapter включается только с explicit capability revision; sessions не
импортируются и не переписываются. Rollback запрещает новые deliveries, сохраняя
receipts и source-owned state. Vendor home не читается и не изменяется.

## Последствия

Продукт компонует work и cross-thread actions без дублирования session truth;
Relay остаётся bounded, attributable и не повышает authority.
