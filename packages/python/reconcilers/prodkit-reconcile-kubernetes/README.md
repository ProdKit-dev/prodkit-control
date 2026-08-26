# `prodkit-reconcile-kubernetes`

`prodkit-reconcile-kubernetes` is the supported first-party boundary for observing Kubernetes workload state and comparing it with the controlled deployment/action record.

It is optional for deployments that do not use Kubernetes, but is a supported implementation. Observation scope should be namespace/resource bounded and use least-privilege read credentials distinct from mutation authority where practical. Exact image digests, generation/resource identity, target namespace, and observation freshness are important evidence dimensions.

Do not treat the executor's own success response as independent reconciliation. Required mismatches, missing resources, or unavailable evidence remain explicit outcomes.

Licensed under Apache-2.0; `LICENSE` and `NOTICE` are shipped with the distribution.