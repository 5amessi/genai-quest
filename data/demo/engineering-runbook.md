# Deployment Recovery Runbook

## Automatic rollback

After two consecutive failed health checks, the deployment controller rolls back to the last known-good revision. The incident commander posts the rollback reason and revision identifier in #eng-incidents.

## Manual follow-up

Engineering pauses further releases until the service owner confirms recovery and records corrective actions in the incident ticket.

