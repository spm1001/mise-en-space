#!/usr/bin/env python3
"""
Test getting action items via the Comments API.

Action items in Google Docs are stored as special comments with assigned users.
Maybe we can query for these directly?

Run with: uv run python scripts/test_action_items_via_comments.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.services import get_drive_service
from googleapiclient.errors import HttpError
import json


def main():
    service = get_drive_service()

    # First, find a doc that might have action items
    print("Looking for docs with 'action' in name...")

    result = service.files().list(
        q="name contains 'action' and mimeType = 'application/vnd.google-apps.document'",
        pageSize=5,
        fields="files(id,name)",
    ).execute()

    files = result.get("files", [])
    print(f"Found {len(files)} docs")

    for f in files:
        print(f"\n{'='*60}")
        print(f"Checking: {f['name']} ({f['id']})")
        print("=" * 60)

        # Try to get comments
        try:
            comments = service.comments().list(
                fileId=f['id'],
                fields="*",
                pageSize=100,
            ).execute()

            comment_list = comments.get("comments", [])
            print(f"Found {len(comment_list)} comments")

            for comment in comment_list[:5]:
                print(f"\n  Comment ID: {comment.get('id')}")
                print(f"  Author: {comment.get('author', {}).get('displayName', '?')}")
                print(f"  Content: {comment.get('content', '')[:100]}...")
                print(f"  Resolved: {comment.get('resolved', False)}")

                # Check for action item markers
                if 'anchor' in comment:
                    print(f"  Anchor: {comment.get('anchor')}")
                if 'quotedFileContent' in comment:
                    print(f"  Quoted: {comment.get('quotedFileContent', {}).get('value', '')[:50]}")

                # The key: assigned field?
                if comment.get('replies'):
                    print(f"  Replies: {len(comment['replies'])}")

                # Look for any assignment-related fields
                interesting = {k: v for k, v in comment.items()
                              if k not in ['id', 'author', 'content', 'modifiedTime', 'createdTime', 'resolved', 'htmlContent']}
                if interesting:
                    print(f"  Other fields: {json.dumps(interesting, indent=4)[:200]}")

        except HttpError as e:
            print(f"  Error: {e.resp.status}")

    # Now let's check what the Drive files.get returns for comments-related fields
    print("\n\n" + "=" * 60)
    print("CHECKING FILE METADATA FOR COMMENT/ACTION INDICATORS")
    print("=" * 60)

    # Use a known doc
    if files:
        file_id = files[0]['id']
        try:
            # Get ALL fields
            metadata = service.files().get(
                fileId=file_id,
                fields="*",
            ).execute()

            # Look for any comment or action-related fields
            comment_fields = {k: v for k, v in metadata.items()
                            if any(term in k.lower() for term in ['comment', 'action', 'follow', 'assign', 'task'])}

            if comment_fields:
                print(f"Found comment/action fields: {json.dumps(comment_fields, indent=2)}")
            else:
                print("No comment/action-specific fields in file metadata")

            # Print all field names for reference
            print(f"\nAll file metadata fields: {sorted(metadata.keys())}")

        except Exception as e:
            print(f"Error: {e}")

    # Try the Drive Activity API - this might show action item assignments
    print("\n\n" + "=" * 60)
    print("TRYING DRIVE ACTIVITY API")
    print("=" * 60)

    try:
        from googleapiclient.discovery import build
        from adapters.services import get_credentials

        creds = get_credentials()

        # Build activity service
        activity_service = build('driveactivity', 'v2', credentials=creds)

        # Query recent activity
        result = activity_service.activity().query(
            body={
                "pageSize": 10,
                "filter": "detail.action_detail_case:COMMENT",  # Filter to comments
            }
        ).execute()

        activities = result.get("activities", [])
        print(f"Found {len(activities)} comment activities")

        for activity in activities[:3]:
            print(f"\n  Timestamp: {activity.get('timestamp', '?')}")
            print(f"  Actions: {[a.get('detail', {}).keys() for a in activity.get('actions', [])]}")
            targets = activity.get('targets', [])
            for target in targets:
                if 'driveItem' in target:
                    print(f"  File: {target['driveItem'].get('title', '?')}")

    except Exception as e:
        print(f"Drive Activity API error: {type(e).__name__}: {e}")

    # Try Tasks API to see if action items sync there
    print("\n\n" + "=" * 60)
    print("TRYING GOOGLE TASKS API")
    print("=" * 60)

    try:
        from googleapiclient.discovery import build
        from adapters.services import get_credentials

        creds = get_credentials()

        tasks_service = build('tasks', 'v1', credentials=creds)

        # List task lists
        tasklists = tasks_service.tasklists().list().execute()
        lists = tasklists.get("items", [])
        print(f"Found {len(lists)} task lists")

        for tl in lists:
            print(f"\n  List: {tl.get('title')}")

            # Get tasks in this list
            tasks = tasks_service.tasks().list(tasklist=tl['id'], maxResults=5).execute()
            items = tasks.get("items", [])
            print(f"  Tasks: {len(items)}")

            for task in items[:3]:
                print(f"    - {task.get('title', '?')[:50]}")
                # Check for any links to Drive
                if 'notes' in task:
                    print(f"      Notes: {task['notes'][:50]}")
                if 'links' in task:
                    print(f"      Links: {task['links']}")

    except Exception as e:
        print(f"Tasks API error: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
