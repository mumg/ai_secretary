from __future__ import annotations

import asyncio
import hashlib
import json
import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from improver.config import AppConfig
from improver.models import Device, Task

log = structlog.get_logger()


class NotificationService:
    def __init__(self, config: AppConfig):
        self.config = config
        self._app = None

    def _initialize(self) -> bool:
        if self.config.server.local_web_only:
            return False
        credentials_payload = self.config.notifications.firebase_credentials
        if not credentials_payload:
            return False
        if self._app is not None:
            return True
        import firebase_admin
        from firebase_admin import credentials

        fingerprint = hashlib.sha256(
            json.dumps(credentials_payload, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        app_name = f"improver-{fingerprint}"
        try:
            self._app = firebase_admin.get_app(app_name)
        except ValueError:
            self._app = firebase_admin.initialize_app(
                credentials.Certificate(credentials_payload),
                name=app_name,
            )
        return True

    async def send(self, session: AsyncSession, event_type: str, object_id: str) -> int:
        if self.config.server.local_web_only:
            return 0
        if not self._initialize():
            log.info("notification_skipped", reason="firebase_not_configured", type=event_type)
            return 0
        result = await session.execute(select(Device.fcm_token).where(Device.active.is_(True)))
        tokens = list(result.scalars())
        if not tokens:
            log.info("notification_skipped", reason="no_active_devices", type=event_type)
            return 0

        from firebase_admin import messaging

        data = {"type": event_type, "object_id": object_id}
        if event_type in {
            "NEW_TASK",
            "CRITICAL_TASK",
            "TASK_CONFIRMATION_REQUIRED",
            "TASK_REMINDER",
            "TASK_DUE_SOON",
            "TASK_OVERDUE",
            "TASK_POSSIBLY_COMPLETED",
        }:
            task = await session.get(Task, uuid.UUID(object_id))
            if task:
                data["task_title"] = task.title[:180]
                data["task_description"] = " ".join((task.description or "").split())[:360]
        message = messaging.MulticastMessage(
            data=data,
            tokens=tokens,
            android=messaging.AndroidConfig(priority="high"),
        )
        try:
            response = await asyncio.to_thread(
                messaging.send_each_for_multicast,
                message,
                app=self._app,
            )
        except Exception as exc:
            log.exception("notification_failed", type=event_type, error=str(exc))
            return 0
        log.info(
            "notification_sent",
            type=event_type,
            success=response.success_count,
            failure=response.failure_count,
            object_id=object_id,
        )
        for token, delivery in zip(tokens, response.responses, strict=True):
            if not delivery.success:
                log.warning(
                    "notification_delivery_failed",
                    type=event_type,
                    object_id=object_id,
                    token_fingerprint=hashlib.sha256(token.encode()).hexdigest()[:12],
                    error_type=type(delivery.exception).__name__,
                    error_code=getattr(delivery.exception, "code", None),
                )
        return response.success_count
