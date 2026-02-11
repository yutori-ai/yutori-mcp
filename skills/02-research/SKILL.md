---
name: Yutori research
description: Execute deep web research on any topic using Yutori's research agents. Use for competitive analysis, market research, finding documentation, or answering complex questions that require synthesizing information from multiple sources.
argument-hint: "[research question]"
---

# Deep Web Research

Help the user conduct thorough web research using Yutori's Research API.

## Process

1. **Understand the research goal**
   - What question needs answering?
   - What type of sources matter? (news, academic, documentation, social, financial filings)
   - Any time constraints? (recent only, historical)
   - What format should the output be in?

2. **Craft the research query**
   Similar to scout queries, comprehensive research queries include:
   - Context on why this research matters
   - Specific questions to answer
   - Sources to prioritize
   - Output format expectations

3. **Start the research task**
   Use `run_research_task` with:
   - `query`: The research question with context
   - `user_timezone`: For time-relevant searches
   - `output_fields`: If structured output is needed (e.g., ["title", "summary", "source_url", "date"])

4. **Poll for results**
   - **Important:** Research typically takes 5-10 minutes (300-600 seconds)
   - Use `get_research_task_result` to check status
   - Poll every 30 seconds until `succeeded` or `failed`
   - The task runs asynchronously - you can inform the user to wait

5. **Synthesize and present findings**
   - Organize results by relevance
   - Highlight key insights
   - Note sources for verification

$ARGUMENTS
