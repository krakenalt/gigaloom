# Attachment UTF-8 decode benchmark

Run from the repository root after the locked environment and ignored Web
assets are prepared:

```bash
./.venv/bin/python benchmarks/gigaloom_performance/attachments/capture_utf8_decode.py \
  --label local-check
```

`baseline.json` and `after.json` use the same 300,000-byte mixed
Russian/English/code workload, lock, machine, Python, warmups, batches and
sample count. The EN-03 change replaces the EN-02 full per-character Unicode
category scan on valid UTF-8 with strict decode, signature/NUL checks and an
8 KiB control-byte sample. Binary and legacy paths retain their bounded
heuristics.

Measured p50 improved from 13.317 ms to 0.186 ms (98.60%); p95 improved from
14.477 ms to 0.197 ms (98.64%). The live gate is deliberately looser than the
captured machine result to avoid treating scheduler noise as a product
regression.
