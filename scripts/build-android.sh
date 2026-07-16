#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: build-android.sh OUTPUT_DIR" >&2
  exit 64
fi

repo_root="$(cd "$(dirname "$0")/.." && pwd -P)"
source "$repo_root/pins/source.env"

: "${RUNNER_TEMP:?RUNNER_TEMP is required}"
: "${ANDROID_NDK_HOME:?ANDROID_NDK_HOME is required}"

output_dir="$1"
scratch="$RUNNER_TEMP/matrix-crypto-android"
archive="$scratch/matrix-rust-sdk.tar.gz"
source_parent="$scratch/source"
source_root="$source_parent/matrix-rust-sdk-$MATRIX_TAG"
target_dir="$scratch/target"
generated_dir="$scratch/generated"
gradle_zip="$scratch/gradle-$GRADLE_VERSION-bin.zip"
gradle_home="$scratch/gradle-$GRADLE_VERSION"
gradle_project="$scratch/validation-aar"

test ! -e "$scratch"
test ! -e "$output_dir"
mkdir -p "$source_parent" "$generated_dir"

curl --http1.1 --fail --location --silent --show-error \
  --retry 5 --retry-all-errors \
  --output "$archive" \
  "$MATRIX_ARCHIVE_URL"

test "$(stat -c '%s' "$archive")" = "$MATRIX_ARCHIVE_BYTES"
printf '%s  %s\n' "$MATRIX_ARCHIVE_SHA256" "$archive" | sha256sum --check --status

python3 - "$archive" <<'PY'
import sys
import tarfile
from pathlib import PurePosixPath

with tarfile.open(sys.argv[1], "r:gz") as archive:
    for member in archive.getmembers():
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit("unsafe upstream archive path")
        if not (member.isfile() or member.isdir()):
            raise SystemExit("unsafe upstream archive member")
PY

tar -xzf "$archive" -C "$source_parent"
test -d "$source_root"
printf '%s  %s\n' "$MATRIX_ORIGINAL_LOCK_SHA256" "$source_root/Cargo.lock" \
  | sha256sum --check --status

(
  cd "$source_root"
  git init --quiet
  git config core.autocrlf false
  git config core.filemode true
  git remote add origin https://github.com/matrix-org/matrix-rust-sdk.git
  git fetch --quiet --no-tags --depth=1 origin \
    "refs/tags/$MATRIX_TAG:refs/tags/$MATRIX_TAG"
  test "$(git cat-file -t "refs/tags/$MATRIX_TAG")" = "tag"
  test "$(git rev-parse "refs/tags/$MATRIX_TAG^{commit}")" = "$MATRIX_COMMIT"
  git reset --hard --quiet "$MATRIX_COMMIT"
  git clean -ffdx --quiet
  printf '%s  %s\n' "$MATRIX_ORIGINAL_LOCK_SHA256" Cargo.lock \
    | sha256sum --check --status
  git apply --check --unidiff-zero "$repo_root/pins/Cargo.lock.overlay"
  git apply --unidiff-zero "$repo_root/pins/Cargo.lock.overlay"
)

printf '%s  %s\n' "$MATRIX_LOCK_OVERLAY_SHA256" "$repo_root/pins/Cargo.lock.overlay" \
  | sha256sum --check --status
printf '%s  %s\n' "$MATRIX_EFFECTIVE_LOCK_SHA256" "$source_root/Cargo.lock" \
  | sha256sum --check --status
printf '%s  %s\n' "$MATRIX_UNIFFI_SHA256" \
  "$source_root/bindings/matrix-sdk-crypto-ffi/uniffi.toml" \
  | sha256sum --check --status
printf '%s  %s\n' "$MATRIX_DEPENDENCY_UNIFFI_SHA256" \
  "$source_root/crates/matrix-sdk-crypto/uniffi.toml" \
  | sha256sum --check --status
printf '%s  %s\n' "$MATRIX_DEPENDENCY_UNIFFI_SHA256" \
  "$source_root/crates/matrix-sdk-common/uniffi.toml" \
  | sha256sum --check --status
printf '%s  %s\n' "$MATRIX_LICENSE_SHA256" "$source_root/LICENSE" \
  | sha256sum --check --status
printf '%s  %s\n' "$THIRD_PARTY_NOTICES_SHA256" "$repo_root/THIRD_PARTY_NOTICES.md" \
  | sha256sum --check --status
printf '%s  %s\n' "$ANDROIDX_ANNOTATION_LICENSE_SHA256" \
  "$repo_root/pins/licenses/ANDROIDX_ANNOTATION_LICENSE" \
  | sha256sum --check --status
printf '%s  %s\n' "$ANDROID_DEPENDENCY_LOCK_SHA256" \
  "$repo_root/pins/android/gradle.lockfile" \
  | sha256sum --check --status
printf '%s  %s\n' "$ANDROID_DEPENDENCY_VERIFICATION_SHA256" \
  "$repo_root/pins/android/verification-metadata.xml" \
  | sha256sum --check --status

test "$(sed -n 's/^Pkg.Revision = //p' "$ANDROID_NDK_HOME/source.properties")" = \
  "$ANDROID_NDK_VERSION"
test "$(protoc --version)" = "libprotoc $PROTOC_VERSION"
cargo ndk --version | grep -F "cargo-ndk $CARGO_NDK_VERSION"
rustup toolchain install "$RUST_TOOLCHAIN" --profile minimal --no-self-update
rustup target add --toolchain "$RUST_TOOLCHAIN" "$ANDROID_TARGET"
cargo "+$RUST_TOOLCHAIN" --version | grep -Eq "^cargo $RUST_TOOLCHAIN "
rustc "+$RUST_TOOLCHAIN" --version | grep -Eq "^rustc $RUST_TOOLCHAIN "

export ANDROID_NDK="$ANDROID_NDK_HOME"
export CARGO_INCREMENTAL=0
export SOURCE_DATE_EPOCH

cd "$source_root"
CARGO_TARGET_DIR="$target_dir" \
  cargo "+$RUST_TOOLCHAIN" ndk \
    --target "$ANDROID_ABI" --platform "$ANDROID_MINIMUM_API" \
    build --locked \
    --manifest-path "$source_root/Cargo.toml" \
    -p "$MATRIX_PACKAGE" --release \
    --no-default-features --features "$MATRIX_FEATURES"

native="$target_dir/$ANDROID_TARGET/release/libmatrix_sdk_crypto_ffi.so"
test -s "$native"

CARGO_TARGET_DIR="$target_dir" \
  cargo "+$RUST_TOOLCHAIN" run --locked \
    --manifest-path "$source_root/Cargo.toml" \
    -p "$MATRIX_PACKAGE" --release \
    --bin matrix_sdk_crypto_ffi \
    --no-default-features --features "$MATRIX_FEATURES" \
    -- generate --language kotlin --library "$native" \
    --out-dir "$generated_dir"

mkdir -p "$output_dir/kotlin" "$output_dir/jni/$ANDROID_ABI" \
  "$output_dir/aar" "$output_dir/LICENSES"
while IFS= read -r -d '' kotlin_file; do
  relative_path="${kotlin_file#"$generated_dir/"}"
  mkdir -p "$output_dir/kotlin/$(dirname "$relative_path")"
  cp "$kotlin_file" "$output_dir/kotlin/$relative_path"
done < <(find "$generated_dir" -type f -name '*.kt' -print0)
cp "$native" "$output_dir/jni/$ANDROID_ABI/libmatrix_sdk_crypto_ffi.so"
test "$(find "$output_dir/kotlin" -type f -name '*.kt' | wc -l | tr -d '[:space:]')" -gt 0
test -s "$output_dir/jni/$ANDROID_ABI/libmatrix_sdk_crypto_ffi.so"

curl --fail --location --silent --show-error \
  --retry 5 --retry-all-errors \
  --output "$gradle_zip" \
  "https://downloads.gradle.org/distributions/gradle-$GRADLE_VERSION-bin.zip"
printf '%s  %s\n' "$GRADLE_SHA256" "$gradle_zip" | sha256sum --check --status
unzip -q "$gradle_zip" -d "$scratch"
test -x "$gradle_home/bin/gradle"
"$gradle_home/bin/gradle" --version | grep -F "Gradle $GRADLE_VERSION"

mkdir -p "$gradle_project/src/main/kotlin" \
  "$gradle_project/src/main/jniLibs/$ANDROID_ABI" "$gradle_project/gradle"
cp -R "$output_dir/kotlin/". "$gradle_project/src/main/kotlin/"
cp "$native" "$gradle_project/src/main/jniLibs/$ANDROID_ABI/libmatrix_sdk_crypto_ffi.so"
cp "$repo_root/pins/android/gradle.lockfile" "$gradle_project/gradle.lockfile"
cp "$repo_root/pins/android/verification-metadata.xml" \
  "$gradle_project/gradle/verification-metadata.xml"

cat > "$gradle_project/settings.gradle.kts" <<'GRADLE'
pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}
rootProject.name = "MatrixCryptoValidationArtifact"
GRADLE

cat > "$gradle_project/build.gradle.kts" <<GRADLE
plugins {
    id("com.android.library") version "$ANDROID_GRADLE_PLUGIN_VERSION"
}

android {
    namespace = "org.matrix.rustcomponents.sdk.crypto.validation"
    compileSdk = 37

    defaultConfig {
        minSdk = $ANDROID_MINIMUM_API
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlin {
        compilerOptions {
            jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
        }
    }

    packaging {
        jniLibs {
            keepDebugSymbols += "**/libmatrix_sdk_crypto_ffi.so"
        }
    }
}

dependencies {
    api("net.java.dev.jna:jna:$JNA_VERSION")
    api("androidx.annotation:annotation:$ANDROIDX_ANNOTATION_VERSION")
}

dependencyLocking {
    lockAllConfigurations()
}
GRADLE

cat > "$gradle_project/src/main/AndroidManifest.xml" <<'XML'
<manifest xmlns:android="http://schemas.android.com/apk/res/android" />
XML

"$gradle_home/bin/gradle" --dependency-verification strict \
  --no-daemon --project-dir "$gradle_project" assembleRelease
aar_path="$(find "$gradle_project/build/outputs/aar" -type f -name '*release.aar' | sort | head -n 1)"
test -s "$aar_path"
cp "$aar_path" "$output_dir/aar/matrix-sdk-crypto-ffi-validation.aar"
cp "$source_root/LICENSE" "$output_dir/LICENSES/MATRIX_RUST_SDK_LICENSE"
cp "$repo_root/pins/licenses/ANDROIDX_ANNOTATION_LICENSE" \
  "$output_dir/LICENSES/ANDROIDX_ANNOTATION_LICENSE"
cp "$repo_root/THIRD_PARTY_NOTICES.md" "$output_dir/THIRD_PARTY_NOTICES.md"
