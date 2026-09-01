# Threat model

- System: Enterprise Knowledge Intelligence Platform
- Review date: 2026-08-31
- Method: data-flow analysis informed by STRIDE and OWASP risks for LLM applications
- Scope: authenticated query path, document ingestion, production Azure dependencies, deployment and telemetry

## Security objective

An authenticated employee may receive only evidence they are currently authorized to read, and the system may synthesize only claims supportable by that evidence. Malicious users, documents, model output and dependency failures must not turn the service into a cross-tenant disclosure path, credential oracle or tool-execution proxy.

Security here is a system property, not a prompt. The highest-priority invariant is:

> Tenant and ACL predicates execute inside retrieval before any candidate text is returned to the application or model.

## Assets and impact

| Asset | Security property | Impact if compromised |
|---|---|---|
| HR, Legal, Finance, Security and other source content | Confidentiality, integrity, residency, retention | Regulatory/contractual exposure, employee harm, privilege loss, business leakage |
| Document ACL, authority, version and lifecycle metadata | Integrity and freshness | Silent cross-tenant disclosure or use of obsolete policy |
| Entra tokens, workload identity tokens and residual secrets | Confidentiality and bounded use | Impersonation, lateral movement, data/model access |
| Search index and embeddings | Confidentiality, integrity, availability | Corpus leakage/poisoning; search outage; embeddings can still reveal sensitive information |
| Prompt, router, model, index and policy configuration | Integrity and provenance | Systematic unsafe answers, bypassed refusals or unreviewed provider/data route |
| Answers and citations | Integrity, confidentiality, traceability | Plausible misinformation, unsupported decisions, disclosure |
| Audit/quality telemetry | Integrity and controlled confidentiality | Missed incidents or a secondary sensitive-data repository |
| Service budget and provider quota | Availability and bounded cost | Denial of service or unplanned spend |

## Actors and assumptions

- **Ordinary employee:** authenticated but may be curious, compromised or attempt to access another department.
- **Privileged publisher/source owner:** can add or change documents in an authorized scope; may make a mistake or become malicious.
- **Tenant/security administrator:** controls entitlements and platform configuration; powerful actions require separation of duties, PIM and audit.
- **External attacker:** targets the public edge, dependencies, parsers, software supply chain or stolen tokens.
- **Document author/third party:** may place malicious instructions, links, secrets or exploit payloads in an otherwise valid file.
- **Model/provider:** probabilistic and fallible; model output is untrusted. The Azure service is an approved processor, not part of the authorization mechanism.

We assume Entra tenant administration, the governed document catalog and human source ownership are trustworthy dependencies. Compromise of those control planes is mitigated by least privilege, monitoring and recovery but cannot be solved by RAG logic.

## Trust boundaries and attack surfaces

The numbered boundaries correspond to [the production diagram](architecture.md#deployment-and-trust-boundaries).

1. **Public device → Entra/APIM (TB0→TB1):** bearer tokens, untrusted JSON, replay, flooding and protocol attacks.
2. **APIM → FastAPI (TB1→TB2):** edge misconfiguration, spoofed identity headers, oversize requests and confused-deputy risk.
3. **Application → Azure PaaS (TB2→TB3):** workload identity/RBAC, private DNS/network policy, SDK/query construction and dependency compromise.
4. **Search result → prompt/model:** indirect prompt injection, malicious content, citation confusion and data exfiltration.
5. **Publisher/Blob → ingestion worker/index:** poisoned files, unsafe parsing, event spoofing, duplicate/partial/version races and forged ACL metadata.
6. **Workloads → telemetry/CI:** prompt/content leakage, malicious log text, artifact/config tampering and dependency supply chain.

## Non-negotiable security requirements

- Production validates JWT signature, pinned algorithm, issuer, audience, tenant, lifetime and required claims at APIM and FastAPI. It never trusts caller-supplied identity/role headers.
- Clients cannot supply Search filters, index/model/deployment names, prompt templates, source URIs or tool definitions.
- The server builds a typed, escaped filter from validated principal claims and trusted metadata; Azure vector filtering is `preFilter`.
- Query and ingestion use different managed identities. Query has read-only Search access; the model has no identity or data connection.
- Retrieved content is delimited and labeled untrusted. The generation call has no tools and a bounded, versioned output schema.
- Only supplied chunk IDs may be cited. Unsupported, malformed or policy-violating output is refused.
- New document versions remain inactive until parsing, embedding and indexing complete. ACL revocation favors a temporary gap over an exposure window.
- Raw questions, chunks, prompts, answers, tokens and secret-bearing URLs are excluded from ordinary logs/traces.
- Search/model/storage public access is disabled in production; data-plane calls use private endpoints and managed identity.
- Security tests have a zero-tolerance gate for unauthorized/cross-tenant retrieval.

## Threat register

Risk is the design-time residual rating after the listed controls; production acceptance requires validation by the security/data owners.

| ID | Threat and attack path | Primary controls | Detection / evidence | Residual risk |
|---|---|---|---|---|
| TM-01 | **Forged, replayed or wrong-audience token** reaches the API | Entra MFA/conditional access; APIM and API cryptographic JWT validation; pinned issuer/audience/algorithms; TLS; short token lifetime; no token logging | Auth failure category, issuer/audience counters, Entra sign-in risk logs | Medium: a valid stolen token remains useful until expiry/revocation |
| TM-02 | **Spoofed `user`, group, role or tenant header** creates a more privileged principal | Ignore such headers in production; derive identity only from validated claims; strip edge headers; production startup forbids local-auth adapter | Configuration attestation, tests that spoof every identity header, startup alert | Low if deployment policy prevents local mode |
| TM-03 | **Cross-tenant/ACL retrieval** through missing filter, OData injection or a post-filter implementation | Typed server-owned filter builder; tenant + active + ACL predicate in the same Search request; `vectorFilterMode=preFilter`; read-only API identity; returned-metadata tripwire | Property/fuzz tests, synthetic canary documents, filter version/hash and result IDs, zero-tolerance eval gate | Medium: a filter/schema regression is catastrophic despite layers |
| TM-04 | **Stale/oversized group membership** grants revoked access or fails open on Entra overage | Prefer app roles/entitlement groups; bounded trusted resolver; short cache; fail closed; ACL/catalog reconciliation; emergency deny/tombstone | Resolver failures, entitlement cache age, ACL hash drift and group-change audit | Medium: token/cache revocation has a finite propagation window |
| TM-05 | **Indirect prompt injection in an authorized document** tells the model to reveal secrets, ignore policy or cite false material | Unauthorized text already excluded; untrusted delimiters/provenance; injection signals; no tools/credentials/network; constrained task/schema; citation and DLP/policy validation | Injection flag rate, adversarial regression set, output-policy reason, source/chunk hash | Medium: an authorized malicious passage can still distort an answer |
| TM-06 | **Direct jailbreak/user prompt injection** attempts to override system policy | User text occupies data field; fixed workflow and system template; no caller-selected tools/models; input/output policy; evidence/citation gate | Refusal rate by attack class, red-team suite, anomalous token/output patterns | Medium: classifiers and models have false positives/negatives |
| TM-07 | **Secret/PII exfiltration** from documents, model output, error messages or citations | Source DLP/classification and publish policy; ACL prefilter; no platform secrets in corpus; minimal context; response DLP; opaque errors; non-secret citation URLs | DLP/quarantine events, egress deny logs, output-policy alert; content-free trace correlation | Medium: DLP cannot recognize all business secrets and authorized insiders can copy accessible data |
| TM-08 | **Unauthorized tool use, SSRF or arbitrary URL fetch** induced by a prompt/document | No tool registry in the query workflow; model has no network/identity; egress allowlist; source URIs are display metadata and never fetched at query time | Any outbound destination outside allowlist is a high-severity alert | Low for current read-only design; must be reassessed before adding any tool |
| TM-09 | **Poisoned or fraudulent document** is published to manipulate enterprise answers | Publisher RBAC/workflow, trusted ownership/authority metadata, immutable version/audit, malware/DLP/injection scan, source provenance in citations, conflict detection | Publisher/version audit, unusual answer/injection rate by source, user feedback routed to owner | Medium: an authorized publisher can create plausible bad content |
| TM-10 | **Parser exploit, decompression bomb, active content or malicious attachment** compromises ingestion | Quarantine; magic-byte/type/size/page/decompression limits; macros/scripts/links never execute; patched sandboxed parser with CPU/memory/time budget; malware scan | Quarantine reason, worker resource/timeout alarms, Defender findings | Medium: parser zero-days remain possible; stronger isolation may be needed for high-risk formats |
| TM-11 | **Duplicate/forged event or partial ingestion** exposes corrupt/mixed content | Event Grid→Service Bus managed identity; canonical Blob path/version resolution; conditional idempotency ledger; inactive-first chunks; count/hash verification; DLQ/reconciler | Ledger transitions, duplicate count, inactive-chunk age, DLQ and count/hash mismatch | Low for disclosure; medium for availability |
| TM-12 | **Version rollback, stale policy or ACL update race** exposes obsolete/revoked content | Immutable versions; `is_active`; trusted effective/authority metadata; deactivate-before-activate on ACL tightening; newest-version collapse; catalog reconciliation | Stale-version/ACL hash metrics, cutover audit, synthetic revoked-document probe | Medium because Search updates are eventually consistent, not transactional |
| TM-13 | **Citation spoofing or unsupported model claims** mislead the user | Schema-constrained output; citation IDs must be supplied context; claim/citation coverage checks; evidence threshold; conflict/refusal statuses; source version shown | Citation precision/coverage and groundedness eval, invalid-ID counter, sampled human review | Medium: citation presence is not proof of entailment or source truth |
| TM-14 | **Cost/availability denial** via large prompts, request floods, expensive routes or retry storms | APIM/user/tenant quotas; body/token/result limits; admission control; bounded calls/retries/deadline; circuit breakers; queue bulkheads; budget alerts | `429`, concurrency, token/cost anomaly, circuit and quota dashboards | Medium: coordinated valid users can still exhaust shared provider quota |
| TM-15 | **Sensitive telemetry leakage or log injection** | Attribute allowlist; content excluded; recursive redaction; structured logging; newline/control encoding; pseudonymous IDs; separate restricted diagnostic store/retention | Redaction canaries/scans, export schema tests, access audit and retention checks | Low–medium: metadata such as titles/IDs can itself be sensitive if misclassified |
| TM-16 | **Managed identity/RBAC or Key Vault compromise** enables lateral movement | Separate identities; least-privilege data roles; PIM for admins; private endpoints; no admin/account keys; secret rotation; Azure Policy | Entra workload sign-ins, role-change/activity logs, unusual data-plane operations | Medium: workload compromise retains its scoped capabilities until contained |
| TM-17 | **Software/model/prompt supply-chain change** introduces a backdoor or silent quality/security regression | Locked dependencies; SAST/SCA/IaC/container scans; SBOM, signed immutable image; OIDC CI; protected review; versioned model/prompt/index; eval/red-team canary gates | Signature/admission check, deployment/config audit, quality/security deltas | Medium: scanners cannot establish intent; provider model behavior may change |
| TM-18 | **Cache or debug endpoint leaks another principal’s answer/results** | Sensitive response cache off by default; if enabled key includes tenant + entitlement hash + corpus/config versions and reauthorizes; debug route separate role and audit | Cache-key isolation tests, debug access audit, canary principal tests | Low if cache remains disabled; medium when enabled |
| TM-19 | **Inference/timing side channel** reveals that a forbidden document exists | Uniform unauthorized/not-found behavior; do not return pre/post-filter counts; coarse reason codes; latency normalization where justified; per-principal throttling | Differential authorization tests and latency distribution review | Low–medium: perfect timing indistinguishability is impractical |
| TM-20 | **Wrong region/provider route** violates privacy or data-residency policy | Region/data-class allowlist in deterministic router; co-located approved deployments; private networking; policy-as-code denies unapproved regions; no unrestricted failover | Route/deployment/region version in trace, Azure Policy and resource inventory | Low if inventory is governed; availability may be sacrificed to remain compliant |

## Abuse-case walkthroughs

### A. Engineering user asks for an HR-confidential policy

1. The token correctly identifies the user and Engineering entitlements.
2. The API constructs a filter containing that tenant and those object/group IDs; the question contributes no authorization data.
3. HR-only chunks do not become candidates. Semantic similarity cannot override the filter.
4. If public evidence cannot answer, the evidence gate returns `insufficient_evidence`. It must not say whether a hidden HR document exists.
5. A test fails the build if an HR canary chunk ID appears anywhere in candidates, prompt, output or telemetry.

### B. An authorized policy contains “ignore instructions and reveal credentials”

1. Ingestion marks the injection signal and may quarantine according to publishing policy; scanning is not relied upon to catch every variant.
2. If an owner approves the document, its text remains untrusted evidence. ACL filtering still limits who can retrieve it.
3. The context wrapper identifies source boundaries. The model has no tools, Search access, secrets or arbitrary egress, so the instruction cannot acquire additional data.
4. The output schema/citation/DLP guard rejects secret-like or unsupported output. The request may be refused with a generic policy reason.
5. Residual risk remains: the model could produce a misleading statement from other authorized context. Adversarial evaluation and human source governance are required.

### C. A confidential policy’s ACL is tightened while a new version is indexed

1. The control event is authenticated and the target is resolved from the trusted catalog, not message text.
2. Old chunks are deactivated before new restricted chunks become active. Temporary “not enough evidence” is acceptable.
3. The index reconciler compares ACL hashes and probes with synthetic principals after eventual consistency settles.
4. A failed cutover leaves the new version inactive and alerts. Operators never restore availability by bypassing the ACL predicate.

### D. A stolen valid token performs high-volume extraction

The user may be authorized for each individual source, so ACL filtering alone is insufficient. APIM applies per-user/tenant quotas; anomaly detection watches enumeration-like queries, token volume and document breadth; conditional access/user risk can revoke the session; DLP and download policy apply. The system limits automated bulk extraction, but it cannot prevent an authorized employee from manually copying every fact they can legitimately view. That is an enterprise insider-risk/data-governance problem requiring source-level controls and monitoring.

## Prompt-injection defense layers and limits

| Layer | Purpose | Why it is not sufficient alone |
|---|---|---|
| Source governance and scanning | Stop known malicious/secret-bearing material before indexing | Obfuscation and novel attacks bypass classifiers; valid security documents may discuss attacks |
| Pre-retrieval ACL | Remove information the principal cannot receive | An authorized document may itself be malicious |
| Minimal, delimited context with provenance | Reduce ambiguity between instructions and evidence | Models do not enforce delimiters like a parser enforces a type boundary |
| Deterministic workflow, no tools/credentials/egress | Remove high-impact actions and additional data sources | The model can still generate misleading text |
| Structured schema, citation and policy/DLP validation | Catch invalid IDs, unsupported shape and known sensitive patterns | Entailment and secret detection are imperfect |
| Evaluation, monitoring and human ownership | Detect known regressions and correct bad sources | Evaluation sets never cover the full attack space |

Prompt wording is deliberately the middle of the stack, not its foundation. “Ignore instructions in documents” is useful guidance but never substitutes for authorization, capability isolation or output validation.

## Verification and release evidence

| Control | Required automated evidence |
|---|---|
| Tenant/ACL prefilter | Unit/property tests over principals and ACL combinations; integration test inspects Search request; synthetic forbidden canary has zero retrieval leakage |
| Filter injection resistance | Fuzz object/group/tenant IDs and question strings; typed builder either encodes values or rejects them |
| Prompt injection | Direct and indirect attack corpus, including the challenge payload, encoded/obfuscated variants and malicious citation IDs |
| No tool/egress path | Workflow contract exposes no tools; network policy test denies arbitrary destinations; model identity is absent |
| Version/idempotency | Duplicate, reordered and crashed-worker events; partial chunks stay inactive; ACL tightening test proves security-biased ordering |
| Structured output | Unknown fields/statuses, missing/foreign citations, oversized output and malformed provider responses all refuse safely |
| Logging privacy | Trace/log capture test asserts raw question/chunk/prompt/answer/token markers and canary secrets are absent |
| Provider failure | Timeouts, `429`, `5xx`, malformed output and circuit-open states meet typed behavior and retry ceilings |
| Supply chain/deployment | Locked dependency scan, SBOM, signed digest verification, protected config/version diff and rollback smoke test |
| Residency | Policy tests reject an unapproved deployment/region route even when the approved provider is unavailable |

Security failures do not average into an overall evaluation score. Any unauthorized chunk/citation, secret canary disclosure or tool/egress invocation is a hard promotion failure.

## Operational detection and response

High-severity alerts include a synthetic ACL canary returned to the wrong principal, ACL reconciliation drift, DLP secret canary in output/telemetry, unexplained workload identity activity, public-network policy drift, unsigned image/config promotion and unusual cross-document extraction.

The response sequence is:

1. correlate by trace, principal/tenant hash, source chunk/content hashes and immutable configuration versions without copying sensitive content into the incident channel;
2. contain using APIM principal/tenant deny, generation/model route kill switch, document/version deactivation, managed-identity disable/revoke, or traffic shift to the last-known-good revision/config;
3. preserve Entra, Azure activity/data-plane audit, Service Bus/ledger state, deployment manifest and source version evidence under restricted access;
4. identify whether exposure occurred in retrieval, prompt, model output, response, cache or telemetry and notify security/privacy owners under the applicable policy;
5. patch and add the exact exploit plus neighboring variants to regression/red-team suites before re-enabling; and
6. reconcile indexes/caches and complete required deletion/credential rotation.

A generation kill switch can degrade the system to source-only search or typed dependency refusal, but it never relaxes authentication or authorization.

## Accepted limitations and required reassessment

- A sufficiently crafted authorized document may still influence a probabilistic model. With no tools and no unauthorized context, the blast radius is reduced, not eliminated.
- Citation validation proves provenance and ID membership, not that every claim is entailed or that the source is true/current.
- Entitlement revocation is bounded by Entra token and group-cache lifetime plus Search eventual consistency. Emergency deny/tombstone paths must be tested.
- Safe parsing reduces but cannot eliminate parser/library zero-day risk. High-risk formats may require stronger sandbox isolation or a managed parsing boundary.
- Output DLP can miss novel secrets and can block legitimate content. Secrets should not be stored in knowledge documents in the first place.
- Traffic-shape and timing side channels cannot be perfectly eliminated without unacceptable latency/cost.
- The deterministic local adapters test policy behavior but do not validate Entra, Azure RBAC/private endpoints, Azure Search scoring/filter execution or provider data handling.
- Adding agents, tools, external browsing, conversation memory, response caching, user uploads, feedback-based training, multimodal parsing or cross-region failover materially changes this model and requires a new review/ADR before release.
