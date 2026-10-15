"""Small, truthful queue summary shared by all application pages."""
from dataclasses import dataclass
import math
import time

from .models import ACTIVE_STATES, TaskState
from .storage import SUCCESS_LINGER


@dataclass(frozen=True)
class TaskSummary:
    text: str = ''
    count: int = 0
    fraction: float | None = None

    @property
    def visible(self):
        return bool(self.text)


def summarize_tasks(records, progress, *, now=None) -> TaskSummary:
    now = time.time() if now is None else now
    records = list(records)
    waiting = [r for r in records if r.state == TaskState.QUEUED]
    active = [r for r in records if r.state in ACTIVE_STATES]
    attention = [r for r in records if r.state in {TaskState.FAILED, TaskState.PARTIAL, TaskState.INTERRUPTED}]
    count = len(waiting) + len(active)
    fraction = None
    if active:
        record = active[0]
        current = progress.get(record.spec.task_id, {})
        if current.get('attempt_id') != record.attempt_id:
            current = {}
        if record.state == TaskState.CANCEL_REQUESTED:
            text = '正在取消'
        elif record.state == TaskState.VALIDATING:
            text = '正在校验'
        elif current.get('phase') == 'processing':
            text = '正在处理'
        else:
            text = '下载中'
            percent = current.get('percent')
            if isinstance(percent, (int, float)) and math.isfinite(percent):
                percent = max(0, min(100, percent))
                fraction = percent / 100
                text += f' {percent:.0f}%'
        if waiting:
            text += f' · {len(waiting)} 个排队'
    elif waiting:
        text = f'{len(waiting)} 个任务排队中'
    else:
        text = ''
    if attention:
        text += (' · ' if text else '') + f'{len(attention)} 项需处理'
    if not text and any(r.state == TaskState.SUCCEEDED and r.completed_at is not None
                        and 0 <= now - r.completed_at < SUCCESS_LINGER for r in records):
        text, fraction = '已完成', 1
    return TaskSummary(text, count, fraction)
