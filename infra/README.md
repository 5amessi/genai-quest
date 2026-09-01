# Azure infrastructure reference

`main.bicep` is a security-oriented production mapping for the local reference implementation. It is an **unexecuted reference deployment**: no Azure resources were created while preparing this submission, and a successful local run does not prove that the template has been deployed.

## What the template provisions

| Concern | Azure resource | Default posture |
|---|---|---|
| API runtime | Azure Container Apps | Internal environment and non-public ingress unless `allowPublicApi=true`; HTTPS only; system-assigned identity; liveness/readiness probes; 1–10 replicas |
| Retrieval | Azure AI Search Standard | Local/key authentication disabled, public access disabled, private endpoint, semantic ranking enabled |
| Generation/embeddings | Azure OpenAI cognitive account | Managed-identity data-plane access, public access disabled, private endpoint |
| Source documents | StorageV2 blob container | OAuth only, shared keys disabled, blob versioning and soft delete, public access disabled, private endpoint |
| Secrets | Azure Key Vault | RBAC, purge protection, public access disabled, private endpoint |
| Network | VNet, delegated Container Apps subnet, private-endpoint subnet, private DNS zones | Dependencies resolve over Private Link |
| Telemetry | Log Analytics and workspace-based Application Insights | Content logging disabled by application configuration; local auth disabled for Application Insights |

The API identity receives only the data-plane roles required by the current combined API/ingestion reference: Search Index Data Contributor, Cognitive Services OpenAI User, Storage Blob Data Contributor, and Key Vault Secrets User. A scaled production system should split query and ingestion into separate identities so the query API is read-only.

## Deliberate boundaries and gaps

The template does not pretend to be a turnkey production landing zone:

- It does not create an Azure OpenAI model deployment. Model names, versions, capacity, content-filter policy, and regional availability require an explicit business decision.
- It does not build or push the image, create an ACR, create the Search index/schema, or migrate indexed data. CI should publish an immutable digest; a deployment job should then pass that digest.
- It does not create the Entra app registration, groups/app roles, APIM, WAF, DNS forwarding, VPN/ExpressRoute, or client connectivity to the internal endpoint.
- The Container App is private by default. For employee access, put an internal APIM or Application Gateway/WAF in front of it and validate Entra tokens at both edge and API. Setting `allowPublicApi=true` is a conscious exception, not a production recommendation.
- Log Analytics and Application Insights query/ingestion endpoints remain public Azure endpoints so Container Apps log delivery works without an Azure Monitor Private Link Scope. Access is identity-controlled and payload logging is disabled. Regulated environments should add AMPLS/private endpoints and approved DNS before deployment.
- Customer-managed keys, cross-region disaster recovery, backup restoration drills, policy assignments, Defender, budgets, and diagnostic settings for every resource belong in the enterprise platform layer.

## Authentication and authorization

The template sets `AUTH_MODE=entra` and passes tenant/audience configuration. The application must cryptographically validate issuer, audience, signature, expiry, and required claims. Document ACLs are then derived from trusted claims and added to the Azure AI Search filter **before retrieval**. Do not deploy the local demo bearer tokens.

Azure dependencies use the Container App's managed identity. No Search, OpenAI, Storage, or Key Vault access key is passed to the container. Key Vault exists for exceptional secrets that cannot use workload identity; it is not a reason to store credentials unnecessarily.

## Prerequisites

1. An Azure subscription and resource group in an approved region.
2. Permission to create role assignments, private endpoints, Azure OpenAI accounts, and Container Apps environments.
3. Provider registrations for `Microsoft.App`, `Microsoft.Search`, `Microsoft.CognitiveServices`, `Microsoft.Storage`, `Microsoft.KeyVault`, `Microsoft.Insights`, `Microsoft.OperationalInsights`, and `Microsoft.Network`.
4. A reachable container image. Use an immutable digest. For a private ACR, extend the template with a user-assigned image-pull identity and `AcrPull`; do not add a registry password.
5. An Entra application/client ID for the API audience.
6. Confirmed Azure OpenAI quota/model availability and data-residency approval for the selected region.

## Validate before any deployment

Copy the example rather than editing it in place:

```bash
cp infra/main.parameters.example.json infra/main.parameters.local.json
```

Replace the image digest, Entra client ID, owner tag, and region. Keep the local parameters file out of source control if it contains organization-specific identifiers. Then lint and compile:

```bash
az bicep lint --file infra/main.bicep
az bicep build --file infra/main.bicep
```

Preview the exact resource changes:

```bash
az deployment group what-if \
  --resource-group <resource-group> \
  --template-file infra/main.bicep \
  --parameters @infra/main.parameters.local.json
```

Only after peer/security review should an authorized operator run:

```bash
az deployment group create \
  --name kentrick-kp-<commit-sha> \
  --resource-group <resource-group> \
  --template-file infra/main.bicep \
  --parameters @infra/main.parameters.local.json
```

These commands are documentation; they were not run as part of this submission.

## Post-deployment work

Create the data-plane index from `search-index.json` after changing
`content_vector.dimensions` to match the chosen embedding deployment. The Bicep
provisions the Search service but intentionally does not hide this model/index
coupling inside infrastructure deployment.

```bash
az rest --method put \
  --url "https://<search>.search.windows.net/indexes/knowledge-chunks-v1?api-version=2025-09-01" \
  --resource "https://search.azure.com" \
  --body @infra/search-index.json
```

1. Deploy version-pinned chat and embedding models; record deployment/model/prompt/index versions together.
2. Create the Search index with vector, searchable text, version, effective-date, classification, and ACL filter fields; test that ACL filtering occurs server-side.
3. Grant the deployment pipeline—not developers—permission to update revisions and run migrations.
4. Exercise private DNS from the Container App and verify that public data-plane access fails.
5. Run smoke, authorization, adversarial, evaluation, load, failover, and rollback tests.
6. Configure alerts on availability, dependency latency/errors, throttling, refusal anomalies, token/cost budgets, and evaluation regressions.
7. Use single-revision mode for automatic rollback simplicity, or deliberately change to multiple revisions for a canary with a tested traffic-shift procedure.

## Scaling and cost notes

Container replicas are not the first or only scale lever. At 10× document volume, Search partitions/index design and ingestion backpressure are likely to bind first. At 10× query traffic, inspect Search replicas, OpenAI provisioned throughput/quota, connection pools, context size, and cache hit rate before increasing API replicas. `Standard_ZRS`, Search Standard, private endpoints, and a minimum replica incur cost even when idle; use budgets and environment-specific teardown policies for non-production subscriptions.
