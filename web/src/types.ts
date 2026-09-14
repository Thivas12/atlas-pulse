import { z } from "zod";

const awareTimestampSchema = z.string().regex(/(?:Z|[+-]\d{2}:\d{2})$/);

const geoPointSchema = z.object({
  latitude: z.number().min(-90).max(90),
  longitude: z.number().min(-180).max(180),
  altitude_km: z.number().nullable(),
});

const positionSchema = z.tuple([z.number().min(-180).max(180), z.number().min(-90).max(90)]);
const linearRingSchema = z.array(positionSchema).min(4);
const polygonCoordinatesSchema = z.array(linearRingSchema).min(1);

export const alertGeometrySchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("Polygon"), coordinates: polygonCoordinatesSchema }),
  z.object({
    type: z.literal("MultiPolygon"),
    coordinates: z.array(polygonCoordinatesSchema).min(1),
  }),
]);

export const eventSchema = z.object({
  event_id: z.string().min(1),
  event_type: z.string().min(1),
  source: z.string().min(1),
  occurred_at: z.string(),
  ingested_at: z.string(),
  schema_version: z.string(),
  location: geoPointSchema.nullable(),
  payload: z.record(z.string(), z.unknown()),
});

export const eventEnvelopeSchema = z.object({
  stream_id: z.string().regex(/^\d+-\d+$/),
  event: eventSchema,
});

export const eventsResponseSchema = z.object({
  count: z.number().int().nonnegative(),
  items: z.array(eventEnvelopeSchema),
});

export const replayResponseSchema = eventsResponseSchema.extend({
  next_cursor: z
    .string()
    .regex(/^\d+-\d+$/)
    .nullable(),
  has_more: z.boolean(),
  order: z.literal("oldest_first"),
});

export const signalsResponseSchema = eventsResponseSchema.extend({
  next_cursor: z
    .string()
    .regex(/^\d+-\d+$/)
    .nullable(),
  has_more: z.boolean(),
  order: z.literal("newest_revision_first"),
});

export const SOURCE_FRESHNESS_CAVEAT =
  "Freshness is a point-in-time evaluation of worker-written Valkey state using the API host clock. It is not independent monitoring, an availability SLA, or proof that an upstream publisher is complete.";

const sourceNameSchema = z.enum(["usgs", "nws", "firms", "gdelt"]);
const sourcePollOutcomeSchema = z.enum(["in_progress", "succeeded", "failed"]);
const sourcePollStageSchema = z.enum(["fetch", "snapshot", "normalize", "publish", "complete"]);
const sourcePollFailureCodeSchema = z.enum([
  "transport_exhausted",
  "source_rejected",
  "source_payload_invalid",
  "snapshot_write_failed",
  "publication_unavailable",
  "unexpected_failure",
]);
const sourcePollStatusSchema = z.enum(["healthy", "degraded", "stale", "starting", "clock_skew"]);
const sourceDataStatusSchema = z.enum(["current", "stale", "not_reported", "future_clock_skew"]);
const sourceTimestampBasisSchema = z.enum(["source_metadata", "latest_record", "fetch_fallback"]);
const freshnessTimestampSchema = z
  .string()
  .regex(/(?:Z|[+-]00:00)$/, "timestamp must use UTC")
  .refine(
    (value) => Number.isFinite(Date.parse(value)),
    "timestamp must be a valid timezone-aware instant",
  );

export const sourceFreshnessItemSchema = z
  .object({
    source: sourceNameSchema,
    interval_seconds: z.number().positive().finite(),
    poll_stale_after_seconds: z.number().positive().finite(),
    source_stale_after_seconds: z.number().positive().finite(),
    poll_status: sourcePollStatusSchema,
    source_data_status: sourceDataStatusSchema,
    last_outcome: sourcePollOutcomeSchema.nullable(),
    last_stage: sourcePollStageSchema.nullable(),
    last_failure_code: sourcePollFailureCodeSchema.nullable(),
    last_attempt_at: freshnessTimestampSchema.nullable(),
    last_success_at: freshnessTimestampSchema.nullable(),
    last_source_generated_at: freshnessTimestampSchema.nullable(),
    last_success_age_seconds: z.number().finite().nullable(),
    source_age_seconds: z.number().finite().nullable(),
    consecutive_failures: z.number().int().nonnegative(),
    transport_attempts: z.number().int().min(0).max(20),
    timestamp_basis: sourceTimestampBasisSchema.nullable(),
    passed: z.boolean(),
  })
  .strict()
  .superRefine((item, context) => {
    const invariant = (valid: boolean, message: string, path: string[]) => {
      if (!valid) context.addIssue({ code: "custom", message, path });
    };
    const attemptFields = [item.last_attempt_at, item.last_outcome, item.last_stage];
    const successFields = [
      item.last_success_at,
      item.last_source_generated_at,
      item.last_success_age_seconds,
      item.source_age_seconds,
      item.timestamp_basis,
    ];
    invariant(
      attemptFields.every((value) => value === null) ||
        attemptFields.every((value) => value !== null),
      "last-attempt fields must be present together",
      ["last_attempt_at"],
    );
    invariant(
      successFields.every((value) => value === null) ||
        successFields.every((value) => value !== null),
      "last-success freshness fields must be present together",
      ["last_success_at"],
    );
    invariant(
      (item.last_failure_code !== null) === (item.last_outcome === "failed"),
      "failure code must match a failed latest poll",
      ["last_failure_code"],
    );
    invariant(
      (item.last_success_at === null) === (item.source_data_status === "not_reported"),
      "upstream status must match last-success evidence",
      ["source_data_status"],
    );
    invariant(
      item.poll_status !== "degraded" || item.last_outcome === "failed",
      "degraded poll status requires a failed latest poll",
      ["poll_status"],
    );
    invariant(
      item.last_attempt_at !== null ||
        (item.last_success_at === null &&
          item.poll_status === "starting" &&
          item.source_data_status === "not_reported" &&
          item.consecutive_failures === 0 &&
          item.transport_attempts === 0),
      "a source without an attempt must remain in the empty starting state",
      ["last_attempt_at"],
    );
    invariant(
      item.last_outcome !== "succeeded" ||
        (item.last_success_at !== null && item.consecutive_failures === 0),
      "a successful latest poll must be the last success and reset failures",
      ["last_outcome"],
    );
    invariant(
      item.last_outcome !== "failed" || item.consecutive_failures > 0,
      "a failed latest poll requires a consecutive failure",
      ["consecutive_failures"],
    );
    if (item.source_age_seconds !== null) {
      const expectedSourceStatus =
        item.source_age_seconds < 0
          ? "future_clock_skew"
          : item.source_age_seconds > item.source_stale_after_seconds
            ? "stale"
            : "current";
      invariant(
        item.source_data_status === expectedSourceStatus,
        "upstream status must match the measured source age",
        ["source_data_status"],
      );
    }
    invariant(
      item.passed === (item.poll_status === "healthy" && item.source_data_status === "current"),
      "source pass status must match poll and upstream states",
      ["passed"],
    );
    invariant(
      item.poll_stale_after_seconds > item.interval_seconds,
      "poll stale threshold must exceed the configured interval",
      ["poll_stale_after_seconds"],
    );
    invariant(
      item.source_stale_after_seconds > item.interval_seconds,
      "source stale threshold must exceed the configured interval",
      ["source_stale_after_seconds"],
    );
  });

export const sourceFreshnessResponseSchema = z
  .object({
    schema_version: z.literal("1.0.0"),
    rule_version: z.literal("source-poll-freshness-v1"),
    generated_at: freshnessTimestampSchema,
    items: z.array(sourceFreshnessItemSchema).min(1).max(4),
    passed: z.boolean(),
    execution_enabled: z.literal(false),
    caveat: z.literal(SOURCE_FRESHNESS_CAVEAT),
  })
  .strict()
  .superRefine((response, context) => {
    const sources = response.items.map((item) => item.source);
    const canonicalSources = [...new Set(sources)].sort();
    if (JSON.stringify(sources) !== JSON.stringify(canonicalSources)) {
      context.addIssue({
        code: "custom",
        message: "source freshness items must be unique and canonically ordered",
        path: ["items"],
      });
    }
    if (response.passed !== response.items.every((item) => item.passed)) {
      context.addIssue({
        code: "custom",
        message: "response pass status must match every source",
        path: ["passed"],
      });
    }
    const generatedAt = Date.parse(response.generated_at);
    response.items.forEach((item, index) => {
      if (
        item.last_success_at !== null &&
        item.last_source_generated_at !== null &&
        item.last_success_age_seconds !== null &&
        item.source_age_seconds !== null
      ) {
        const successAge = (generatedAt - Date.parse(item.last_success_at)) / 1_000;
        const sourceAge = (generatedAt - Date.parse(item.last_source_generated_at)) / 1_000;
        if (Math.abs(successAge - item.last_success_age_seconds) > 0.002) {
          context.addIssue({
            code: "custom",
            message: "last-success age must match response generation time",
            path: ["items", index, "last_success_age_seconds"],
          });
        }
        if (Math.abs(sourceAge - item.source_age_seconds) > 0.002) {
          context.addIssue({
            code: "custom",
            message: "source age must match response generation time",
            path: ["items", index, "source_age_seconds"],
          });
        }
      }
    });
  });

const incidentCenterSchema = z.object({
  latitude: z.number().min(-90).max(90),
  longitude: z.number().min(-180).max(180),
});

const evidenceNodeSchema = z.object({
  node_id: z.string().min(1),
  stream_id: z.string().regex(/^\d+-\d+$/),
  event: eventSchema,
});

const evidenceEdgeSchema = z.object({
  edge_id: z.string().min(1),
  from_node_id: z.string().min(1),
  to_node_id: z.string().min(1),
  relation: z.literal("spatiotemporal_cooccurrence"),
  spatial_relation: z.enum(["intersects", "within_radius"]),
  distance_km: z.number().nonnegative(),
  time_delta_minutes: z.number().nonnegative(),
  from_geometry_basis: z.enum(["point", "polygon"]),
  to_geometry_basis: z.enum(["point", "polygon"]),
  rule_version: z.string().min(1),
});

const evidenceClaimSchema = z.object({
  claim_id: z.string().min(1),
  node_id: z.string().min(1),
  predicate: z.enum(["hazard_domain", "evacuation_state", "road_access_state"]),
  value: z.string().min(1),
  scope: z.enum(["measured_edge_area", "named_place"]),
  scope_value: z.string().min(1).nullable(),
  evidence_field: z.string().min(1),
  evidence_excerpt: z.string().min(1),
  qualifier: z.string().min(1).nullable(),
  rule_version: z.string().min(1),
});

const evidenceRelationshipSchema = z.object({
  relationship_id: z.string().min(1),
  edge_id: z.string().min(1),
  from_node_id: z.string().min(1),
  to_node_id: z.string().min(1),
  label: z.enum(["corroborates", "contradicts", "insufficient_evidence"]),
  predicate: z.enum(["hazard_domain", "evacuation_state", "road_access_state"]).nullable(),
  normalized_value: z.string().min(1).nullable(),
  from_claim_id: z.string().min(1).nullable(),
  to_claim_id: z.string().min(1).nullable(),
  basis: z.enum([
    "exact_normalized_agreement",
    "mutually_exclusive_structured_values",
    "no_decisive_comparison",
  ]),
  rationale: z.string().min(1),
  rule_version: z.string().min(1),
});

const relationshipAnalysisSchema = z.object({
  claims: z.array(evidenceClaimSchema),
  relationships: z.array(evidenceRelationshipSchema),
  analyzed_edge_count: z.number().int().nonnegative(),
  corroboration_count: z.number().int().nonnegative(),
  contradiction_count: z.number().int().nonnegative(),
  insufficient_evidence_count: z.number().int().nonnegative(),
  rule_version: z.string().min(1),
  caveat: z.string().min(1),
});

export const incidentCandidateSchema = z.object({
  incident_id: z.string().min(1),
  title: z.string().min(1),
  started_at: z.string(),
  latest_signal_at: z.string(),
  center: incidentCenterSchema.nullable(),
  sources: z.array(z.string().min(1)).min(2),
  node_count: z.number().int().min(2),
  edge_count: z.number().int().min(1),
  max_distance_km: z.number().nonnegative(),
  time_span_minutes: z.number().nonnegative(),
  nodes: z.array(evidenceNodeSchema).min(2),
  edges: z.array(evidenceEdgeSchema).min(1),
  relationship_analysis: relationshipAnalysisSchema,
  rule_version: z.string().min(1),
  caveat: z.string().min(1),
});

export const incidentsResponseSchema = z.object({
  count: z.number().int().nonnegative(),
  total_incidents: z.number().int().nonnegative(),
  items: z.array(incidentCandidateSchema),
  incidents_truncated: z.boolean(),
  candidate_edges_truncated: z.boolean(),
  rule_version: z.string().min(1),
  caveat: z.string().min(1),
  relationship_rule_version: z.string().min(1),
  relationship_caveat: z.string().min(1),
  parameters: z.object({
    radius_km: z.number().positive(),
    time_window_minutes: z.number().int().positive(),
    lookback_hours: z.number().int().positive(),
    candidate_edge_limit: z.number().int().positive(),
    incident_limit: z.number().int().positive(),
    active_only: z.boolean(),
    bbox: z.tuple([z.number(), z.number(), z.number(), z.number()]).nullable(),
  }),
});

const citationSchema = z.object({
  status: z.enum(["traceable", "missing", "rejected"]),
  url: z.string().url().nullable(),
  source_field: z.string().nullable(),
  reasons: z.array(z.string().min(1)),
});

const rankingSchema = z.object({
  lexical_rank: z.number().int().positive().nullable(),
  lexical_score: z.number().nullable(),
  dense_rank: z.number().int().positive().nullable(),
  dense_similarity: z.number().nullable(),
  rrf_score: z.number().nonnegative(),
  exact_phrase_match: z.boolean(),
  token_coverage: z.number().min(0).max(1),
  rerank_score: z.number().nonnegative(),
});

export const searchHitSchema = z.object({
  stream_id: z.string().regex(/^\d+-\d+$/),
  event: eventSchema,
  document_text: z.string().min(1),
  distance_km: z.number().nonnegative().nullable(),
  ranking: rankingSchema,
  citation: citationSchema,
});

const searchParametersSchema = z.object({
  query: z.string().min(2),
  limit: z.number().int().positive(),
  candidate_limit: z.number().int().positive(),
  source: z.enum(["usgs", "nws", "firms", "gdelt"]).nullable(),
  occurred_after: z.string().nullable(),
  occurred_before: z.string().nullable(),
  active_only: z.boolean(),
  bbox: z.tuple([z.number(), z.number(), z.number(), z.number()]).nullable(),
  near: z.tuple([z.number(), z.number()]).nullable(),
  radius_km: z.number().positive().nullable(),
  ranking_mode: z.enum(["lexical", "dense", "rrf", "hybrid"]),
});

export const searchResponseSchema = z.object({
  count: z.number().int().nonnegative(),
  candidates_considered: z.number().int().nonnegative(),
  items: z.array(searchHitSchema),
  embedding_model: z.string().min(1),
  ranking_mode: z.enum(["lexical", "dense", "rrf", "hybrid"]),
  ranking_rule: z.string().min(1),
  caveat: z.string().min(1),
  parameters: searchParametersSchema,
});

const sha256Schema = z.string().regex(/^[0-9a-f]{64}$/);
const traceableCitationSchema = citationSchema.extend({
  status: z.literal("traceable"),
  url: z.string().url(),
  source_field: z.string().min(1),
});

const evidencePackItemSchema = z.object({
  evidence_id: z.string().regex(/^evidence-[0-9a-f]{64}$/),
  retrieval_rank: z.number().int().positive(),
  stream_id: z.string().regex(/^\d+-\d+$/),
  event_id: z.string().min(1),
  event_type: z.string().min(1),
  source: z.string().min(1),
  occurred_at: z.string(),
  ingested_at: z.string(),
  event_schema_version: z.string().min(1),
  text: z.string().min(1),
  document_sha256: sha256Schema,
  text_sha256: sha256Schema,
  document_characters: z.number().int().positive(),
  text_characters: z.number().int().positive(),
  truncated: z.boolean(),
  distance_km: z.number().nonnegative().nullable(),
  ranking: rankingSchema,
  citation: traceableCitationSchema,
});

const evidencePackExclusionSchema = z.object({
  retrieval_rank: z.number().int().positive(),
  stream_id: z.string().regex(/^\d+-\d+$/),
  event_id: z.string().min(1),
  event_type: z.string().min(1),
  source: z.string().min(1),
  occurred_at: z.string(),
  ingested_at: z.string(),
  event_schema_version: z.string().min(1),
  document_sha256: sha256Schema,
  distance_km: z.number().nonnegative().nullable(),
  ranking: rankingSchema,
  citation: citationSchema,
  reason: z.enum([
    "citation_missing",
    "citation_rejected",
    "citation_contract_invalid",
    "empty_source_text",
    "item_limit",
    "source_text_character_limit",
  ]),
});

export const evidencePackResponseSchema = z
  .object({
    pack_id: z.string().regex(/^pack-[0-9a-f]{64}$/),
    schema_version: z.literal("1.0.0"),
    rule_version: z.literal("retrieval-evidence-pack-v1"),
    identity_algorithm: z.literal("sha256-canonical-json-v1"),
    status: z.enum(["traceable_evidence_available", "no_traceable_evidence"]),
    item_count: z.number().int().nonnegative(),
    exclusion_count: z.number().int().nonnegative(),
    source_text_characters: z.number().int().nonnegative(),
    budget: z.object({
      max_items: z.number().int().min(1).max(50),
      max_characters_per_item: z.number().int().min(1).max(8_000),
      max_total_characters: z.number().int().min(1).max(64_000),
      character_unit: z.literal("unicode_code_points"),
    }),
    retrieval: z.object({
      candidates_considered: z.number().int().nonnegative(),
      returned_hits: z.number().int().nonnegative(),
      embedding_model: z.string().min(1),
      ranking_mode: z.enum(["lexical", "dense", "rrf", "hybrid"]),
      ranking_rule: z.string().min(1),
      caveat: z.string().min(1),
      parameters: searchParametersSchema,
    }),
    items: z.array(evidencePackItemSchema),
    exclusions: z.array(evidencePackExclusionSchema),
    answer_generated: z.literal(false),
    trust_boundary: z.string().min(1),
    caveat: z.string().min(1),
  })
  .superRefine((pack, context) => {
    const textCharacters = pack.items.reduce((total, item) => total + [...item.text].length, 0);
    const invariant = (valid: boolean, message: string, path: (string | number)[]) => {
      if (!valid) context.addIssue({ code: "custom", message, path });
    };
    invariant(pack.item_count === pack.items.length, "item_count does not match items", [
      "item_count",
    ]);
    invariant(
      pack.exclusion_count === pack.exclusions.length,
      "exclusion_count does not match exclusions",
      ["exclusion_count"],
    );
    invariant(
      pack.retrieval.returned_hits === pack.items.length + pack.exclusions.length,
      "returned_hits does not match the accounted retrieval results",
      ["retrieval", "returned_hits"],
    );
    invariant(
      pack.source_text_characters === textCharacters,
      "source_text_characters does not match included text",
      ["source_text_characters"],
    );
    invariant(pack.items.length <= pack.budget.max_items, "item budget exceeded", ["items"]);
    invariant(
      textCharacters <= pack.budget.max_total_characters,
      "total source-text budget exceeded",
      ["items"],
    );
    invariant(
      pack.status ===
        (pack.items.length > 0 ? "traceable_evidence_available" : "no_traceable_evidence"),
      "status does not match evidence availability",
      ["status"],
    );
    pack.items.forEach((item, index) => {
      const length = [...item.text].length;
      invariant(item.text_characters === length, "text_characters does not match text", [
        "items",
        index,
        "text_characters",
      ]);
      invariant(
        length <= pack.budget.max_characters_per_item,
        "per-item source-text budget exceeded",
        ["items", index, "text"],
      );
    });
    pack.exclusions.forEach((exclusion, index) => {
      const expectedStatus =
        exclusion.reason === "citation_missing"
          ? "missing"
          : exclusion.reason === "citation_rejected"
            ? "rejected"
            : "traceable";
      invariant(
        exclusion.citation.status === expectedStatus,
        "exclusion reason does not match citation status",
        ["exclusions", index, "reason"],
      );
    });
  });

const authorizationBlockReasonSchema = z.enum([
  "no_traceable_evidence",
  "model_adapter_not_selected",
  "live_relationship_benchmark_incomplete",
  "grounded_answer_evaluation_missing",
  "agent_trajectory_evaluation_missing",
  "trajectory_drift_evidence_missing",
  "release_thresholds_not_met",
  "human_release_not_granted",
  "human_release_not_yet_valid",
  "human_release_expired",
  "human_release_revoked",
  "human_release_scope_mismatch",
  "human_release_untrusted",
  "approval_ledger_invalid",
  "approval_ledger_unavailable",
  "execution_disabled",
]);

const authorizationCheckIdSchema = z.enum([
  "evidence_pack_integrity",
  "traceable_evidence",
  "capability_scope",
  "model_adapter",
  "live_relationship_benchmark",
  "grounded_answer_evaluation",
  "agent_trajectory_evaluation",
  "trajectory_drift_monitoring",
  "release_threshold_policy",
  "human_release",
  "execution_release",
]);

export const agentRunManifestSchema = z
  .object({
    manifest_id: z.string().regex(/^manifest-[0-9a-f]{64}$/),
    schema_version: z.literal("1.2.0"),
    rule_version: z.literal("agent-run-manifest-v3"),
    identity_algorithm: z.literal("sha256-canonical-json-v1"),
    proposal_id: z.string().regex(/^proposal-[0-9a-f]{64}$/),
    status: z.literal("blocked"),
    request: z.object({
      purpose: z.literal("evidence_triage"),
      mode: z.literal("read_only"),
      requested_output: z.literal("grounded_evidence_brief"),
      capabilities: z.object({
        read_evidence: z.literal(true),
        generate_text: z.literal(true),
        network_access: z.literal(false),
        tool_access: z.literal(false),
        external_side_effects: z.literal(false),
      }),
    }),
    evidence: z.object({
      pack_id: z.string().regex(/^pack-[0-9a-f]{64}$/),
      pack_rule_version: z.literal("retrieval-evidence-pack-v1"),
      pack_status: z.enum(["traceable_evidence_available", "no_traceable_evidence"]),
      item_count: z.number().int().nonnegative(),
      exclusion_count: z.number().int().nonnegative(),
      source_text_characters: z.number().int().nonnegative(),
      evidence_ids: z.array(z.string().regex(/^evidence-[0-9a-f]{64}$/)),
    }),
    policy: z.object({
      policy_version: z.literal("agent-authorization-v3"),
      default_decision: z.literal("deny"),
      execution_enabled: z.literal(false),
      human_release_required: z.literal(true),
      evaluated_model_required: z.literal(true),
      relationship_benchmark_required: z.literal(true),
      grounded_answer_evaluation_required: z.literal(true),
      agent_trajectory_evaluation_required: z.literal(true),
      trajectory_drift_monitoring_required: z.literal(true),
      release_threshold_policy_required: z.literal(true),
      network_access_allowed: z.literal(false),
      tool_access_allowed: z.literal(false),
      external_side_effects_allowed: z.literal(false),
    }),
    release: z.object({
      status: z.enum(["not_supplied", "blocked", "eligible_for_human_review"]),
      assessment_id: z
        .string()
        .regex(/^release-assessment-[0-9a-f]{20}$/)
        .nullable(),
      assessment_sha256: z
        .string()
        .regex(/^[0-9a-f]{64}$/)
        .nullable(),
      policy_id: z
        .string()
        .regex(/^release-policy-[0-9a-f]{20}$/)
        .nullable(),
      policy_sha256: z
        .string()
        .regex(/^[0-9a-f]{64}$/)
        .nullable(),
      agent_candidate_id: z.string().min(1).nullable(),
      relationship_report_id: z.string().min(1).nullable(),
      trajectory_report_ids: z.array(z.string().regex(/^trajectory-report-[0-9a-f]{20}$/)),
      model_adapter_evaluated: z.boolean(),
      relationship_benchmark_passed: z.boolean(),
      grounded_answer_evaluation_passed: z.boolean(),
      agent_trajectory_evaluation_passed: z.boolean(),
      trajectory_drift_monitoring_passed: z.boolean(),
      release_threshold_policy_passed: z.boolean(),
      blocking_reasons: z.array(z.string().regex(/^[a-z0-9_]+$/)),
    }),
    approval: z.object({
      status: z.enum([
        "not_supplied",
        "not_found",
        "not_yet_valid",
        "active",
        "expired",
        "revoked",
        "scope_mismatch",
        "untrusted_signer",
        "ledger_invalid",
        "ledger_unavailable",
      ]),
      approval_id: z
        .string()
        .regex(/^approval-[0-9a-f]{64}$/)
        .nullable(),
      approved_proposal_id: z
        .string()
        .regex(/^proposal-[0-9a-f]{64}$/)
        .nullable(),
      source_manifest_id: z
        .string()
        .regex(/^manifest-[0-9a-f]{64}$/)
        .nullable(),
      approver_id: z.string().min(1).nullable(),
      signing_key_id: z
        .string()
        .regex(/^ed25519-[0-9a-f]{64}$/)
        .nullable(),
      issued_at: awareTimestampSchema.nullable(),
      expires_at: awareTimestampSchema.nullable(),
      revocation_id: z
        .string()
        .regex(/^revocation-[0-9a-f]{64}$/)
        .nullable(),
      evaluated_at: awareTimestampSchema.nullable(),
    }),
    authorization: z.object({
      decision: z.literal("blocked"),
      passed_check_count: z.number().int().nonnegative(),
      blocked_check_count: z.number().int().positive(),
      blocking_reasons: z.array(authorizationBlockReasonSchema).min(1),
      checks: z
        .array(
          z.object({
            check_id: authorizationCheckIdSchema,
            status: z.enum(["passed", "blocked"]),
            observed: z.string().min(1),
            required: z.string().min(1),
            blocking_reason: authorizationBlockReasonSchema.nullable(),
          }),
        )
        .length(11),
    }),
    execution: z.object({
      status: z.literal("not_started"),
      agent_model_invoked: z.literal(false),
      agent_network_accessed: z.literal(false),
      agent_tools_invoked: z.literal(false),
      answer_generated: z.literal(false),
      agent_side_effects_performed: z.literal(false),
    }),
    caveat: z.string().min(1),
  })
  .superRefine((manifest, context) => {
    const invariant = (valid: boolean, message: string, path: (string | number)[]) => {
      if (!valid) context.addIssue({ code: "custom", message, path });
    };
    const passedChecks = manifest.authorization.checks.filter((check) => check.status === "passed");
    const blockedChecks = manifest.authorization.checks.filter(
      (check) => check.status === "blocked",
    );
    const expectedReasons = blockedChecks.flatMap((check) =>
      check.blocking_reason === null ? [] : [check.blocking_reason],
    );
    invariant(
      new Set(manifest.authorization.checks.map((check) => check.check_id)).size === 11,
      "authorization check IDs must be unique",
      ["authorization", "checks"],
    );
    invariant(
      manifest.authorization.passed_check_count === passedChecks.length,
      "passed_check_count does not match checks",
      ["authorization", "passed_check_count"],
    );
    invariant(
      manifest.authorization.blocked_check_count === blockedChecks.length,
      "blocked_check_count does not match checks",
      ["authorization", "blocked_check_count"],
    );
    invariant(
      JSON.stringify(manifest.authorization.blocking_reasons) === JSON.stringify(expectedReasons),
      "blocking_reasons do not match blocked checks",
      ["authorization", "blocking_reasons"],
    );
    invariant(
      manifest.evidence.item_count === manifest.evidence.evidence_ids.length,
      "evidence item_count does not match evidence IDs",
      ["evidence", "item_count"],
    );
    manifest.authorization.checks.forEach((check, index) => {
      invariant(
        check.status === "blocked"
          ? check.blocking_reason !== null
          : check.blocking_reason === null,
        "check status does not match blocking reason",
        ["authorization", "checks", index, "blocking_reason"],
      );
    });
    const humanRelease = manifest.authorization.checks.find(
      (check) => check.check_id === "human_release",
    );
    invariant(
      (manifest.approval.status === "active") === (humanRelease?.status === "passed"),
      "human release check does not match approval status",
      ["approval", "status"],
    );
    if (manifest.approval.status === "active") {
      invariant(
        manifest.approval.approved_proposal_id === manifest.proposal_id,
        "active approval is not bound to the manifest proposal",
        ["approval", "approved_proposal_id"],
      );
    }
    const releaseThreshold = manifest.authorization.checks.find(
      (check) => check.check_id === "release_threshold_policy",
    );
    invariant(
      (manifest.release.status === "eligible_for_human_review") ===
        (releaseThreshold?.status === "passed"),
      "release threshold check does not match release assessment",
      ["release", "status"],
    );
    const releaseIdentityFields = [
      manifest.release.assessment_id,
      manifest.release.assessment_sha256,
      manifest.release.policy_id,
      manifest.release.policy_sha256,
      manifest.release.agent_candidate_id,
      manifest.release.relationship_report_id,
    ];
    const releaseFlags = [
      manifest.release.model_adapter_evaluated,
      manifest.release.relationship_benchmark_passed,
      manifest.release.grounded_answer_evaluation_passed,
      manifest.release.agent_trajectory_evaluation_passed,
      manifest.release.trajectory_drift_monitoring_passed,
      manifest.release.release_threshold_policy_passed,
    ];
    if (manifest.release.status === "not_supplied") {
      invariant(
        releaseIdentityFields.every((value) => value === null) &&
          manifest.release.trajectory_report_ids.length === 0 &&
          releaseFlags.every((value) => value === false) &&
          manifest.release.blocking_reasons.length === 0,
        "not_supplied release state cannot contain assessment fields",
        ["release"],
      );
    } else {
      invariant(
        releaseIdentityFields.every((value) => value !== null) &&
          manifest.release.trajectory_report_ids.length > 0,
        "resolved release state requires complete assessment identity",
        ["release"],
      );
    }
    if (manifest.release.status === "eligible_for_human_review") {
      invariant(
        releaseFlags.every((value) => value === true) &&
          manifest.release.blocking_reasons.length === 0,
        "eligible release state requires every quality gate to pass",
        ["release"],
      );
    }
    if (manifest.release.status === "blocked") {
      invariant(
        !manifest.release.release_threshold_policy_passed &&
          manifest.release.blocking_reasons.length > 0,
        "blocked release state requires threshold blocking reasons",
        ["release"],
      );
    }
    const approvalFields = [
      manifest.approval.approval_id,
      manifest.approval.approved_proposal_id,
      manifest.approval.source_manifest_id,
      manifest.approval.approver_id,
      manifest.approval.signing_key_id,
      manifest.approval.issued_at,
      manifest.approval.expires_at,
      manifest.approval.revocation_id,
      manifest.approval.evaluated_at,
    ];
    if (manifest.approval.status === "not_supplied") {
      invariant(
        approvalFields.every((value) => value === null),
        "not_supplied approval state cannot contain ledger fields",
        ["approval"],
      );
    } else {
      invariant(
        manifest.approval.approval_id !== null && manifest.approval.evaluated_at !== null,
        "resolved approval states require approval_id and evaluated_at",
        ["approval"],
      );
    }
    const detailedApproval = [
      "not_yet_valid",
      "active",
      "expired",
      "revoked",
      "scope_mismatch",
    ].includes(manifest.approval.status);
    if (detailedApproval) {
      invariant(
        approvalFields.slice(1, 7).every((value) => value !== null),
        "resolved approval artifact details are incomplete",
        ["approval"],
      );
    }
    invariant(
      (manifest.approval.status === "revoked") === (manifest.approval.revocation_id !== null),
      "revocation ID does not match approval status",
      ["approval", "revocation_id"],
    );
  });

export const agentRunPreflightResponseSchema = z
  .object({
    evidence_pack: evidencePackResponseSchema,
    manifest: agentRunManifestSchema,
  })
  .superRefine((preflight, context) => {
    const evidence = preflight.manifest.evidence;
    const pack = preflight.evidence_pack;
    const invariant = (valid: boolean, message: string, path: (string | number)[]) => {
      if (!valid) context.addIssue({ code: "custom", message, path });
    };
    invariant(evidence.pack_id === pack.pack_id, "manifest is not bound to the returned pack", [
      "manifest",
      "evidence",
      "pack_id",
    ]);
    invariant(evidence.pack_status === pack.status, "pack status binding does not match", [
      "manifest",
      "evidence",
      "pack_status",
    ]);
    invariant(evidence.item_count === pack.item_count, "pack item binding does not match", [
      "manifest",
      "evidence",
      "item_count",
    ]);
    invariant(
      evidence.exclusion_count === pack.exclusion_count,
      "pack exclusion binding does not match",
      ["manifest", "evidence", "exclusion_count"],
    );
    invariant(
      evidence.source_text_characters === pack.source_text_characters,
      "pack character binding does not match",
      ["manifest", "evidence", "source_text_characters"],
    );
    invariant(
      JSON.stringify(evidence.evidence_ids) ===
        JSON.stringify(pack.items.map((item) => item.evidence_id)),
      "manifest evidence IDs do not match the returned pack",
      ["manifest", "evidence", "evidence_ids"],
    );
  });

export interface ViewportBounds {
  west: number;
  south: number;
  east: number;
  north: number;
}

export type AtlasEvent = z.infer<typeof eventSchema>;
export type AlertGeometry = z.infer<typeof alertGeometrySchema>;
export type EventEnvelope = z.infer<typeof eventEnvelopeSchema>;
export type EventsResponse = z.infer<typeof eventsResponseSchema>;
export type ReplayResponse = z.infer<typeof replayResponseSchema>;
export type SignalsResponse = z.infer<typeof signalsResponseSchema>;
export type SourceFreshnessItem = z.infer<typeof sourceFreshnessItemSchema>;
export type SourceFreshnessResponse = z.infer<typeof sourceFreshnessResponseSchema>;
export type IncidentCandidate = z.infer<typeof incidentCandidateSchema>;
export type IncidentsResponse = z.infer<typeof incidentsResponseSchema>;
export type SearchHit = z.infer<typeof searchHitSchema>;
export type SearchResponse = z.infer<typeof searchResponseSchema>;
export type EvidencePack = z.infer<typeof evidencePackResponseSchema>;
export type AgentRunManifest = z.infer<typeof agentRunManifestSchema>;
export type AgentRunPreflightResponse = z.infer<typeof agentRunPreflightResponseSchema>;
