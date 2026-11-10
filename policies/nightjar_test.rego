package nightjar_test

import data.nightjar

test_read_only_allowed if {
  result := nightjar.result with input as {
    "action": {"action_type": "probe", "risk": "r0", "target": "https://example.com"},
    "context": {"confidence": 0.2, "evidence_count": 0, "blast_radius": 0},
  }
  result.decision == "allow"
}

test_r1_auto_allowed if {
  result := nightjar.result with input as {
    "action": {"action_type": "purge_cache", "risk": "r1", "target": "zone-1"},
    "context": {"confidence": 0.9, "evidence_count": 3, "blast_radius": 1},
  }
  result.decision == "allow"
}

test_no_evidence_denied if {
  result := nightjar.result with input as {
    "action": {"action_type": "purge_cache", "risk": "r1", "target": "zone-1"},
    "context": {"confidence": 0.9, "evidence_count": 0, "blast_radius": 1},
  }
  result.decision == "deny"
}

test_r2_requires_approval if {
  result := nightjar.result with input as {
    "action": {"action_type": "revoke_credential", "risk": "r2", "target": "key-1"},
    "context": {"confidence": 0.95, "evidence_count": 4, "blast_radius": 1, "approvals_granted": 0},
  }
  result.decision == "require_approval"
  result.required_approvals == 1
}

test_r2_with_approval_allowed if {
  result := nightjar.result with input as {
    "action": {"action_type": "revoke_credential", "risk": "r2", "target": "key-1"},
    "context": {"confidence": 0.95, "evidence_count": 4, "blast_radius": 1, "approvals_granted": 1},
  }
  result.decision == "allow"
}

test_r3_denied_without_break_glass if {
  result := nightjar.result with input as {
    "action": {"action_type": "delete_data", "risk": "r3", "target": "db"},
    "context": {"confidence": 1.0, "evidence_count": 9, "blast_radius": 1},
  }
  result.decision == "deny"
}

test_change_freeze_denied if {
  result := nightjar.result with input as {
    "action": {"action_type": "purge_cache", "risk": "r1", "target": "zone-1"},
    "context": {"confidence": 0.9, "evidence_count": 3, "blast_radius": 1, "change_freeze": true},
  }
  result.decision == "deny"
}

test_denied_action_does_not_report_approval_reasons if {
  result := nightjar.result with input as {
    "action": {"action_type": "purge_cache", "risk": "r1", "target": "zone-1"},
    "context": {"confidence": 0.9, "evidence_count": 0, "blast_radius": 1},
  }
  result.decision == "deny"
  result.reasons == ["no evidence has been collected"]
  not "low_risk_auto" in result.matched_rules
}

test_low_confidence_r1_requires_approval if {
  result := nightjar.result with input as {
    "action": {"action_type": "purge_cache", "risk": "r1", "target": "zone-1"},
    "context": {"confidence": 0.6, "evidence_count": 3, "blast_radius": 1},
  }
  result.decision == "require_approval"
  result.required_approvals == 1
}
