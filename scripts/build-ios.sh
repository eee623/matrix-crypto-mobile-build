#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: build-ios.sh OUTPUT_DIR" >&2
  exit 64
fi

repo_root="$(cd "$(dirname "$0")/.." && pwd -P)"
source "$repo_root/pins/source.env"

: "${RUNNER_TEMP:?RUNNER_TEMP is required}"
: "${DEVELOPER_DIR:?DEVELOPER_DIR is required}"

output_dir="$1"
scratch="$RUNNER_TEMP/matrix-crypto-ios"
archive="$scratch/matrix-rust-sdk.tar.gz"
source_parent="$scratch/source"
source_root="$source_parent/matrix-rust-sdk-$MATRIX_TAG"
target_dir="$scratch/target"
generated_dir="$scratch/generated"

test ! -e "$scratch"
test ! -e "$output_dir"
mkdir -p "$source_parent" "$generated_dir"

curl --http1.1 --fail --location --silent --show-error \
  --retry 5 --retry-all-errors \
  --output "$archive" \
  "$MATRIX_ARCHIVE_URL"

test "$(stat -f '%z' "$archive")" = "$MATRIX_ARCHIVE_BYTES"
test "$(shasum -a 256 "$archive" | awk '{print $1}')" = "$MATRIX_ARCHIVE_SHA256"

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
test "$(shasum -a 256 "$source_root/Cargo.lock" | awk '{print $1}')" = \
  "$MATRIX_ORIGINAL_LOCK_SHA256"

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
  test "$(shasum -a 256 Cargo.lock | awk '{print $1}')" = \
    "$MATRIX_ORIGINAL_LOCK_SHA256"
  git apply --check --unidiff-zero "$repo_root/pins/Cargo.lock.overlay"
  git apply --unidiff-zero "$repo_root/pins/Cargo.lock.overlay"
)

test "$(shasum -a 256 "$repo_root/pins/Cargo.lock.overlay" | awk '{print $1}')" = \
  "$MATRIX_LOCK_OVERLAY_SHA256"
test "$(shasum -a 256 "$source_root/Cargo.lock" | awk '{print $1}')" = \
  "$MATRIX_EFFECTIVE_LOCK_SHA256"
test "$(shasum -a 256 "$source_root/bindings/matrix-sdk-crypto-ffi/uniffi.toml" | awk '{print $1}')" = \
  "$MATRIX_UNIFFI_SHA256"
test "$(shasum -a 256 "$source_root/LICENSE" | awk '{print $1}')" = \
  "$MATRIX_LICENSE_SHA256"

test "$(xcodebuild -version | sed -n '1p')" = "Xcode $IOS_XCODE_VERSION"
test "$(xcodebuild -version | sed -n '2p')" = "Build version $IOS_XCODE_BUILD"
test "$(protoc --version)" = "libprotoc $PROTOC_VERSION"
rustup toolchain install "$RUST_TOOLCHAIN" --profile minimal --no-self-update
rustup component add llvm-tools-preview --toolchain "$RUST_TOOLCHAIN"
rustup target add --toolchain "$RUST_TOOLCHAIN" \
  "$IOS_DEVICE_TARGET" "$IOS_SIMULATOR_TARGET"
cargo "+$RUST_TOOLCHAIN" --version | grep -Eq "^cargo $RUST_TOOLCHAIN "
rustc "+$RUST_TOOLCHAIN" --version | grep -Eq "^rustc $RUST_TOOLCHAIN "
rustc_sysroot="$(rustc "+$RUST_TOOLCHAIN" --print sysroot)"
rustc_host="$(rustc "+$RUST_TOOLCHAIN" -vV | sed -n 's/^host: //p')"
llvm_nm="$rustc_sysroot/lib/rustlib/$rustc_host/bin/llvm-nm"
test -x "$llvm_nm"

export CARGO_INCREMENTAL=0
export IPHONEOS_DEPLOYMENT_TARGET="$IOS_MINIMUM_OS"
export SOURCE_DATE_EPOCH

cd "$source_root"
CARGO_TARGET_DIR="$target_dir" \
  cargo "+$RUST_TOOLCHAIN" build --locked \
    --manifest-path "$source_root/Cargo.toml" \
    -p "$MATRIX_PACKAGE" --release \
    --target "$IOS_DEVICE_TARGET" \
    --no-default-features --features "$MATRIX_FEATURES"
CARGO_TARGET_DIR="$target_dir" \
  cargo "+$RUST_TOOLCHAIN" build --locked \
    --manifest-path "$source_root/Cargo.toml" \
    -p "$MATRIX_PACKAGE" --release \
    --target "$IOS_SIMULATOR_TARGET" \
    --no-default-features --features "$MATRIX_FEATURES"

device_native="$target_dir/$IOS_DEVICE_TARGET/release/libmatrix_sdk_crypto_ffi.a"
simulator_native="$target_dir/$IOS_SIMULATOR_TARGET/release/libmatrix_sdk_crypto_ffi.a"
test -s "$device_native"
test -s "$simulator_native"
device_symbols="$scratch/device-symbols.txt"
simulator_symbols="$scratch/simulator-symbols.txt"
"$llvm_nm" -gUj "$device_native" > "$device_symbols"
"$llvm_nm" -gUj "$simulator_native" > "$simulator_symbols"
test -s "$device_symbols"
test -s "$simulator_symbols"
grep -Eq '^_?(ffi|uniffi)_[A-Za-z0-9_]+$' "$device_symbols"
grep -Eq '^_?(ffi|uniffi)_[A-Za-z0-9_]+$' "$simulator_symbols"

CARGO_TARGET_DIR="$target_dir" \
  cargo "+$RUST_TOOLCHAIN" run --locked \
    --manifest-path "$source_root/Cargo.toml" \
    -p "$MATRIX_PACKAGE" --release \
    --bin matrix_sdk_crypto_ffi \
    --no-default-features --features "$MATRIX_FEATURES" \
    -- generate --language swift --library "$device_native" \
    --out-dir "$generated_dir"

mkdir -p "$output_dir/swift" "$output_dir/headers" "$output_dir/LICENSES"
find "$generated_dir" -maxdepth 1 -type f -name '*.swift' \
  -exec cp {} "$output_dir/swift/" \;
find "$generated_dir" -maxdepth 1 -type f -name '*.h' \
  -exec cp {} "$output_dir/headers/" \;
test "$(find "$output_dir/swift" -type f -name '*.swift' | wc -l | tr -d '[:space:]')" -gt 0
test "$(find "$output_dir/headers" -type f -name '*.h' | wc -l | tr -d '[:space:]')" -gt 0
test "$(find "$generated_dir" -maxdepth 1 -type f -name '*.modulemap' | wc -l | tr -d '[:space:]')" = "1"
find "$generated_dir" -maxdepth 1 -type f -name '*.modulemap' \
  -exec cp {} "$output_dir/headers/module.modulemap" \;

xcodebuild -create-xcframework \
  -library "$device_native" -headers "$output_dir/headers" \
  -library "$simulator_native" -headers "$output_dir/headers" \
  -output "$output_dir/MatrixSDKCryptoFFI.xcframework"

test -s "$output_dir/MatrixSDKCryptoFFI.xcframework/Info.plist"
cp "$source_root/LICENSE" "$output_dir/LICENSES/MATRIX_RUST_SDK_LICENSE"
cp "$repo_root/THIRD_PARTY_NOTICES.md" "$output_dir/THIRD_PARTY_NOTICES.md"
