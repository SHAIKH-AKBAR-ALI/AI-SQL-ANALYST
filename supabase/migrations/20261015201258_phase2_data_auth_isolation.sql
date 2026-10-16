-- App tables stay outside the Data API. Passwords are provisioned separately.
create role hisaab_query login nosuperuser nocreatedb nocreaterole noinherit nobypassrls;
create role hisaab_import login nosuperuser nocreatedb nocreaterole noinherit nobypassrls;
create role hisaab_demo login nosuperuser nocreatedb nocreaterole noinherit nobypassrls;
create schema app;
revoke all on schema app from public, anon, authenticated, service_role;
grant usage on schema app to hisaab_query, hisaab_import, hisaab_demo;
grant usage on schema auth to hisaab_query, hisaab_import;
grant execute on function auth.uid() to hisaab_query, hisaab_import;

create table app.datasets (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid references auth.users(id),
    name text not null check (length(btrim(name)) between 1 and 200),
    is_demo boolean not null default false,
    currency text not null default 'INR' check (currency ~ '^[A-Z]{3}$'),
    business_timezone text not null default 'Asia/Kolkata' check (business_timezone = 'Asia/Kolkata'),
    amount_definition text not null default 'confirmed_line_total' check (amount_definition = 'confirmed_line_total'),
    version integer not null default 1 check (version > 0),
    created_at timestamptz not null default now(),
    check ((is_demo and owner_id is null) or (not is_demo and owner_id is not null)),
    unique (id, owner_id)
);
create index datasets_owner_idx on app.datasets(owner_id);

create table app.sales (
    id uuid primary key default gen_random_uuid(),
    dataset_id uuid not null references app.datasets(id),
    source_row integer not null check (source_row > 0),
    sale_date date not null,
    product_name text not null check (length(btrim(product_name)) between 1 and 300),
    quantity numeric(18,3) not null check (quantity > 0 and quantity < 'Infinity'::numeric),
    unit text not null check (unit in ('litre', 'kg', 'piece')),
    unit_price numeric(18,2) check (unit_price >= 0 and unit_price < 'Infinity'::numeric),
    amount numeric(18,2) not null check (amount >= 0 and amount < 'Infinity'::numeric),
    unique (dataset_id, source_row)
);
create index sales_dataset_date_idx on app.sales(dataset_id, sale_date);

create table app.imports (
    id uuid primary key default gen_random_uuid(),
    dataset_id uuid not null,
    owner_id uuid not null references auth.users(id),
    dataset_version integer not null check (dataset_version > 0),
    status text not null check (status in ('pending', 'committed', 'failed')),
    file_sha256 text not null check (file_sha256 ~ '^[a-f0-9]{64}$'),
    confirmed_mapping jsonb not null default '{}'::jsonb check (jsonb_typeof(confirmed_mapping) = 'object'),
    accepted_rows integer not null default 0 check (accepted_rows >= 0),
    rejected_rows integer not null default 0 check (rejected_rows >= 0),
    created_at timestamptz not null default now(),
    foreign key (dataset_id, owner_id) references app.datasets(id, owner_id),
    unique (dataset_id, dataset_version)
);
create index imports_owner_idx on app.imports(owner_id);

create table app.conversations (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null references auth.users(id),
    dataset_id uuid not null,
    dataset_version integer not null check (dataset_version > 0),
    created_at timestamptz not null default now(),
    foreign key (dataset_id, owner_id) references app.datasets(id, owner_id),
    unique (id, dataset_id, owner_id, dataset_version)
);
create index conversations_dataset_owner_idx on app.conversations(dataset_id, owner_id);
create index conversations_owner_idx on app.conversations(owner_id);

create table app.query_runs (
    id uuid primary key default gen_random_uuid(),
    conversation_id uuid not null,
    dataset_id uuid not null,
    owner_id uuid not null references auth.users(id),
    dataset_version integer not null check (dataset_version > 0),
    question text not null check (length(btrim(question)) between 1 and 4000),
    sql_text text,
    status text not null check (status in ('pending', 'succeeded', 'failed', 'clarification')),
    created_at timestamptz not null default now(),
    foreign key (conversation_id, dataset_id, owner_id, dataset_version)
        references app.conversations(id, dataset_id, owner_id, dataset_version)
);
create index query_runs_conversation_idx on app.query_runs(conversation_id, dataset_id, owner_id, dataset_version);
create index query_runs_owner_idx on app.query_runs(owner_id);

alter table app.datasets enable row level security;
alter table app.datasets force row level security;
alter table app.sales enable row level security;
alter table app.sales force row level security;
alter table app.imports enable row level security;
alter table app.imports force row level security;
alter table app.conversations enable row level security;
alter table app.conversations force row level security;
alter table app.query_runs enable row level security;
alter table app.query_runs force row level security;

grant select on app.datasets, app.sales to hisaab_query, hisaab_demo;
grant select on app.imports, app.conversations, app.query_runs to hisaab_query;
grant select, insert, update, delete on all tables in schema app to hisaab_import;
revoke all on all tables in schema app from public, anon, authenticated, service_role;

create policy datasets_query on app.datasets for select to hisaab_query
    using ((select auth.uid()) is not null and (owner_id = (select auth.uid()) or is_demo));
create policy datasets_demo on app.datasets for select to hisaab_demo using (is_demo);
create policy datasets_import on app.datasets for all to hisaab_import
    using (owner_id = (select auth.uid()) and not is_demo)
    with check (owner_id = (select auth.uid()) and not is_demo);

create policy sales_query on app.sales for select to hisaab_query, hisaab_demo
    using (exists (select 1 from app.datasets d where d.id = dataset_id));
create policy sales_import on app.sales for all to hisaab_import
    using (exists (select 1 from app.datasets d where d.id = dataset_id and d.owner_id = (select auth.uid()) and not d.is_demo))
    with check (exists (select 1 from app.datasets d where d.id = dataset_id and d.owner_id = (select auth.uid()) and not d.is_demo));

create policy imports_query on app.imports for select to hisaab_query using (owner_id = (select auth.uid()));
create policy imports_write on app.imports for all to hisaab_import
    using (owner_id = (select auth.uid())) with check (owner_id = (select auth.uid()));
create policy conversations_query on app.conversations for select to hisaab_query using (owner_id = (select auth.uid()));
create policy conversations_write on app.conversations for all to hisaab_import
    using (owner_id = (select auth.uid())) with check (owner_id = (select auth.uid()));
create policy query_runs_query on app.query_runs for select to hisaab_query using (owner_id = (select auth.uid()));
create policy query_runs_write on app.query_runs for all to hisaab_import
    using (owner_id = (select auth.uid())) with check (owner_id = (select auth.uid()));

alter role hisaab_query set default_transaction_read_only = on;
alter role hisaab_demo set default_transaction_read_only = on;
alter role hisaab_query set statement_timeout = '5s';
alter role hisaab_demo set statement_timeout = '5s';
