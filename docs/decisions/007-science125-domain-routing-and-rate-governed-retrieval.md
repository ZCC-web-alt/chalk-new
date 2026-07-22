# ADR-007: Route Science 125 through domain prompts and rate-governed official APIs

## Status

Accepted

## Date

2026-07-20

## Context

ADR-006 restricts the cross-disciplinary workbench to the authoritative Science 125
questions and prevents them from entering Chalk's legacy chemistry prompt. The
benchmark spans 12 official domains, but many questions require a more precise
subdomain, a cross-domain review, and a method-specific research plan. Sending every
question through one generic prompt or the same literature providers would weaken
retrieval relevance and make scientific quality difficult to audit.

Literature services also publish different request policies. arXiv requires a delay
between calls, NCBI has separate anonymous and API-key ceilings, and Semantic Scholar
applies an API-key rate. Other providers expose dynamic quota headers or do not publish
a stable numerical quota. Process-local throttling is insufficient because retries,
concurrent jobs, and service restarts could otherwise exceed a provider's policy.

## Decision

- Keep `science125-v1` immutable as the source-question authority. Store runtime
  classification in the separately hashed `science125-routing-v1` overlay, bound to
  the authoritative manifest SHA-256. Every `S125-001` through `S125-125` retains its
  official `benchmarkDomain` and adds a reviewed primary subdomain, cross-domain tags,
  primary/secondary method profile, prompt profile, and retrieval profile.
- Compose Science 125 prompts from the `research-v1` contract, a dedicated Science 125
  base prompt, one of 12 domain modules, and a method module. Do not import or fall back
  to the legacy chemistry hypothesis prompt. The server resolves prompt and retrieval
  profiles from the question ID; clients cannot select arbitrary profiles or providers.
- Use official APIs and openly documented indexes first. The approved registry covers
  arXiv, NCBI, Semantic Scholar, Crossref, OpenAlex, Europe PMC, INSPIRE HEP, NASA ADS,
  DBLP, GBIF Literature, OSTI, DOAJ, and ClinicalTrials.gov. Arbitrary publisher-page
  scraping, Google Scholar scraping, and unregistered URLs are not permitted. A source
  without a documented policy or access boundary cannot be required by a profile.
- Treat each provider's documented policy as a hard lower bound. arXiv uses a
  three-second interval; NCBI uses at most 3 requests per second without a key and 10
  with a key; keyed Semantic Scholar starts at 1 request per second. Providers without
  a stable public numeric quota use response-header-driven limits, one concurrent
  request, and a conservative local interval. All pagination, detail, retry, and search
  calls for a provider share the same limiter and credential scope.
- Honor `Retry-After` and dynamic quota/reset headers. Only 429, 500, 502, 503, 504,
  connection timeouts, and read timeouts are network-retryable. Retried calls must pass
  through the same limiter; retries cannot create a parallel burst.
- Record the policy source and check date in every provider definition. The initial
  registry was checked on 2026-07-20 against:

  | Provider | Policy or API source | Applied rule |
  | --- | --- | --- |
  | arXiv | [API manual](https://info.arxiv.org/help/api/user-manual.html) | Official three-second interval |
  | NCBI | [E-utilities usage guidelines](https://www.ncbi.nlm.nih.gov/books/NBK25497/) | Official anonymous/keyed ceilings |
  | Semantic Scholar | [API documentation](https://www.semanticscholar.org/product/api) | Keyed one-request/second floor |
  | Crossref | [REST API tips](https://www.crossref.org/documentation/retrieve-metadata/rest-api/tips-for-using-the-crossref-rest-api/) | Header-driven; local single-concurrency floor |
  | OpenAlex | [Rate limits and authentication](https://docs.openalex.org/how-to-use-the-api/rate-limits-and-authentication) | Header-driven; local single-concurrency floor |
  | Europe PMC | [REST service](https://europepmc.org/RestfulWebService) | Header-driven; local single-concurrency floor |
  | INSPIRE HEP | [REST API](https://github.com/inspirehep/rest-api) | Header-driven; local single-concurrency floor |
  | NASA ADS | [API help](https://ui.adsabs.harvard.edu/help/api/) | Header-driven; local single-concurrency floor |
  | DBLP | [Search API guidance](https://dblp.org/faq/How+to+use+the+dblp+search+API.html) | Header-driven; local single-concurrency floor |
  | GBIF | [Literature API](https://techdocs.gbif.org/en/openapi/v1/literature) | Header-driven; local single-concurrency floor |
  | OSTI | [API documentation](https://www.osti.gov/api/v1/docs) | Header-driven; local single-concurrency floor |
  | DOAJ | [API documentation](https://doaj.org/docs/api) | Header-driven; local single-concurrency floor |
  | ClinicalTrials.gov | [Data API](https://clinicaltrials.gov/data-api/about-api) | Header-driven; local single-concurrency floor |

  The header-driven one-second interval is a conservative Chalk safety floor, not a
  claim that an undocumented provider quota equals one request per second.
- Persist request reservations, cooldowns, remaining quota, and policy hashes in the
  Web SQLite table `science125_provider_rate_state`. State survives a service restart,
  and each reservation records the policy hash used for that decision. A registry
  policy change requires an explicit review and deployment update rather than silently
  inheriting an undocumented timing assumption. A query/profile cache prevents repeat
  provider calls within its configured window.
- Read DashScope and literature credentials only from the server process or protected
  deployment secrets. Science 125 must not read per-user `api_keys.json`. Public profile
  APIs expose provider URLs, policy sources, and redacted readiness codes, but never
  credentials, authorization headers, environment-variable names, private paths, or
  credential-scope material.
- Keep Qwen as the only foundation model through DashScope. The initial generation gate
  is limited to `S125-006`, `S125-043`, and `S125-054`; every pilot run must retain the
  routing, prompt, evidence-snapshot, policy, model request, token, latency, retry, and
  cost provenance before the other 122 questions are enabled.
- Enable those three pilot IDs only after the server reloads a completed owned search,
  validates at least three reviewed full-text evidence records from two provider
  families, and revalidates every selected PDF page hash. The request carries stable
  evidence/page IDs rather than browser-assembled source text. All other IDs return
  `SCIENCE125_PILOT_NOT_ENABLED` before a Qwen call.
- Keep the complete source-context index and human-reviewed Canary evidence outside
  the repository. A manual GitHub Canary receives the authorized context index as
  two gzip-base64 protected-environment secrets and the controlled evidence JSON as
  a separate protected-environment secret. The runner reconstructs and validates
  them in ignored runtime storage before it receives the DashScope credential; it
  never uploads the input secrets as an artifact.

## Alternatives Considered

### Put routing fields into `science125-v1`

Rejected because runtime prompt and retrieval choices will evolve independently from
the source booklet. Rewriting the authoritative manifest would blur source
classification, invalidate its stable hash, and make submissions harder to reproduce.

### Use one generic prompt and provider list for all domains

Rejected because mathematical proof, clinical evidence, astronomical observation,
materials engineering, and policy studies require different falsification criteria,
methods, and evidence sources.

### Reuse the legacy chemistry hypothesis pipeline

Rejected because its terminology, evidence assumptions, and output guidance are not
valid across the 12 Science 125 domains. A silent fallback would make a successful API
response scientifically misleading.

### Keep rate state only in memory

Rejected because a restart would erase cooldowns and concurrent jobs could bypass
provider-wide limits. Persisted reservations are required for reproducible, policy-
compliant retries on the single ECS instance.

### Scrape search-result and publisher pages when APIs are incomplete

Rejected because access rules, page structure, robots policy, and rate limits are less
stable than official APIs. User-owned, hash-verified PDF pages remain the explicit
human-reviewed path for supplemental full text.

### Commit the complete booklet context or review evidence for CI convenience

Rejected because the booklet context is not repository source and reviewed evidence
may include controlled research material. A protected-environment input boundary
keeps the regular CI checkout reproducible without copying either into Git.

## Consequences

- The workbench and batch runner can audit why each question used a domain prompt,
  method policy, and provider set without changing the official benchmark identity.
- Missing required credentials or provider readiness blocks generation rather than
  silently reducing evidence quality. Non-required provider outages may degrade a run
  only when the evidence and provider-family gates still pass.
- Provider-policy changes require a registry update, a new policy hash, and regression
  tests before deployment. Operators must preserve `web.db` with the rest of the
  persistent data volume so cooldown state survives restarts.
- Public APIs remain useful for configuration diagnostics without disclosing secrets.
  Operators must inject credentials into the backend process or protected environment;
  putting real values in committed `.env.example` files is forbidden.
- The dedicated Qwen path is available only for the three pilots. The remaining 122
  questions remain gated until the pilots pass schema, citation, evidence-sufficiency,
  provenance, and human-support review.
- Canary operators need to rotate the protected inputs whenever the authoritative
  context extraction or reviewed evidence changes. A malformed, incomplete, or stale
  index stops before any DashScope request and therefore cannot generate a false
  success or consume the Qwen budget.
