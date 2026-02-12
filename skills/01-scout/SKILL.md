---
name: Yutori-scout
description: Set up continuous web monitoring with Yutori Scouts. Use when the user wants to track news, competitors, product updates, funding rounds, price changes, or any recurring web information.
argument-hint: "[topic or monitoring goal]"
---

# Scout Setup

Help the user set up a Yutori Scout for continuous web monitoring.

## Process

1. **Understand the monitoring context**
   Ask about:
   - Who is monitoring and why? (e.g., "We're a fintech looking for recently funded startups")
   - What specific information matters? (funding events, product launches, pricing changes)
   - What geography or market segments?
   - How often should it run? (daily, twice daily)
   - Notification preference: email, webhook, or both?

2. **Craft a comprehensive query**

   A well-structured scout query includes:

   **Context on the monitoring goal:**
   - Who is doing the monitoring and what's the use case
   - What decisions this information supports

   **What to Monitor:**
   - Specific events/triggers to track
   - Data sources to check (news sites, SEC filings, social media, etc.)
   - Geographic or segment focus
   - Exclusion criteria (what NOT to report)

   **Deliverables:**
   - Frequency of reports
   - Output format (tables, narrative, both)
   - Required fields and citations

   **Example structure:**
   ```
   **Context:** [Who is monitoring and why]

   ## What to Monitor
   - [Specific events to track]
   - [Sources to check]
   - [Exclusions]

   ## Deliverables
   - [Output format]
   - [Required fields]
   ```

3. **Create the scout**
   Use the `create_scout` tool with:
   - `query`: The comprehensive monitoring query
   - `output_interval`: 86400 (daily), 43200 (twice daily), or 1800 (minimum, every 30 min)
   - `webhook_url` and `webhook_format` if they want webhook notifications
   - `skip_email: true` if they only want webhooks
   - `output_fields`: For structured data extraction (e.g., ["company", "amount", "round_type", "source_url"])

4. **Provide next steps**
   - Share the scout ID for future management
   - Explain how to pause/resume with `edit_scout`
   - Mention they can get updates with `get_scout_updates`

## Query Quality Tips

**Good queries:**
- Provide context on who is monitoring and why
- Specify exact events/triggers (not just "news about X")
- List data sources to check
- Include exclusion criteria
- Define output format expectations
- Request citations and source links

**Avoid:**
- Vague queries like "monitor competitor X"
- Missing context about the monitoring goal
- No output format specification

$ARGUMENTS
