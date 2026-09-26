#!/usr/bin/env python3
"""Keep immutable released LLM prompt snapshots and update the planned release."""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER = "backend/internal/server/"
MANIFEST = ROOT / "llm_manifest.json"
PROMPTS = (
    ("message_analysis", "analyze", "AnalysisResult", "llm_contracts.json#prompts.analyze"),
    ("task_extraction", "extract_tasks", "TaskExtractionResult", "llm_contracts.json#prompts.extract_tasks"),
    ("delegation_analysis", "delegations", "DelegationAnalysis", "llm_contracts.json#prompts.delegations"),
    ("meeting_delegations", "delegations", "MeetingDelegationAnalysis", "llm_contracts.json#prompts.delegations"),
    ("email_thread_match", None, "EmailThreadMatch", "subjects.go"),
    ("meeting_topic_match", None, "MeetingTopicMatch", "results.go"),
)


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True)


def source(path, commit=None):
    if commit:
        return git("show", f"{commit}:{SERVER}{path}")
    return (ROOT / SERVER / path).read_text(encoding="utf-8")


def literal_prompt(source_text, request_type, schema_name):
    match = re.search(r'q\.llm\(("(?:\\.|[^"\\])*")', source_text)
    if not match:
        raise ValueError("LLM prompt literal not found")
    call_line = source_text[match.start():source_text.find("\n", match.end())]
    if f'"{request_type}"' not in call_line and f'"{schema_name}"' not in call_line:
        raise ValueError(f"LLM prompt source does not match {request_type}")
    return json.loads(match.group(1))


def snapshot(version, commit=None):
    contracts = json.loads(source("llm_contracts.json", commit))
    revision_match = re.search(
        r'const llmPromptTemplateRevision = "([^"]+)"',
        source("llm_temporary_corrections.go", commit) if not commit else "",
    )
    revision = revision_match.group(1) if revision_match else None
    prompts = []
    for request_type, key, schema_name, file in PROMPTS:
        prompt = contracts["prompts"][key] if key else literal_prompt(source(file, commit), request_type, schema_name)
        response_schema = contracts["schemas"][schema_name]
        canonical = json.dumps(
            {"system_prompt": prompt, "response_schema": response_schema},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        entry = {
            "request_type": request_type,
            "system_prompt": prompt,
            "response_schema_name": schema_name,
            "response_schema": response_schema,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "contract_sha256": hashlib.sha256(canonical).hexdigest(),
            "source": SERVER + file,
        }
        if revision:
            entry["template_revision"] = revision
        prompts.append(entry)
    result = {"release_version": version, "prompts": prompts}
    if commit:
        result["source_commit"] = commit
        result["state"] = "historical_source"
    else:
        result["state"] = "planned"
    return result


def historic_versions():
    seen = set()
    for commit in git("log", "--format=%H", "--", "version").splitlines():
        version = git("show", f"{commit}:version").strip()
        if version.startswith("0.7.") and version not in seen:
            seen.add(version)
            yield version, commit


def version_key(version):
    return tuple(map(int, version.split(".")))


def latest_snapshot(releases, version):
    eligible = (entry for release, entry in releases.items() if version_key(release) <= version_key(version))
    return max(eligible, key=lambda entry: version_key(entry["release_version"]), default=None)


def prompts_match(left, right):
    fields = ("request_type", "system_prompt", "response_schema_name", "response_schema")
    return [tuple(prompt[field] for field in fields) for prompt in left] == [
        tuple(prompt[field] for field in fields) for prompt in right
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backfill", action="store_true", help="add available 0.7.x Git snapshots")
    parser.add_argument("--check", action="store_true", help="verify current source matches manifest without writing")
    parser.add_argument("--finalize", action="store_true", help="mark current planned snapshot as released")
    args = parser.parse_args()
    if args.check and (args.backfill or args.finalize):
        parser.error("--check cannot be combined with updating options")
    if MANIFEST.exists():
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 2 or manifest.get("component") != "backend":
            if "release_version" in manifest and "prompts" in manifest:
                manifest = {"schema_version": 2, "component": "backend", "releases": []}
            else:
                raise ValueError("unknown LLM manifest format")
    else:
        manifest = {"schema_version": 2, "component": "backend", "releases": []}
    releases = {entry["release_version"]: entry for entry in manifest["releases"]}
    current_version = (ROOT / "version").read_text(encoding="utf-8").strip()
    current = snapshot(current_version)
    if args.check:
        baseline = latest_snapshot(releases, current_version)
        if baseline is None or not prompts_match(baseline["prompts"], current["prompts"]):
            raise ValueError(f"LLM manifest is missing or stale for {current_version}")
        for release in releases.values():
            for entry in release["prompts"]:
                prompt_hash = hashlib.sha256(entry["system_prompt"].encode("utf-8")).hexdigest()
                canonical = json.dumps(
                    {"system_prompt": entry["system_prompt"], "response_schema": entry["response_schema"]},
                    ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                ).encode("utf-8")
                if entry["prompt_sha256"] != prompt_hash or entry["contract_sha256"] != hashlib.sha256(canonical).hexdigest():
                    raise ValueError(f"LLM manifest hash mismatch in {release['release_version']}")
        print(f"LLM manifest matches current source: {current_version}")
        return
    if args.backfill:
        for version, commit in sorted(historic_versions(), key=lambda item: version_key(item[0])):
            if version != current_version and version not in releases:
                historical = snapshot(version, commit)
                baseline = latest_snapshot(releases, version)
                if baseline is None or not prompts_match(baseline["prompts"], historical["prompts"]):
                    releases[version] = historical
    old = releases.get(current_version)
    if old:
        if not prompts_match(old["prompts"], current["prompts"]):
            raise ValueError(f"{current_version} is immutable; raise version before changing LLM prompts")
    elif (baseline := latest_snapshot(releases, current_version)) is None or not prompts_match(baseline["prompts"], current["prompts"]):
        releases[current_version] = current
    if args.finalize and current_version in releases:
        releases[current_version]["state"] = "released"
    manifest["releases"] = sorted(
        releases.values(), key=lambda item: tuple(map(int, item["release_version"].split(".")))
    )
    serialized = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    if not MANIFEST.exists() or MANIFEST.read_text(encoding="utf-8") != serialized:
        MANIFEST.write_text(serialized, encoding="utf-8")
        print(f"Updated {MANIFEST.name}: {len(manifest['releases'])} versions, current {current_version}")
    else:
        print(f"LLM prompts unchanged; {MANIFEST.name} left unchanged for {current_version}")


if __name__ == "__main__":
    main()
