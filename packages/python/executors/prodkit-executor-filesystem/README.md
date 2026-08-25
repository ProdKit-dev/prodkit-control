# `prodkit-executor-filesystem`

`prodkit-executor-filesystem` is the supported first-party executor for bounded filesystem effects.

It is an optional runtime dependency: the canonical core does not require filesystem access, but this package is a supported implementation when filesystem capability is selected. Operators must constrain the executor to explicitly permitted roots and operations and isolate it from unrelated host paths and credentials.

Filesystem effects remain subject to ProdKit Control action authorization, idempotency, evidence, and executor-isolation rules. Do not mount a broad host filesystem or production secret directories into an untrusted agent environment merely because this executor is installed.

Start with the executor exports in this package and the repository extension/deployment documentation. Licensed under Apache-2.0; `LICENSE` and `NOTICE` are shipped with the distribution.