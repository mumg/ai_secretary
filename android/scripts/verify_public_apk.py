#!/usr/bin/env python3
"""Reject private identities while allowing public certificate PEM resources."""

import re
import sys
import zipfile
from pathlib import Path


CERTIFICATE_CHAIN = re.compile(
    rb"\s*(?:-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\r\n]+-----END CERTIFICATE-----\s*)+"
)
PRIVATE_SUFFIXES = (".p12", ".pfx", ".jks", ".keystore", ".key")


def verify_public_apk(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            lower = name.lower()
            if lower.endswith(PRIVATE_SUFFIXES):
                raise ValueError(f"Private identity in APK: {name}")
            if lower.endswith(".pem") and not CERTIFICATE_CHAIN.fullmatch(archive.read(name)):
                raise ValueError(f"Private or invalid PEM in APK: {name}")


if __name__ == "__main__":
    verify_public_apk(Path(sys.argv[1]))
    print("APK contains no private identity")
