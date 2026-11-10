package nightjar

# Nightjar policy — deterministic, fail-closed.
#
# Input shape:
#   input.action  { action_type, risk, target }
#   input.context { confidence, evidence_count, blast_radius, change_freeze,
#                   approvals_granted, break_glass, severity, incident_class }
#
# Output: { decision, reasons, matched_rules, required_approvals }

default decision := "deny"

prohibited := {"delete_data", "transfer_money", "disable_logging", "contact_third_party", "rotate_root_credential", "modify_billing", "create_admin_user"}

deny_reasons contains "prohibited action" if {
  prohibited[input.action.action_type]
}

deny_reasons contains "no evidence has been collected" if {
  input.action.risk != "r0"
  input.context.evidence_count == 0
}

deny_reasons contains "confidence below 0.50" if {
  input.action.risk != "r0"
  input.context.confidence < 0.5
}

deny_reasons contains "change freeze is active" if {
  input.action.risk != "r0"
  input.context.change_freeze == true
}

break_glass_ok if {
  input.context.break_glass == true
  input.context.approvals_granted >= 2
}

r1_auto_ok if {
  input.context.confidence >= 0.7
  input.context.blast_radius <= 1
}

deny_reasons contains "irreversible action requires break-glass and two approvers" if {
  input.action.risk == "r3"
  not break_glass_ok
}

approval_reasons contains "action may affect customers" if {
  count(deny_reasons) == 0
  input.action.risk == "r2"
  input.context.approvals_granted < 1
}

approval_reasons contains "low-confidence or wide blast radius" if {
  count(deny_reasons) == 0
  input.action.risk == "r1"
  not r1_auto_ok
}

decision := "deny" if {
  count(deny_reasons) > 0
}

decision := "require_approval" if {
  count(deny_reasons) == 0
  count(approval_reasons) > 0
}

decision := "allow" if {
  count(deny_reasons) == 0
  count(approval_reasons) == 0
}

required_approvals := 2 if {
  input.action.risk == "r3"
} else := 1 if {
  input.action.risk == "r2"
} else := 1 if {
  input.action.risk == "r1"
  not r1_auto_ok
} else := 0

matched_rules contains "prohibited_action" if {
  prohibited[input.action.action_type]
}

matched_rules contains "evidence_required" if {
  input.action.risk != "r0"
  input.context.evidence_count == 0
}

matched_rules contains "confidence_floor" if {
  input.action.risk != "r0"
  input.context.confidence < 0.5
}

matched_rules contains "change_freeze" if {
  input.action.risk != "r0"
  input.context.change_freeze == true
}

matched_rules contains "read_only_allow" if {
  input.action.risk == "r0"
}

matched_rules contains "customer_impact_requires_approval" if {
  count(deny_reasons) == 0
  input.action.risk == "r2"
  input.context.approvals_granted < 1
}

matched_rules contains "low_risk_auto" if {
  count(deny_reasons) == 0
  input.action.risk == "r1"
  input.context.confidence >= 0.7
  input.context.blast_radius <= 1
}

matched_rules contains "low_risk_needs_review" if {
  count(deny_reasons) == 0
  input.action.risk == "r1"
  not r1_auto_ok
}

matched_rules contains "break_glass" if {
  input.action.risk == "r3"
  input.context.break_glass == true
  input.context.approvals_granted >= 2
}

reasons := array.concat(
  sort([reason | deny_reasons[reason]]),
  sort([reason | approval_reasons[reason]]),
)

result := {
  "decision": decision,
  "reasons": reasons,
  "matched_rules": sort([rule | matched_rules[rule]]),
  "required_approvals": required_approvals,
}
