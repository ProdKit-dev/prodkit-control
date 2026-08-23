# Capacity and overload envelope

ProdKit Control publishes conservative capacity envelopes instead of implying unlimited horizontal scalability. A deployment is supported only when its storage, provider APIs, network, and control-plane replicas are operated within a measured envelope appropriate to that deployment.

## v0.4.0 reference HA envelope

The built-in `REFERENCE_CAPACITY_ENVELOPE` is the deterministic qualification profile exercised in CI. It is a control-plane safety envelope, not a latency or provider-throughput SLA.

| Dimension | Qualified value |
| --- | ---: |
| Active durable work per queue | 1,000 |
| In-flight work per replica | 128 |
| In-flight work per tenant per replica | 32 |
| Default scheduler lease TTL | 30 seconds |
| Graceful shutdown budget | 30 seconds |
| Concurrent qualification workers/contenders | 128 |
| Work items in load qualification | 1,000 |
| Continuous lease soak | 10 seconds |

The release gate exercises the exact declared queue bound, concurrent lease acquisition, parallel draining of the qualified work set, failover after an in-flight external-effect acknowledgment, stale-owner rejection, and a sustained lease acquire/release soak.

## What this envelope means

The values above establish that the shipped algorithms and contracts remain bounded and correct at those dimensions in the repository's qualification environment. They do **not** mean every deployment can execute 128 external API calls concurrently. Real safe throughput is the minimum of:

- the control-plane admission envelope;
- database connection/transaction capacity;
- provider quotas and rate limits;
- executor-specific concurrency constraints;
- tenant policy limits;
- network and CPU/memory capacity;
- downstream retry and reconciliation capacity.

Operators should lower limits when any dependency has a smaller safe envelope. Raising limits above the published profile requires local load, failover, and soak qualification with production-like dependencies.

## Backpressure policy

ProdKit uses bounded admission rather than unbounded buffering.

- `CapacityAdmissionController` rejects new in-flight work when the global or tenant limit is exhausted.
- `DurableWorkQueue` rejects new unique work when the active queue reaches its configured bound.
- idempotent duplicate enqueue of the same work identity remains safe even when the queue is full because it does not increase active depth.
- retries remain bounded by `max_attempts` and terminal failures move to `dead_letter`.
- callers should use bounded exponential backoff with jitter for retryable overload; they must not spin in a tight retry loop.

## Sizing a production deployment

Start with observed service times rather than a desired request rate. For each work class, estimate:

`required concurrency ≈ arrival rate × p95 service time`

Then constrain that result by provider quotas and database capacity. Keep headroom for reconciliation, failover, and rolling replacement. For tenant fairness, choose a per-tenant limit below the global limit so one tenant cannot occupy every slot.

Queue depth should cover a bounded burst, not an indefinite outage. If a dependency can be unavailable for hours, model that outage explicitly: pause admission, divert work to a durable external queue with a documented retention policy, or reject upstream work. Do not solve an unbounded outage by setting an unbounded queue.

## Metrics and alerts

Production adapters should expose at least:

- queue depth by queue and tenant class;
- oldest queued work age;
- leased work count and lease-expiry rate;
- stale-fence rejection count;
- retry and dead-letter rates;
- global/per-tenant admission rejections;
- in-flight count;
- drain duration and forced-shutdown count;
- PostgreSQL transaction latency, lock waits, pool saturation, replication lag, and failover state;
- external provider throttling and retry-after signals.

Alerting should focus on sustained saturation or increasing work age rather than a single short burst.

## Requalification

Re-run the v0.4 scale gate and deployment-specific load/failover tests when changing any of:

- queue or in-flight limits;
- PostgreSQL topology/version/pool sizing;
- scheduler or lease implementation;
- executor concurrency;
- retry policy;
- provider APIs or quotas;
- container CPU/memory requests or limits;
- replica count or rollout strategy.

A higher benchmark result is not automatically a new supported product envelope. Update the published envelope only in a reviewed release with reproducible evidence.
