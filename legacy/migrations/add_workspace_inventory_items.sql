BEGIN;

CREATE TABLE IF NOT EXISTS workspace_inventory_items (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    sku TEXT NOT NULL,
    product_name TEXT NOT NULL,
    manufacturer TEXT NULL,
    make TEXT NULL,
    model TEXT NULL,
    category TEXT NULL,
    year_start INTEGER NULL,
    year_end INTEGER NULL,
    quantity_available INTEGER NOT NULL DEFAULT 0,
    unit_price NUMERIC(12,2) NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'USD',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, workspace_id, sku)
);

CREATE INDEX IF NOT EXISTS idx_workspace_inventory_items_scope
    ON workspace_inventory_items(tenant_id, workspace_id, category);
CREATE INDEX IF NOT EXISTS idx_workspace_inventory_items_vehicle
    ON workspace_inventory_items(tenant_id, workspace_id, make, model, year_start, year_end);
CREATE INDEX IF NOT EXISTS idx_workspace_inventory_items_product_name
    ON workspace_inventory_items(tenant_id, workspace_id, product_name);

ALTER TABLE workspace_inventory_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_inventory_items FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS empyralis_workspace_inventory_items_scope ON workspace_inventory_items;
CREATE POLICY empyralis_workspace_inventory_items_scope ON workspace_inventory_items
    FOR ALL
    USING (public.empyralis_rls_scope_match(tenant_id, workspace_id))
    WITH CHECK (public.empyralis_rls_scope_match(tenant_id, workspace_id));

COMMIT;
