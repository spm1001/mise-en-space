"""
Activity adapter — Google Drive Activity API v2 wrapper.

Provides activity search and filtering for comment-related activities.
Useful for finding action items (comments mentioning you) across files.
"""

from typing import Any

from adapters.services import get_activity_service
from models import (
    ActivityActor,
    ActivityTarget,
    CommentActivity,
    ActivitySearchResult,
)
from retry import with_retry


def _parse_actor(actor_data: dict[str, Any]) -> ActivityActor:
    """Parse actor from Activity API response."""
    # Actor can be user, impersonation, or system
    user = actor_data.get("user", {})
    known_user = user.get("knownUser", {})

    # Try to get person name from knownUser
    # Note: Activity API often returns people/ID format instead of display names
    person_name = known_user.get("personName", "")
    if person_name.startswith("people/"):
        person_name = ""  # Not a display name — treat as unknown

    # Fallback to displayName from other actor types
    if not person_name:
        # Check for administrator, impersonation, etc.
        if "administrator" in actor_data:
            person_name = "Administrator"
        elif "impersonation" in actor_data:
            person_name = actor_data["impersonation"].get("impersonatedUser", {}).get("knownUser", {}).get("personName", "Unknown")
        elif "system" in actor_data:
            person_name = "System"
        else:
            person_name = "Unknown"

    return ActivityActor(
        name=person_name,
        email=None,  # Activity API doesn't expose email directly for privacy
    )


def _parse_target(target_data: dict[str, Any]) -> ActivityTarget | None:
    """Parse target from Activity API response.

    Handles two target types:
    - driveItem: used for edits, creates, moves, renames, etc.
    - fileComment: used for comment activities (parent has same shape as driveItem)
    """
    drive_item = target_data.get("driveItem", {})
    if not drive_item:
        # Comment activities use fileComment with drive item nested in parent
        file_comment = target_data.get("fileComment", {})
        drive_item = file_comment.get("parent", {})
    if not drive_item:
        return None

    # Parse the item name (format: "items/FILE_ID")
    item_name = drive_item.get("name", "")
    file_id = item_name.replace("items/", "") if item_name.startswith("items/") else item_name

    # Title and mimeType
    title = drive_item.get("title", "")
    mime_type = drive_item.get("mimeType", "")

    # Construct web link (basic format)
    web_link = None
    if file_id:
        if mime_type == "application/vnd.google-apps.document":
            web_link = f"https://docs.google.com/document/d/{file_id}/edit"
        elif mime_type == "application/vnd.google-apps.spreadsheet":
            web_link = f"https://docs.google.com/spreadsheets/d/{file_id}/edit"
        elif mime_type == "application/vnd.google-apps.presentation":
            web_link = f"https://docs.google.com/presentation/d/{file_id}/edit"
        else:
            web_link = f"https://drive.google.com/file/d/{file_id}/view"

    return ActivityTarget(
        file_id=file_id,
        file_name=title,
        mime_type=mime_type,
        web_link=web_link,
    )


def _parse_comment_action(action_detail: dict[str, Any]) -> tuple[str, list[str], str | None]:
    """
    Parse comment action details.

    Returns:
        Tuple of (action_type, mentioned_users, comment_content)
    """
    comment = action_detail.get("comment", {})
    if not comment:
        return ("unknown", [], None)

    # Determine action type from the nested structure
    post = comment.get("post", {})
    assignment = comment.get("assignment", {})
    suggestion = comment.get("suggestion", {})

    # Parse mentions from mentionedUsers
    mentioned_users: list[str] = []
    for mention in comment.get("mentionedUsers", []):
        known_user = mention.get("knownUser", {})
        # Activity API exposes personName but not email
        # We can only note that mentions exist
        if known_user.get("personName"):
            mentioned_users.append(known_user["personName"])

    # Determine action type
    if post:
        subtype = post.get("subtype", "")
        if subtype == "ADDED":
            action_type = "comment"
        elif subtype == "REPLY_ADDED":
            action_type = "reply"
        elif subtype == "RESOLVED":
            action_type = "resolve"
        elif subtype == "REOPENED":
            action_type = "reopen"
        elif subtype == "DELETED":
            action_type = "delete"
        else:
            action_type = f"post_{subtype.lower()}" if subtype else "post"
    elif assignment:
        subtype = assignment.get("subtype", "")
        if subtype == "ADDED":
            action_type = "assign"
        elif subtype == "REMOVED":
            action_type = "unassign"
        else:
            action_type = f"assignment_{subtype.lower()}" if subtype else "assignment"
    elif suggestion:
        subtype = suggestion.get("subtype", "")
        if subtype == "ADDED":
            action_type = "suggest"
        elif subtype == "ACCEPTED":
            action_type = "accept_suggestion"
        elif subtype == "REJECTED":
            action_type = "reject_suggestion"
        else:
            action_type = f"suggestion_{subtype.lower()}" if subtype else "suggestion"
    else:
        action_type = "comment_action"

    return (action_type, mentioned_users, None)


@with_retry(max_attempts=3, delay_ms=1000)
def search_comment_activities(
    page_size: int = 50,
    page_token: str | None = None,
) -> ActivitySearchResult:
    """
    Find comment activities across all accessible files.

    Filters to comment-related actions only. Useful for finding:
    - Comments mentioning you (action items)
    - Recent discussions on your files
    - Comment threads you've participated in

    Args:
        page_size: Number of activities per page (max 100)
        page_token: Pagination token for next page

    Returns:
        ActivitySearchResult with comment activities
    """
    service = get_activity_service()

    body: dict[str, Any] = {
        "pageSize": min(page_size, 100),
        "filter": "detail.action_detail_case:COMMENT",
    }
    if page_token:
        body["pageToken"] = page_token

    response = service.activity().query(body=body).execute()

    activities: list[CommentActivity] = []
    warnings: list[str] = []

    for activity in response.get("activities", []):
        # Get timestamp
        timestamp = activity.get("timestamp", "")

        # Get primary action (first in list)
        actions = activity.get("primaryActionDetail", {})
        action_type, mentioned_users, content = _parse_comment_action(actions)

        # Get actors (usually just one)
        actors = activity.get("actors", [])
        actor = _parse_actor(actors[0]) if actors else ActivityActor(name="Unknown")

        # Get targets (usually just one)
        targets = activity.get("targets", [])
        target = _parse_target(targets[0]) if targets else None

        if not target:
            warnings.append(f"Activity {activity.get('name', 'unknown')}: missing target")
            continue

        activities.append(
            CommentActivity(
                activity_id=activity.get("name", ""),
                timestamp=timestamp,
                actor=actor,
                target=target,
                action_type=action_type,
                mentioned_users=mentioned_users,
                comment_content=content,
            )
        )

    return ActivitySearchResult(
        activities=activities,
        next_page_token=response.get("nextPageToken"),
        warnings=warnings,
    )


@with_retry(max_attempts=3, delay_ms=1000)
def get_file_activities(
    file_id: str,
    page_size: int = 50,
    filter_type: str | None = "comments",
) -> ActivitySearchResult:
    """
    Get activity history for a specific file.

    Args:
        file_id: Drive file ID
        page_size: Number of activities per page (max 100)
        filter_type: Activity type filter:
            - "comments": Comment-related activities only
            - "edits": Edit-related activities only
            - None: All activities

    Returns:
        ActivitySearchResult with file activities
    """
    service = get_activity_service()

    body: dict[str, Any] = {
        "pageSize": min(page_size, 100),
        "itemName": f"items/{file_id}",
    }

    # Add filter if specified
    if filter_type == "comments":
        body["filter"] = "detail.action_detail_case:COMMENT"
    elif filter_type == "edits":
        body["filter"] = "detail.action_detail_case:EDIT"

    response = service.activity().query(body=body).execute()

    activities: list[CommentActivity] = []
    warnings: list[str] = []

    for activity in response.get("activities", []):
        # Get timestamp
        timestamp = activity.get("timestamp", "")

        # Get primary action
        actions = activity.get("primaryActionDetail", {})

        # Determine action type based on what's present
        if "comment" in actions:
            action_type, mentioned_users, content = _parse_comment_action(actions)
        elif "edit" in actions:
            action_type = "edit"
            mentioned_users = []
            content = None
        elif "create" in actions:
            action_type = "create"
            mentioned_users = []
            content = None
        elif "move" in actions:
            action_type = "move"
            mentioned_users = []
            content = None
        elif "rename" in actions:
            action_type = "rename"
            mentioned_users = []
            content = None
        elif "delete" in actions:
            action_type = "delete"
            mentioned_users = []
            content = None
        elif "restore" in actions:
            action_type = "restore"
            mentioned_users = []
            content = None
        else:
            action_type = "other"
            mentioned_users = []
            content = None

        # Get actors
        actors = activity.get("actors", [])
        actor = _parse_actor(actors[0]) if actors else ActivityActor(name="Unknown")

        # Get targets
        targets = activity.get("targets", [])
        target = _parse_target(targets[0]) if targets else None

        if not target:
            # Use file_id as fallback
            target = ActivityTarget(
                file_id=file_id,
                file_name="",
                mime_type=None,
                web_link=None,
            )

        activities.append(
            CommentActivity(
                activity_id=activity.get("name", ""),
                timestamp=timestamp,
                actor=actor,
                target=target,
                action_type=action_type,
                mentioned_users=mentioned_users,
                comment_content=content,
            )
        )

    return ActivitySearchResult(
        activities=activities,
        next_page_token=response.get("nextPageToken"),
        warnings=warnings,
    )
