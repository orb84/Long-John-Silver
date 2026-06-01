#!/usr/bin/env python3
"""Round 208 notification delivery-ledger regression tests.

These tests intentionally use a tiny in-memory fake repository so they can run
in packaging environments where aiosqlite is not installed.  The compile pass
covers the real SQLite repository methods; this script validates the service
contract that prevents bridge replay spam after restart.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.models import NotificationMessage
from src.core.notifications import NotificationService


class FakeNotificationRepo:
    def __init__(self) -> None:
        self.rows: dict[int, dict[str, Any]] = {}
        self.dedupe_to_id: dict[str, int] = {}
        self.deliveries: dict[tuple[int, str], str] = {}
        self.next_id = 1

    async def create(self, **kwargs: Any) -> tuple[int, bool]:
        dedupe_key = str(kwargs.get("dedupe_key") or "")
        if dedupe_key and dedupe_key in self.dedupe_to_id:
            notification_id = self.dedupe_to_id[dedupe_key]
            row = self.rows[notification_id]
            # Mirror the real repository's Round 208 behavior: duplicate events
            # can refresh text/metadata, but they do not become new unread emits.
            row.update({
                "title": kwargs.get("title"),
                "body": kwargs.get("body"),
                "level": kwargs.get("level"),
                "category_id": kwargs.get("category_id"),
                "item_id": kwargs.get("item_id"),
                "event_type": kwargs.get("event_type"),
                "actions": kwargs.get("actions") or [],
                "metadata": kwargs.get("metadata") or {},
                "dedupe_key": dedupe_key,
            })
            return notification_id, False
        notification_id = self.next_id
        self.next_id += 1
        self.rows[notification_id] = {
            "id": notification_id,
            "title": kwargs.get("title"),
            "body": kwargs.get("body"),
            "level": kwargs.get("level"),
            "category_id": kwargs.get("category_id"),
            "item_id": kwargs.get("item_id"),
            "event_type": kwargs.get("event_type"),
            "actions": kwargs.get("actions") or [],
            "metadata": kwargs.get("metadata") or {},
            "dedupe_key": dedupe_key,
            "status": "unread",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if dedupe_key:
            self.dedupe_to_id[dedupe_key] = notification_id
        return notification_id, True

    async def list(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        rows = list(self.rows.values())
        if status:
            rows = [row for row in rows if row.get("status") == status]
        return list(reversed(rows[-limit:]))

    async def unread_count(self) -> int:
        return sum(1 for row in self.rows.values() if row.get("status") == "unread")

    async def should_deliver_to_bridge(self, notification_id: int, bridge_id: str) -> bool:
        return self.deliveries.get((notification_id, bridge_id)) != "delivered"

    async def record_bridge_delivery_attempt(self, notification_id: int, bridge_id: str) -> None:
        self.deliveries.setdefault((notification_id, bridge_id), "pending")

    async def record_bridge_delivery_success(self, notification_id: int, bridge_id: str) -> None:
        self.deliveries[(notification_id, bridge_id)] = "delivered"

    async def record_bridge_delivery_failure(self, notification_id: int, bridge_id: str, error: str) -> None:
        if self.deliveries.get((notification_id, bridge_id)) != "delivered":
            self.deliveries[(notification_id, bridge_id)] = "failed"


class FakeDB:
    def __init__(self, repo: FakeNotificationRepo) -> None:
        self.notifications = repo


class FakeBridge:
    def __init__(self, delivery_id: str) -> None:
        self.delivery_id = delivery_id
        self.sent: list[NotificationMessage] = []

    async def send_notification(self, message: NotificationMessage) -> bool:
        self.sent.append(message)
        return True


async def main() -> None:
    repo = FakeNotificationRepo()
    service = NotificationService(FakeDB(repo))
    discord_a = FakeBridge("discord:111")
    service.register_bridge(discord_a, bridge_id="discord")
    await asyncio.sleep(0)

    await service.notify(
        NotificationMessage(title="Download Complete", body="Download complete: The Boys S01", level="success"),
        event_type="download_complete",
        category_id="tv",
        item_id="The Boys",
        dedupe_key="download_complete:download:abc",
    )
    assert len(discord_a.sent) == 1, discord_a.sent

    # Duplicate logical event in the same process must not hit the same bridge.
    await service.notify(
        NotificationMessage(title="Download Complete", body="Download complete: The Boys S01", level="success"),
        event_type="download_complete",
        category_id="tv",
        item_id="The Boys",
        dedupe_key="download_complete:download:abc",
    )
    assert len(discord_a.sent) == 1, discord_a.sent

    # Restart: replay unread notification to the same Discord channel is skipped
    # because the bridge delivery row is already delivered.
    restarted = NotificationService(FakeDB(repo))
    discord_a_after_restart = FakeBridge("discord:111")
    restarted.register_bridge(discord_a_after_restart, bridge_id="discord")
    await asyncio.sleep(0.05)
    assert len(discord_a_after_restart.sent) == 0, discord_a_after_restart.sent

    # Different target/channel also should not receive legacy pre-restart rows.
    discord_b = FakeBridge("discord:222")
    restarted.register_bridge(discord_b, bridge_id="discord-other")
    await asyncio.sleep(0.05)
    assert len(discord_b.sent) == 0, discord_b.sent

    # A notification created during the current process while the bridge is not
    # yet registered is replayed once when that target comes online.
    delayed = NotificationService(FakeDB(repo))
    await delayed.notify(
        NotificationMessage(title="Release Found", body="Candidate ready", level="info"),
        event_type="release_watch_candidate_found",
        category_id="tv",
        item_id="The Boys",
        dedupe_key="release_watch_found:tv:The Boys:S01",
    )
    discord_c = FakeBridge("discord:333")
    delayed.register_bridge(discord_c, bridge_id="discord-delayed")
    await asyncio.sleep(0.05)
    assert len(discord_c.sent) == 1, discord_c.sent

    print("Round 208 notification delivery ledger tests passed")


if __name__ == "__main__":
    asyncio.run(main())
