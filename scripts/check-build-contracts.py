#!/usr/bin/env python3
import hashlib
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SWIFT_COMPONENTS = (
    "MatrixSDKCrypto",
    "matrix_sdk_common",
    "matrix_sdk_crypto",
)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_pins(errors):
    pins = {}
    for line in (REPO_ROOT / "pins" / "source.env").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator != "=" or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or not value:
            errors.append("source.env contains a noncanonical pin")
            continue
        pins[key] = value
    return pins


def require(errors, condition, message):
    if not condition:
        errors.append(message)


def check_android_contract(errors, pins):
    build = (REPO_ROOT / "scripts" / "build-android.sh").read_text(encoding="utf-8")
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    notices = (REPO_ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    manifest = (REPO_ROOT / "scripts" / "create-manifest.py").read_text(encoding="utf-8")
    workflow = (REPO_ROOT / ".github" / "workflows" / "mobile-artifacts.yml").read_text(
        encoding="utf-8"
    )

    required_pins = (
        "ANDROIDX_ANNOTATION_VERSION",
        "ANDROIDX_ANNOTATION_LICENSE_SHA256",
        "ANDROID_DEPENDENCY_LOCK_SHA256",
        "ANDROID_DEPENDENCY_VERIFICATION_SHA256",
        "MATRIX_DEPENDENCY_UNIFFI_SHA256",
        "THIRD_PARTY_NOTICES_SHA256",
        "UNIFFI_VERSION",
    )
    for key in required_pins:
        require(errors, key in pins, f"Android dependency contract is missing {key}")
    if any(key not in pins for key in required_pins):
        return

    annotation_version = pins["ANDROIDX_ANNOTATION_VERSION"]
    require(
        errors,
        re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", annotation_version) is not None,
        "AndroidX Annotation must use a fixed stable semantic version",
    )
    require(errors, "+" not in annotation_version and "latest" not in annotation_version.lower(),
            "AndroidX Annotation must not use a dynamic version")
    coordinate = f"androidx.annotation:annotation:{annotation_version}"
    require(errors, 'api("androidx.annotation:annotation:$ANDROIDX_ANNOTATION_VERSION")' in build,
            "temporary AAR project does not compile against the pinned AndroidX coordinate")
    require(errors, coordinate in readme,
            "README does not state the AndroidX dependency required by the file AAR")
    require(errors, coordinate in notices,
            "THIRD_PARTY_NOTICES does not identify the pinned AndroidX dependency")
    require(errors, sha256(REPO_ROOT / "THIRD_PARTY_NOTICES.md") == pins["THIRD_PARTY_NOTICES_SHA256"],
            "third-party notices hash pin mismatch")

    license_path = REPO_ROOT / "pins" / "licenses" / "ANDROIDX_ANNOTATION_LICENSE"
    require(errors, license_path.is_file() and not license_path.is_symlink(),
            "pinned AndroidX license file is missing or unsafe")
    if license_path.is_file():
        require(errors, sha256(license_path) == pins["ANDROIDX_ANNOTATION_LICENSE_SHA256"],
                "pinned AndroidX license hash mismatch")
    require(errors, "ANDROIDX_ANNOTATION_LICENSE" in build,
            "Android artifact does not copy the pinned AndroidX license")
    require(errors, 'relative == "LICENSES/ANDROIDX_ANNOTATION_LICENSE"' in manifest,
            "artifact allowlist omits the AndroidX license")
    require(errors, 'and "LICENSES/ANDROIDX_ANNOTATION_LICENSE" in files' in manifest,
            "Android artifact completeness gate omits the AndroidX license")
    require(errors, 'matrix_license["sha256"] != pins["MATRIX_LICENSE_SHA256"]' in manifest,
            "artifact contract does not verify the Matrix license hash")
    require(errors, 'android_license["sha256"] != pins[' in manifest,
            "artifact contract does not verify the AndroidX license hash")
    require(errors, "LICENSES/ANDROIDX_ANNOTATION_LICENSE" in workflow,
            "Android upload allowlist omits the AndroidX license")

    lock_path = REPO_ROOT / "pins" / "android" / "gradle.lockfile"
    require(errors, lock_path.is_file() and not lock_path.is_symlink(),
            "Android dependency lock is missing or unsafe")
    if lock_path.is_file():
        lock_text = lock_path.read_text(encoding="utf-8")
        require(errors, sha256(lock_path) == pins["ANDROID_DEPENDENCY_LOCK_SHA256"],
                "Android dependency lock hash mismatch")
        for locked in (
            coordinate,
            f"androidx.annotation:annotation-jvm:{annotation_version}",
            f"net.java.dev.jna:jna:{pins['JNA_VERSION']}",
        ):
            require(errors, locked in lock_text, f"Android dependency lock is missing {locked}")
    require(errors, "dependencyLocking" in build and "lockAllConfigurations" in build,
            "temporary AAR project does not enforce dependency locking")
    require(errors, 'pins/android/gradle.lockfile' in build,
            "temporary AAR project does not install the pinned dependency lock")

    verification_path = REPO_ROOT / "pins" / "android" / "verification-metadata.xml"
    require(errors, verification_path.is_file() and not verification_path.is_symlink(),
            "Android dependency verification metadata is missing or unsafe")
    if verification_path.is_file():
        require(errors, sha256(verification_path) == pins["ANDROID_DEPENDENCY_VERIFICATION_SHA256"],
                "Android dependency verification metadata hash mismatch")
        try:
            root = ET.parse(verification_path).getroot()
        except ET.ParseError:
            errors.append("Android dependency verification metadata is invalid XML")
        else:
            namespace = {"v": "https://schema.gradle.org/dependency-verification"}
            verify_metadata = root.findtext("v:configuration/v:verify-metadata", namespaces=namespace)
            require(errors, verify_metadata == "true",
                    "Android dependency metadata verification is not strict")
            require(errors, root.find("v:configuration/v:trusted-artifacts", namespace) is None,
                    "Android dependency verification contains unverified trusted-artifact bypasses")
            verified_artifacts = {
                (
                    component.get("group"),
                    component.get("name"),
                    component.get("version"),
                    artifact.get("name"),
                )
                for component in root.findall("v:components/v:component", namespace)
                for artifact in component.findall("v:artifact", namespace)
                if artifact.find("v:sha256", namespace) is not None
            }
            required_artifacts = {
                ("androidx.annotation", "annotation-jvm", annotation_version,
                 f"annotation-jvm-{annotation_version}.jar"),
                ("net.java.dev.jna", "jna", pins["JNA_VERSION"], f"jna-{pins['JNA_VERSION']}.jar"),
                ("com.android.tools.build", "gradle", pins["ANDROID_GRADLE_PLUGIN_VERSION"],
                 f"gradle-{pins['ANDROID_GRADLE_PLUGIN_VERSION']}.jar"),
                ("org.jetbrains.kotlin", "kotlin-compiler-embeddable", pins["KOTLIN_VERSION"],
                 f"kotlin-compiler-embeddable-{pins['KOTLIN_VERSION']}.jar"),
                ("com.android.tools.build", "aapt2", "9.2.0-15009934",
                 "aapt2-9.2.0-15009934-linux.jar"),
            }
            require(errors, required_artifacts.issubset(verified_artifacts),
                    "Android dependency verification omits a required runtime or Linux build artifact")
            required_metadata_artifacts = {
                ("com.google.guava", "guava-parent", "33.3.1-jre",
                 "guava-parent-33.3.1-jre.pom"),
                ("org.junit", "junit-bom", "5.10.2", "junit-bom-5.10.2.module"),
                ("org.junit", "junit-bom", "5.11.0-M2", "junit-bom-5.11.0-M2.module"),
            }
            require(errors, required_metadata_artifacts.issubset(verified_artifacts),
                    "Android dependency verification omits hosted fresh-resolution metadata")
            hash_nodes = root.findall(".//v:sha256", namespace)
            require(errors, bool(hash_nodes) and all(
                re.fullmatch(r"[0-9a-f]{64}", node.get("value", "")) for node in hash_nodes
            ), "Android dependency verification contains a non-SHA-256 checksum")
    require(errors, 'pins/android/verification-metadata.xml' in build,
            "temporary AAR project does not install dependency verification metadata")
    require(errors, "--dependency-verification strict" in build,
            "temporary AAR build does not explicitly enforce strict dependency verification")
    fresh_resolution = REPO_ROOT / "scripts" / "check-android-gradle-fresh-resolution.py"
    require(errors, fresh_resolution.is_file() and not fresh_resolution.is_symlink(),
            "Android fresh Gradle resolution regression is missing or unsafe")
    if fresh_resolution.is_file():
        regression = fresh_resolution.read_text(encoding="utf-8")
        require(errors, "GRADLE_USER_HOME" in regression,
                "Android fresh Gradle resolution regression does not isolate Gradle cache")
        require(errors, "--dependency-verification" in regression and "strict" in regression,
                "Android fresh Gradle resolution regression does not enforce strict verification")
        require(errors, "--offline" in regression,
                "Android fresh Gradle resolution regression does not repeat from cache offline")
        require(errors, "com.android.tools.build:gradle:" in regression,
                "Android fresh Gradle resolution regression does not resolve the AGP classpath")

    require(errors, '"androidx_annotation": pins["ANDROIDX_ANNOTATION_VERSION"]' in manifest,
            "artifact toolchain omits the AndroidX Annotation version")
    require(errors, '"dependency_lock_sha256": pins["ANDROID_DEPENDENCY_LOCK_SHA256"]' in manifest,
            "artifact toolchain omits the Android dependency lock hash")
    require(errors,
            '"dependency_verification_sha256": pins["ANDROID_DEPENDENCY_VERIFICATION_SHA256"]'
            in manifest,
            "artifact toolchain omits the Android dependency verification hash")
    require(errors, '"uniffi": pins["UNIFFI_VERSION"]' in manifest,
            "artifact source contract omits the UniFFI version")
    require(errors, '"dependency_configs_sha256": pins["MATRIX_DEPENDENCY_UNIFFI_SHA256"]' in manifest,
            "artifact source contract omits dependency UniFFI config hashes")
    require(errors, '"third_party_notices_sha256": pins["THIRD_PARTY_NOTICES_SHA256"]' in manifest,
            "artifact source contract omits the third-party notices hash")
    require(errors, "org.jetbrains.kotlin.android" not in build,
            "Android build restored the forbidden legacy Kotlin plugin")


def write_swift_fixture(generated_dir):
    for component in EXPECTED_SWIFT_COMPONENTS:
        (generated_dir / f"{component}.swift").write_text(
            f"// generated {component}\n", encoding="utf-8"
        )
        (generated_dir / f"{component}FFI.h").write_text(
            f"void {component}_contract(void);\n", encoding="utf-8"
        )
        (generated_dir / f"{component}FFI.modulemap").write_text(
            f'module {component}FFI {{ header "{component}FFI.h" export * }}\n',
            encoding="utf-8",
        )


def check_swift_merge_helper(errors):
    helper = REPO_ROOT / "scripts" / "merge-swift-modulemaps.py"
    require(errors, helper.is_file() and not helper.is_symlink(),
            "Swift multi-component merge helper is missing or unsafe")
    if not helper.is_file():
        return
    with tempfile.TemporaryDirectory(prefix="matrix-swift-contract-") as temp:
        generated_dir = Path(temp) / "generated"
        output = Path(temp) / "headers" / "module.modulemap"
        generated_dir.mkdir()
        output.parent.mkdir()
        write_swift_fixture(generated_dir)
        result = subprocess.run(
            [sys.executable, str(helper), "--generated-dir", str(generated_dir), "--output", str(output)],
            check=False,
            capture_output=True,
            text=True,
        )
        require(errors, result.returncode == 0,
                f"Swift multi-component merge helper rejected the exact fixture: {result.stderr.strip()}")
        if output.is_file():
            merged = output.read_text(encoding="utf-8")
            offsets = [merged.find(f"module {component}FFI") for component in EXPECTED_SWIFT_COMPONENTS]
            require(errors, all(offset >= 0 for offset in offsets) and offsets == sorted(offsets),
                    "Swift modulemaps were not merged in the fixed component order")
        else:
            errors.append("Swift multi-component merge helper did not create its output")

        (generated_dir / "unexpectedFFI.modulemap").write_text(
            'module unexpectedFFI { header "unexpectedFFI.h" export * }\n', encoding="utf-8"
        )
        rejected = subprocess.run(
            [sys.executable, str(helper), "--generated-dir", str(generated_dir),
             "--output", str(Path(temp) / "unexpected.modulemap")],
            check=False,
            capture_output=True,
            text=True,
        )
        require(errors, rejected.returncode != 0,
                "Swift multi-component merge helper accepted an unexpected modulemap")


def check_safe_diagnostic_helper(errors):
    helper = REPO_ROOT / "scripts" / "print-safe-diagnostic.py"
    require(errors, helper.is_file() and not helper.is_symlink(),
            "safe generator diagnostic helper is missing or unsafe")
    if not helper.is_file():
        return
    with tempfile.TemporaryDirectory(prefix="matrix-diagnostic-contract-") as temp:
        root = Path(temp)
        diagnostic = root / "generator.stderr"
        secret = "github_pat_" + "contract_secret_value"
        bearer = "bearer_contract_secret_value"
        diagnostic.write_text(
            f"failure at {root}/source\ntoken={secret}\nAuthorization: Bearer {bearer}\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [sys.executable, str(helper), "--label", "stderr", "--input", str(diagnostic),
             "--redact-root", str(root)],
            check=False,
            capture_output=True,
            text=True,
        )
        require(errors, result.returncode == 0, "safe generator diagnostic helper failed")
        require(errors, secret not in result.stdout and bearer not in result.stdout
                and str(root) not in result.stdout,
                "safe generator diagnostic helper leaked a secret or absolute scratch path")
        require(errors, "[REDACTED" in result.stdout,
                "safe generator diagnostic helper did not mark redacted content")

        oversized = root / "oversized.stderr"
        with oversized.open("wb") as handle:
            handle.seek(512 * 1024)
            handle.write(b"x")
        omitted = subprocess.run(
            [sys.executable, str(helper), "--label", "stderr", "--input", str(oversized)],
            check=False,
            capture_output=True,
            text=True,
        )
        require(errors, omitted.returncode == 0 and "<omitted:" in omitted.stdout,
                "oversized generator diagnostics are not safely omitted")


def check_ios_contract(errors):
    build = (REPO_ROOT / "scripts" / "build-ios.sh").read_text(encoding="utf-8")
    require(errors, "merge-swift-modulemaps.py" in build,
            "iOS build does not use the Swift multi-component merge helper")
    require(errors, "print-safe-diagnostic.py" in build,
            "iOS build does not use the safe generator diagnostic helper")
    require(errors, "UniFFI Swift generator exit code:" in build,
            "iOS build does not expose the generator exit code")
    require(errors, "*.modulemap' | wc -l" not in build,
            "iOS build still assumes a single generated modulemap")
    check_swift_merge_helper(errors)
    check_safe_diagnostic_helper(errors)


def check_repository_contract(errors):
    workflow = (REPO_ROOT / ".github" / "workflows" / "mobile-artifacts.yml").read_text(
        encoding="utf-8"
    )
    manifest = (REPO_ROOT / "scripts" / "create-manifest.py").read_text(encoding="utf-8")
    require(errors, workflow.count("python3 scripts/check-build-contracts.py") == 2,
            "both platform jobs must run the build contract before expensive installation")
    require(errors, workflow.count("python3 scripts/check-manifest-contracts.py") == 2,
            "both platform jobs must run the synthetic manifest contract")
    require(errors, "      - README.md" in workflow,
            "workflow path filters omit a file consumed by the build contract")
    require(errors, 'part.startswith(".")' in manifest,
            "artifact scanner does not reject hidden paths omitted by upload-artifact")
    for token_prefix in ('b"ghr_"', 'b"ghs_"', 'b"ghu_"'):
        require(errors, token_prefix in manifest,
                f"artifact sensitive scanner omits {token_prefix[2:-1]}")


def main():
    errors = []
    pins = load_pins(errors)
    check_android_contract(errors, pins)
    check_ios_contract(errors)
    check_repository_contract(errors)
    if errors:
        for error in errors:
            print(f"contract_error: {error}", file=sys.stderr)
        raise SystemExit(1)
    print("mobile_build_contracts_verified")


if __name__ == "__main__":
    main()
