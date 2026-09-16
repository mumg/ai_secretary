import asyncio
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from improver.config import SourceConfig
from improver.services.mts_link import mts_link_reference_keys
from improver.services.source_links import (
    LinkSource,
    default_link_patterns,
    inject_source_link_rules,
    parse_source_link,
    source_references,
    validate_patterns,
)

PATTERN = r'^https://mts\.mts-link\.ru/j/MTC/(?P<meeting_id>\d+)(?:/[^?#]*)?(?:\?[^#]*)?(?:#.*)?$'


class ParsingTests(TestCase):
    def test_mts_defaults_apply_only_when_rules_are_missing(self):
        from improver.api.admin import _clean_source_settings

        default = SourceConfig(id="new", type="mts_link", enabled=False)
        self.assertEqual(default.link_patterns, [PATTERN])
        for source_type in ("imap", "exchange", "external_tasks"):
            self.assertEqual(SourceConfig(id="new", type=source_type, enabled=False).link_patterns, [])
        for patterns in ([], [r"^https://example\.test/(?P<meeting_id>\d+)$"]):
            saved = _clean_source_settings("mts_link", {"link_patterns": patterns})
            self.assertEqual(saved["link_patterns"], patterns)
            self.assertEqual(SourceConfig(id="mts", type="mts_link", enabled=False,
                                          **saved).link_patterns, patterns)
        self.assertEqual(_clean_source_settings("mts_link", {})["link_patterns"], [PATTERN])
        default.link_patterns.clear()
        self.assertEqual(default_link_patterns("mts_link"), [PATTERN])

    def test_user_urls_extract_event_id_not_session_suffix(self):
        source = LinkSource('work_meetings', 'mts_link', (PATTERN,))
        for suffix in ['', '/session/23248178199', '/sesssion/XXX', '?from=calendar#join']:
            self.assertEqual(parse_source_link('https://mts.mts-link.ru/j/MTC/23854886808'+suffix, (source,)),
                             [{'source_id': 'work_meetings', 'source_type': 'mts_link', 'meeting_id': '23854886808'}])
        self.assertEqual(parse_source_link('https://evil.test/j/MTC/23854886808', (source,)), [])
        self.assertEqual(parse_source_link('https://user:secret@mts.mts-link.ru/j/MTC/23854886808', (source,)), [])

    def test_validation_and_match_timeout(self):
        for patterns in [['['], ['https://example.test/(.*)'], ['x'*1025], [PATTERN]*21]:
            with self.assertRaises(ValueError):
                SourceConfig(id='mts', type='mts_link', enabled=False, link_patterns=patterns)
        expensive = LinkSource('test', 'external_tasks', (r'^https://example\.test/(?P<meeting_id>(a+)+)$',))
        self.assertEqual(parse_source_link('https://example.test/'+'a'*3900+'!', (expensive,)), [])

    def test_preview_uses_unsaved_rules_and_reports_invalid_patterns(self):
        from improver.api.admin import router
        app = FastAPI()
        app.include_router(router)
        with TestClient(app) as client:
            payload = {'source_id':'work_meetings','source_type':'mts_link','patterns':[PATTERN],
                       'url':'https://mts.mts-link.ru/j/MTC/23854886808/sesssion/XXX'}
            response=client.post('/admin/source-link-preview',json=payload)
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['matches'][0]['meeting_id'],'23854886808')
            response=client.post('/admin/source-link-preview',json={**payload,'patterns':['[']})
            self.assertEqual(response.status_code,422)


class InjectionTests(IsolatedAsyncioTestCase):
    async def test_api_save_keeps_explicit_overrides_and_returns_defaults_for_old_sources(self):
        from datetime import UTC, datetime

        from improver.api.admin import _save_source, _source_read
        from improver.models import CommunicationSource
        from improver.schemas import SourceWrite
        from improver.services.settings import source_config_from_record

        created = await _save_source(
            SourceWrite(id="fresh", label="MTS", source_type="mts_link", enabled=False),
            AsyncMock(spec=AsyncSession), None,
        )
        self.assertEqual(created.settings["link_patterns"], [PATTERN])
        self.assertEqual(source_config_from_record(created).link_patterns, [PATTERN])
        now = datetime.now(UTC)
        row = CommunicationSource(id="mts", label="MTS", source_type="mts_link", enabled=False,
                                  settings={}, tags=[], created_at=now, updated_at=now)
        self.assertEqual(_source_read(row).settings["link_patterns"], [PATTERN])
        self.assertEqual(source_config_from_record(row).link_patterns, [PATTERN])
        for patterns in ([], [r"^https://example\.test/(?P<meeting_id>\d+)$"]):
            row.settings = {"link_patterns": patterns}
            saved = await _save_source(SourceWrite(id="mts", label="MTS", source_type="mts_link",
                                                  enabled=False, settings={}), AsyncMock(spec=AsyncSession), row)
            self.assertEqual(saved.settings["link_patterns"], patterns)
            self.assertEqual(_source_read(saved).settings["link_patterns"], patterns)
            self.assertEqual(source_config_from_record(saved).link_patterns, patterns)
        cleared = await _save_source(SourceWrite(id="mts", label="MTS", source_type="mts_link",
                                                enabled=False, settings={"link_patterns": []}),
                                     AsyncMock(spec=AsyncSession), row)
        self.assertEqual(cleared.settings["link_patterns"], [])

    async def test_context_is_isolated_and_custom_rules_override_default_session_parser(self):
        url='https://mts.mts-link.ru/j/MTC/23854886808/session/23248178199'
        class Operation:
            def __init__(self, patterns):
                self.config=SimpleNamespace(communication_sources=SimpleNamespace(items=[
                    SimpleNamespace(id='mts',type='mts_link',link_patterns=patterns)]))
            @inject_source_link_rules
            async def parse(self):
                await asyncio.sleep(0)
                return mts_link_reference_keys(url), source_references(url)
        custom, default=await asyncio.gather(Operation([PATTERN]).parse(),Operation([]).parse())
        self.assertEqual(custom[0],['id:23854886808'])
        self.assertEqual(custom[1][0]['source_type'],'mts_link')
        self.assertIn('id:23248178199',default[0])
        self.assertEqual(default[1],[])
        self.assertIn('id:23248178199',mts_link_reference_keys(url))
        multiple = Operation([PATTERN])
        multiple.config.communication_sources.items.append(
            SimpleNamespace(id="second_mts", type="mts_link", link_patterns=[PATTERN])
        )
        self.assertEqual((await multiple.parse())[0], ["id:23854886808"])
