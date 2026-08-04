# Контролируемый сетевой доступ

Статус: принято 27 июля 2026 года.

## Контекст

Схема authority уже отделяет network endpoint от filesystem, process, GitHub,
browser, MCP, integration и child-agent authority. Approval Center умеет
показывать network target, но прежний interactive profile считал
`network.connect` неявно разрешённым, а у Harness-owned transport не было
контракта, защищающего соединение после approval.

Актуальные рекомендации Codex по умолчанию отключают сеть для команд, разделяют
sandbox boundary и approval policy и применяют allowlist-first правила
назначений при включённой network isolation. Они также разделяют классификацию
непубличных адресов и transport-level pinning как две меры против DNS rebinding.
GigaLoom принимает эти границы без blanket-переключателя интернета.

## Решение

`gigaloom.runtime.network_access` владеет schema version 1 для
Harness-enforced исходящего HTTPS. Для authorization одновременно нужны:

1. явно включённая sandbox network boundary;
2. неотозванный, непросроченный, Harness-enforced `AuthorityGrant`.

Grant привязан к точным host, port, protocol, safe-or-write method class,
redirect policy, content-free purpose и preview digest. Содержимое request body
не сохраняется: непустое body представлено только размером и SHA-256. Запрошенный
response ceiling также входит в preview.

Interactive permission profile теперь разрешает `network.connect` только через
`ask`. Intent, interactive session или выбор provider сами по себе не включают
сеть.

## Граница SSRF и DNS rebinding

До открытия socket transport передаёт все resolved addresses в
`authorize_scoped_network_access`. Пустой resolution, некорректный адрес и любой
loopback, private, link-local, unspecified, multicast, reserved или иной
непубличный результат fail closed. IP-literal target должен разрешаться ровно в
этот literal.

Authorization возвращает короткоживущий `NetworkAccessTicket` с pinned набором
публичных адресов. До отправки request transport обязан проверить connected peer
по этому набору. Другой peer блокируется. DNS classification и transport
pinning остаются явными отдельными мерами, без ложного обещания, что один
pre-connect lookup полностью устраняет rebinding.

Redirect по умолчанию запрещён. Grant может разрешить `same_origin`, но каждый
hop заново проходит scope, preview, expiry, allowlist, DNS и peer validation.
Cross-origin redirect, изменение method или purpose и автоматический retry
требуют новой authority.

## Reviewed domain proxy policy

Опциональный `ReviewedDomainProxyPolicy` добавляет вторую аудируемую allowlist:

- listener — точный loopback IP;
- каждое exact-, `*.example.com`- или `**.example.com`-правило имеет явные
  purposes, reviewer identity и expiry;
- global `*` запрещён;
- отсутствие совпавшего правила означает deny;
- request и response body ceilings ограничены жёсткими пределами schema;
- нормализованная policy имеет стабильный SHA-256 в authorization receipt.

Этот класс политики применяется транспортом проверяемого прокси. Сам класс не
открывает порт и не отправляет трафик. Принятие решения не включает сетевой
доступ для провайдеров, MCP, каталога и других интеграций.

## Аудит и конфиденциальность

Квитанция содержит выданное разрешение, область действия, предварительный
просмотр, политику, назначение, срок действия, количество адресов и контрольные
суммы. В ней нет пути и параметров URL, тела запроса, заголовков, учётных
данных и разрешённых IP-адресов. Сведения о подключённой стороне содержат
только контрольную сумму адреса.

## Последствия

Контракт задаёт единую точку проверки для будущих сетевых клиентов Harness.
Изолированные среды провайдеров остаются отдельной границей безопасности.
Одного запроса подтверждения недостаточно: клиент обязан получить разрешение,
проверить его при подключении и соблюдать ограничение на объём ответа.

Само принятие решения не выполняет DNS-запросы, не открывает соединения, не
запускает прокси, не настраивает учётные данные, не обращается к GitHub и не
меняет развёртывание.

## Ссылки

- [Codex agent approvals and network security](https://learn.chatgpt.com/docs/agent-approvals-security)
- [Полномочия и подтверждение операций](authority-and-approvals.md).
