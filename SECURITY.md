# Security Policy

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Send a private report to
`security@prodkit.dev` with:

- affected version or commit;
- deployment assumptions;
- reproduction steps;
- impact and possible mitigations;
- whether the issue is already public.

Until a dedicated security advisory process is configured, maintainers will acknowledge reports
on a best-effort basis. Do not send production credentials, private keys, or personal data.

## Supported versions

The project is pre-1.0. Security fixes are applied to the latest release line. Earlier snapshots
may not receive backports.

## Security boundary

This software cannot provide a complete audit record for actions that bypass its broker or use
credentials outside controlled executors. Treat bypass detection and external reconciliation as
mandatory production controls.
