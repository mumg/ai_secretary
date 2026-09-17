"""Elevated Windows setup/upgrade helper. Never prints credentials."""
from __future__ import annotations

import argparse
import ctypes
import csv
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile

from layout import SERVICES, caddy_config, environment, service_xml, validate_options


def run(*args, env=None, input=None):
    result = subprocess.run([str(arg) for arg in args], env=env, input=input, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
    if result.returncode:
        detail = "SQL command failed" if input is not None else result.stderr.strip()
        for key, value in (env or {}).items():
            if value and any(word in key.upper() for word in ("PASSWORD", "TOKEN", "SECRET")):
                detail = detail.replace(value, "[redacted]")
        raise RuntimeError(f"{Path(args[0]).name} завершился с кодом {result.returncode}: {detail[:800]}")
    return result.stdout


def state(name):
    result = subprocess.run(["sc.exe", "query", name], capture_output=True, text=True)
    if result.returncode == 1060:
        return "missing"
    if result.returncode:
        raise RuntimeError(f"Не удалось проверить службу {name}: {result.returncode}")
    return "stopped" if "STOPPED" in result.stdout else "running"


def wrapper(root, name, command):
    run(root / "services" / f"{name}.exe", command)


def stop(root, name):
    if state(name) == "running":
        wrapper(root, name, "stop")
        deadline = time.monotonic() + 90
        while state(name) == "running":
            if time.monotonic() > deadline:
                raise RuntimeError(f"Не удалось остановить {name}")
            time.sleep(1)


def secure_directory(data):
    data.mkdir(parents=True, exist_ok=True)
    # Use SIDs so this works on Russian and English Windows alike.
    run("icacls.exe", data, "/inheritance:r", "/grant:r", "*S-1-5-18:(OI)(CI)F",
        "*S-1-5-32-544:(OI)(CI)F", "*S-1-5-19:(OI)(CI)M")


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_options(data):
    return validate_options(**read_json(data / "connection.json"))


def version_tuple(value):
    values = value.strip().split(".")
    if len(values) != 3 or not all(p.isdecimal() for p in values):
        raise ValueError("Некорректный номер версии")
    return tuple(map(int, values))


def wait_url(url, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return json.load(response)
        except (OSError, ValueError):
            time.sleep(1)
    raise RuntimeError(f"Компонент не готов: {url}. Проверьте папку logs.")


def database_env(data):
    return {**os.environ, "PGPASSWORD": (data / "secrets" / "postgres-admin-password").read_text().strip()}


def sql(root, data, options, statement, database="postgres"):
    return run(root / "postgres" / "bin" / "psql.exe", "-X", "-v", "ON_ERROR_STOP=1",
               "-h", "127.0.0.1", "-p", options["database_port"], "-U", "postgres", "-d", database,
               "-t", "-A", env=database_env(data), input=statement)


def wait_database(root, data, options):
    for _ in range(90):
        try:
            sql(root, data, options, "SELECT 1;")
            return
        except (RuntimeError, OSError):
            time.sleep(1)
    raise RuntimeError("PostgreSQL не запустился. Проверьте журнал AISecretaryDatabase.")


def create_certificates(data):
    directory = data / "certificates"
    if (directory / "client-ca.pem").is_file():
        if not all((directory / name).is_file() for name in ("client-ca.key", "client.p12", "client-password.txt")):
            raise RuntimeError("Неполный комплект сертификатов. Восстановите его из резервной копии.")
        return
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AI Secretary Client CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
          .not_valid_after(now + timedelta(days=3650)).add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    certificate = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AI Secretary client")]))
                   .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=730))
                   .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
                   .sign(ca_key, hashes.SHA256()))
    password = secrets.token_urlsafe(24)
    (directory / "client-ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (directory / "client-ca.key").write_bytes(ca_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    (directory / "client.p12").write_bytes(pkcs12.serialize_key_and_certificates(b"AI Secretary", key, certificate, [ca], serialization.BestAvailableEncryption(password.encode())))
    (directory / "client-password.txt").write_text(password, encoding="utf-8")


def configure(root, data, args):
    # Fail before creating a database or changing services if the OS cannot load a dependency.
    run(root / "python/python.exe", "-B", root / "setup/check_runtime.py", "--root", root)
    secure_directory(data)
    fresh = not (data / "connection.json").exists()
    options = validate_options(args.api_port, args.parser_port, args.database_port, args.public_host, args.llm_url) if fresh else read_options(data)
    for name in ("secrets", "data", "logs", "certificates", "caddy", "backups"):
        (data / name).mkdir(exist_ok=True)
    if fresh:
        for port in (options["api_port"], options["parser_port"], options["database_port"]):
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", port))
        write_json(data / "connection.json", options)
    for filename in ("master-key", "database-password", "postgres-admin-password"):
        path = data / "secrets" / filename
        if not path.exists():
            if (data / "postgres" / "PG_VERSION").exists():
                raise RuntimeError("Отсутствует ключ или пароль существующей установки. Восстановите secrets из копии.")
            path.write_text(secrets.token_urlsafe(48), encoding="utf-8")
    active = list(SERVICES if options["public_host"] else SERVICES[:-1])
    services_dir = root / "services"
    services_dir.mkdir(exist_ok=True)
    for name in reversed(SERVICES):
        stop(root, name)
    for name in active:
        if state(name) != "missing":
            wrapper(root, name, "uninstall")
        shutil.copyfile(root / "vendor" / "WinSW.exe", services_dir / f"{name}.exe")
        (services_dir / f"{name}.xml").write_text(service_xml(root, data, options, name), encoding="utf-8")
    if not (data / "postgres" / "PG_VERSION").exists():
        # initdb re-executes with Administrators disabled in its restricted token.
        # Temporarily grant the installing user's SID, then revoke it after bootstrap.
        user_sid = next(csv.reader(run("whoami.exe", "/user", "/fo", "csv", "/nh").splitlines()))[1]
        temporary_user = user_sid not in {"S-1-5-18", "S-1-5-19"}
        if temporary_user:
            run("icacls.exe", data, "/grant:r", f"*{user_sid}:(OI)(CI)M")
        try:
            run(root / "postgres" / "bin" / "initdb.exe", "-D", data / "postgres", "-U", "postgres",
                "--pwfile", data / "secrets" / "postgres-admin-password", "--auth=scram-sha-256", "--encoding=UTF8", "--locale=C")
            run("icacls.exe", data / "postgres", "/grant", "*S-1-5-19:(OI)(CI)M", "/T", "/Q")
        finally:
            if temporary_user:
                run("icacls.exe", data, "/remove:g", f"*{user_sid}", "/T", "/Q")
    elif (data / "postgres" / "PG_VERSION").read_text().strip() != "17":
        raise RuntimeError("Версия PostgreSQL требует отдельной миграции; каталог базы не изменён.")
    for name in active:
        wrapper(root, name, "install")
    wrapper(root, "AISecretaryDatabase", "start")
    wait_database(root, data, options)
    if sql(root, data, options, "SELECT 1 FROM pg_roles WHERE rolname='improver';").strip() != "1":
        password = (data / "secrets" / "database-password").read_text().strip().replace("'", "''")
        sql(root, data, options, f"CREATE ROLE improver LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '{password}';")
    if sql(root, data, options, "SELECT 1 FROM pg_database WHERE datname='improver';").strip() != "1":
        sql(root, data, options, "CREATE DATABASE improver OWNER improver;")
    write_json(data / "upgrade-state.json", {"phase": "migrating"})
    run(root / "python" / "python.exe", "-m", "alembic", "-c", root / "backend" / "alembic.ini", "upgrade", "head",
        env={**os.environ, **environment(data, options)})
    wrapper(root, "AISecretaryParser", "start")
    wait_url(f"http://127.0.0.1:{options['parser_port']}/health")
    wrapper(root, "AISecretaryApi", "start")
    wait_url(f"http://127.0.0.1:{options['api_port']}/health/ready")
    if options["public_host"]:
        create_certificates(data)
        (data / "Caddyfile").write_text(caddy_config(data, options), encoding="utf-8")
        run(root / "caddy" / "caddy.exe", "validate", "--config", data / "Caddyfile", "--adapter", "caddyfile")
        for port in (80, 443):
            rule = f"AISecretary-HTTPS-{port}"
            subprocess.run(["netsh.exe", "advfirewall", "firewall", "delete", "rule", f"name={rule}"], capture_output=True)
            run("netsh.exe", "advfirewall", "firewall", "add", "rule", f"name={rule}", "dir=in", "action=allow", "protocol=TCP", f"localport={port}", f"program={root / 'caddy' / 'caddy.exe'}")
        wrapper(root, "AISecretaryProxy", "start")
    wrapper(root, "AISecretaryWorker", "start")
    version = (root / "version").read_text().strip()
    write_json(data / "installed.json", {"version": version, "root": str(root), "services": active})
    write_json(data / "upgrade-state.json", {"phase": "complete", "version": version})
    for filename, path in (("Open.url", "/app"), ("Settings.url", "/admin")):
        (root / filename).write_text(f"[InternetShortcut]\nURL=http://127.0.0.1:{options['api_port']}{path}\n", encoding="utf-8")
    print("Службы установлены. Настройте источники и модель в панели администратора.")


def prepare(root, data, target_version):
    if not (data / "connection.json").exists():
        return
    options = read_options(data)
    if (data / "installed.json").exists() and version_tuple(target_version) < version_tuple(read_json(data / "installed.json")["version"]):
        raise RuntimeError("Установка более старой версии запрещена.")
    running = [name for name in SERVICES if state(name) == "running"]
    backup = data / "backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup.mkdir(parents=True)
    try:
        for name in reversed(SERVICES[1:]):
            stop(root, name)
        if state("AISecretaryDatabase") != "running":
            wrapper(root, "AISecretaryDatabase", "start")
        wait_database(root, data, options)
        run(root / "postgres" / "bin" / "pg_dump.exe", "-h", "127.0.0.1", "-p", options["database_port"],
            "-U", "postgres", "-d", "improver", "--format=custom", "--no-owner", "--no-acl", "-f", backup / "database.dump", env=database_env(data))
        for name in ("connection.json", "installed.json", "secrets", "certificates", "data", "Caddyfile", "caddy"):
            source = data / name
            if source.is_dir():
                shutil.copytree(source, backup / name)
            elif source.is_file():
                shutil.copyfile(source, backup / name)
        with zipfile.ZipFile(backup / "program.zip", "w", zipfile.ZIP_STORED) as archive:
            for path in root.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(root))
        stop(root, "AISecretaryDatabase")
        write_json(data / "upgrade-state.json", {"phase": "prepared", "backup": str(backup)})
    except Exception:
        # No executable/schema has changed yet: resume the previous installation.
        for name in running:
            if state(name) != "running":
                wrapper(root, name, "start")
        raise
    print("Резервная копия создана:", backup)


def remove(root, data):
    for name in reversed(SERVICES):
        if state(name) != "missing":
            stop(root, name)
            wrapper(root, name, "uninstall")
    for port in (80, 443):
        subprocess.run(["netsh.exe", "advfirewall", "firewall", "delete", "rule", f"name=AISecretary-HTTPS-{port}"], capture_output=True)
    print("Службы удалены. База, настройки и резервные копии сохранены в", data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["configure", "prepare", "remove", "export-client"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--target-version", default="0.1.0")
    parser.add_argument("--api-port", type=int, default=18000)
    parser.add_argument("--parser-port", type=int, default=18080)
    parser.add_argument("--database-port", type=int, default=15432)
    parser.add_argument("--public-host", default="")
    parser.add_argument("--llm-url", default="http://127.0.0.1:11434")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if os.name != "nt" or not ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError("Запустите установку с правами администратора Windows.")
    secure_directory(args.data)
    (args.data / "logs").mkdir(exist_ok=True)
    with (args.data / "logs" / "installer.log").open("a", encoding="utf-8") as log:
        with redirect_stdout(log), redirect_stderr(log):
            print(datetime.now(timezone.utc).isoformat(), args.command, flush=True)
            return execute(args)


def execute(args):
    os.chdir(args.root / "backend")
    try:
        if args.command == "configure":
            configure(args.root, args.data, args)
        elif args.command == "prepare":
            prepare(args.root, args.data, args.target_version)
        elif args.command == "remove":
            remove(args.root, args.data)
        else:
            if not args.output:
                raise ValueError("Нужен каталог --output для экспорта сертификата")
            args.output.mkdir(parents=True, exist_ok=True)
            for name in ("client.p12", "client-password.txt"):
                shutil.copyfile(args.data / "certificates" / name, args.output / name)
            print("Сертификат и пароль сохранены в", args.output)
    except Exception as error:
        if args.command == "configure":
            # Never expose a partially migrated/configured application.
            for name in reversed(SERVICES[1:]):
                try:
                    stop(args.root, name)
                    if state(name) != "missing":
                        run("sc.exe", "config", name, "start=", "demand")
                except Exception:
                    pass
        print("Ошибка установки:", str(error), file=sys.stderr)
        print("Данные сохранены в", args.data, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
