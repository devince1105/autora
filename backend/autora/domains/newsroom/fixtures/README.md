# Newsroom fixtures

Offline data for `TOOLS_PROFILE=fixture` (tests and simulation, T-500 / T-518). Everything here is
**fictional** (Lumen City, 流明市) and lives on `*.fixtures.autora.test` hosts, which cannot
resolve on the internet, so a fixture can never be mistaken for, or leak into, real reporting.

- `search.json`: the corpus `web_search` searches in fixture mode (`FixtureSearchProvider`).
- `routes.json`: which fixture file each URL serves in fixture mode (`FixtureFetcher`); any other
  URL answers 404. Feeds for the source poller (T-501) are in `feeds/`.
- `pages/`: the pages behind the search results and feed items, for `fetch_url` (T-502), with
  navigation, scripts and footers around the article so text extraction is exercised; plus a
  page that needs JavaScript and a PDF, which cannot become evidence.
