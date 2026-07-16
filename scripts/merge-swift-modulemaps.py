#!/usr/bin/env python3
import argparse
import os
import stat
from pathlib import Path


COMPONENTS = (
    "MatrixSDKCrypto",
    "matrix_sdk_common",
    "matrix_sdk_crypto",
)
MAX_GENERATED_FILE_BYTES = 64 * 1024 * 1024


def fail(message):
    raise SystemExit(message)


def direct_file_set(root, suffix):
    return {
        path.name
        for path in root.iterdir()
        if path.name.endswith(suffix) and not path.is_dir()
    }


def validate_regular_file(path):
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except OSError:
        fail(f"missing generated file: {path.name}")
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        fail(f"unsafe generated file: {path.name}")
    if metadata.st_size <= 0 or metadata.st_size > MAX_GENERATED_FILE_BYTES:
        fail(f"generated file size is outside the allowed range: {path.name}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--generated-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    generated_dir = args.generated_dir
    output = args.output
    if not generated_dir.is_dir() or generated_dir.is_symlink():
        fail("generated directory is missing or unsafe")
    if not output.parent.is_dir() or output.parent.is_symlink() or output.exists():
        fail("modulemap output path is missing, unsafe, or already exists")

    expected_swift = {f"{component}.swift" for component in COMPONENTS}
    expected_headers = {f"{component}FFI.h" for component in COMPONENTS}
    expected_modulemaps = {f"{component}FFI.modulemap" for component in COMPONENTS}
    expected_entries = expected_swift | expected_headers | expected_modulemaps
    actual_entries = {path.name for path in generated_dir.iterdir()}
    if actual_entries != expected_entries:
        fail("generated Swift binding entry set mismatch")
    actual_swift = direct_file_set(generated_dir, ".swift")
    actual_headers = direct_file_set(generated_dir, ".h")
    actual_modulemaps = direct_file_set(generated_dir, ".modulemap")

    if actual_swift != expected_swift:
        fail("generated Swift component set mismatch")
    if actual_headers != expected_headers:
        fail("generated header component set mismatch")
    if actual_modulemaps != expected_modulemaps:
        fail("generated modulemap component set mismatch")

    merged_parts = []
    for component in COMPONENTS:
        swift = generated_dir / f"{component}.swift"
        header = generated_dir / f"{component}FFI.h"
        modulemap = generated_dir / f"{component}FFI.modulemap"
        for path in (swift, header, modulemap):
            validate_regular_file(path)
        try:
            modulemap_text = modulemap.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            fail(f"invalid UTF-8 modulemap: {modulemap.name}")
        if (
            f"module {component}FFI" not in modulemap_text
            or f'header "{component}FFI.h"' not in modulemap_text
        ):
            fail(f"modulemap contract mismatch: {modulemap.name}")
        merged_parts.append(modulemap_text.rstrip("\n") + "\n")

    with output.open("x", encoding="utf-8", newline="\n") as handle:
        for part in merged_parts:
            handle.write(part)
            handle.write("\n")

    print(f"swift_component_count={len(actual_swift)}")
    print(f"header_component_count={len(actual_headers)}")
    print(f"modulemap_component_count={len(actual_modulemaps)}")
    print("swift_modulemaps_merged")


if __name__ == "__main__":
    main()
