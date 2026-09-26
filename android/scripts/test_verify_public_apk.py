import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile


spec = importlib.util.spec_from_file_location("verify_public_apk", Path(__file__).with_name("verify_public_apk.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PublicAPKTests(unittest.TestCase):
    def verify_entries(self, entries):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "release.apk"
            with zipfile.ZipFile(path, "w") as archive:
                for name, contents in entries.items():
                    archive.writestr(name, contents)
            module.verify_public_apk(path)

    def test_allows_public_certificate(self):
        self.verify_entries({"res/root.pem": b"-----BEGIN CERTIFICATE-----\ndGVzdA==\n-----END CERTIFICATE-----\n"})

    def test_rejects_private_containers_and_keys(self):
        for suffix in module.PRIVATE_SUFFIXES:
            with self.subTest(suffix=suffix), self.assertRaisesRegex(ValueError, "Private identity"):
                self.verify_entries({"assets/client" + suffix: b"secret"})

    def test_rejects_private_or_mixed_pem(self):
        certificate = b"-----BEGIN CERTIFICATE-----\ndGVzdA==\n-----END CERTIFICATE-----\n"
        private = b"-----BEGIN PRIVATE KEY-----\ndGVzdA==\n-----END PRIVATE KEY-----\n"
        for contents in (private, certificate + private, b"not a certificate"):
            with self.subTest(contents=contents[:32]), self.assertRaisesRegex(ValueError, "Private or invalid PEM"):
                self.verify_entries({"assets/client.pem": contents})


if __name__ == "__main__":
    unittest.main()
