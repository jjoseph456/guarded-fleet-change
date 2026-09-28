# Security

## Scope

`fleetguard` plans, checks, and approves rollouts from local files. It runs
Ansible only through `fleetguard run`, which defaults to `--check --diff` and
requires a digest-bound approval before `--apply`.

Do not place real credentials, private hostnames, inventories, signing keys,
customer information, or employer material in this repository. Every host,
domain, and realm here is synthetic and uses the reserved `example.test`
domain.

## Reporting

Report a suspected vulnerability privately to the repository owner. Do not
include live credentials or confidential production data in a public issue.

## Known Limits

- Approval files are unsigned JSON. They prevent accidental drift, not a
  malicious actor with write access. Signed approvals are on the roadmap.
- The policy gate trusts the fleet facts it is given. Stale inventory data
  produces a confidently wrong plan.
