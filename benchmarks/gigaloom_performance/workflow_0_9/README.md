# GigaLoom 0.9 workflow performance gate

This capture compares the exact Checkpoint I2 revision
`7143d9bf421765f0207beda7c8abe9a33c4d478b` with the Wave C security revision
`6a96d3119021eaa28d56c2bf030eb28f2f35d566`. Both runs used the same 20 samples,
three warmups, lock SHA, CPython 3.13.8 interpreter, machine, content-free
fixtures and collector. Positive change means faster; tiny sub-millisecond
gateway deltas are reported but are not treated as optimization claims.

| Workload | I2 p50 ms | I2 p95 ms | Wave C p50 ms | Wave C p95 ms | p50 change | p95 change |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Work initial request graph | 4.576 | 4.929 | 4.375 | 4.679 | +4.39% | +5.08% |
| Run narrative update | 1.028 | 1.063 | 0.994 | 1.088 | +3.27% | -2.38% |
| Thread list page | 253.823 | 262.645 | 252.936 | 261.833 | +0.35% | +0.31% |
| Thread read page | 11.060 | 12.191 | 11.154 | 11.655 | -0.86% | +4.40% |
| Valid UTF-8 decode | 0.174 | 0.195 | 0.171 | 0.194 | +1.84% | +0.22% |
| Instruction discovery, small | 34.966 | 35.692 | 35.065 | 36.974 | -0.28% | -3.59% |
| Instruction discovery, large | 57.941 | 64.036 | 57.920 | 60.932 | +0.04% | +4.85% |
| Gateway preflight | 0.010 | 0.012 | 0.011 | 0.018 | -7.85% | -44.64% |
| Route/model discovery | 0.021 | 0.024 | 0.022 | 0.025 | -5.67% | -3.83% |
| Managed sidecar cold start | 0.223 | 0.315 | 0.224 | 0.360 | -0.19% | -14.04% |
| Managed sidecar warm attach | 0.032 | 0.054 | 0.029 | 0.044 | +7.68% | +18.75% |
| Local evidence export | 0.071 | 0.078 | 0.067 | 0.088 | +5.25% | -12.39% |

The release gate uses stable counters for the mechanisms: six bounded Work
requests including the route summary; one 200-node narrative request; 50-item
thread pages; one gateway discovery; three machine-contract reads; one cold
spawn; zero warm spawns with lease reuse; bounded instruction paths/sources;
and zero evidence-export network calls. The required UTF-8 p95 regression is
at most 10%; the measured change is a 0.22% improvement. Work initial p95 is
5.08% faster than I2.

The Wave C capture reports a dirty source tree because the collector and its
JSON evidence were the uncommitted files under measurement; the product source
revision is recorded separately and the I2 baseline was clean. No provider,
external network, native home or persistent product state is touched.

Reproduce from a clean repository root with a prepared locked environment:

```bash
./.venv/bin/python benchmarks/gigaloom_performance/workflow_0_9/capture.py \
  --label local-check \
  --samples 20 \
  --baseline benchmarks/gigaloom_performance/workflow_0_9/baseline.json \
  --output /tmp/gigaloom-workflow-0.9-local.json
```
