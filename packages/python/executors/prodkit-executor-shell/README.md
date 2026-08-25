# `prodkit-executor-shell`

`prodkit-executor-shell` is the supported first-party executor for tightly bounded command execution.

It is an optional runtime dependency. Installing it does not authorize arbitrary shell access: commands, working directories, environment exposure, time/resource limits, and credential access must be constrained by deployment-owned configuration. Untrusted models and agents remain proposers and must not receive a general-purpose production shell merely because this adapter exists.

Run privileged command execution in an independently enforced isolation boundary, without host runtime sockets or ambient production credentials, and retain bounded execution evidence. Ambiguous side effects follow the control plane's uncertain-execution rules.

Start with the package exports and executor-isolation/security documentation. Licensed under Apache-2.0; `LICENSE` and `NOTICE` are shipped with the distribution.