---
name: yutori-competitor-watch
description: Quickly set up monitoring for a competitor company. Tracks news, product updates, funding, and public announcements.
argument-hint: "[competitor name]"
disable-model-invocation: true
---

# Competitor Monitoring Setup

Set up comprehensive monitoring for a competitor.

Create a scout with this query structure:

```
**Context:** Monitoring competitive intelligence for $ARGUMENTS

## What to Monitor
- **News & Announcements:** Press releases, media coverage, blog posts
- **Product Updates:** New features, pricing changes, product launches
- **Funding & Business:** Funding rounds, acquisitions, partnerships, executive changes
- **Public Communications:** Social media announcements, conference talks, webinars

**Sources to check:** TechCrunch, company blog, LinkedIn, Twitter/X, Crunchbase, press releases

**Exclusions:** Job postings (unless executive level), routine social media, unverified rumors

## Deliverables
**Frequency:** Daily

**Output format:**
1. Summary of significant developments (if any)
2. Table of news items with: Date, Headline, Category, Source Link
3. Analysis of competitive implications
```

Use `create_scout` with:
- `query`: The above structured query with $ARGUMENTS replaced
- `output_interval`: 86400 (daily)

After creation, inform the user:
- Scout will run daily and email results
- They can check updates anytime with `get_scout_updates`
- They can pause/resume with `edit_scout`

**Fetching documentation or source URLs:**
If you use a web fetch tool to look up the competitor's blog, docs, or other pages while preparing the query, include the `Accept: text/markdown` header. Many documentation sites (Cloudflare-hosted) will return clean Markdown instead of HTML — fewer tokens, easier to parse.
