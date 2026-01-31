#!/usr/bin/env python3
"""
Test new APIs: Activity, Tasks, Calendar, Labels.

Run with: uv run python scripts/test_new_apis.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.services import (
    get_activity_service,
    get_tasks_service,
    get_calendar_service,
    get_labels_service,
    get_drive_service,
)
from googleapiclient.errors import HttpError
import json


def test_activity_api():
    """Test Drive Activity API - action items via comments."""
    print("\n" + "=" * 60)
    print("DRIVE ACTIVITY API")
    print("=" * 60)

    try:
        activity = get_activity_service()

        # Query recent activity
        result = activity.activity().query(
            body={
                "pageSize": 10,
            }
        ).execute()

        activities = result.get("activities", [])
        print(f"✅ Found {len(activities)} recent activities")

        for act in activities[:3]:
            timestamp = act.get("timestamp", "?")[:19]
            print(f"\n  Time: {timestamp}")

            # Targets
            for target in act.get("targets", []):
                if "driveItem" in target:
                    print(f"  File: {target['driveItem'].get('title', '?')}")

            # Actions
            for action in act.get("actions", []):
                detail = action.get("detail", {})
                action_type = list(detail.keys())[0] if detail else "unknown"
                print(f"  Action: {action_type}")

        # Now specifically look for COMMENT actions
        print("\n  --- Comment Activity ---")
        result = activity.activity().query(
            body={
                "pageSize": 20,
                "filter": "detail.action_detail_case:COMMENT",
            }
        ).execute()

        comment_activities = result.get("activities", [])
        print(f"  Found {len(comment_activities)} comment activities")

        for act in comment_activities[:3]:
            for target in act.get("targets", []):
                if "driveItem" in target:
                    print(f"    - {target['driveItem'].get('title', '?')}")
            for action in act.get("actions", []):
                comment = action.get("detail", {}).get("comment", {})
                if "mentionedUsers" in comment:
                    print(f"      Mentions: {len(comment['mentionedUsers'])} users")
                if "assignment" in comment:
                    print(f"      Assignment: {comment['assignment']}")

    except HttpError as e:
        print(f"❌ API error: {e.resp.status} - {e.reason}")
    except Exception as e:
        print(f"❌ Error: {type(e).__name__}: {e}")


def test_tasks_api():
    """Test Google Tasks API."""
    print("\n" + "=" * 60)
    print("GOOGLE TASKS API")
    print("=" * 60)

    try:
        tasks = get_tasks_service()

        # List task lists
        result = tasks.tasklists().list(maxResults=10).execute()
        lists = result.get("items", [])
        print(f"✅ Found {len(lists)} task lists")

        for tl in lists:
            print(f"\n  List: {tl.get('title')}")

            # Get tasks including assigned ones
            tasks_result = tasks.tasks().list(
                tasklist=tl["id"],
                maxResults=10,
                showCompleted=False,
                showHidden=True,
                showAssigned=True,  # Important: include assigned tasks from Docs
            ).execute()

            items = tasks_result.get("items", [])
            print(f"  Tasks: {len(items)}")

            for task in items[:5]:
                title = task.get("title", "(no title)")[:50]
                status = task.get("status", "?")
                print(f"    [{status}] {title}")

                # Check for assignment info
                if task.get("assignmentInfo"):
                    info = task["assignmentInfo"]
                    print(f"           Assigned via: {info.get('linkBack', '?')}")

                # Check for notes with Drive links
                notes = task.get("notes", "")
                if "drive.google" in notes:
                    print(f"           Has Drive link")

    except HttpError as e:
        print(f"❌ API error: {e.resp.status} - {e.reason}")
    except Exception as e:
        print(f"❌ Error: {type(e).__name__}: {e}")


def test_calendar_api():
    """Test Calendar API - meeting context."""
    print("\n" + "=" * 60)
    print("GOOGLE CALENDAR API")
    print("=" * 60)

    try:
        calendar = get_calendar_service()

        # List calendars
        result = calendar.calendarList().list(maxResults=5).execute()
        calendars = result.get("items", [])
        print(f"✅ Found {len(calendars)} calendars")

        for cal in calendars[:3]:
            print(f"  - {cal.get('summary', '?')}")

        # Get recent events from primary calendar
        from datetime import datetime, timedelta

        now = datetime.utcnow()
        week_ago = (now - timedelta(days=7)).isoformat() + "Z"
        now_str = now.isoformat() + "Z"

        events_result = calendar.events().list(
            calendarId="primary",
            timeMin=week_ago,
            timeMax=now_str,
            maxResults=10,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        events = events_result.get("items", [])
        print(f"\n  Recent events (last 7 days): {len(events)}")

        for event in events[:5]:
            summary = event.get("summary", "(no title)")[:40]
            start = event.get("start", {}).get("dateTime", event.get("start", {}).get("date", "?"))[:16]
            print(f"    {start} - {summary}")

            # Check for attachments (Drive docs linked to meeting)
            attachments = event.get("attachments", [])
            if attachments:
                print(f"              Attachments: {len(attachments)}")
                for att in attachments[:2]:
                    print(f"                - {att.get('title', '?')}")

    except HttpError as e:
        print(f"❌ API error: {e.resp.status} - {e.reason}")
    except Exception as e:
        print(f"❌ Error: {type(e).__name__}: {e}")


def test_labels_api():
    """Test Drive Labels API - organizational metadata."""
    print("\n" + "=" * 60)
    print("DRIVE LABELS API")
    print("=" * 60)

    try:
        labels = get_labels_service()

        # List available labels
        result = labels.labels().list(
            view="LABEL_VIEW_FULL",
            pageSize=10,
        ).execute()

        label_list = result.get("labels", [])
        print(f"✅ Found {len(label_list)} labels")

        for label in label_list[:5]:
            name = label.get("properties", {}).get("title", label.get("name", "?"))
            label_type = label.get("labelType", "?")
            print(f"  - {name} ({label_type})")

            # Show fields
            fields = label.get("fields", [])
            if fields:
                print(f"    Fields: {len(fields)}")
                for field in fields[:3]:
                    field_name = field.get("properties", {}).get("displayName", "?")
                    field_type = field.get("valueType", "?")
                    print(f"      - {field_name} ({field_type})")

    except HttpError as e:
        if e.resp.status == 404:
            print("⚠️  Labels API not available (enterprise feature)")
        else:
            print(f"❌ API error: {e.resp.status} - {e.reason}")
    except Exception as e:
        print(f"❌ Error: {type(e).__name__}: {e}")


def main():
    print("Testing new APIs with expanded scopes...")

    test_activity_api()
    test_tasks_api()
    test_calendar_api()
    test_labels_api()

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print("""
With these new scopes, mise-en-space can now:

📋 ACTIVITY API:
   - See who did what on any file
   - Find action items via COMMENT events with mentions
   - Track file access patterns

✅ TASKS API:
   - See Google Tasks lists and items
   - Find tasks assigned from Docs via showAssigned=True
   - Correlate tasks with Drive files

📅 CALENDAR API:
   - Get meeting context (who, when, what)
   - Find Drive attachments linked to meetings
   - Understand document timelines via calendar events

🏷️  LABELS API:
   - Read organizational metadata (priority, status, etc.)
   - Query files by label values
   - (Enterprise feature - may not be available)
""")


if __name__ == "__main__":
    main()
