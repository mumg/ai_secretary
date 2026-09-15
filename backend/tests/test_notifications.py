import json
import uuid
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

from improver.config import AppConfig
from improver.models import Task
from improver.services.notifications import NotificationService


class NotificationTests(IsolatedAsyncioTestCase):
    async def test_task_push_contains_bounded_content_without_phone_api_fetch(self):
        task_id = uuid.uuid4()
        session = Mock()
        session.execute = AsyncMock(return_value=Mock(scalars=lambda: ["private-token"]))
        session.get = AsyncMock(
            return_value=Task(title="Задача" * 100, description="Текст\n" * 300)
        )
        service = NotificationService(AppConfig())
        response = SimpleNamespace(
            success_count=1, failure_count=0,
            responses=[SimpleNamespace(success=True, exception=None)],
        )
        with (
            patch.object(service, "_initialize", return_value=True),
            patch(
                "firebase_admin.messaging.send_each_for_multicast", return_value=response
            ) as send,
        ):
            self.assertEqual(await service.send(session, "NEW_TASK", str(task_id)), 1)
        message = send.call_args.args[0]
        self.assertEqual(message.android.priority, "high")
        self.assertEqual(message.data["object_id"], str(task_id))
        self.assertEqual(len(message.data["task_title"]), 180)
        self.assertLessEqual(len(message.data["task_description"]), 360)
        self.assertNotIn("\n", message.data["task_description"])
        self.assertLess(len(json.dumps(message.data, ensure_ascii=False).encode()), 4096)
        session.get.assert_awaited_once_with(Task, task_id)

    async def test_partial_failure_is_logged_without_token_or_exception_text(self):
        session = Mock()
        session.execute = AsyncMock(return_value=Mock(scalars=lambda: ["private-token"]))
        service = NotificationService(AppConfig())
        response = SimpleNamespace(
            success_count=0, failure_count=1,
            responses=[SimpleNamespace(success=False, exception=ValueError("private-token"))],
        )
        with (
            patch.object(service, "_initialize", return_value=True),
            patch("firebase_admin.messaging.send_each_for_multicast", return_value=response),
            patch("improver.services.notifications.log") as log,
        ):
            self.assertEqual(await service.send(session, "DAILY_PLAN_READY", "plan-id"), 0)
        self.assertEqual(log.warning.call_args.kwargs["error_type"], "ValueError")
        self.assertNotIn("private-token", str(log.mock_calls))

    async def test_no_devices_does_not_call_firebase(self):
        session = Mock()
        session.execute = AsyncMock(return_value=Mock(scalars=lambda: []))
        service = NotificationService(AppConfig())
        with (
            patch.object(service, "_initialize", return_value=True),
            patch("firebase_admin.messaging.send_each_for_multicast") as send,
        ):
            self.assertEqual(await service.send(session, "NEW_TASK", str(uuid.uuid4())), 0)
        send.assert_not_called()
