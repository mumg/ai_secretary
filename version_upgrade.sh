#!/usr/bin/env bash
# Run from the deployed Git checkout. Python is used only for Docker JSON and backup handling.
set -euo pipefail
upgrade_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec python3 - "$upgrade_dir" "$@" <<'PY'
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit


def run(*args, capture=False, **kwargs):
    return subprocess.run(args, check=True, text=capture,
                          stdout=subprocess.PIPE if capture else None, **kwargs)


def output(*args):
    return run(*args, capture=True).stdout.strip()


def fail(message):
    raise RuntimeError(message)


def announce(message):
    print(message, flush=True)


def compose_config(command):
    # Read definitions of already-running services even when their profile isn't the default.
    return json.loads(output(*command, "--profile", "*", "config", "--format", "json"))


def source_version():
    value = Path("version").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value):
        fail("Файл version должен содержать MAJOR.MINOR.PATCH.")
    return value


def main():
    parser = argparse.ArgumentParser(prog="version_upgrade.sh", description="Обновить существующую Docker Compose установку через git pull --ff-only.")
    parser.add_argument("--check", action="store_true", help="проверить установку и показать план без git pull и перезапуска")
    parser.add_argument("--dockerhub", action="store_true", help="скачать образы mumg/ai_secretary для полученного Git-коммита")
    parser.add_argument("--wait-timeout", type=int, default=180, help="таймаут готовности сервисов, секунд (по умолчанию 180)")
    args = parser.parse_args(sys.argv[2:])
    if args.wait_timeout < 10:
        fail("--wait-timeout должен быть не меньше 10 секунд.")
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    root = Path(sys.argv[1]).resolve()
    os.chdir(root)
    os.umask(0o077)
    for executable in ("git", "docker"):
        if not shutil.which(executable):
            fail(f"Не найден {executable}.")
    try:
        git_root = Path(output("git", "rev-parse", "--show-toplevel")).resolve()
    except subprocess.CalledProcessError:
        fail("Каталог установки не является Git-копией. Сначала выполните переход по инструкции README.")
    if git_root != root:
        fail("Скрипт должен находиться в корне Git-копии установки.")
    lock_path = Path(output("git", "rev-parse", "--git-path", "secretary-upgrade.lock"))
    lock = lock_path.open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fail("Обновление уже выполняется в другом процессе.")
    if output("git", "status", "--porcelain", "--untracked-files=no"):
        fail("Есть незакоммиченные изменения отслеживаемых файлов. Сохраните их перед обновлением.")
    upstream = output("git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    old_revision = output("git", "rev-parse", "HEAD")
    output("docker", "compose", "version")
    ids = output("docker", "ps", "-q", "--filter", "label=com.docker.compose.service=api").split()
    apis = json.loads(output("docker", "inspect", *ids)) if ids else []
    candidates = [c for c in apis if Path(c["Config"]["Labels"].get(
        "com.docker.compose.project.working_dir", "/missing")).resolve() == root]
    if len(candidates) != 1:
        fail("Не удалось однозначно найти запущенный API из этого каталога установки.")
    api = candidates[0]
    labels = api["Config"]["Labels"]
    project = labels["com.docker.compose.project"]
    files = [Path(p).resolve() for p in labels["com.docker.compose.project.config_files"].split(",")]
    registry_overlay = root / "compose.dockerhub.yaml"
    registry_mode = args.dockerhub or registry_overlay in files
    if registry_overlay in files:
        # Snapshot the configuration of the deployed tag before selecting the new one.
        deployed_image = api["Config"].get("Image", "")
        if not deployed_image.startswith("mumg/ai_secretary:"):
            fail("Не удалось определить установленный тег Docker Hub.")
        os.environ["SECRETARY_IMAGE_TAG"] = deployed_image.split(":", 1)[1]
    command = ["docker", "compose", "--project-directory", str(root), "-p", project]
    for path in files:
        command += ["-f", str(path)]
    before = compose_config(command)
    ids = output("docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={project}").split()
    containers = json.loads(output("docker", "inspect", *ids))
    running = {c["Config"]["Labels"].get("com.docker.compose.service"): c for c in containers
               if c["Config"]["Labels"].get("com.docker.compose.oneoff", "False").lower() == "false"}
    if registry_mode and "extensions" in running:
        command += ["--profile", "integrations"]
    if not {"api", "worker", "db"} <= running.keys():
        fail("Перед обновлением должны быть запущены api, worker и db.")
    services = [s for s in ("api", "worker", "document-parser", "proxy", "extensions") if s in running]
    writers = [s for s in ("proxy", "extensions", "worker", "api") if s in running]
    announce(f"Установка: {root}; проект: {project}; ветка обновления: {upstream}.")
    announce("Сервисы: " + ", ".join(services) + ". PostgreSQL и Docker volumes сохраняются.")
    if args.check:
        preparation = "загрузка образов Docker Hub" if registry_mode else "сборка"
        announce(f"Проверка завершена. План: git pull --ff-only → {preparation} → резервная копия → миграции → запуск.")
        return

    backup = root / ".upgrade-backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup.mkdir(parents=True, mode=0o700)
    (backup / "compose-before.json").write_text(json.dumps(before, indent=2), encoding="utf-8")
    (backup / "revision-before").write_text(old_revision + "\n", encoding="utf-8")
    # Keep a complete copy of local configuration (including ignored overrides and secrets).
    with tarfile.open(backup / "local-config.tar.gz", "w:gz") as archive:
        config_paths = [root / ".env", root / "secrets", root / "config", *files]
        for index, path in enumerate(dict.fromkeys(config_paths)):
            if path.exists():
                name = str(path.relative_to(root)) if path.is_relative_to(root) else f"external/{index}-{path.name}"
                archive.add(path, arcname=name)
    run("git", "archive", "--format=tar", f"--output={backup / 'sources-before.tar'}", old_revision)
    stopped = False
    migration_started = False
    try:
        announce("1/5 Получение исходников: git pull --ff-only")
        run("git", "pull", "--ff-only")
        version = source_version()
        revision = output("git", "rev-parse", "HEAD")
        if registry_mode:
            os.environ["SECRETARY_IMAGE_TAG"] = "sha-" + revision
            if registry_overlay not in files:
                command += ["-f", str(registry_overlay)]
        after = compose_config(command)
        for service in ("api", "worker", "db", "migrate", *services):
            if service not in after["services"]:
                fail(f"В новой Compose-конфигурации отсутствует {service}.")
        for service in ("db", "api", "worker", "migrate"):
            previous = before["services"][service]
            current = after["services"][service]
            if previous.get("volumes", []) != current.get("volumes", []):
                fail(f"Изменились подключения данных сервиса {service}; требуется ручное обновление.")
        if before.get("volumes", {}) != after.get("volumes", {}):
            fail("Изменились определения Docker volumes; требуется ручное обновление.")
        if any(before.get(key, {}) != after.get(key, {}) for key in ("networks", "secrets")):
            fail("Изменились сети или подключения секретов; требуется ручное обновление.")
        if before["services"]["db"] != after["services"]["db"]:
            fail("Изменилась конфигурация PostgreSQL; его обновление выполняется отдельно.")
        db_env = after["services"]["db"].get("environment", {})
        api_env = after["services"]["api"].get("environment", {})
        live_db_env = dict(item.split("=", 1) for item in running["db"]["Config"].get("Env", []))
        if any(live_db_env.get(key) != db_env.get(key) for key in ("POSTGRES_DB", "POSTGRES_USER")):
            fail("Настройки PostgreSQL на диске отличаются от запущенного контейнера.")
        live_api_env = dict(item.split("=", 1) for item in api["Config"].get("Env", []))
        if any(live_api_env.get(key) != api_env.get(key) for key in (
            "DATABASE_URL", "DATABASE_PASSWORD_FILE", "APP_MASTER_KEY_FILE", "DATA_DIR"
        )):
            fail("Подключения API на диске отличаются от запущенного контейнера.")
        db_url = urlsplit(api_env.get("DATABASE_URL", ""))
        if (db_url.hostname != "db" or db_url.port not in (None, 5432)
                or db_url.path.lstrip("/") != db_env.get("POSTGRES_DB", "improver")):
            fail("Скрипт поддерживает PostgreSQL сервиса db; для внешней базы нужен отдельный порядок резервного копирования.")
        for service in ("api", "worker", "migrate"):
            env = after["services"][service].get("environment", {})
            old_env = before["services"][service].get("environment", {})
            if env.get("DATABASE_URL") != api_env.get("DATABASE_URL") or any(
                old_env.get(key) != env.get(key)
                for key in ("DATABASE_URL", "DATABASE_PASSWORD_FILE", "APP_MASTER_KEY_FILE", "DATA_DIR")
            ):
                fail(f"Изменилось подключение к данным сервиса {service}; требуется ручная проверка.")
        if (not registry_mode and not after["services"]["api"].get("build")) or any(
            after["services"][s].get("image") != after["services"]["api"].get("image")
            for s in ("worker", "migrate")
        ):
            fail("api, worker и migrate должны использовать один образ.")
        data_dir = api_env.get("DATA_DIR", "/data")
        if not data_dir.startswith("/") or data_dir == "/":
            fail("Недопустимый DATA_DIR для резервного копирования.")
        stamp = backup.name.lower()
        rollback = {"services": {}}
        for service in services:
            tag = f"{project}-upgrade-backup:{stamp}-{service}"
            run("docker", "tag", running[service]["Image"], tag)
            rollback["services"][service] = {"image": tag, "pull_policy": "never"}
        (backup / "images-before.json").write_text(json.dumps(rollback, indent=2), encoding="utf-8")
        announce("2/5 Загрузка образов Docker Hub" if registry_mode else "2/5 Сборка новой версии до остановки приложения")
        builds = [s for s in services if s != "worker" and after["services"][s].get("build")]
        pulls = [s for s in services if not after["services"][s].get("build")]
        if builds:
            run(*command, "build", *builds)
        if pulls:
            run(*command, "pull", *pulls)
        if registry_mode:
            for service in ("api", "document-parser"):
                if service not in services:
                    continue
                image = after["services"][service]["image"]
                metadata = json.loads(output("docker", "image", "inspect", image))[0]
                image_labels = metadata["Config"].get("Labels") or {}
                if (image_labels.get("org.opencontainers.image.revision") != revision
                        or image_labels.get("org.opencontainers.image.version") != version):
                    fail(f"Образ {service} не соответствует полученным исходникам. Дождитесь успешной сборки GitHub Actions.")
        announce(f"3/5 Остановка приложения и резервное копирование в {backup}")
        stopped = True  # Also recover if stopping only some containers succeeds.
        run("docker", "stop", "--time", "60", *(running[s]["Id"] for s in writers))
        with (backup / "database.dump").open("wb") as stream:
            subprocess.run([*command, "exec", "-T", "db", "sh", "-ec",
                'export PGPASSWORD="$(cat "$POSTGRES_PASSWORD_FILE")"; '
                'exec pg_dump --format=custom --no-owner --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"'],
                stdout=stream, check=True)
        if not (backup / "database.dump").stat().st_size:
            fail("Резервная копия базы оказалась пустой.")
        with (backup / "app-data.tar.gz").open("wb") as stream:
            subprocess.run(["docker", "run", "--rm", "--network", "none", "--volumes-from",
                api["Id"] + ":ro", "--entrypoint", "tar", api["Image"], "-czf", "-", "-C", data_dir, "."],
                stdout=stream, check=True)
        announce("4/5 Применение миграций базы")
        migration_started = True
        run(*command, "run", "--rm", "--no-deps", "-T", "migrate", "alembic", "upgrade", "head")
        announce("5/5 Запуск сервисов и проверка готовности")
        run(*command, "up", "-d", "--no-deps", "--no-build", "--pull", "never", "--wait", "--wait-timeout", str(args.wait_timeout), *services)
        probe = (
            "import json,urllib.request; "
            "r=json.load(urllib.request.urlopen('http://127.0.0.1:8000/health/ready',timeout=5)); "
            "assert r['status']=='ready'; "
            "v=json.load(urllib.request.urlopen('http://127.0.0.1:8000/health/live',timeout=5)); "
            "assert v['version']==" + repr(version)
        )
        deadline = time.monotonic() + args.wait_timeout
        while True:
            result = subprocess.run([*command, "exec", "-T", "api", "python", "-c", probe],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if result.returncode == 0:
                break
            if time.monotonic() >= deadline:
                fail("API не подтвердил готовность базы и установленную версию.")
            time.sleep(2)
        (backup / "completed").write_text(version + "\n", encoding="utf-8")
        announce(f"Обновление до {version} завершено. Резервная копия: {backup}")
    except BaseException:
        if stopped and not migration_started:
            announce("Обновление остановлено до миграций. Запускаем прежние контейнеры.")
            subprocess.run(["docker", "start", *(running[s]["Id"] for s in writers)], check=False)
        elif migration_started:
            announce("Ошибка после начала миграций. Останавливаем приложение; автоматический откат базы не выполняется.")
            subprocess.run([*command, "stop", "--timeout", "60", *writers], check=False)
        announce(f"Материалы для восстановления: {backup}. См. README: «Обновление одной командой».")
        raise


try:
    main()
except (RuntimeError, OSError, subprocess.CalledProcessError, ValueError, KeyError) as exc:
    print(f"Обновление не выполнено: {exc}", file=sys.stderr)
    sys.exit(1)
except KeyboardInterrupt:
    print("Обновление прервано.", file=sys.stderr)
    sys.exit(130)
PY
