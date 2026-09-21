'use strict';
const os = require('node:os');
const fs = require('node:fs/promises');
const path = require('node:path');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const execute = promisify(execFile);
const GiB = 1024 ** 3;
const issue = (code, ...args) => ({ code, args });

async function command(file, args) {
  return (await execute(file, args, { timeout: 30000, maxBuffer: 4 * 1024 ** 2, windowsHide: true, encoding: 'utf8' })).stdout.trim();
}
async function volume(location) {
  let current = path.resolve(location);
  for (;;) {
    try {
      const [stats, disk] = await Promise.all([fs.stat(current), fs.statfs(current)]);
      const free = Number(disk.bavail) * Number(disk.bsize);
      if (!Number.isFinite(free) || free < 0 || !disk.bsize) throw Error('Unknown disk space');
      return { id: String(stats.dev), free };
    } catch (error) {
      if (error.code !== 'ENOENT' || path.dirname(current) === current) throw error;
      current = path.dirname(current);
    }
  }
}
function locations(platform, home = os.homedir(), env = process.env) {
  return {
    install: platform === 'win32' ? path.join(env.LOCALAPPDATA || path.join(home, 'AppData/Local'), 'Programs/Ollama') : path.join(home, 'Applications'),
    temporary: os.tmpdir(),
    models: env.OLLAMA_MODELS ? path.resolve(env.OLLAMA_MODELS) : path.join(home, '.ollama/models'),
  };
}
async function inspectSystem(platform = process.platform) {
  const result = { platform, arch: os.arch(), osVersion: os.release(), cpu: os.cpus()[0]?.model || '', threads: os.cpus().length,
    ram: os.totalmem(), freeRam: os.freemem(), avx2: null, gpus: [], volumes: {} };
  if (platform === 'darwin') {
    result.osVersion = await command('/usr/bin/sw_vers', ['-productVersion']);
    const arm = await command('/usr/sbin/sysctl', ['-n', 'hw.optional.arm64']).catch(() => '0');
    result.arch = arm === '1' ? 'arm64' : 'x64';
    if (result.arch === 'x64') result.avx2 = /\bAVX2\b/.test(await command('/usr/sbin/sysctl', ['-n', 'machdep.cpu.leaf7_features']));
    // GPU information is advisory: CPU execution is supported.
    try {
      const data = JSON.parse(await command('/usr/sbin/system_profiler', ['SPDisplaysDataType', '-json']));
      result.gpus = (data.SPDisplaysDataType || []).map(gpu => gpu.sppci_model).filter(Boolean);
    } catch { /* CPU fallback remains available. */ }
  } else if (platform === 'win32') {
    const script = `
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -TypeDefinition 'using System.Runtime.InteropServices; public static class SecretaryCPU { [DllImport("kernel32.dll")] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool IsProcessorFeaturePresent(uint feature); }'
$cpu = @(Get-CimInstance Win32_Processor)[0]
$os = Get-CimInstance Win32_OperatingSystem
$gpu = @(); try { $gpu = @(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }) } catch {}
@{ architecture = [int]$cpu.Architecture; version = $os.Version; avx2 = [SecretaryCPU]::IsProcessorFeaturePresent(40); gpus = $gpu } | ConvertTo-Json -Compress
`;
    const data = JSON.parse(await command(path.join(process.env.SystemRoot || 'C:\\Windows', 'System32/WindowsPowerShell/v1.0/powershell.exe'),
      ['-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(script, 'utf16le').toString('base64')]));
    result.arch = ({ 9: 'x64', 12: 'arm64' })[data.architecture] || 'unsupported';
    result.osVersion = data.version; result.avx2 = data.avx2; result.gpus = data.gpus || [];
  }
  for (const [key, location] of Object.entries(locations(platform))) result.volumes[key] = await volume(location);
  return result;
}

// Product eligibility policy, deliberately stricter than merely launching a CLI.
// GPU memory is not added to RAM: that would double-count Apple unified memory
// and assume GPU/driver compatibility which cannot be established pre-install.
function assess(system, modelBytes, contextLength, { installing = true, stagedBytes = 0 } = {}) {
  const blockers = [], warnings = [];
  const version = String(system.osVersion).split('.').map(Number);
  if (!['darwin', 'win32'].includes(system.platform)) blockers.push(issue('platform'));
  if (!['x64', 'arm64'].includes(system.arch)) blockers.push(issue('architecture'));
  if (system.platform === 'darwin' && !(version[0] >= 14)) blockers.push(issue('macos'));
  if (system.platform === 'win32' && !(version[0] >= 10 && version[2] >= 19045)) blockers.push(issue('windows'));
  if (system.arch === 'x64' && system.avx2 !== true) blockers.push(issue('avx2'));
  if (!(system.threads >= 4)) blockers.push(issue('cores'));
  if (!(system.ram >= 8 * GiB)) blockers.push(issue('ram', 8));
  let requiredRam = 8 * GiB;
  if (Number.isSafeInteger(modelBytes) && modelBytes > 0 && Number.isInteger(contextLength) && contextLength >= 4096 && contextLength <= 131072) {
    requiredRam = Math.max(requiredRam, Math.ceil(modelBytes * 1.25 + 3 * GiB + (contextLength / 4096) * 0.5 * GiB));
    if (system.ram >= 8 * GiB && system.ram < requiredRam) blockers.push(issue('model_ram', Math.ceil(requiredRam / GiB)));
  } else blockers.push(issue('model_unknown'));
  const needs = { install: installing ? (system.platform === 'win32' ? 5 * GiB : 1 * GiB) : 0,
    temporary: installing ? Math.max(0, (system.platform === 'win32' ? 5 * GiB : 1 * GiB) - stagedBytes) : 0, models: Number.isSafeInteger(modelBytes) && modelBytes > 0 ? Math.ceil(modelBytes * 1.1) : 0 };
  const disks = new Map();
  for (const [key, bytes] of Object.entries(needs)) {
    const disk = system.volumes?.[key];
    if (!disk || !Number.isFinite(disk.free) || disk.free < 0 || !disk.id) { blockers.push(issue('disk_unknown')); continue; }
    const item = disks.get(disk.id) || { required: 2 * GiB, free: disk.free };
    item.required += bytes; item.free = Math.min(item.free, disk.free); disks.set(disk.id, item);
  }
  for (const disk of disks.values()) if (disk.free < disk.required) blockers.push(issue('disk', Math.ceil(disk.required / GiB), Math.floor(disk.free / GiB)));
  if (system.arch === 'x64') warnings.push(issue('cpu_speed'));
  warnings.push(issue('estimate'));
  return { eligible: blockers.length === 0, blockers, warnings, requiredRam, modelBytes, system };
}
module.exports = { inspectSystem, assess, locations, command, GiB };
