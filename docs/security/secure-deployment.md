# Secure deployment

The included in-memory application is for development. A production deployment should:

1. Replace in-memory stores with PostgreSQL and encrypted object storage.
2. Authenticate humans with OIDC and services with workload identity.
3. Authorize tenant access outside client-controlled request fields.
4. Run executors in isolated workers with short-lived credentials.
5. Deny direct agent network access to production control planes.
6. Use an organization-owned policy bundle and exact approval service.
7. Export signed run checkpoints to retention-locked storage.
8. Reconcile Git, CI, cloud, Kubernetes, database, registry, and deployment evidence.
9. Define RPO, RTO, backup, restore, retention, deletion, export, and legal-hold procedures.
10. Conduct adversarial tests for bypass, replay, race, crash, and partial-side-effect scenarios.

The API's example `X-ProdKit-Tenant-ID` mechanism is not production authentication. Replace it with
claims from a verified identity context.
