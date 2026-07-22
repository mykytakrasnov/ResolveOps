# Synthetic dataset

Run `make generate-data` to create deterministic AtlasFlow fixtures under
`data/generated/synthetic/v1`. Generated output is intentionally ignored by Git because it can
be reproduced byte-for-byte from the source generator and its fixed default seed.

The v1 layout keeps application-visible fixtures and evaluation truth separate:

```text
synthetic/v1/
  manifest.json
  crm/accounts/                 # public synthetic source records
  crm/users/
  billing/accounts/
  billing/invoices/
  billing/payment-attempts/
  telemetry/accounts/
  incidents/
  kb/
  policies/
  cases/public/                 # safe case inputs for ordinary application routes
  cases/ground-truth/           # evaluation-only expected outcomes
  replays/                      # static public event timelines for curated cases
```

Nothing under `cases/ground-truth` is an application fixture. Future route and storage adapters
must allowlist public/source prefixes and must not expose that evaluation-only prefix.
