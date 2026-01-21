"""Format API responses as human-readable text for LLM consumption."""

from __future__ import annotations

from typing import Any

DEFAULT_LIMIT = 10


def dict_to_markdown(obj: Any, level: int = 0) -> str:
    """Convert a nested dict/list structure to markdown text."""
    return "\n".join(_to_markdown_lines(obj, level))


def _to_markdown_lines(obj: Any, level: int = 0) -> list[str]:
    """Recursively convert obj to markdown lines with indentation."""
    lines: list[str] = []
    indent = "  " * level

    if isinstance(obj, dict):
        for key, val in obj.items():
            if isinstance(val, (dict, list)) and val:
                lines.append(f"{indent}{key}:")
                lines.extend(_to_markdown_lines(val, level + 1))
            elif val is not None and val != "":
                lines.append(f"{indent}{key}: {val}")
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict) and item:
                # For dicts in a list, format as bullet with first key-value
                first_key = next(iter(item))
                first_val = item[first_key]
                lines.append(f"{indent}- {first_key}: {first_val}")
                # Add remaining fields indented
                for k, v in item.items():
                    if k != first_key and v is not None and v != "":
                        lines.append(f"{indent}  {k}: {v}")
            elif item is not None and item != "":
                lines.append(f"{indent}- {item}")
    else:
        if obj is not None and obj != "":
            lines.append(f"{indent}{obj}")

    return lines


def format_response(tool_name: str, response: dict[str, Any], **context: Any) -> str:
    """Route to appropriate formatter based on tool name."""
    formatters = {
        "list_scouts": format_list_scouts,
        "get_scout_detail": format_scout_detail,
        "get_scout_updates": format_scout_updates,
        "create_scout": format_scout_created,
        "edit_scout": format_scout_edited,
        "delete_scout": format_scout_deleted,
        "run_browsing_task": format_task_started,
        "get_browsing_task_result": format_task_result,
        "run_research_task": format_task_started,
        "get_research_task_result": format_task_result,
    }

    formatter = formatters.get(tool_name)
    if formatter:
        return formatter(response, **context)

    # Fallback: use generic dict_to_markdown
    return dict_to_markdown(response)


def _format_interval(seconds: int | None) -> str:
    """Convert interval in seconds to human-readable string."""
    if seconds is None:
        return "not set"
    if seconds < 3600:
        return f"every {seconds // 60} minutes"
    if seconds < 86400:
        hours = seconds // 3600
        return f"every {hours} hour{'s' if hours > 1 else ''}"
    days = seconds // 86400
    if days == 1:
        return "daily"
    return f"every {days} days"


def _format_date(iso_string: str | None) -> str:
    """Format ISO date string to readable format."""
    if not iso_string:
        return "not set"
    # Extract date portion (YYYY-MM-DD)
    return iso_string[:10] if len(iso_string) >= 10 else iso_string


def _format_datetime(iso_string: str | None) -> str:
    """Format ISO datetime string to readable format."""
    if not iso_string:
        return "not set"
    # Format as "YYYY-MM-DD HH:MM UTC"
    if len(iso_string) >= 16:
        return f"{iso_string[:10]} {iso_string[11:16]} UTC"
    return iso_string


def _truncate(text: str, max_len: int = 60) -> str:
    """Truncate text with ellipsis if too long."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


# -----------------------------------------------------------------------------
# Scout formatters
# -----------------------------------------------------------------------------


def format_list_scouts(response: dict[str, Any], **context: Any) -> str:
    """Format list_scouts response as readable text."""
    scouts = response.get("scouts", [])
    total = response.get("total", len(scouts))
    summary = response.get("summary", {})
    has_more = response.get("has_more", False)

    # Build summary line
    active = summary.get("active", 0)
    paused = summary.get("paused", 0)
    done = summary.get("done", 0)

    lines = [f"Found {total} scouts: {active} active, {paused} paused, {done} done."]

    if not scouts:
        lines.append("\nNo scouts to display.")
        return "\n".join(lines)

    # Show count context
    showing = len(scouts)
    if has_more:
        lines.append(f"\nShowing {showing} of {total}:")
    else:
        lines.append(f"\nShowing all {showing}:")

    # Format each scout
    for i, scout in enumerate(scouts, 1):
        name = scout.get("display_name") or scout.get("query", "Untitled")[:40]
        status = scout.get("status", "unknown")
        query = scout.get("query", "")
        scout_id = scout.get("id", "")
        interval = _format_interval(scout.get("output_interval"))
        next_run = _format_date(scout.get("next_output_timestamp"))

        lines.append(f"\n{i}. {name} ({status})")
        lines.append(f"   Query: \"{_truncate(query)}\"")
        lines.append(f"   ID: {scout_id}")
        lines.append(f"   Runs {interval} | Next: {next_run}")

    # Add hints
    lines.append("")
    if has_more:
        lines.append("Use list_scouts(limit=50) to see more.")
    lines.append("Use list_scouts(status=\"active\") to filter by status.")
    lines.append("Use get_scout_detail(scout_id) for full details.")

    return "\n".join(lines)


def format_scout_detail(response: dict[str, Any], **context: Any) -> str:
    """Format get_scout_detail response as readable text."""
    name = response.get("display_name") or "Untitled"
    scout_id = response.get("id", "")
    status = response.get("status", "unknown")
    query = response.get("query", "")
    created = _format_date(response.get("created_at"))

    lines = [
        f"Scout: {name}",
        f"ID: {scout_id}",
        f"Status: {status}",
        "",
        f"Query: \"{query}\"",
        "",
        "Schedule:",
        f"  Interval: {_format_interval(response.get('output_interval'))}",
        f"  Next run: {_format_datetime(response.get('next_output_timestamp'))}",
    ]

    if response.get("user_timezone"):
        lines.append(f"  Timezone: {response['user_timezone']}")

    lines.append("")
    lines.append("Configuration:")

    webhook = response.get("webhook_url")
    lines.append(f"  Webhook: {webhook if webhook else 'not configured'}")

    skip_email = response.get("skip_email", False)
    lines.append(f"  Email notifications: {'disabled' if skip_email else 'enabled'}")

    is_public = response.get("is_public", False)
    lines.append(f"  Public: {'yes' if is_public else 'no'}")

    if response.get("user_location"):
        lines.append(f"  Location: {response['user_location']}")

    lines.append("")
    lines.append(f"Created: {created}")

    return "\n".join(lines)


def format_scout_updates(response: dict[str, Any], **context: Any) -> str:
    """Format get_scout_updates response as readable text."""
    updates = response.get("updates", [])
    has_more = response.get("has_more", False)
    next_cursor = response.get("next_cursor")

    if not updates:
        return "No updates found for this scout."

    lines = [f"Found {len(updates)} update(s):"]

    for i, update in enumerate(updates, 1):
        lines.append("")
        lines.append(f"--- Update #{i} —")

        timestamp = _format_datetime(update.get("created_at") or update.get("timestamp"))
        lines.append(f"Date: {timestamp}")

        # Handle different update formats
        content = update.get("content") or update.get("formatted_output") or update.get("report")
        if content:
            if isinstance(content, str):
                # Indent content
                for line in content.split("\n")[:20]:  # Limit lines shown
                    lines.append(f"  {line}")
                if content.count("\n") > 20:
                    lines.append("  ... (truncated)")
            elif isinstance(content, dict):
                lines.append(dict_to_markdown(content, level=1))

        findings = update.get("findings", [])
        if findings:
            lines.append(f"\nFindings ({len(findings)}):")
            for finding in findings[:5]:  # Limit to 5
                if isinstance(finding, dict):
                    title = finding.get("title") or finding.get("summary", "")
                    lines.append(f"  • {_truncate(title, 80)}")
                else:
                    lines.append(f"  • {_truncate(str(finding), 80)}")
            if len(findings) > 5:
                lines.append(f"  ... and {len(findings) - 5} more")

    if has_more and next_cursor:
        lines.append("")
        lines.append(f"More updates available. Use get_scout_updates(scout_id, cursor=\"{next_cursor}\") to load more.")

    return "\n".join(lines)


def format_scout_created(response: dict[str, Any], **context: Any) -> str:
    """Format create_scout response as confirmation."""
    name = response.get("display_name") or response.get("query", "")[:40]
    scout_id = response.get("id", "")
    status = response.get("status", "active")
    query = response.get("query", "")
    interval = _format_interval(response.get("output_interval"))
    next_run = _format_datetime(response.get("next_output_timestamp"))

    lines = [
        "Scout created successfully.",
        "",
        f"Name: {name}",
        f"ID: {scout_id}",
        f"Status: {status}",
        "",
        f"Query: \"{_truncate(query, 80)}\"",
        f"Schedule: runs {interval}",
        f"First run: {next_run}",
    ]

    return "\n".join(lines)


def format_scout_edited(response: dict[str, Any], **context: Any) -> str:
    """Format edit_scout response showing changes."""
    old = response.get("old", {})
    new = response.get("new", response)  # Fallback to response if no old/new structure

    # If we don't have old state, just show current state
    if not old:
        name = new.get("display_name") or new.get("query", "")[:40]
        scout_id = new.get("id", "")
        status = new.get("status", "unknown")

        return "\n".join([
            "Scout updated successfully.",
            "",
            f"Name: {name}",
            f"ID: {scout_id}",
            f"Status: {status}",
            "",
            "Use get_scout_detail(scout_id) for full details.",
        ])

    # Show diff
    name = new.get("display_name") or new.get("query", "")[:40]
    scout_id = new.get("id", "")

    lines = [
        "Scout updated successfully.",
        "",
        f"Name: {name}",
        f"ID: {scout_id}",
        "",
        "Changes applied:",
    ]

    # Compare fields
    fields_to_compare = [
        ("status", "Status"),
        ("query", "Query"),
        ("output_interval", "Interval"),
        ("webhook_url", "Webhook"),
        ("skip_email", "Skip email"),
        ("user_timezone", "Timezone"),
        ("user_location", "Location"),
        ("is_public", "Public"),
    ]

    changes_found = False
    for field, label in fields_to_compare:
        old_val = old.get(field)
        new_val = new.get(field)
        if old_val != new_val:
            changes_found = True
            if field == "output_interval":
                old_display = _format_interval(old_val)
                new_display = _format_interval(new_val)
            elif field in ("skip_email", "is_public"):
                old_display = "yes" if old_val else "no"
                new_display = "yes" if new_val else "no"
            elif field == "query":
                old_display = f"\"{_truncate(old_val or '', 40)}\""
                new_display = f"\"{_truncate(new_val or '', 40)}\""
            else:
                old_display = old_val or "(not set)"
                new_display = new_val or "(not set)"
            lines.append(f"  • {label}: {old_display} → {new_display}")

    if not changes_found:
        lines.append("  (no changes detected)")

    return "\n".join(lines)


def format_scout_deleted(response: dict[str, Any], **context: Any) -> str:
    """Format delete_scout response as confirmation."""
    scout_id = context.get("scout_id", "")

    lines = [
        "Scout deleted.",
        "",
        f"ID: {scout_id}",
        "",
        "This action cannot be undone.",
    ]

    return "\n".join(lines)


# -----------------------------------------------------------------------------
# Task formatters (browsing and research)
# -----------------------------------------------------------------------------


def format_task_started(response: dict[str, Any], **context: Any) -> str:
    """Format run_*_task response showing task ID and next steps."""
    task_id = response.get("task_id", "")
    status = response.get("status", "queued")
    view_url = response.get("view_url", "")

    # Determine task type from context or response
    task_type = context.get("task_type", "")
    if not task_type:
        if "query" in response:
            task_type = "Research"
        elif "task" in response or "start_url" in response:
            task_type = "Browsing"
        else:
            task_type = "Task"

    lines = [
        f"{task_type} task started.",
        "",
        f"Task ID: {task_id}",
        f"Status: {status}",
    ]

    if view_url:
        lines.append(f"View progress: {view_url}")

    lines.append("")

    # Determine the right poll function
    if task_type == "Research":
        poll_fn = "get_research_task_result"
    elif task_type == "Browsing":
        poll_fn = "get_browsing_task_result"
    else:
        poll_fn = "get_task_result"

    lines.append(f"Poll with {poll_fn}(task_id=\"{task_id}\") to check status.")

    return "\n".join(lines)


def format_task_result(response: dict[str, Any], **context: Any) -> str:
    """Format get_*_task_result response based on status."""
    task_id = response.get("task_id", "")
    status = response.get("status", "unknown")

    # Handle in-progress states
    if status in ("queued", "running", "pending"):
        lines = [
            f"Task in progress.",
            "",
            f"Task ID: {task_id}",
            f"Status: {status}",
        ]

        progress = response.get("progress")
        if progress:
            lines.append(f"Progress: {progress}")

        lines.append("")
        lines.append("Poll again in a few seconds.")
        return "\n".join(lines)

    # Handle failed state
    if status == "failed":
        error = response.get("error") or response.get("message") or "Unknown error"
        return "\n".join([
            "Task failed.",
            "",
            f"Task ID: {task_id}",
            f"Error: {error}",
        ])

    # Handle completed state
    lines = [
        "Task completed.",
        "",
        f"Task ID: {task_id}",
        f"Status: {status}",
    ]

    # Add result content
    result = response.get("result") or response.get("output") or response.get("content")
    if result:
        lines.append("")
        lines.append("Result:")
        if isinstance(result, str):
            lines.append(result)
        elif isinstance(result, dict):
            lines.append(dict_to_markdown(result, level=0))
        elif isinstance(result, list):
            for item in result:
                if isinstance(item, dict):
                    lines.append(dict_to_markdown(item, level=0))
                    lines.append("")
                else:
                    lines.append(f"- {item}")

    # Add sources if present
    sources = response.get("sources") or response.get("citations")
    if sources:
        lines.append("")
        lines.append("Sources:")
        for source in sources[:10]:  # Limit to 10
            if isinstance(source, dict):
                url = source.get("url", "")
                title = source.get("title", url)
                lines.append(f"- {title}: {url}")
            else:
                lines.append(f"- {source}")
        if len(sources) > 10:
            lines.append(f"... and {len(sources) - 10} more")

    return "\n".join(lines)
