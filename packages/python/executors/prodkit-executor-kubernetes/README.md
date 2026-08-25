# `prodkit-executor-kubernetes`

Fail-closed Kubernetes Deployment executor for production rollout effects.

The executor talks to an explicit HTTPS Kubernetes API server, requires namespace/deployment/operation allowlists and a short-lived bearer credential lease, and supports only constrained Deployment mutations: immutable-digest image changes, bounded replica changes, and explicit restarts. It does not accept arbitrary Kubernetes manifests or cluster-wide resource paths. Callers may inject an `httpx.AsyncClient` configured with the cluster CA/mTLS policy required by their environment.
