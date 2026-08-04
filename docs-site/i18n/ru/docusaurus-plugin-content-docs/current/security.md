# Безопасность

GigaLoom разделяет локальное выполнение, evidence, credentials, сетевой доступ
и внешние мутации на отдельные trust boundaries.

## Модель безопасности

- Provider credentials остаются в provider-owned homes или явных secret
  resolution boundaries.
- Секреты редактируются до persistence, logs, diagnostics, previews и UI.
- Content capture включается явно.
- Мутации требуют scoped authority, когда это задано policy.
- Approval связывает точные scope и preview; dispatch проверяет их снова.
- Внешние команды используют явные arguments, controlled cwd, bounded output и
  redacted records.
- Network и GitHub capabilities fail closed без точного grant.

Не коммитьте credentials, tokens, `.env`, certificates, raw traffic или
fixtures с секретами.

## Проверки границ 0.9

- Thread Relay связывает actor, project, target revision, TTL, idempotency key и
  optional active turn до delivery. Допускается только user-role message;
  agent-proposed content требует явного user approval.
- Relay reads ограничены и маскируются. Receipts хранят content digests, а не
  текст сообщения; failed delivery не переписывает target history.
- Декодирование attachments не использует replacement characters, ограничено
  по размеру и распознаёт binary. Charset evidence содержит только факты и
  digest, но не source content.
- Effective Instructions — project-root-confined read-only projection. Она не
  сканирует private provider homes и не объединяет или инъецирует rules.
- Gateway launch принимает только reviewed route/profile identities, bounded
  API-key headers, pinned compatibility evidence и managed overlays GigaLoom.
  Arbitrary URL/header injection, запись native-home и hidden fallback
  запрещаются до provider traffic.
- Product beta evidence включается явно, остаётся local, project-scoped,
  content-free и bounded. Экспорт не выдаёт upload или outreach authority.

Unknown, stale, malformed или mismatched evidence остаётся blocker. Не обходите
его выбором другого route или копированием credentials в arguments.

## Сообщение об уязвимостях

Не раскрывайте предполагаемые уязвимости в публичном issue, discussion или pull
request. Следуйте repository
[security policy](https://github.com/krakenalt/gigaloom/blob/main/SECURITY.md)
и используйте
[GitHub private vulnerability reporting](https://github.com/krakenalt/gigaloom/security/advisories/new).
Передавайте минимальный redacted reproduction и никогда не отправляйте
credentials, user content, native-home data или raw provider traffic.

Primary security owner —
[`@krakenalt`](https://github.com/krakenalt). Роль backup maintainer, response
targets, 2FA gate и восстановление compromised publisher определены в security
и
[governance](https://github.com/krakenalt/gigaloom/blob/main/GOVERNANCE.md)
policies. Public cutover заблокирован, пока отдельный backup owner не принял
доступ.
