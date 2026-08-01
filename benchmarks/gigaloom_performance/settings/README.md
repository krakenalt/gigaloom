# Settings initial-load benchmark

This directory freezes the pre-fission Settings cost at the baseline source commit.
Both collectors use disposable state and content-free observations. They never
contact a provider, external network, or native agent home.

Capture the backend cold/warm projection and its observable operation counts:

```bash
uv run python benchmarks/gigaloom_performance/settings/capture_backend.py
```

Capture the post-fission summary against the same disposable-state model:

```bash
uv run python benchmarks/gigaloom_performance/settings/capture_summary.py
```

For browser evidence, first build the ignored Web assets and start a local UI
with a disposable `GIGALOOM_DATA_DIR`, then run:

```bash
node benchmarks/gigaloom_performance/settings/capture_browser.mjs
```

The current first-content collector distinguishes the one Settings request
required by the surface from unrelated shell requests and asserts that no
section request or section chunk precedes Appearance:

```bash
node benchmarks/gigaloom_performance/settings/capture_current_browser.mjs
```

`baseline.json` remains the pre-fission evidence, `after.json` is the current
post-fission evidence, and `budgets.json` contains the ratchets. Wall time is
host-specific evidence. The
gate uses relative improvement plus deterministic request, probe, list,
history, cache, and chunk-loading counts; it deliberately has no absolute
millisecond ceiling.
