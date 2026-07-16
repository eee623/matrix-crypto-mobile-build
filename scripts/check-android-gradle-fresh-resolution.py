#!/usr/bin/env python3
import hashlib
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_pins():
    pins = {}
    for line in (REPO_ROOT / "pins" / "source.env").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator != "=" or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise SystemExit(f"invalid pin line: {line!r}")
        pins[key] = value
    return pins


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command, env=None, timeout_seconds=360):
    process = subprocess.Popen(
        [str(part) for part in command],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,
        text=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=10)
        print(f"command timed out after {timeout_seconds}s: {' '.join(map(str, command))}", file=sys.stderr)
        for label, text in (("stdout", stdout), ("stderr", stderr)):
            lines = text.splitlines()
            if lines:
                print(f"{label} tail:", file=sys.stderr)
                print("\n".join(lines[-80:]), file=sys.stderr)
        raise SystemExit(124)
    result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    if result.returncode != 0:
        print(f"command failed with exit {result.returncode}: {' '.join(map(str, command))}", file=sys.stderr)
        for label, text in (("stdout", result.stdout), ("stderr", result.stderr)):
            lines = text.splitlines()
            if lines:
                print(f"{label} tail:", file=sys.stderr)
                print("\n".join(lines[-80:]), file=sys.stderr)
        raise SystemExit(result.returncode)
    return result


def safe_extract_zip(archive, destination):
    with zipfile.ZipFile(archive) as zipped:
        destination_real = destination.resolve()
        for member in zipped.infolist():
            target = (destination / member.filename).resolve()
            if not str(target).startswith(str(destination_real) + os.sep):
                raise SystemExit("unsafe Gradle archive member")
        zipped.extractall(destination)


def write_gradle_project(project, pins):
    (project / "gradle").mkdir(parents=True)
    shutil.copy2(
        REPO_ROOT / "pins" / "android" / "verification-metadata.xml",
        project / "gradle" / "verification-metadata.xml",
    )
    (project / "settings.gradle.kts").write_text(
        """pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
rootProject.name = "MatrixCryptoAgpClasspathVerification"
""",
        encoding="utf-8",
    )
    (project / "build.gradle.kts").write_text(
        f"""buildscript {{
    repositories {{
        google()
        mavenCentral()
        gradlePluginPortal()
    }}
    dependencies {{
        classpath("com.android.tools.build:gradle:{pins["ANDROID_GRADLE_PLUGIN_VERSION"]}")
    }}
}}

tasks.register("verifyAgpClasspath")
tasks.named("verifyAgpClasspath") {{
    doLast {{
        val files = buildscript.configurations.getByName("classpath").resolve()
        require(files.isNotEmpty()) {{ "AGP classpath did not resolve" }}
        println("agp_classpath_files=${{files.size}}")
    }}
}}
""",
        encoding="utf-8",
    )


def main():
    pins = load_pins()
    required = ("GRADLE_VERSION", "GRADLE_SHA256", "ANDROID_GRADLE_PLUGIN_VERSION")
    missing = [key for key in required if key not in pins]
    if missing:
        raise SystemExit(f"missing pins: {', '.join(missing)}")

    curl = shutil.which("curl")
    if curl is None:
        raise SystemExit("curl is required")

    with tempfile.TemporaryDirectory(prefix="matrix-gradle-fresh-") as scratch_name:
        scratch = Path(scratch_name)
        gradle_version = pins["GRADLE_VERSION"]
        gradle_zip = scratch / f"gradle-{gradle_version}-bin.zip"
        gradle_home = scratch / f"gradle-{gradle_version}"
        project = scratch / "validation-classpath"
        gradle_user_home = scratch / "gradle-user-home"

        run(
            [
                curl,
                "--fail",
                "--location",
                "--silent",
                "--show-error",
                "--retry",
                "5",
                "--retry-all-errors",
                "--output",
                gradle_zip,
                f"https://downloads.gradle.org/distributions/gradle-{gradle_version}-bin.zip",
            ]
        )
        if sha256(gradle_zip) != pins["GRADLE_SHA256"]:
            raise SystemExit("Gradle distribution hash mismatch")
        safe_extract_zip(gradle_zip, scratch)

        gradle = gradle_home / "bin" / "gradle"
        if not gradle.is_file():
            raise SystemExit("Gradle executable missing after extraction")
        gradle.chmod(gradle.stat().st_mode | 0o755)

        project.mkdir()
        write_gradle_project(project, pins)

        env = os.environ.copy()
        env["GRADLE_USER_HOME"] = str(gradle_user_home)
        env["GRADLE_OPTS"] = "-Dorg.gradle.daemon=false -Dorg.gradle.console=plain"
        base_command = [
            gradle,
            "--dependency-verification",
            "strict",
            "--console",
            "plain",
            "--max-workers",
            "2",
            "--no-daemon",
            "--project-dir",
            project,
            "verifyAgpClasspath",
        ]
        run(base_command, env=env)
        run([*base_command, "--offline"], env=env)

    print("android_gradle_fresh_resolution_verified")


if __name__ == "__main__":
    main()
