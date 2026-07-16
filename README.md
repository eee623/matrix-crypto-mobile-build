# Matrix Crypto Mobile Build

This public repository builds validation-only mobile artifacts from the public
Matrix Rust SDK source pinned to `matrix-sdk-0.18.0` at commit
`1c44fb66214667c6d00acaf72ab592493653708b`.

## Security boundary

The repository may contain only public upstream source pins, minimal build
automation, license and attribution material, canonical manifests, checksums,
and generated validation artifacts derived from that public source.

It must never contain consumer application source, private repository history,
user or device data, cryptographic state or keys, credentials, endpoints,
private provider configuration, production activation, caches, source dumps,
or raw runtime logs. Workflows use only the default `GITHUB_TOKEN`, require no
repository secrets, and publish short-lived validation artifacts only.

This repository is a build boundary, not an application runtime and not a
production distribution channel.

## Validation artifacts

The owner-only workflow builds two short-lived artifacts in parallel:

- `matrix-crypto-mobile-validation-ios`: generated Swift/header/modulemap and
  an XCFramework containing arm64 device and Apple Silicon simulator slices.
- `matrix-crypto-mobile-validation-android`: generated Kotlin, an arm64-v8a
  native library, and a validation-only AAR.

The validation AAR is a file artifact rather than a Maven publication. A
consumer must add the pinned public dependency `net.java.dev.jna:jna:5.17.0`
when compiling or running the generated Kotlin bindings.

Every artifact contains a canonical `manifest.json`, `SHA256SUMS`, and upstream
license attribution. Artifact retention is one day. A consumer must verify the
workflow commit, event, branch, complete file allowlist, and all checksums before
using an artifact for validation.
