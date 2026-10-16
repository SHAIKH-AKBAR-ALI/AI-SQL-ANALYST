-- Existing owner RLS and role grants apply to this bounded clarification field.
alter table app.query_runs add column clarification text
    check (clarification is null or length(clarification) <= 6000);
