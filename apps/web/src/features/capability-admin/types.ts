export type SkillStatus = "draft" | "in_review" | "published" | "retired" | "quarantined";
export type SkillType = "instruction-only" | "tool-assisted";

export type SkillResource = {
  path: string;
  media_type: string;
  size: number;
};

export type SkillVersion = {
  skill_id: string;
  version: number;
  slug: string;
  name: string;
  description: string;
  instructions: string;
  change_note: string;
  status: SkillStatus;
  active: boolean;
  created_at: string;
  resources: SkillResource[];
  governance: {
    skill_type?: SkillType;
    risk_level?: string;
    required_approvals?: number;
    [key: string]: unknown;
  };
  quarantine_reasons: string[];
  publish_blockers: string[];
};

export type SkillSummary = {
  skill_id: string;
  slug: string;
  name: string;
  description: string;
  versions: SkillVersion[];
};

export type ToolVersion = {
  tool_id: string;
  version: string;
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  confirmation_schema: Record<string, unknown> | null;
  effect: "read" | "write";
  allowed_stages: Array<"pre_visit" | "in_visit">;
  provider_approval_required: boolean;
  enabled: boolean;
  available: boolean;
  approval_required: boolean;
};

export type ToolSummary = {
  tool_id: string;
  versions: ToolVersion[];
};

export type ToolBinding = {
  skill_id: string;
  skill_version: number;
  tool_id: string;
  tool_version: string;
};

export type SkillAudit = {
  action: string;
  skill_id: string | null;
  version: number | null;
  actor: string;
  created_at: string;
  visit_matter_id: string | null;
  turn_id: string | null;
};

export type ToolAudit = {
  action: string;
  actor: string;
  created_at: string;
  tool_id: string | null;
  version: string | null;
  visit_matter_id: string | null;
  turn_id: string | null;
};

export type CapabilitySnapshot = {
  skills: SkillSummary[];
  tools: ToolSummary[];
  bindings: ToolBinding[];
  skillAudits: SkillAudit[];
  toolAudits: ToolAudit[];
};

export type AdminCommand =
  | {
      operation: "create-skill";
      payload: {
        slug: string;
        name: string;
        description: string;
        instructions: string;
        change_note: string;
        skill_type: SkillType;
      };
    }
  | {
      operation: "edit-skill";
      skillId: string;
      payload: { instructions: string; change_note: string };
    }
  | {
      operation: "transition-skill";
      skillId: string;
      version: number;
      action: "submit-review" | "publish" | "retire" | "activate";
    }
  | {
      operation: "bind-tool";
      skillId: string;
      skillVersion: number;
      toolId: string;
      toolVersion: string;
    }
  | {
      operation: "unbind-tool";
      skillId: string;
      skillVersion: number;
      toolId: string;
      toolVersion: string;
    }
  | {
      operation: "set-tool-enabled";
      toolId: string;
      toolVersion: string;
      enabled: boolean;
    }
  | {
      operation: "set-tool-approval";
      toolId: string;
      toolVersion: string;
      approvalRequired: boolean;
    };
