from datetime import UTC, datetime, timedelta
from unittest import TestCase

from pydantic import ValidationError

from improver.enums import ComponentHealthStatus
from improver.models import ComponentStatus
from improver.schemas import ComponentStatusWrite
from improver.services.system_status import _read_persisted


NOW = datetime(2026, 9, 14, 12, tzinfo=UTC)


class ComponentStatusContractTests(TestCase):
    def test_routes_are_exposed_in_openapi(self) -> None:
        from improver.main import app

        paths = app.openapi()["paths"]
        self.assertIn("/api/v1/system/components/{component_id}", paths)
        self.assertIn("/api/v1/system/status", paths)

    def test_report_requires_numeric_metrics(self) -> None:
        with self.assertRaises(ValidationError):
            ComponentStatusWrite(
                label="Loader", status="OK", metrics={"authenticated": True}
            )

    def test_report_requires_timezone_for_explicit_timestamp(self) -> None:
        with self.assertRaises(ValidationError):
            ComponentStatusWrite(
                label="Loader",
                status="OK",
                observed_at=datetime(2026, 9, 14, 12),
            )

    def test_internal_component_type_cannot_be_spoofed(self) -> None:
        with self.assertRaises(ValidationError):
            ComponentStatusWrite(label="Fake worker", component_type="worker", status="OK")

    def test_expired_heartbeat_is_stale(self) -> None:
        row = ComponentStatus(
            id="tabs-loader",
            label="TABS",
            component_type="external_loader",
            status="OK",
            message=None,
            metrics={"loaded": 10},
            observed_at=NOW - timedelta(minutes=10),
            expires_at=NOW - timedelta(minutes=5),
        )

        result = _read_persisted(row, NOW)

        self.assertEqual(result.status, ComponentHealthStatus.STALE)
        self.assertIn("Heartbeat", result.message or "")
