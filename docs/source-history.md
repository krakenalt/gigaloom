# Source history and migration

GigaLoom was extracted from the combined
[`ai-forever/gpt2giga`](https://github.com/ai-forever/gpt2giga) repository with
filtered history. That link is a **historical source reference**. Current
development, documentation edits, issues, releases, and project links point to
[`krakenalt/gigaloom`](https://github.com/krakenalt/gigaloom).

The first target-owned distribution is `gigaloom`. Historical
`gpt2giga-harness` releases remain available but receive no new target
release. The standalone distribution uses only the `gigaloom` Python namespace
and the `giga` command; it does not ship a compatibility namespace or command
shim. Existing local state paths are handled by a separate migration gate.

Older changelog comparison links intentionally point to the historical source
repository so pre-split tags remain resolvable. They do not imply current
ownership or a source checkout dependency.

For migration from the old combined prerelease package, uninstall
`gpt2giga-harness`, install the standalone `gigaloom` distribution, and retain
the legacy `~/.gpt2giga/harness` state until `giga state migrate` has verified
its separate backup and canonical `~/.gigaloom` copy. See the detailed
[Harness migration section](harness.md#migration-from-the-combined-prerelease).
