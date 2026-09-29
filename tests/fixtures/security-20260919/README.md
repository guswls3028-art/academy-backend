# Historical ECR policy fixtures

These exact public policy snapshots come from backend commit
`efd2d225196503e4e5ac10ad826f7a736f866981`, before the September 20 stable
glibc/SQLite/GLib security update. They exercise exact-identity, budget and
expiration rejection independently of the current release policy.

Production reads only `docs/ssot/ecr-*.json`. These expired fixtures never
authorize a release. Current-policy tests separately require zero accepted
Critical and zero High findings across all six repositories.
