#!/usr/bin/env python3
import argparse
import os
import re
import stat
from pathlib import Path


MAX_DIAGNOSTIC_BYTES = 512 * 1024
TOKEN_PATTERN = re.compile(
    r"(?i)(?:github_pat_|gh[pousr]_)[A-Za-z0-9_=-]{8,}"
)
CREDENTIAL_PATTERN = re.compile(
    r"(?i)\b(token|password|secret|authorization)\s*[:=]\s*(?:bearer\s+)?[^\r\n]+"
)
URL_CREDENTIAL_PATTERN = re.compile(r"(?i)(https?://)[^\s/:@]+:[^@\s]+@")
PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN [^-\n]*PRIVATE KEY-----.*?-----END [^-\n]*PRIVATE KEY-----",
    re.DOTALL,
)


def fail(message):
    raise SystemExit(message)


def redact(text, roots):
    for root in sorted(set(roots), key=len, reverse=True):
        if root:
            text = text.replace(root, "[REDACTED_PATH]")
    text = PRIVATE_KEY_PATTERN.sub("[REDACTED_PRIVATE_KEY]", text)
    text = URL_CREDENTIAL_PATTERN.sub(r"\1[REDACTED_CREDENTIALS]@", text)
    text = TOKEN_PATTERN.sub("[REDACTED_TOKEN]", text)
    text = CREDENTIAL_PATTERN.sub(lambda match: f"{match.group(1)}=[REDACTED_VALUE]", text)
    return "".join(character for character in text if character in "\n\t" or ord(character) >= 32)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", choices=("stdout", "stderr"), required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--redact-root", action="append", default=[])
    args = parser.parse_args()

    try:
        metadata = os.stat(args.input, follow_symlinks=False)
    except OSError:
        fail("diagnostic file is missing")
    if args.input.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        fail("diagnostic file is unsafe")
    if metadata.st_size > MAX_DIAGNOSTIC_BYTES:
        print(f"{args.label}_diagnostic_begin")
        print("<omitted: diagnostic exceeds the safe output limit>")
        print(f"{args.label}_diagnostic_end")
        return

    try:
        raw = args.input.read_bytes()
    except OSError:
        fail("diagnostic file could not be read")
    text = redact(raw.decode("utf-8", errors="replace"), args.redact_root).rstrip("\n")
    if not text:
        text = "<empty>"
    print(f"{args.label}_diagnostic_begin")
    print(text)
    print(f"{args.label}_diagnostic_end")


if __name__ == "__main__":
    main()
