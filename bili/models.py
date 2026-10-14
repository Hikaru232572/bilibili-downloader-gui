from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4


class Platform(StrEnum):
    BILIBILI = 'bilibili'
    DOUYIN = 'douyin'
    XIAOHONGSHU = 'xiaohongshu'


class TaskState(StrEnum):
    QUEUED = 'queued'
    RUNNING = 'running'
    VALIDATING = 'validating'
    SUCCEEDED = 'succeeded'
    PARTIAL = 'partial'
    FAILED = 'failed'
    CANCEL_REQUESTED = 'cancel_requested'
    CANCELLED = 'cancelled'
    INTERRUPTED = 'interrupted'


TERMINAL_STATES = frozenset({TaskState.SUCCEEDED, TaskState.PARTIAL,
                            TaskState.FAILED, TaskState.CANCELLED,
                            TaskState.INTERRUPTED})
ACTIVE_STATES = frozenset({TaskState.RUNNING, TaskState.VALIDATING,
                          TaskState.CANCEL_REQUESTED})
TRANSITIONS = {
    TaskState.QUEUED: {TaskState.RUNNING, TaskState.CANCELLED, TaskState.INTERRUPTED},
    TaskState.RUNNING: {TaskState.VALIDATING, TaskState.PARTIAL, TaskState.FAILED,
                       TaskState.CANCEL_REQUESTED, TaskState.INTERRUPTED},
    TaskState.VALIDATING: {TaskState.SUCCEEDED, TaskState.PARTIAL, TaskState.FAILED,
                          TaskState.CANCEL_REQUESTED, TaskState.INTERRUPTED},
    TaskState.CANCEL_REQUESTED: {TaskState.CANCELLED, TaskState.INTERRUPTED},
    TaskState.SUCCEEDED: set(),
    TaskState.PARTIAL: {TaskState.QUEUED},
    TaskState.FAILED: {TaskState.QUEUED},
    TaskState.CANCELLED: {TaskState.QUEUED},
    TaskState.INTERRUPTED: {TaskState.QUEUED},
}


class Component(StrEnum):
    STILL = 'still'
    MOTION = 'motion'
    VIDEO = 'video'
    AUDIO = 'audio'
    SUBTITLE = 'subtitle'


class Watermark(StrEnum):
    CLEAN = 'clean'
    MARKED = 'marked'
    UNKNOWN = 'unknown'


@dataclass(frozen=True)
class Candidate:
    # URLs and required headers are transient. Do not persist as plain JSON.
    url: str = field(repr=False)
    component: Component
    source: str
    width: int | None = None
    height: int | None = None
    bitrate: int | None = None
    size: int | None = None
    codec: str | None = None
    watermark: Watermark = Watermark.UNKNOWN
    evidence: str | None = None
    headers: tuple[tuple[str, str], ...] = field(default=(), repr=False)

    def __post_init__(self):
        if self.watermark != Watermark.UNKNOWN and not self.evidence:
            raise ValueError('Watermark classification requires evidence')
        if not self.url.startswith('https://'):
            raise ValueError('Media candidates require HTTPS')


@dataclass(frozen=True)
class MediaItem:
    item_id: str
    original_index: int
    kind: str
    still_candidates: tuple[Candidate, ...] = ()
    motion_candidates: tuple[Candidate, ...] = ()
    video_candidates: tuple[Candidate, ...] = ()
    audio_candidates: tuple[Candidate, ...] = ()
    preview_url: str | None = field(default=None, repr=False)
    missing_components: tuple[Component, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self):
        if self.original_index < 1:
            raise ValueError('Original indices start at one')
        if self.kind not in ('image', 'live', 'video', 'missing'):
            raise ValueError('Unknown media kind')


@dataclass(frozen=True)
class Work:
    platform: Platform
    work_id: str
    content_type: str
    items: tuple[MediaItem, ...]
    title: str | None = None
    author: str | None = None
    declared_item_count: int | None = None
    warnings: tuple[str, ...] = ()
    backend_revision: str = ''

    def __post_init__(self):
        if not self.work_id or self.content_type not in ('video', 'gallery', 'unsupported'):
            raise ValueError('Invalid work identity/type')
        if len({i.item_id for i in self.items}) != len(self.items):
            raise ValueError('Duplicate media identity')
        indices = [i.original_index for i in self.items]
        if indices != sorted(set(indices)):
            raise ValueError('Media order must preserve distinct original positions')


@dataclass(frozen=True)
class TaskSpec:
    platform: Platform
    work_id: str
    title: str
    source_ref: str  # opaque key to DPAPI-protected source URL
    output_dir: str
    selected_item_ids: tuple[str, ...]
    components: tuple[Component, ...]
    format_selector: str | None = None
    audio_format: str | None = None
    subtitle_language: str | None = None
    auth_ref: str | None = None
    task_id: str = field(default_factory=lambda: str(uuid4()))
    submission_key: str = field(default_factory=lambda: str(uuid4()))
    work_ref: str | None = None  # encrypted transient resource snapshot

    def __post_init__(self):
        if not self.work_id or not self.selected_item_ids or not self.components:
            raise ValueError('A task requires a work, selection and output types')
        if not Path(self.output_dir).is_absolute():
            raise ValueError('Task output directory must be absolute')
        if len(set(self.selected_item_ids)) != len(self.selected_item_ids):
            raise ValueError('Duplicate selection')
        if not self.source_ref or '://' in self.source_ref:
            raise ValueError('Source must be an opaque protected reference')
        if self.work_ref is not None and (not self.work_ref or '://' in self.work_ref):
            raise ValueError('Work snapshot must be an opaque protected reference')

    def record(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    @classmethod
    def from_record(cls, data: dict[str, Any]) -> TaskSpec:
        fields = dict(data)
        fields['platform'] = Platform(fields['platform'])
        fields['selected_item_ids'] = tuple(fields['selected_item_ids'])
        fields['components'] = tuple(Component(v) for v in fields['components'])
        return cls(**fields)


@dataclass(frozen=True)
class OutputSpec:
    task_id: str
    item_id: str
    component: Component
    original_index: int
    output_id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(frozen=True)
class TaskRecord:
    spec: TaskSpec
    state: TaskState
    sequence: int
    batch_id: str
    attempt_id: str | None
    error_code: str | None = None
    message: str | None = None
