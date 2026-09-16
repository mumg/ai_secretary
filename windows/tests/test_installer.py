import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'windows/setup'))
import layout
import manage
spec = importlib.util.spec_from_file_location('windows_build', ROOT / 'windows/build.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class InstallerTests(unittest.TestCase):
    def test_defaults_bind_every_internal_component_to_loopback(self):
        options = layout.validate_options()
        env = layout.environment(Path('C:/ProgramData/AI Secretary'), options)
        self.assertEqual(env['LOCAL_WEB_ONLY'], 'true')
        self.assertEqual(env['PUBLIC_URL'], 'http://127.0.0.1:18000')
        for name in layout.SERVICES[:-1]:
            xml = ET.fromstring(layout.service_xml(Path('C:/Program Files/AI Secretary'), Path('C:/ProgramData/AI Secretary'), options, name))
            self.assertEqual(xml.findtext('startmode'), 'Automatic')
            self.assertEqual(xml.findtext('serviceaccount/user'), 'LocalService')
            self.assertNotIn('0.0.0.0', ET.tostring(xml, encoding='unicode'))
        self.assertEqual(env['DOCUMENT_PARSER_URL'], 'http://127.0.0.1:18080')

    def test_rejects_colliding_ports_and_configuration_injection(self):
        for changes in ({'api_port': 80}, {'api_port': 15432}, {'public_host': 'example.org { respond 200 }'},
                        {'public_host': 'https://example.org'}, {'llm_url': 'http://user:password@example.org'},
                        {'llm_url': 'file:///secret'}):
            with self.assertRaises(ValueError): layout.validate_options(**changes)

    def test_https_keeps_mtls_and_http_challenge(self):
        options = layout.validate_options(public_host='assistant.example.org')
        config = layout.caddy_config(Path('C:/ProgramData/AI Secretary'), options)
        self.assertIn('disable_tlsalpn_challenge', config)
        self.assertIn('require_and_verify', config)
        self.assertIn('reverse_proxy 127.0.0.1:18000', config)
        self.assertEqual(layout.environment(Path('data'), options)['LOCAL_WEB_ONLY'], 'false')

    def test_service_xml_escapes_paths(self):
        result = layout.service_xml(Path('C:/A & B'), Path('C:/Data & More'), layout.validate_options(), 'AISecretaryApi')
        parsed = ET.fromstring(result)
        self.assertIn('A & B', parsed.findtext('executable'))
        self.assertIn('&amp;', result)
        self.assertEqual([e.text for e in parsed.findall('depend')], ['AISecretaryDatabase', 'AISecretaryParser'])

    def test_certificates_are_password_protected_and_preserved(self):
        from cryptography.hazmat.primitives.serialization import pkcs12
        from cryptography.x509.oid import ExtendedKeyUsageOID
        from cryptography.x509 import ExtendedKeyUsage
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp); (data / 'certificates').mkdir()
            manage.create_certificates(data)
            blob = (data / 'certificates/client.p12').read_bytes()
            password = (data / 'certificates/client-password.txt').read_text().encode()
            key, certificate, chain = pkcs12.load_key_and_certificates(blob, password)
            self.assertIsNotNone(key)
            self.assertEqual(len(chain), 1)
            self.assertIn(ExtendedKeyUsageOID.CLIENT_AUTH, certificate.extensions.get_extension_for_class(ExtendedKeyUsage).value)
            with self.assertRaises(ValueError): pkcs12.load_key_and_certificates(blob, b'wrong')
            manage.create_certificates(data)
            self.assertEqual(blob, (data / 'certificates/client.p12').read_bytes())

    def test_archive_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'bad.zip'
            with zipfile.ZipFile(archive, 'w') as file: file.writestr('../outside.txt', 'bad')
            with self.assertRaises(ValueError): builder.extract(archive, Path(tmp) / 'output')
            self.assertFalse((Path(tmp) / 'outside.txt').exists())

    def test_download_hash_is_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'source'; source.write_bytes(b'corrupt download')
            with self.assertRaises(ValueError):
                builder.verified_download(dict(filename='runtime.zip', url=source.as_uri(), sha256='0'*64), Path(tmp) / 'cache')
            self.assertFalse((Path(tmp) / 'cache/runtime.zip').exists())

    def test_failed_backup_resumes_original_running_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'program'; root.mkdir()
            data = Path(tmp) / 'data'; data.mkdir()
            (data / 'connection.json').write_text(json.dumps(layout.validate_options()))
            running = {n: 'running' for n in layout.SERVICES[:-1]}
            def status(name): return running.get(name, 'missing')
            def stop(_root, name):
                if name in running: running[name] = 'stopped'
            def wrapper(_root, name, command):
                if command == 'start': running[name] = 'running'
            with patch.object(manage, 'state', status), patch.object(manage, 'stop', stop), patch.object(manage, 'wrapper', wrapper), \
                 patch.object(manage, 'wait_database'), patch.object(manage, 'database_env', return_value={}), \
                 patch.object(manage, 'run', side_effect=RuntimeError('dump failed')):
                with self.assertRaises(RuntimeError): manage.prepare(root, data, '0.1.0')
            self.assertTrue(all(s == 'running' for s in running.values()))

    def test_downgrade_refused_before_stopping(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            (data / 'connection.json').write_text(json.dumps(layout.validate_options()))
            (data / 'installed.json').write_text(json.dumps({'version': '1.0.0'}))
            with patch.object(manage, 'stop') as stop:
                with self.assertRaises(RuntimeError): manage.prepare(data, data, '0.1.0')
                stop.assert_not_called()


if __name__ == '__main__': unittest.main()
