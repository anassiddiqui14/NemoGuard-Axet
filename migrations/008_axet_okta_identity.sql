-- Link aXet/Okta identities to NemoGuard application users.
-- Okta remains responsible for authentication; NemoGuard keeps ownership
-- of application roles, tenant, workspace, and active/inactive status.

ALTER TABLE platform_user
    ADD COLUMN IF NOT EXISTS okta_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_platform_user_okta_id
    ON platform_user (okta_id)
    WHERE okta_id IS NOT NULL;