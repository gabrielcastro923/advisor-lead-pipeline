# Contributing

Use small pull requests with a short description of the data contract changed, the migration
impact, and the checks run. Add a regression test whenever identity, deduplication, source-use,
suppression, spend, or export behavior changes.

The supported local check is:

```bash
python -m unittest discover -s tests -v
```

Never use real people or contact data in fixtures. New provider adapters must preserve the
original request ID and must not correlate results by list position.
