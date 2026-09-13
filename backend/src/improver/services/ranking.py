from __future__ import annotations

from datetime import datetime

from improver.enums import TaskPriority, TaskStatus
from improver.models import Task

PRIORITY_WEIGHTS = {
    TaskPriority.LOW: 100.0,
    TaskPriority.NORMAL: 200.0,
    TaskPriority.HIGH: 300.0,
    TaskPriority.CRITICAL: 400.0,
}


def rank_task(task: Task, now: datetime) -> tuple[float, list[str]]:
    priority = TaskPriority(task.priority)
    score = PRIORITY_WEIGHTS[priority]
    reasons = [f"приоритет: {priority.value.lower()}"]

    if task.due_at is None:
        reasons.append("срок не указан")
    else:
        due = task.due_at
        if due.tzinfo is None:
            due = due.replace(tzinfo=now.tzinfo)
        seconds = (due - now).total_seconds()
        hours = seconds / 3600
        if seconds < 0:
            overdue_days = min(abs(seconds) / 86_400, 30)
            score += 600 + overdue_days * 10
            reasons.append("задача просрочена")
        elif hours <= 2:
            score += 500
            reasons.append("до срока меньше двух часов")
        elif hours <= 8:
            score += 420
            reasons.append("срок сегодня")
        elif hours <= 24:
            score += 340
            reasons.append("срок в течение суток")
        elif hours <= 72:
            score += 240
            reasons.append("срок в ближайшие три дня")
        elif hours <= 168:
            score += 140
            reasons.append("срок в течение недели")

    if task.status == TaskStatus.POSSIBLY_COMPLETED:
        score -= 50
        reasons.append("ожидает подтверждения выполнения")
    elif task.status == TaskStatus.IN_PROGRESS:
        score += 20
        reasons.append("задача в работе")

    return round(score, 2), reasons


def task_sort_key(task: Task) -> tuple[float, datetime, str]:
    due = task.due_at or datetime.max.replace(tzinfo=None)
    if due.tzinfo:
        due = due.replace(tzinfo=None)
    return (-task.ranking_score, due, str(task.id))
