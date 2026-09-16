ALTER TABLE leads ADD COLUMN in_current_build INTEGER NOT NULL DEFAULT 1
    CHECK(in_current_build IN (0, 1));

CREATE INDEX idx_leads_current_build ON leads(in_current_build, cohort, workflow_status);
