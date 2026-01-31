#!/usr/bin/env python3
"""
Discover action items via Drive Activity API and Comments.

Action items = comments with @mentions (mentionedEmailAddresses).
Can we query for these efficiently?

Run with: uv run python scripts/test_action_items_discovery.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.services import get_drive_service, _get_credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import json


def main():
    drive = get_drive_service()
    creds = _get_credentials()

    # 1. Find docs with comments
    print("=" * 60)
    print("FINDING DOCS VIA UI-STYLE SEARCH (baseline)")
    print("=" * 60)

    # First, let's see if we can find docs that Google considers to have action items
    # by searching for docs I've been mentioned in
    try:
        # Try different queries that might correlate with action items
        queries = [
            # Files I can access that have comments
            "'me' in readers and mimeType = 'application/vnd.google-apps.document'",
        ]

        for q in queries:
            print(f"\nQuery: {q}")
            result = drive.files().list(
                q=q,
                pageSize=10,
                fields="files(id,name)",
            ).execute()
            files = result.get("files", [])
            print(f"  Found {len(files)} files")

            # Check each for comments with mentions
            for f in files[:5]:
                try:
                    comments = drive.comments().list(
                        fileId=f['id'],
                        fields="comments(id,content,resolved,mentionedEmailAddresses)",
                        pageSize=100,
                    ).execute()

                    comment_list = comments.get("comments", [])
                    mentions = [c for c in comment_list if c.get('mentionedEmailAddresses')]

                    if mentions:
                        print(f"\n    📌 {f['name']}")
                        print(f"       {len(mentions)} comment(s) with mentions")
                        for m in mentions[:2]:
                            print(f"       - To: {m.get('mentionedEmailAddresses')}")
                            print(f"         Text: {m.get('content', '')[:50]}...")
                            print(f"         Resolved: {m.get('resolved', False)}")
                except Exception as e:
                    pass

    except Exception as e:
        print(f"Error: {e}")

    # 2. Try Drive Activity API - specifically for comment assignments
    print("\n" + "=" * 60)
    print("DRIVE ACTIVITY API - COMMENT EVENTS")
    print("=" * 60)

    try:
        activity = build('driveactivity', 'v2', credentials=creds)

        # Query for comment-related activities
        result = activity.activity().query(
            body={
                "pageSize": 20,
                # Filter to comments with assignments
                "filter": "detail.action_detail_case:COMMENT",
            }
        ).execute()

        activities = result.get("activities", [])
        print(f"Found {len(activities)} comment activities")

        for act in activities[:5]:
            print(f"\n  Time: {act.get('timestamp', '?')[:19]}")

            # Get targets
            for target in act.get('targets', []):
                if 'driveItem' in target:
                    item = target['driveItem']
                    print(f"  File: {item.get('title', '?')}")

            # Get actions
            for action in act.get('actions', []):
                detail = action.get('detail', {})
                if 'comment' in detail:
                    comment = detail['comment']
                    print(f"  Comment action: {list(comment.keys())}")
                    if 'mentionedUsers' in comment:
                        print(f"  Mentioned: {comment['mentionedUsers']}")
                    if 'assignment' in comment:
                        print(f"  Assignment: {comment['assignment']}")

    except HttpError as e:
        print(f"API error: {e.resp.status} - {e.reason}")
    except Exception as e:
        print(f"Error: {type(e).__name__}: {e}")

    # 3. Check if scopes allow Tasks API
    print("\n" + "=" * 60)
    print("GOOGLE TASKS API")
    print("=" * 60)

    try:
        tasks = build('tasks', 'v1', credentials=creds)

        # List task lists
        result = tasks.tasklists().list(maxResults=10).execute()
        lists = result.get("items", [])
        print(f"Found {len(lists)} task lists")

        for tl in lists:
            print(f"\n  List: {tl.get('title')}")

            # Get tasks
            tasks_result = tasks.tasks().list(
                tasklist=tl['id'],
                maxResults=10,
                showCompleted=False,
                showHidden=True,
            ).execute()

            items = tasks_result.get("items", [])
            for task in items[:3]:
                print(f"    - {task.get('title', '?')[:40]}")
                # Check for Drive links in notes or links field
                if task.get('notes'):
                    if 'drive.google' in task.get('notes', ''):
                        print(f"      (has Drive link)")
                if task.get('links'):
                    print(f"      Links: {len(task['links'])}")

    except HttpError as e:
        if e.resp.status == 403:
            print("Tasks API not authorized - need to add tasks scope")
        else:
            print(f"API error: {e.resp.status}")
    except Exception as e:
        print(f"Error: {type(e).__name__}: {e}")

    # 4. Summary: What IS possible
    print("\n" + "=" * 60)
    print("SUMMARY: WHAT'S ACTUALLY POSSIBLE")
    print("=" * 60)

    print("""
Action items in Drive are comments with @mentions. Here's what we can do:

✅ POSSIBLE:
   - List files user can access
   - Get comments on each file
   - Check which comments have mentionedEmailAddresses
   - Filter to unresolved comments
   - This effectively finds action items, but requires N+1 queries

❌ NOT POSSIBLE:
   - Single query for "all files with action items for me"
   - The followup:actionitems operator (UI-only)
   - Native "assigned to me" filter in files.list

🔄 WORKAROUND:
   1. Query recent/relevant docs (e.g., modifiedTime > X, specific folders)
   2. Batch-fetch comments for those docs
   3. Filter to comments with mentions of current user
   4. Return files with open action items

   Cost: ~2-3 API calls per file checked
   Practical limit: ~50-100 files per search
""")


if __name__ == "__main__":
    main()
