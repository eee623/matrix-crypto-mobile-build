# Third-party notices

The generated bindings and native libraries are derived from the public
[Matrix Rust SDK](https://github.com/matrix-org/matrix-rust-sdk) source pinned
to tag `matrix-sdk-0.18.0` and commit
`1c44fb66214667c6d00acaf72ab592493653708b`.

Redistributed generated or native outputs must retain the upstream license and
notices shipped with that exact source revision. Build artifacts include the
exact upstream `LICENSE` file with SHA-256
`0d542e0c8804e39aa7f37eb00da5a762149dc682d7829451287e11b938e94594`.

The validation-only Android AAR is compiled against the fixed public
dependencies `net.java.dev.jna:jna:5.17.0` and
`androidx.annotation:annotation:1.10.0`. Dependency jars are not bundled in the
AAR. JNA 5.17.0 is dual-licensed under LGPL-2.1-or-later or Apache-2.0.
AndroidX Annotation is licensed under the Apache License 2.0; the Android
artifact includes the exact license text pinned by this build repository.
