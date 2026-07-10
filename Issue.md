# Creating GitHub Issues

Use `gh` from the repository root.

## Labels

Use one type label:

- `bug`: something is broken or fragile.
- `enhancement`: a new capability or improvement.
- `documentation`: docs-only work.

Use one priority label:

- `priority:1-low`
- `priority:2-med-low`
- `priority:3-medium`
- `priority:4-med-high`
- `priority:5-high`

## Command

```sh
gh issue create \
  --title "Short imperative title" \
  --label bug \
  --label priority:3-medium \
  --body-file issue-body.md
```

For small issues, `--body "..."` is fine. Include:

- problem
- expected behaviour
- evidence or where it was observed
- acceptance criteria

