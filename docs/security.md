# Security

GigaLoom treats local execution, stored evidence, credentials, network access,
and remote-service mutations as separate trust boundaries.

## Safety model

- Provider credentials stay in provider-owned homes or explicit secret
  resolution boundaries.
- Secrets are redacted before persistence, logs, diagnostics, previews, and UI
  responses.
- Content capture is opt-in.
- Mutating actions require scoped authority where policy demands it.
- Approval binds the exact scope and preview; dispatch revalidates both.
- External commands use explicit arguments, controlled working directories,
  bounded output, and redacted records.
- Network and GitHub capabilities fail closed unless an exact grant exists.

Do not commit credentials, tokens, `.env` values, certificates, raw traffic, or
secret-bearing fixtures.

## 0.9 boundary checks

- Thread Relay binds actor, project, target revision, TTL, idempotency key, and
  optional active turn before delivery. It admits only a user-role message;
  agent-proposed content needs an explicit user approval.
- Relay reads are bounded and redacted. Receipts retain content digests, not
  message text; failed delivery does not rewrite target history.
- Attachment decoding is replacement-free, size-bounded, and binary-aware.
  Charset evidence contains facts and a digest, never source content.
- Effective Instructions is a project-root-confined, read-only projection. It
  neither scans private provider homes nor merges/injects rules.
- Gateway launch accepts only reviewed route/profile identities, bounded API-key
  headers, pinned compatibility evidence, and GigaLoom-managed overlays.
  Arbitrary URL/header injection, native-home writes, and hidden fallback are
  denied before provider traffic.
- Product beta evidence is opt-in, local, project-scoped, content-free, and
  bounded. Export grants no upload or outreach authority.

Unknown, stale, malformed, or mismatched evidence remains a blocker. Do not
bypass it by selecting another route or by copying credentials into arguments.

## Reporting vulnerabilities

Do not disclose suspected vulnerabilities in a public issue, discussion, or
pull request. Follow the repository
[security policy](https://github.com/krakenalt/gigaloom/blob/main/SECURITY.md)
and use
[GitHub private vulnerability reporting](https://github.com/krakenalt/gigaloom/security/advisories/new).
Provide a minimal redacted reproduction and never send credentials, user
content, native-home data, or raw provider traffic.

The primary security owner is
[`@krakenalt`](https://github.com/krakenalt). The backup maintainer role,
response targets, 2FA gate, and compromised-publisher recovery are defined in
the security and
[governance](https://github.com/krakenalt/gigaloom/blob/main/GOVERNANCE.md)
policies. Public cutover remains blocked until the distinct backup owner has
accepted access.
