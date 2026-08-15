# Observability

OpenTelemetry spans, logs, and metrics are projections from canonical events and runtime services.
They support operations, latency analysis, model/provider cost analysis, error diagnosis, and alerting.

Recommended correlations:

- W3C trace ID and span ID;
- run, event, action, approval, execution-attempt, and verification IDs;
- provider request ID;
- CI workflow, deployment, cloud request, database transaction, and Kubernetes audit IDs.

Do not put secret values, unrestricted prompts, raw personal data, or credential material into span
attributes. Export full content only through the artifact-retention policy.
