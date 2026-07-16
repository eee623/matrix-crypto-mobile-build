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
