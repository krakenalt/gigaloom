# Settings initial-load benchmark

This directory freezes the pre-fission Settings cost at the I1 source commit.
Both collectors use disposable state and content-free observations. They never
contact a provider, external network, or native agent home.

Capture the backend cold/warm projection and its observable operation counts:

```bash
uv run python benchmarks/gigaloom_performance/settings/capture_backend.py
```

For browser evidence, first build the ignored Web assets and start a local UI
with a disposable `GIGALOOM_DATA_DIR`, then run:

```bash
node benchmarks/gigaloom_performance/settings/capture_browser.mjs
```

Wall time is host-specific evidence. The ratcheting gate uses relative
improvement plus deterministic request, probe, list, and chunk-loading counts.
