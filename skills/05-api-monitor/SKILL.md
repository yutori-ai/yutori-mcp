---
name: yutori-api-monitor
description: Set up monitoring for API changes, changelogs, or documentation updates. Useful for tracking breaking changes in services you depend on.
argument-hint: "[service or API name]"
disable-model-invocation: true
---

# API & Changelog Monitoring

Set up monitoring for API or service changes.

Create a scout with this query structure:

```
**Context:** Monitoring API stability and changes for $ARGUMENTS to ensure our integration stays up-to-date.

## What to Monitor
- **Breaking Changes:** API version changes, deprecated endpoints, removed features
- **New Features:** New endpoints, parameters, capabilities
- **Rate Limits & Quotas:** Changes to limits, pricing tier updates
- **Deprecation Notices:** Sunset timelines, migration guides
- **Security Updates:** Authentication changes, security advisories

**Sources to check:** Official changelog, documentation, developer blog, GitHub releases, status page, developer Twitter/X account

**Exclusions:** Minor bug fixes, internal refactoring, documentation typos

## Deliverables
**Frequency:** Every 12 hours

**Output format:**
1. **URGENT** section for breaking changes (if any)
2. Table of changes: Date, Type (breaking/feature/deprecation), Description, Migration Required (yes/no), Source Link
3. Recommended actions for each change
```

Use `create_scout` with:
- `query`: The above structured query with $ARGUMENTS replaced
- `output_interval`: 43200 (every 12 hours)

After creation, explain:
- Scout will check twice daily for changes
- Critical for staying ahead of breaking changes
- Can set up webhook for immediate notifications

**Fetching API documentation or changelogs:**
If you use a web fetch tool to look up the service's changelog, API reference, or developer docs while preparing the query, include the `Accept: text/markdown` header. Many developer documentation sites (Cloudflare-hosted) will return clean Markdown instead of HTML — fewer tokens, easier to parse.
