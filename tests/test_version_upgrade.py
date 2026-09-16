"""Exercise the actual updater and git pull against disposable repositories, without Docker."""
import json
import fcntl
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOCKER = r'''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys
a = sys.argv[1:]
root = pathlib.Path(os.environ['UPGRADE_TEST_ROOT'])
with (root.parent / 'docker.log').open('a') as f:
    f.write(json.dumps({'args': a, 'version': (root / 'version').read_text().strip()}) + '\n')
fail = os.environ.get('UPGRADE_TEST_FAIL')
app_env = {'DATABASE_URL': 'postgresql+asyncpg://improver@db:5432/improver',
           'DATA_DIR': '/data', 'DATABASE_PASSWORD_FILE': '/run/secrets/postgres_password',
           'APP_MASTER_KEY_FILE': '/run/secrets/app_master_key'}
db_env = {'POSTGRES_DB': 'improver', 'POSTGRES_USER': 'improver',
          'POSTGRES_PASSWORD_FILE': '/run/secrets/postgres_password'}
names = ['api', 'worker', 'db', 'document-parser']
if os.environ.get('UPGRADE_TEST_PROXY'): names.append('proxy')
if a[0] == 'ps':
    print('api-id' if 'label=com.docker.compose.service=api' in a else '\n'.join(s+'-id' for s in names))
elif a[0] == 'inspect':
    result = []
    for ident in a[1:]:
        name = ident.removesuffix('-id')
        result.append({'Id': ident, 'Image': 'sha256:old-'+name, 'Config': {
            'Image': 'mumg/ai_secretary:0.1.0' if os.environ.get('UPGRADE_TEST_HUB') else 'backend:local',
            'Env': [k+'='+v for k,v in (db_env if name=='db' else app_env).items()],
            'Labels': {'com.docker.compose.project': 'test_secretary',
            'com.docker.compose.project.working_dir': str(root),
            'com.docker.compose.project.config_files': str(root/'compose.yaml')+','+str(root/'compose.local-web.yaml')+(','+str(root/'compose.dockerhub.yaml') if os.environ.get('UPGRADE_TEST_HUB') else ''),
            'com.docker.compose.service': name, 'com.docker.compose.oneoff': 'False'}}})
    print(json.dumps(result))
elif a[0] == 'compose':
    if a[1:] == ['version']: print('Docker Compose v2.40.3')
    elif 'config' in a:
        services = {s: {'image': 'backend:local', 'build': {'context': str(root)},
                       'environment': app_env, 'volumes': [{'type': 'volume', 'source': 'app_data', 'target': '/data'}]}
                    for s in ['api','worker','migrate']}
        services['db'] = {'image': 'postgres:17', 'environment': db_env}
        services['document-parser'] = {'build': {'context': str(root/'document-parser')}}
        services['proxy'] = {'image': 'caddy:2'}
        if str(root/'compose.dockerhub.yaml') in a:
            tag = os.environ.get('SECRETARY_IMAGE_TAG', 'latest')
            for s in ['api','worker','migrate','document-parser']:
                services[s].pop('build', None)
                services[s]['image'] = 'mumg/ai_secretary:'+tag+('-document-parser' if s=='document-parser' else '')
        print(json.dumps({'services': services, 'volumes': {'app_data': {'name':'test_secretary_app_data'}}}))
    elif 'build' in a and fail == 'build': sys.exit(42)
    elif 'exec' in a and 'db' in a:
        if fail == 'dump': sys.exit(42)
        sys.stdout.buffer.write(b'PGDMPsynthetic')
    elif 'run' in a and 'migrate' in a and fail == 'migrate': sys.exit(42)
    elif 'up' in a and fail == 'up': sys.exit(42)
    elif 'pull' in a and fail == 'pull': sys.exit(42)
elif a[:2] == ['image', 'inspect']:
    revision = subprocess.check_output(['git','rev-parse','HEAD'], cwd=root, text=True).strip()
    if fail == 'revision': revision = 'wrong-revision'
    print(json.dumps([{'Config': {'Labels': {'org.opencontainers.image.revision': revision,
        'org.opencontainers.image.version': (root/'version').read_text().strip()}}}]))
elif a[0] == 'run': sys.stdout.buffer.write(b'synthetic app data')
'''


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.origin = self.base / 'remote.git'
        self.author = self.base / 'author'
        self.install = self.base / 'install'
        self.git('init', '--bare', str(self.origin))
        self.git('clone', str(self.origin), str(self.author))
        self.git('-C', str(self.author), 'checkout', '-b', 'main')
        shutil.copy2(ROOT / 'version_upgrade.sh', self.author)
        for name, value in {'version': '0.1.0\n', 'compose.yaml': 'name: test_secretary\n',
                            'compose.local-web.yaml': 'services: {}\n',
                            'compose.dockerhub.yaml': 'services: {}\n',
                            '.gitignore': '.env\nsecrets/\n.upgrade-backups/\n'}.items():
            (self.author / name).write_text(value)
        self.commit('initial')
        self.git('-C', str(self.author), 'push', '-u', 'origin', 'main')
        self.git('clone', '-b', 'main', str(self.origin), str(self.install))
        (self.author / 'version').write_text('0.1.1\n')
        self.commit('release')
        self.git('-C', str(self.author), 'push')
        (self.install / '.env').write_text('LOCAL_SETTING=preserve\n')
        (self.install / 'secrets').mkdir()
        (self.install / 'secrets' / 'app_master_key').write_text('synthetic-key')
        bindir = self.base / 'bin'
        bindir.mkdir()
        (bindir / 'docker').write_text(DOCKER)
        (bindir / 'docker').chmod(0o755)
        self.env = {**os.environ, 'PATH': str(bindir)+os.pathsep+os.environ['PATH'],
                    'UPGRADE_TEST_ROOT': str(self.install)}

    def git(self, *args):
        return subprocess.run(['git', '-c', 'user.name=Upgrade Test', '-c', 'user.email=test@example.test', *args],
                              check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, message):
        self.git('-C', str(self.author), 'add', '.')
        self.git('-C', str(self.author), 'commit', '-m', message)

    def execute(self, *args, fail=None):
        env = {**self.env}
        if fail: env['UPGRADE_TEST_FAIL'] = fail
        return subprocess.run(['bash', str(self.install/'version_upgrade.sh'), *args],
                              env=env, capture_output=True, text=True, timeout=20)

    def commands(self):
        log = self.base / 'docker.log'
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def test_pull_first_then_backup_migrate_start_and_preserve_settings(self):
        self.env['UPGRADE_TEST_PROXY'] = '1'
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.install/'version').read_text().strip(), '0.1.1')
        commands = self.commands()
        build = next(i for i,c in enumerate(commands) if 'build' in c['args'])
        dump = next(i for i,c in enumerate(commands) if 'exec' in c['args'] and 'db' in c['args'])
        migrate = next(i for i,c in enumerate(commands) if 'migrate' in c['args'])
        up = next(i for i,c in enumerate(commands) if 'up' in c['args'])
        self.assertEqual(commands[build]['version'], '0.1.1')
        self.assertLess(build, dump)
        self.assertLess(dump, migrate)
        self.assertLess(migrate, up)
        self.assertIn('proxy', commands[up]['args'])
        self.assertIn(str(self.install/'compose.local-web.yaml'), commands[up]['args'])
        self.assertNotIn('db', commands[up]['args'])
        self.assertFalse(any('down' in c['args'] for c in commands))
        self.assertEqual((self.install/'.env').read_text(), 'LOCAL_SETTING=preserve\n')
        self.assertEqual((self.install/'secrets/app_master_key').read_text(), 'synthetic-key')
        backup = next((self.install/'.upgrade-backups').iterdir())
        self.assertTrue((backup/'completed').exists())
        for name in ['database.dump','app-data.tar.gz','local-config.tar.gz','sources-before.tar','images-before.json']:
            self.assertGreater((backup/name).stat().st_size, 0)

    def test_check_does_not_pull_or_stop(self):
        result = self.execute('--check')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.install/'version').read_text().strip(), '0.1.0')
        self.assertFalse(any('stop' in c['args'] or 'build' in c['args'] for c in self.commands()))
        self.assertFalse((self.install/'.upgrade-backups').exists())

    def test_dockerhub_pulls_exact_commit_without_building(self):
        result = self.execute('--dockerhub')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        commands = self.commands()
        revision = self.git('-C', str(self.install), 'rev-parse', 'HEAD')
        inspections = [c['args'] for c in commands if c['args'][:2] == ['image', 'inspect']]
        self.assertIn(['image', 'inspect', 'mumg/ai_secretary:sha-'+revision], inspections)
        self.assertFalse(any('build' in c['args'] for c in commands))
        self.assertTrue(any('pull' in c['args'] for c in commands))

    def test_existing_dockerhub_install_keeps_registry_mode(self):
        self.env['UPGRADE_TEST_HUB'] = '1'
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(any('build' in c['args'] for c in self.commands()))

    def test_missing_registry_image_keeps_application_running(self):
        result = self.execute('--dockerhub', fail='pull')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any('stop' in c['args'] for c in self.commands()))

    def test_wrong_registry_revision_keeps_application_running(self):
        result = self.execute('--dockerhub', fail='revision')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any('stop' in c['args'] for c in self.commands()))

    def test_dirty_tree_stops_before_docker(self):
        (self.install/'version').write_text('user change')
        result = self.execute()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.commands(), [])
        self.assertEqual((self.install/'version').read_text(), 'user change')

    def test_non_git_install_stops_before_docker(self):
        shutil.rmtree(self.install / '.git')
        result = self.execute()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('не является Git-копией', result.stderr)
        self.assertEqual(self.commands(), [])

    def test_concurrent_upgrade_is_rejected(self):
        with (self.install / '.git/secretary-upgrade.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.execute()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('уже выполняется', result.stderr)
        self.assertEqual(self.commands(), [])

    def test_diverged_branch_is_not_merged(self):
        (self.install/'local-file').write_text('local change')
        self.git('-C', str(self.install), 'add', '.')
        self.git('-C', str(self.install), 'commit', '-m', 'local divergence')
        revision = self.git('-C', str(self.install), 'rev-parse', 'HEAD')
        result = self.execute()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.git('-C', str(self.install), 'rev-parse', 'HEAD'), revision)
        self.assertFalse(any('build' in c['args'] or 'stop' in c['args'] for c in self.commands()))

    def test_build_failure_keeps_application_running(self):
        result = self.execute(fail='build')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any('stop' in c['args'] or 'migrate' in c['args'] for c in self.commands()))

    def test_dump_failure_restarts_old_containers_without_migration(self):
        result = self.execute(fail='dump')
        self.assertNotEqual(result.returncode, 0)
        commands = self.commands()
        self.assertTrue(any(c['args'][0] == 'start' for c in commands))
        self.assertFalse(any('migrate' in c['args'] for c in commands))

    def test_migration_failure_leaves_writers_stopped(self):
        result = self.execute(fail='migrate')
        self.assertNotEqual(result.returncode, 0)
        commands = self.commands()
        self.assertFalse(any(c['args'][0] == 'start' or 'up' in c['args'] for c in commands))
        self.assertIn('stop', commands[-1]['args'])

    def test_launch_failure_leaves_writers_stopped(self):
        result = self.execute(fail='up')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c['args'][0] == 'start' for c in self.commands()))
        self.assertIn('stop', self.commands()[-1]['args'])


if __name__ == '__main__':
    unittest.main()
