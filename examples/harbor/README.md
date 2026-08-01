# GigaLoom headless agent for Harbor

The optional adapter targets Harbor `0.20.x`. Both the Harbor host environment
and the task image must contain the same installed GigaLoom build; the task
image must expose `giga` on `PATH`.

```bash
harbor run \
  --agent-import-path gigaloom.integrations.harbor.adapter:GigaLoomAgent \
  --agent-env GIGALOOM_AGENT=qwen-code \
  --agent-env GIGALOOM_TIMEOUT_SECONDS=600 \
  --agent-env GIGALOOM_PERMISSION_PROFILE=unattended \
  --agent-env GIGALOOM_NETWORK_PROFILE=none \
  --agent-env GIGALOOM_CAPSULE_MODE=export \
  ...
```

Only the keys shown in [`agent.env.example`](agent.env.example), plus optional
`GIGALOOM_ROUTE` and `GIGALOOM_MODEL`, are accepted. Credentials and arbitrary
agent environment variables are rejected before Harbor scopes them onto
container commands.

The adapter uploads Harbor's instruction as a dedicated prompt file and invokes
`giga run --headless` with stdin disconnected. JSONL events, diagnostics,
results, and exported capsules are written below `/logs/agent/gigaloom/`, which
is Harbor's per-trial agent log boundary. Adapter metadata records GigaLoom's
process exit and terminal event, but `task_success` remains unset: Harbor's
verifier is the only task-outcome authority.
