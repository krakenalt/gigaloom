# Gateway integration

GigaLoom is independently installable. Its base distribution neither imports
the gateway nor requires a gateway repository checkout.

Install the optional released integration only when using Direct Chat or the
legacy local-gateway preset:

```sh
uv tool install 'gigaloom[gpt2giga]==0.7.0'
```

This extra admits stable `gpt2giga>=0.2.6,<0.3.0`; the committed lock resolves
the exact reviewed public artifact. Release testing never creates an editable
sibling dependency.

## Canonical gateway contracts

The separate gateway project owns the following compatibility references:

- [Normalized messages](https://github.com/ai-forever/gpt2giga/blob/main/docs/architecture/normalized-messages.md)
- [API compatibility](https://github.com/ai-forever/gpt2giga/blob/main/docs/api-compatibility.md)
- [Client parameter compatibility](https://github.com/ai-forever/gpt2giga/blob/main/docs/client-parameter-compatibility.md)
- [Built-in tool mapping](https://github.com/ai-forever/gpt2giga/blob/main/docs/builtin-tools.md)

These are current canonical gateway links, not GigaLoom development links.
GigaLoom issues and changes belong in
[`krakenalt/gigaloom`](https://github.com/krakenalt/gigaloom).
