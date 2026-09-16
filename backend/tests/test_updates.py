import asyncio
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import httpx

from improver import __version__
from improver.main import app
from improver.services.updates import VERSION_URL, UpdateChecker, version_number


class UpdateTests(IsolatedAsyncioTestCase):
    async def test_numeric_versions_and_source_version(self):
        self.assertGreater(version_number("0.10.0"), version_number("0.9.9"))
        self.assertEqual(len(version_number(__version__)), 3)
        for bad in ("", "01.2.3", "1.2", "1.2.3-rc1", "<html>1.2.3</html>", "1.2.3\n1.2.4"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                version_number(bad)

    async def test_cache_concurrency_and_latest_version_comparison(self):
        requests = []

        async def respond(request):
            requests.append(request)
            await asyncio.sleep(0)
            return httpx.Response(200, text="0.10.0\n")

        checker = UpdateChecker("0.9.9")
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            results = await asyncio.gather(*(checker.check(client=client) for _ in range(5)))
            await checker.check(manual=True, client=client)
        self.assertEqual(len(requests), 1)
        self.assertEqual(str(requests[0].url), VERSION_URL)
        self.assertTrue(all(result.update_available for result in results))
        self.assertEqual(results[0].latest_version, "0.10.0")
        self.assertEqual((results[0].next_check_at - results[0].checked_at).total_seconds(), 21600)
        for current in ("0.10.0", "1.0.0"):
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                result = await UpdateChecker(current).check(client=client)
                self.assertFalse(result.update_available)

    async def test_unavailable_invalid_and_timeout_responses_never_claim_current(self):
        def timeout(request):
            raise httpx.ReadTimeout("timeout", request=request)

        responses = [
            lambda _: httpx.Response(404), lambda _: httpx.Response(503), timeout,
            lambda _: httpx.Response(200, text="<html>error</html>"),
            lambda _: httpx.Response(200, text="1.2.3" + " " * 128),
            lambda _: httpx.Response(200, content=b"\xff"),
            lambda _: httpx.Response(302, headers={"Location": "https://other.test/version"}),
        ]
        for handler in responses:
            with self.subTest(handler=handler):
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    result = await UpdateChecker("0.1.0").check(client=client)
                self.assertTrue(result.error)
                self.assertFalse(result.update_available)
                self.assertIsNone(result.latest_version)
                self.assertIsNone(result.last_success_at)
                self.assertEqual((result.next_check_at - result.checked_at).total_seconds(), 900)

    async def test_failure_preserves_last_confirmed_update(self):
        checker = UpdateChecker("0.1.0")
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, text="0.2.0")
        )) as client:
            success = await checker.check(client=client)
        checker._manual_due = 0
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(503)
        )) as client:
            failed = await checker.check(manual=True, client=client)
        self.assertTrue(failed.update_available)
        self.assertTrue(failed.error)
        self.assertEqual(failed.latest_version, "0.2.0")
        self.assertEqual(failed.last_success_at, success.last_success_at)

    async def test_api_is_cached_and_manual_check_uses_server(self):
        checker = UpdateChecker("0.1.0")
        with patch("improver.api.system_status.update_checker", checker), patch.object(
            checker, "_fetch", return_value="0.2.0"
        ) as fetch:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                initial = await client.get("/api/v1/system/version")
                self.assertEqual(initial.status_code, 200)
                self.assertIsNone(initial.json()["checked_at"])
                fetch.assert_not_called()
                result = await client.post("/api/v1/system/version/check")
                self.assertTrue(result.json()["update_available"])
                self.assertEqual(result.headers["cache-control"], "no-store")
                await client.post("/api/v1/system/version/check")
                fetch.assert_awaited_once()
