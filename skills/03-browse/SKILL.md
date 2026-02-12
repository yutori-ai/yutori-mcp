---
name: yutori-browse
description: Automate browser tasks like form filling, data extraction, or multi-step web workflows. Use when the user needs to interact with websites that require clicking, typing, or navigation.
argument-hint: "[task description] [starting URL]"
---

# Browser Automation

Help the user automate browser-based tasks using Yutori's Navigator agent.

## Process

1. **Understand the task**
   - What website needs to be automated?
   - What actions are required? (clicking, typing, extracting data)
   - Does it require login or authentication?

2. **Define the task clearly**
   - Break complex workflows into clear steps
   - Specify what data to extract if applicable
   - Note any buttons or elements to interact with

3. **Start the browsing task**
   Use `run_browsing_task` with:
   - `task`: Clear natural language instructions
   - `start_url`: The URL to begin browsing
   - `max_steps`: 25 (default) to 100 for complex flows
   - `output_fields`: For structured data extraction (e.g., ["name", "price", "url"])

4. **Poll for results**
   - Browsing typically takes 30-120 seconds depending on complexity
   - Use `get_browsing_task_result` to check status
   - Poll every 10-15 seconds until complete

5. **Review and validate**
   - Check the extracted data or confirmation
   - Verify the task completed as expected

## Task Writing Tips

- Be specific about UI elements: "Click the blue 'Submit' button"
- Reference visible text when possible
- For forms, specify which fields get which values

$ARGUMENTS
