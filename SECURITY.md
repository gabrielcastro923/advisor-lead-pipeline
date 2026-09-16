# Data and security policy

This repository is safe to share only while operational data remains outside source control.

- Never commit property-owner exports, phone numbers, emails, suppression lists, CRM snapshots,
  provider responses, API keys, cookies, or local SQLite files.
- Keep credentials in the environment or an approved secret manager. Logs must use stable IDs,
  counts, and provider request IDs—not personal contact values.
- Treat a provider match as a candidate association. It is not proof of identity, ownership,
  signing authority, consent, or channel eligibility.
- A DNC-clear result is not consent. Source-use restrictions and team policy still apply.
- RentCast-derived observations default to `unsolicited_email_allowed=false`.
- FOREWARN is outside this pipeline and must remain a separate, permitted manual safety/fraud
  workflow after a qualifying prospect interaction.
- The pipeline does not send messages or write to production CRM systems.

Before sharing a branch, run:

```bash
git status --short
git ls-files 'var/**' 'data/**' 'exports/**' '*.db' '*.sqlite' '*.sqlite3'
git grep -n -E '(API_KEY|Bearer |@gmail\.com|@yahoo\.com)' || true
```
