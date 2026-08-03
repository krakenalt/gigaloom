# ADR: Gateway routes и managed launch overlays

Статус: принято 2026-08-04 для foundation GigaLoom 0.9.

## Контекст

GigaLoom должен запускать coding agents через точный public artifact
`gpt2giga`, не смешивая identities agent, protocol, gateway, model и route.
Convenience command не должен создавать implicit fallback, второй supervisor
или менять provider-native configuration.

## Решение

### Identity и command grammar

`agent_id`, client protocol, `gateway_id`, public model alias и immutable
`route_id` различны. Canonical form:
`giga --route <route-id> <agent-id>`. Convenience form
`giga --with <gateway-id> --model <model> <agent-id>` до spawn обязан
разрешиться ровно в один immutable route; ambiguity в non-interactive режиме —
ошибка.

Global options заканчиваются на agent id; последующие tokens принадлежат native
agent. `giga --model X codex` выбирает route model, а
`giga codex --model X` передаёт option Codex. Dry-run JSON redacted и не создаёт
traffic. Missing capability не переключает protocol/provider/model/agent.

### Public contracts

`GatewayProfileV1` связывает identity/name, managed/external mode, package или
executable identity, exact version window, base URL, startup/health/readiness/
models/capability revisions, secret/TLS refs и artifact/profile digest.

`BridgeRouteV1` связывает route/agent ids, client protocol, gateway profile,
public alias, upstream facts gateway, capability/loss revisions, support status,
reasoning selector и acknowledgement. `LaunchOverlayV1` связывает route с
GigaLoom-managed home, redacted env delta, config refs, существующим process
lease, preflight receipt и capability digest. Secret values нигде не
сохраняются; через launch boundary проходят только refs.

### Lifecycle и compatibility truth

`external` preflight-ит работающий gateway. `managed` получает существующий
GigaLoom process lease, запускает точный public artifact, ждёт readiness и
привязывает process evidence к run. Cancellation, recovery, redaction и managed
roots сохраняют owners; второго daemon supervisor нет.

Ровно четыре статуса:

- `stable` — pinned agent window, gateway matrix cell и E2E green;
- `technical_preview` — tested path с bounded limitations/drift;
- `vendor_unsupported` — технически совместимый wire path вне vendor support;
- `blocked` — endpoint/injection отсутствует или semantics потеряны.

GigaLoom не повышает gateway/adapter evidence. В `gpt2giga 0.3.0` путь Codex
Responses -> GigaChat остаётся `technical_preview`, потому что normalized
Responses parity неполна. Profile связывает public wheel/version/digest и
использует только installed CLI/HTTP; private protocol/provider imports
запрещены. Unknown/stale revisions fail closed до revalidation.

### Граница overlay

Generated config живёт только в GigaLoom managed root или bounded temp.
Launch/cleanup не меняют `~/.codex`, `~/.claude`, `~/.gemini`, другие native
homes или global configuration. Codex использует Responses wire;
Chat Completions fallback запрещён.

## Миграция и откат

Profiles/routes additive и versioned. Legacy choice становится reviewed route
только после capability/artifact validation; отсутствие match видно как
blocked. Rollback запрещает admission, освобождает существующий lease и удаляет
только overlay, не меняя homes, unrelated external gateways или fallback.

## Последствия

Каждый launch объясним immutable route/artifact evidence; lifecycle owners,
secrets и native config остаются в своих границах.
