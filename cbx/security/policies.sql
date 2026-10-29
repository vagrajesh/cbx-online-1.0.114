-- Postgres row-level security policies mirroring cbx/security/rls.py.
-- Apply with: psql -f policies.sql  (after the app has created its tables).
-- The app sets `SET LOCAL cbx.user_id = <id>; SET LOCAL cbx.role = '<role>'` per request
-- (see cbx/db.py when CBX_DATABASE_URL is Postgres).

CREATE OR REPLACE FUNCTION cbx_is_admin() RETURNS boolean AS $$
  SELECT current_setting('cbx.role', true) IN ('owner','admin');
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION cbx_uid() RETURNS integer AS $$
  SELECT NULLIF(current_setting('cbx.user_id', true), '')::integer;
$$ LANGUAGE sql STABLE;

ALTER TABLE requests ENABLE ROW LEVEL SECURITY;
CREATE POLICY requests_owner ON requests USING (cbx_is_admin() OR user_id = cbx_uid());

ALTER TABLE pdf_batches ENABLE ROW LEVEL SECURITY;
CREATE POLICY batches_owner ON pdf_batches USING (cbx_is_admin() OR user_id = cbx_uid());

ALTER TABLE stored_files ENABLE ROW LEVEL SECURITY;
CREATE POLICY files_owner ON stored_files USING (cbx_is_admin() OR uploaded_by_id = cbx_uid());

ALTER TABLE axl_messages ENABLE ROW LEVEL SECURITY;
CREATE POLICY msgs_owner ON axl_messages USING (cbx_is_admin() OR user_id = cbx_uid());

ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY audit_admin ON audit_log FOR SELECT USING (cbx_is_admin());
CREATE POLICY audit_insert ON audit_log FOR INSERT WITH CHECK (true);
-- No UPDATE/DELETE policy on audit_log: nothing can change history.

-- Managers (role 'manager') see their own team's rows where a table carries team_id, and their team's
-- audit entries. The app sets `SET LOCAL cbx.team_id = <id>` alongside user_id and role.
CREATE OR REPLACE FUNCTION cbx_team() RETURNS integer AS $$
  SELECT NULLIF(current_setting('cbx.team_id', true), '')::integer;
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION cbx_is_manager() RETURNS boolean AS $$
  SELECT current_setting('cbx.role', true) = 'manager';
$$ LANGUAGE sql STABLE;

ALTER TABLE watched_docs ENABLE ROW LEVEL SECURITY;
CREATE POLICY docs_owner ON watched_docs USING (
  cbx_is_admin() OR owner_id = cbx_uid() OR (cbx_is_manager() AND team_id IS NOT NULL AND team_id = cbx_team())
);

-- a manager reads the trail of the users/managers on their team (and their own); admins read all
DROP POLICY IF EXISTS audit_admin ON audit_log;
CREATE POLICY audit_admin ON audit_log FOR SELECT USING (
  cbx_is_admin()
  OR actor_id = cbx_uid()
  OR (cbx_is_manager() AND actor_id IN (SELECT id FROM users WHERE team_id = cbx_team() AND role IN ('user','manager') AND is_axl = false))
);
