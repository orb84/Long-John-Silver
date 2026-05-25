"""Core enumeration types shared across LJS domains."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_serializer, model_validator


"""
Shared data models for LJS.

All Pydantic models that cross module boundaries are defined here
to prevent circular imports and ensure a single source of truth.
"""

from enum import Enum
import re
from typing import Optional, Any, Literal
from pydantic import BaseModel, Field, field_validator, model_validator, model_serializer
from datetime import datetime


# --- Enums ---


class Intent(str, Enum):
    """Classified user intent for the AI assistant."""

    SEARCH = "SEARCH"
    DOWNLOAD = "DOWNLOAD"
    CONFIG = "CONFIG"
    CHAT = "CHAT"
    CLARIFY = "CLARIFY"


class CategoryCapability(str, Enum):
    """Capability identifiers advertised by category manifests."""

    METADATA = "metadata"
    DOWNLOADABLE = "downloadable"
    FILE_ORGANIZATION = "file_organization"
    EPISODIC = "episodic"
    SUBTITLES = "subtitles"
    SCHEDULED_UPDATES = "scheduled_updates"
    COLLECTIONS = "collections"
    QUALITY_UPGRADES = "quality_upgrades"
    TRACKS = "tracks"
    CHAPTERS = "chapters"
    RATINGS = "ratings"


class DownloadStatus(str, Enum):
    """Lifecycle state of a torrent download."""

    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    SEEDING = "seeding"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STALLED = "stalled"


class DownloadPriority(str, Enum):
    """Priority level for queue ordering."""

    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class ShowLifecycleState(str, Enum):
    """Classification of where a show is in its lifecycle.

    Used by the scheduler to determine check frequency and whether
    to search for new episodes or just poll for status changes.
    """
    ACTIVE_AIRING = "active_airing"
    BETWEEN_SEASONS = "between_seasons"
    HIATUS = "hiatus"
    ENDED = "ended"
    UNKNOWN = "unknown"


class DownloadReason(str, Enum):
    """Why a download was triggered — displayed in the UI."""
    NEW_EPISODE = "new_episode"
    UPGRADE_QUALITY = "upgrade_quality"
    BUNDLE = "bundle"
    MANUAL = "manual"
    RETRY = "retry"
    RACE = "race"


class UpgradeStatus(str, Enum):
    """State of an upgrade candidate in the approval workflow."""
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


class SizeLimitMode(str, Enum):
    """Strategy for limiting download size: bitrate, file size, or smart."""

    BITRATE = "bitrate"
    FILE_SIZE = "file_size"
    SMART = "smart"


class ActionSource(str, Enum):
    """Origin of an action command.

    Each source corresponds to a client type that can trigger actions:
    CHAT (LLM tool calls), UI (REST button clicks), SCHEDULER (automated
    tasks), and SYSTEM (internal events).
    """

    CHAT = "chat"
    UI = "ui"
    SCHEDULER = "scheduler"
    SYSTEM = "system"


class TaskCriticality(str, Enum):
    """How critical a supervised task is — affects restart and logging behavior.

    CRITICAL: Always restart on crash (e.g., download monitors, queue manager).
      Crashes are logged at ERROR level.
    IMPORTANT: Restart up to MAX_RESTARTS times (e.g., file organization, selective
      download config). Crashes are logged at WARNING level.
    BEST_EFFORT: Do not restart (e.g., one-shot WebSocket broadcasts, polling
      callbacks). Crashes are logged at INFO level.
    """

    CRITICAL = "critical"
    IMPORTANT = "important"
    BEST_EFFORT = "best_effort"

