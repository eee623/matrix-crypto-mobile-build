#!/usr/bin/env python3
import io
import json
import plistlib
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_COMMIT = "0" * 40
SWIFT_COMPONENTS = (
    "MatrixSDKCrypto",
    "matrix_sdk_common",
    "matrix_sdk_crypto",
)


def fail(message):
    raise SystemExit(message)


def run(command, expected_success=True):
    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if (result.returncode == 0) != expected_success:
        detail = (result.stderr or result.stdout).strip()
        fail(f"synthetic manifest command had unexpected status: {detail}")
    return result


def copy_contract_text(destination, source):
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes())


def write_matrix_license(destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    apache = (REPO_ROOT / "pins" / "licenses" / "ANDROIDX_ANNOTATION_LICENSE").read_bytes()
    destination.write_bytes(b"\n" + apache)


def write_ios_fixture(root):
    swift_dir = root / "swift"
    headers_dir = root / "headers"
    swift_dir.mkdir(parents=True)
    headers_dir.mkdir(parents=True)
    merged_modulemap = []
    for component in SWIFT_COMPONENTS:
        (swift_dir / f"{component}.swift").write_text(
            f"public func {component}Contract() {{}}\n", encoding="utf-8"
        )
        header_name = f"{component}FFI.h"
        (headers_dir / header_name).write_text(
            f"void {component}_contract(void);\n", encoding="utf-8"
        )
        merged_modulemap.append(
            f'module {component}FFI {{ header "{header_name}" export * }}\n'
        )
    (headers_dir / "module.modulemap").write_text(
        "\n".join(merged_modulemap), encoding="utf-8"
    )

    container = root / "MatrixSDKCryptoFFI.xcframework"
    libraries = []
    for identifier, variant in (
        ("ios-arm64", None),
        ("ios-arm64-simulator", "simulator"),
    ):
        slice_root = container / identifier
        slice_headers = slice_root / "Headers"
        slice_headers.mkdir(parents=True)
        (slice_root / "libmatrix_sdk_crypto_ffi.a").write_bytes(
            b"!<arch>\nsynthetic-contract\n"
        )
        for header in headers_dir.iterdir():
            (slice_headers / header.name).write_bytes(header.read_bytes())
        entry = {
            "HeadersPath": "Headers",
            "LibraryIdentifier": identifier,
            "LibraryPath": "libmatrix_sdk_crypto_ffi.a",
            "SupportedArchitectures": ["arm64"],
            "SupportedPlatform": "ios",
        }
        if variant is not None:
            entry["SupportedPlatformVariant"] = variant
        libraries.append(entry)
    (container / "Info.plist").write_bytes(
        plistlib.dumps({"AvailableLibraries": libraries}, sort_keys=True)
    )
    write_matrix_license(root / "LICENSES" / "MATRIX_RUST_SDK_LICENSE")
    copy_contract_text(root / "THIRD_PARTY_NOTICES.md", REPO_ROOT / "THIRD_PARTY_NOTICES.md")


def classes_jar_bytes():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for package in (
            "org/matrix/rustcomponents/sdk/crypto",
            "uniffi/matrix_sdk_crypto",
            "uniffi/matrix_sdk_common",
        ):
            archive.writestr(
                f"{package}/SyntheticContract.class",
                b"synthetic-class-contract",
            )
    return buffer.getvalue()


def write_android_fixture(root):
    for package in (
        "org.matrix.rustcomponents.sdk.crypto",
        "uniffi.matrix_sdk_crypto",
        "uniffi.matrix_sdk_common",
    ):
        kotlin = root / "kotlin" / Path(*package.split("."))
        kotlin.mkdir(parents=True)
        (kotlin / "SyntheticContract.kt").write_text(
            f"package {package}\n"
            "import androidx.annotation.RequiresApi\n"
            "@RequiresApi(26) fun syntheticContract() = 26\n",
            encoding="utf-8",
        )
    native = root / "jni" / "arm64-v8a" / "libmatrix_sdk_crypto_ffi.so"
    native.parent.mkdir(parents=True)
    native.write_bytes(b"\x7fELFsynthetic-contract")
    aar = root / "aar" / "matrix-sdk-crypto-ffi-validation.aar"
    aar.parent.mkdir(parents=True)
    with zipfile.ZipFile(aar, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("AndroidManifest.xml", b"synthetic-manifest")
        archive.writestr("classes.jar", classes_jar_bytes())
        archive.writestr(
            "jni/arm64-v8a/libmatrix_sdk_crypto_ffi.so", native.read_bytes()
        )
    write_matrix_license(root / "LICENSES" / "MATRIX_RUST_SDK_LICENSE")
    copy_contract_text(
        root / "LICENSES" / "ANDROIDX_ANNOTATION_LICENSE",
        REPO_ROOT / "pins" / "licenses" / "ANDROIDX_ANNOTATION_LICENSE",
    )
    copy_contract_text(root / "THIRD_PARTY_NOTICES.md", REPO_ROOT / "THIRD_PARTY_NOTICES.md")


def create_command(root, platform):
    return [
        sys.executable,
        "scripts/create-manifest.py",
        "--platform",
        platform,
        "--artifact-root",
        str(root),
        "--workflow-commit",
        WORKFLOW_COMMIT,
        "--event",
        "push",
        "--branch",
        "main",
    ]


def exercise_platform(root, platform):
    run(create_command(root, platform))
    verify = [
        sys.executable,
        "scripts/verify-manifest.py",
        "--platform",
        platform,
        "--artifact-root",
        str(root),
        "--expected-workflow-commit",
        WORKFLOW_COMMIT,
        "--expected-event",
        "push",
        "--expected-branch",
        "main",
    ]
    run(verify)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest["production_activation"] is not False or manifest["validation_only"] is not True:
        fail("synthetic manifest changed the validation-only activation contract")
    notice = root / "THIRD_PARTY_NOTICES.md"
    notice.write_bytes(notice.read_bytes() + b"tamper\n")
    run(verify, expected_success=False)
    print(f"{platform}_synthetic_manifest_verified")


def main():
    with tempfile.TemporaryDirectory(prefix="matrix-manifest-contract-") as temp:
        root = Path(temp)
        ios = root / "ios"
        android = root / "android"
        ios.mkdir()
        android.mkdir()
        write_ios_fixture(ios)
        write_android_fixture(android)

        hidden = ios / "swift" / ".hidden.swift"
        hidden.write_text("// hidden contract\n", encoding="utf-8")
        run(create_command(ios, "ios"), expected_success=False)
        hidden.unlink()

        kotlin = (
            android / "kotlin" / "uniffi" / "matrix_sdk_crypto" / "SyntheticContract.kt"
        )
        original_kotlin = kotlin.read_text(encoding="utf-8")
        kotlin.write_text(
            original_kotlin + "// " + "ghs_" + "contract_secret\n", encoding="utf-8"
        )
        run(create_command(android, "android"), expected_success=False)
        kotlin.write_text(original_kotlin, encoding="utf-8")

        exercise_platform(ios, "ios")
        exercise_platform(android, "android")
    print("synthetic_manifest_contracts_verified")


if __name__ == "__main__":
    main()
