#!/usr/bin/env python3
import argparse
import hashlib
import importlib.util
import json
import os
import re
import stat
import sys
from pathlib import Path, PurePosixPath


REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPO_ROOT / "scripts" / "create-manifest.py"
sys.dont_write_bytecode = True


def fail(message):
    raise SystemExit(message)


def load_contract_module():
    spec = importlib.util.spec_from_file_location("mobile_artifact_contract", CONTRACT_PATH)
    if spec is None or spec.loader is None:
        fail("artifact contract could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail("duplicate manifest key")
        result[key] = value
    return result


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def regular_metadata_file(path):
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    return (
        not path.is_symlink()
        and stat.S_ISREG(metadata.st_mode)
        and 0 < metadata.st_size <= 5 * 1024 * 1024
    )


def parse_checksums(path):
    checksums = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if match is None:
            fail("noncanonical checksum line")
        digest, relative = match.groups()
        posix_path = PurePosixPath(relative)
        if (
            relative.startswith("/")
            or "\\" in relative
            or ".." in posix_path.parts
            or relative in checksums
        ):
            fail("unsafe checksum path")
        checksums[relative] = digest
    canonical = "".join(f"{digest}  {name}\n" for name, digest in sorted(checksums.items()))
    if path.read_text(encoding="utf-8") != canonical:
        fail("checksum file is not canonical")
    return checksums


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=("ios", "android"), required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--expected-workflow-commit", required=True)
    parser.add_argument("--expected-event", choices=("push", "workflow_dispatch"), required=True)
    parser.add_argument("--expected-branch", default="main")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_workflow_commit):
        fail("invalid expected workflow commit")

    contract = load_contract_module()
    root = args.artifact_root
    manifest_path = root / "manifest.json"
    checksum_path = root / "SHA256SUMS"
    if not regular_metadata_file(manifest_path) or not regular_metadata_file(checksum_path):
        fail("artifact metadata is missing or unsafe")
    contract.scan_sensitive(manifest_path)
    contract.scan_sensitive(checksum_path)

    try:
        raw_manifest = manifest_path.read_text(encoding="utf-8")
        manifest = json.loads(raw_manifest, object_pairs_hook=reject_duplicate_keys)
    except (OSError, UnicodeError, json.JSONDecodeError):
        fail("invalid manifest JSON")
    if not isinstance(manifest, dict):
        fail("manifest root must be an object")
    expected_keys = {
        "artifact_contract",
        "files",
        "platform",
        "production_activation",
        "schema_version",
        "source",
        "toolchain",
        "validation_only",
        "workflow",
    }
    if set(manifest) != expected_keys:
        fail("manifest schema mismatch")
    canonical = json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if raw_manifest != canonical:
        fail("manifest is not canonical")

    pins = contract.load_pins()
    expected_workflow = {
        "branch": args.expected_branch,
        "commit": args.expected_workflow_commit,
        "event": args.expected_event,
    }
    if manifest != {
        "artifact_contract": "matrix-crypto-mobile-validation-v1",
        "files": manifest["files"],
        "platform": args.platform,
        "production_activation": False,
        "schema_version": 1,
        "source": contract.source_contract(pins),
        "toolchain": contract.toolchain_contract(args.platform, pins),
        "validation_only": True,
        "workflow": expected_workflow,
    }:
        fail("manifest contract mismatch")

    actual_files = contract.scan_payload(root, args.platform)
    if manifest["files"] != actual_files:
        fail("artifact file hash mismatch")
    expected_checksums = {name: entry["sha256"] for name, entry in actual_files.items()}
    expected_checksums["manifest.json"] = sha256(manifest_path)
    if parse_checksums(checksum_path) != expected_checksums:
        fail("SHA256SUMS mismatch")
    print(f"{args.platform}_validation_artifact_verified")


if __name__ == "__main__":
    main()
