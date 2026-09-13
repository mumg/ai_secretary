from __future__ import annotations

import asyncio
import hashlib
import json

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from improver.config import AppConfig
from improver.models import Device

log = structlog.get_logger()


class NotificationService:
    def __init__(self, config: AppConfig):
        self.config = config
        self._app = None

    def _initialize(self) -> bool:
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
        if not self._initialize():
            log.info("notification_skipped", reason="firebase_not_configured", type=event_type)
            return 0
        result = await session.execute(select(Device.fcm_token).where(Device.active.is_(True)))
        tokens = list(result.scalars())
        if not tokens:
            return 0

        from firebase_admin import messaging

        message = messaging.MulticastMessage(
            data={"type": event_type, "object_id": object_id},
            tokens=tokens,
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
        )
        return response.success_count
