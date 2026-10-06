# Vendored: specgate

A copy of `plugins/specgate` (source, schema, pyproject; not its tests) from
<https://github.com/josephrobertlopez/harness-engineering-demo>, commit
`b84df18` on branch `fix/specgate-portable`
([PR #6](https://github.com/josephrobertlopez/harness-engineering-demo/pull/6)).

Kept inside this repo on purpose: the gates must run with no network fetch of
the gate itself, and a PR here cannot quietly move the gate to another version.

- `.specgate-skip` keeps specgate's own `# implements:` markers out of this
  repo's AC trace. Do not delete it.
- Do not edit these files in place. Fix upstream, then re-copy and update the
  commit above, so the two never silently diverge.
