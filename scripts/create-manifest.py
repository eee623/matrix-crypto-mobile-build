#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import plistlib
import re
import stat
import sys
import zipfile
from pathlib import Path, PurePosixPath


REPO_ROOT = Path(__file__).resolve().parents[1]
METADATA_FILES = {"manifest.json", "SHA256SUMS"}
MAX_FILE_BYTES = 1024 * 1024 * 1024
MAX_TOTAL_BYTES = 3 * 1024 * 1024 * 1024
SENSITIVE_NEEDLES = (
    b"github_pat_",
    b"ghp_",
    b"gho_",
    b"ghr_",
    b"ghs_",
    b"ghu_",
    b"-----begin private key-----",
    b"-----begin rsa private key-----",
    b"-----begin ec private key-----",
)
ANDROID_KOTLIN_PREFIXES = (
    "org/matrix/rustcomponents/sdk/crypto/",
    "uniffi/matrix_sdk_crypto/",
    "uniffi/matrix_sdk_common/",
)


def fail(message):
    raise SystemExit(message)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_pins():
    pins = {}
    for line in (REPO_ROOT / "pins" / "source.env").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator != "=" or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or not value:
            fail("invalid source pin file")
        pins[key] = value
    return pins


def allowed_path(platform, relative):
    path = PurePosixPath(relative)
    parts = path.parts
    if relative in {"LICENSES/MATRIX_RUST_SDK_LICENSE", "THIRD_PARTY_NOTICES.md"}:
        return True
    if platform == "android" and relative == "LICENSES/ANDROIDX_ANNOTATION_LICENSE":
        return True
    if platform == "ios":
        if len(parts) == 2 and parts[0] == "swift" and path.suffix == ".swift":
            return True
        if len(parts) == 2 and parts[0] == "headers" and (
            path.suffix == ".h" or path.name == "module.modulemap"
        ):
            return True
        if relative == "MatrixSDKCryptoFFI.xcframework/Info.plist":
            return True
        if (
            len(parts) == 3
            and parts[0] == "MatrixSDKCryptoFFI.xcframework"
            and parts[1] in {"ios-arm64", "ios-arm64-simulator"}
            and parts[2] == "libmatrix_sdk_crypto_ffi.a"
        ):
            return True
        if (
            len(parts) == 4
            and parts[0] == "MatrixSDKCryptoFFI.xcframework"
            and parts[1] in {"ios-arm64", "ios-arm64-simulator"}
            and parts[2] == "Headers"
            and (path.suffix == ".h" or path.name == "module.modulemap")
        ):
            return True
    if platform == "android":
        if (
            path.suffix == ".kt"
            and any(relative.startswith(f"kotlin/{prefix}") for prefix in ANDROID_KOTLIN_PREFIXES)
        ):
            return True
        if relative == "jni/arm64-v8a/libmatrix_sdk_crypto_ffi.so":
            return True
        if relative == "aar/matrix-sdk-crypto-ffi-validation.aar":
            return True
    return False


def scan_sensitive_stream(handle):
    overlap = b""
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        lowered = overlap + chunk.lower()
        if any(needle in lowered for needle in SENSITIVE_NEEDLES):
            fail("sensitive material detected")
        overlap = lowered[-64:]


def scan_sensitive(path):
    with path.open("rb") as handle:
        scan_sensitive_stream(handle)


def hash_stream(handle):
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def safe_zip_name(name):
    path = PurePosixPath(name)
    return bool(name) and not name.startswith("/") and "\\" not in name and ".." not in path.parts


def zip_entry_is_symlink(info):
    return stat.S_ISLNK((info.external_attr >> 16) & 0o170000)


def validate_aar(root):
    aar = root / "aar" / "matrix-sdk-crypto-ffi-validation.aar"
    native = root / "jni" / "arm64-v8a" / "libmatrix_sdk_crypto_ffi.so"
    with zipfile.ZipFile(aar) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            fail("Android AAR has duplicate entries")
        required = {
            "AndroidManifest.xml",
            "classes.jar",
            "jni/arm64-v8a/libmatrix_sdk_crypto_ffi.so",
        }
        if not required.issubset(set(names)):
            fail("Android AAR is incomplete")
        expanded_bytes = 0
        for info in infos:
            if not safe_zip_name(info.filename) or zip_entry_is_symlink(info):
                fail("unsafe Android AAR entry")
            allowed = (
                info.is_dir()
                or info.filename in {"AndroidManifest.xml", "classes.jar", "R.txt", "proguard.txt", "consumer-rules.pro"}
                or info.filename.startswith("META-INF/")
                or info.filename.startswith("res/")
                or info.filename == "jni/arm64-v8a/libmatrix_sdk_crypto_ffi.so"
            )
            if not allowed:
                fail("unexpected Android AAR entry")
            if not info.is_dir():
                if info.file_size > MAX_FILE_BYTES:
                    fail("Android AAR entry is too large")
                expanded_bytes += info.file_size
                with archive.open(info) as handle:
                    scan_sensitive_stream(handle)
        if expanded_bytes > MAX_TOTAL_BYTES:
            fail("Android AAR expanded size is too large")
        with archive.open("jni/arm64-v8a/libmatrix_sdk_crypto_ffi.so") as handle:
            packaged_native_hash = hash_stream(handle)
        if packaged_native_hash != sha256(native):
            fail("Android AAR native library mismatch")
        classes = archive.read("classes.jar")
    import io

    with zipfile.ZipFile(io.BytesIO(classes)) as jar:
        infos = jar.infolist()
        class_names = [info.filename for info in infos]
        if len(class_names) != len(set(class_names)):
            fail("Android classes.jar has duplicate entries")
        if not all(
            any(name.startswith(prefix) and name.endswith(".class") for name in class_names)
            for prefix in ANDROID_KOTLIN_PREFIXES
        ):
            fail("Android AAR generated component class set is incomplete")
        expanded_bytes = 0
        for info in infos:
            if not safe_zip_name(info.filename) or zip_entry_is_symlink(info):
                fail("unsafe Android classes.jar entry")
            allowed = (
                info.is_dir()
                or info.filename.startswith("META-INF/")
                or (
                    any(info.filename.startswith(prefix) for prefix in ANDROID_KOTLIN_PREFIXES)
                    and info.filename.endswith(".class")
                )
            )
            if not allowed:
                fail("unexpected Android classes.jar entry")
            if not info.is_dir():
                if info.file_size > 128 * 1024 * 1024:
                    fail("Android classes.jar entry is too large")
                expanded_bytes += info.file_size
                with jar.open(info) as handle:
                    scan_sensitive_stream(handle)
        if expanded_bytes > 512 * 1024 * 1024:
            fail("Android classes.jar expanded size is too large")


def validate_xcframework(root):
    container = root / "MatrixSDKCryptoFFI.xcframework"
    try:
        plist = plistlib.loads((container / "Info.plist").read_bytes())
    except (OSError, plistlib.InvalidFileException):
        fail("invalid XCFramework Info.plist")
    libraries = {
        entry.get("LibraryIdentifier"): entry
        for entry in plist.get("AvailableLibraries", [])
        if isinstance(entry, dict)
    }
    required = {
        "ios-arm64": None,
        "ios-arm64-simulator": "simulator",
    }
    if set(libraries) != set(required):
        fail("XCFramework slice set mismatch")
    for identifier, variant in required.items():
        entry = libraries[identifier]
        if (
            entry.get("SupportedArchitectures") != ["arm64"]
            or entry.get("SupportedPlatform") != "ios"
            or entry.get("SupportedPlatformVariant") != variant
            or not isinstance(entry.get("LibraryPath"), str)
            or entry.get("HeadersPath") != "Headers"
        ):
            fail("XCFramework slice metadata mismatch")
        slice_root = container / identifier
        if not (slice_root / entry["LibraryPath"]).is_file():
            fail("XCFramework slice library is missing")
        headers = slice_root / "Headers"
        if not (headers / "module.modulemap").is_file() or not any(headers.glob("*.h")):
            fail("XCFramework slice headers are incomplete")


def scan_payload(root, platform):
    if not root.is_dir() or root.is_symlink():
        fail("artifact root is unsafe")
    files = {}
    total = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        metadata = os.stat(path, follow_symlinks=False)
        if path.is_symlink():
            fail("artifact symlink is forbidden")
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            fail("artifact contains a non-regular file")
        relative = path.relative_to(root).as_posix()
        if relative in METADATA_FILES:
            continue
        if any(part.startswith(".") for part in PurePosixPath(relative).parts):
            fail("artifact hidden path is forbidden")
        if not allowed_path(platform, relative):
            fail("artifact path is not allowlisted")
        if metadata.st_size <= 0 or metadata.st_size > MAX_FILE_BYTES:
            fail("artifact file size is outside the allowed range")
        scan_sensitive(path)
        files[relative] = {"bytes": metadata.st_size, "sha256": sha256(path)}
        total += metadata.st_size
    if not files or total > MAX_TOTAL_BYTES:
        fail("artifact tree size is outside the allowed range")
    pins = load_pins()
    matrix_license = files.get("LICENSES/MATRIX_RUST_SDK_LICENSE")
    if matrix_license is None or matrix_license["sha256"] != pins["MATRIX_LICENSE_SHA256"]:
        fail("Matrix Rust SDK license hash mismatch")
    notices = files.get("THIRD_PARTY_NOTICES.md")
    if notices is None or notices["sha256"] != pins["THIRD_PARTY_NOTICES_SHA256"]:
        fail("third-party notices hash mismatch")
    if platform == "ios":
        required = (
            any(name.startswith("swift/") and name.endswith(".swift") for name in files)
            and any(name.startswith("headers/") and name.endswith(".h") for name in files)
            and "headers/module.modulemap" in files
            and "MatrixSDKCryptoFFI.xcframework/Info.plist" in files
            and any("ios-arm64/" in name and name.endswith(".a") for name in files)
            and any("ios-arm64-simulator/" in name and name.endswith(".a") for name in files)
            and "LICENSES/MATRIX_RUST_SDK_LICENSE" in files
            and "THIRD_PARTY_NOTICES.md" in files
        )
        if required:
            validate_xcframework(root)
    else:
        required = (
            all(
                any(name.startswith(f"kotlin/{prefix}") and name.endswith(".kt") for name in files)
                for prefix in ANDROID_KOTLIN_PREFIXES
            )
            and "jni/arm64-v8a/libmatrix_sdk_crypto_ffi.so" in files
            and "aar/matrix-sdk-crypto-ffi-validation.aar" in files
            and "LICENSES/ANDROIDX_ANNOTATION_LICENSE" in files
            and "LICENSES/MATRIX_RUST_SDK_LICENSE" in files
            and "THIRD_PARTY_NOTICES.md" in files
        )
        android_license = files.get("LICENSES/ANDROIDX_ANNOTATION_LICENSE")
        if android_license is None or android_license["sha256"] != pins[
            "ANDROIDX_ANNOTATION_LICENSE_SHA256"
        ]:
            fail("AndroidX Annotation license hash mismatch")
        if required:
            validate_aar(root)
    if not required:
        fail("artifact tree is incomplete")
    return files


def source_contract(pins):
    return {
        "archive": {
            "bytes": int(pins["MATRIX_ARCHIVE_BYTES"]),
            "sha256": pins["MATRIX_ARCHIVE_SHA256"],
        },
        "cargo_lock": {
            "effective_sha256": pins["MATRIX_EFFECTIVE_LOCK_SHA256"],
            "original_sha256": pins["MATRIX_ORIGINAL_LOCK_SHA256"],
            "overlay_sha256": pins["MATRIX_LOCK_OVERLAY_SHA256"],
        },
        "commit": pins["MATRIX_COMMIT"],
        "feature_args": ["--no-default-features", "--features", pins["MATRIX_FEATURES"]],
        "package": f'{pins["MATRIX_PACKAGE"]}@{pins["MATRIX_PACKAGE_VERSION"]}',
        "tag": pins["MATRIX_TAG"],
        "third_party_notices_sha256": pins["THIRD_PARTY_NOTICES_SHA256"],
        "uniffi": pins["UNIFFI_VERSION"],
        "dependency_configs_sha256": pins["MATRIX_DEPENDENCY_UNIFFI_SHA256"],
        "uniffi_toml_sha256": pins["MATRIX_UNIFFI_SHA256"],
    }


def toolchain_contract(platform, pins):
    common = {
        "cargo": pins["RUST_TOOLCHAIN"],
        "profile": "release",
        "protoc": pins["PROTOC_VERSION"],
        "rust": pins["RUST_TOOLCHAIN"],
        "source_date_epoch": int(pins["SOURCE_DATE_EPOCH"]),
    }
    if platform == "ios":
        return {
            **common,
            "minimum_os": pins["IOS_MINIMUM_OS"],
            "runner": pins["IOS_RUNNER"],
            "targets": [pins["IOS_DEVICE_TARGET"], pins["IOS_SIMULATOR_TARGET"]],
            "xcode": pins["IOS_XCODE_VERSION"],
            "xcode_build": pins["IOS_XCODE_BUILD"],
        }
    return {
        **common,
        "abi": pins["ANDROID_ABI"],
        "android_gradle_plugin": pins["ANDROID_GRADLE_PLUGIN_VERSION"],
        "androidx_annotation": pins["ANDROIDX_ANNOTATION_VERSION"],
        "cargo_ndk": pins["CARGO_NDK_VERSION"],
        "dependency_lock_sha256": pins["ANDROID_DEPENDENCY_LOCK_SHA256"],
        "dependency_verification_sha256": pins["ANDROID_DEPENDENCY_VERIFICATION_SHA256"],
        "gradle": pins["GRADLE_VERSION"],
        "jna": pins["JNA_VERSION"],
        "kotlin": pins["KOTLIN_VERSION"],
        "minimum_api": int(pins["ANDROID_MINIMUM_API"]),
        "ndk": pins["ANDROID_NDK_VERSION"],
        "ndk_release": pins["ANDROID_NDK_RELEASE"],
        "runner": pins["ANDROID_RUNNER"],
        "target": pins["ANDROID_TARGET"],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=("ios", "android"), required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--workflow-commit", required=True)
    parser.add_argument("--event", choices=("push", "workflow_dispatch"), required=True)
    parser.add_argument("--branch", default="main")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.workflow_commit) or args.branch != "main":
        fail("invalid workflow identity")

    pins = load_pins()
    files = scan_payload(args.artifact_root, args.platform)
    manifest = {
        "artifact_contract": "matrix-crypto-mobile-validation-v1",
        "files": files,
        "platform": args.platform,
        "production_activation": False,
        "schema_version": 1,
        "source": source_contract(pins),
        "toolchain": toolchain_contract(args.platform, pins),
        "validation_only": True,
        "workflow": {
            "branch": args.branch,
            "commit": args.workflow_commit,
            "event": args.event,
        },
    }
    manifest_path = args.artifact_root / "manifest.json"
    checksum_path = args.artifact_root / "SHA256SUMS"
    if manifest_path.exists() or checksum_path.exists():
        fail("artifact metadata already exists")
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    checksums = {name: entry["sha256"] for name, entry in files.items()}
    checksums["manifest.json"] = sha256(manifest_path)
    checksum_path.write_text(
        "".join(f"{digest}  {name}\n" for name, digest in sorted(checksums.items())),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
