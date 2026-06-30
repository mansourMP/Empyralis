from __future__ import annotations

import asyncio
import re
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional

from server_modules import control_plane_repository


_YEAR_PATTERN = re.compile(r"\b(19\d{2}|20\d{2}|21\d{2})\b")
_MAKE_MODEL_PATTERNS: list[tuple[str, str]] = [
    ("tesla", "model 3"),
    ("tesla", "model y"),
    ("toyota", "camry"),
    ("honda", "civic"),
]
_PRODUCT_KEYWORDS = [
    "wiper",
    "wipers",
    "brake",
    "brakes",
    "rotor",
    "rotors",
    "filter",
    "filters",
    "pad",
    "pads",
]


def _metadata_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _extract_inventory_query(goal: str) -> Dict[str, Any]:
    normalized = _normalize_text(goal).lower()
    year_match = _YEAR_PATTERN.search(normalized)
    year = int(year_match.group(1)) if year_match else None

    make = ""
    model = ""
    for candidate_make, candidate_model in _MAKE_MODEL_PATTERNS:
        if candidate_make in normalized and candidate_model in normalized:
            make = candidate_make
            model = candidate_model
            break
    if not make:
        for token in ("tesla", "toyota", "honda", "ford", "bmw", "mercedes"):
            if token in normalized:
                make = token
                break

    product_terms = [keyword for keyword in _PRODUCT_KEYWORDS if keyword in normalized]
    if not product_terms and "blade" in normalized:
        product_terms.append("wiper")

    return {
        "year": year,
        "make": make,
        "model": model,
        "product_terms": product_terms,
        "normalized_goal": normalized,
    }


def _to_item_payload(row: Any) -> Dict[str, Any]:
    payload = dict(row)
    unit_price = payload.get("unit_price")
    if isinstance(unit_price, Decimal):
        unit_price = float(unit_price)
    elif unit_price is not None:
        unit_price = float(unit_price)
    return {
        "id": _normalize_text(payload.get("id")),
        "tenant_id": _normalize_text(payload.get("tenant_id")),
        "workspace_id": _normalize_text(payload.get("workspace_id")),
        "sku": _normalize_text(payload.get("sku")),
        "product_name": _normalize_text(payload.get("product_name")),
        "manufacturer": _normalize_text(payload.get("manufacturer")) or None,
        "make": _normalize_text(payload.get("make")) or None,
        "model": _normalize_text(payload.get("model")) or None,
        "category": _normalize_text(payload.get("category")) or None,
        "year_start": payload.get("year_start"),
        "year_end": payload.get("year_end"),
        "quantity_available": int(payload.get("quantity_available") or 0),
        "unit_price": unit_price or 0.0,
        "currency": _normalize_text(payload.get("currency")) or "USD",
        "metadata": _metadata_dict(payload.get("metadata")),
    }


async def ensure_demo_inventory_seeded(*, tenant_id: str, workspace_id: str) -> None:
    async with control_plane_repository._scoped_connection(tenant_id=tenant_id, workspace_id=workspace_id) as connection:
        if connection is None:
            return
        count = await connection.fetchval(
            "SELECT COUNT(*) FROM workspace_inventory_items WHERE tenant_id = $1 AND workspace_id = $2",
            tenant_id,
            workspace_id,
        )
        if int(count or 0) > 0:
            return
        demo_items = [
            {
                "sku": "TES-WIPER-M3-2022",
                "product_name": "Tesla Model 3 Aero Wiper Kit",
                "manufacturer": "Empyralis Shadow Catalog",
                "make": "Tesla",
                "model": "Model 3",
                "category": "wipers",
                "year_start": 2021,
                "year_end": 2023,
                "quantity_available": 3,
                "unit_price": 24.99,
                "currency": "USD",
                "metadata": {"demo_seed": True, "fitment": "2021-2023 Tesla Model 3"},
            },
            {
                "sku": "TES-WIPER-MY-2022",
                "product_name": "Tesla Model Y All-Season Wiper Blades",
                "manufacturer": "Empyralis Shadow Catalog",
                "make": "Tesla",
                "model": "Model Y",
                "category": "wipers",
                "year_start": 2021,
                "year_end": 2024,
                "quantity_available": 6,
                "unit_price": 29.99,
                "currency": "USD",
                "metadata": {"demo_seed": True},
            },
            {
                "sku": "BOSCH-CAMRY-BRAKE-2019",
                "product_name": "Bosch Front Brake Pad Set",
                "manufacturer": "Bosch",
                "make": "Toyota",
                "model": "Camry",
                "category": "brakes",
                "year_start": 2018,
                "year_end": 2020,
                "quantity_available": 8,
                "unit_price": 74.5,
                "currency": "USD",
                "metadata": {"demo_seed": True},
            },
        ]
        for item in demo_items:
            await connection.execute(
                """
                INSERT INTO workspace_inventory_items (
                    id, tenant_id, workspace_id, sku, product_name, manufacturer, make, model, category,
                    year_start, year_end, quantity_available, unit_price, currency, metadata, created_at, updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9,
                    $10, $11, $12, $13, $14, $15::jsonb, NOW(), NOW()
                )
                ON CONFLICT (tenant_id, workspace_id, sku) DO NOTHING
                """,
                f"inventory-{uuid.uuid4()}",
                tenant_id,
                workspace_id,
                item["sku"],
                item["product_name"],
                item["manufacturer"],
                item["make"],
                item["model"],
                item["category"],
                item["year_start"],
                item["year_end"],
                item["quantity_available"],
                item["unit_price"],
                item["currency"],
                control_plane_repository._to_json(item["metadata"], default={}),
            )


async def search_workspace_inventory(
    *,
    tenant_id: str,
    workspace_id: str,
    goal: str,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    query = _extract_inventory_query(goal)
    patterns = []
    for token in [query["make"], query["model"], *query["product_terms"]]:
        normalized = _normalize_text(token).lower()
        if normalized:
            patterns.append(f"%{normalized}%")
    if not patterns:
        patterns.append(f"%{_normalize_text(goal).lower()}%")

    async with control_plane_repository._scoped_connection(tenant_id=tenant_id, workspace_id=workspace_id) as connection:
        if connection is None:
            return []
        rows = await connection.fetch(
            """
            SELECT *
            FROM workspace_inventory_items
            WHERE tenant_id = $1
              AND workspace_id = $2
              AND (
                    LOWER(product_name) LIKE ANY($3::text[])
                 OR LOWER(COALESCE(sku, '')) LIKE ANY($3::text[])
                 OR LOWER(COALESCE(category, '')) LIKE ANY($3::text[])
              )
              AND ($4::text = '' OR LOWER(COALESCE(make, '')) = $4::text OR LOWER(product_name) LIKE '%' || $4::text || '%')
              AND ($5::text = '' OR LOWER(COALESCE(model, '')) = $5::text OR LOWER(product_name) LIKE '%' || $5::text || '%')
              AND ($6::int IS NULL OR (COALESCE(year_start, $6::int) <= $6::int AND COALESCE(year_end, $6::int) >= $6::int))
            ORDER BY quantity_available DESC, unit_price ASC, product_name ASC
            LIMIT $7
            """,
            tenant_id,
            workspace_id,
            patterns,
            query["make"],
            query["model"],
            query["year"],
            max(1, min(int(limit or 5), 10)),
        )
    return [_to_item_payload(row) for row in rows]


async def execute_inventory_skill(
    *,
    tenant_id: str,
    workspace_id: str,
    goal: str,
    agent_label: str,
    hard_context: str,
    operational_policy: str,
) -> Dict[str, Any]:
    try:
        items = await asyncio.wait_for(
            search_workspace_inventory(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                goal=goal,
            ),
            timeout=2.5,
        )
    except Exception:
        return {
            "status": "unavailable",
            "reply": "The inventory system is currently updating, one moment.",
            "artifact": None,
            "steps": [
                {"label": "Resolving inventory query", "detail": goal, "status": "done", "kind": "thinking"},
                {"label": "Inventory tool unavailable", "detail": "Database timeout or runtime failure", "status": "error", "kind": "connector"},
            ],
        }

    if not items:
        return {
            "status": "no_match",
            "reply": "That part could not be confirmed in stock yet. Double-checking catalog and fitment before confirming availability.",
            "artifact": {
                "label": "Inventory lookup result",
                "kind": "inventory-live-data",
                "summary": "No live matches returned from the workspace inventory table.",
                "media_type": "text/markdown",
                "preview_content": "\n".join([
                    "# Inventory lookup result",
                    "",
                    f"- Agent: {agent_label}",
                    f"- Customer request: {goal}",
                    f"- Hard Context: {hard_context or 'None'}",
                    f"- Operational Policy: {operational_policy or 'None'}",
                    "",
                    "No matching inventory items were returned from `workspace_inventory_items`.",
                ]),
            },
            "steps": [
                {"label": "Resolving inventory query", "detail": goal, "status": "done", "kind": "thinking"},
                {"label": "Querying workspace inventory", "detail": "0 matches", "status": "done", "kind": "connector"},
            ],
        }

    top = items[0]
    quantity = int(top.get("quantity_available") or 0)
    unit_price = float(top.get("unit_price") or 0)
    currency = _normalize_text(top.get("currency") or "USD")
    reply = f"Found {quantity} {top['product_name']} in stock. Price: ${unit_price:.2f}."
    artifact_lines = [
        "# Live inventory result",
        "",
        f"- Agent: {agent_label}",
        f"- Customer request: {goal}",
        f"- Hard Context: {hard_context or 'None'}",
        f"- Operational Policy: {operational_policy or 'None'}",
        "",
        "## Top match",
        f"- Product: {top['product_name']}",
        f"- SKU: {top['sku']}",
        f"- Quantity available: {quantity}",
        f"- Price: {currency} {unit_price:.2f}",
    ]
    if top.get("make") or top.get("model"):
        artifact_lines.append(f"- Fitment: {top.get('make') or ''} {top.get('model') or ''}".strip())
    if top.get("year_start") or top.get("year_end"):
        artifact_lines.append(f"- Year range: {top.get('year_start') or '?'}-{top.get('year_end') or '?'}")

    return {
        "status": "ok",
        "reply": reply,
        "items": items,
        "artifact": {
            "label": "Inventory tool result",
            "kind": "inventory-live-data",
            "summary": f"{quantity} units available in the workspace inventory table.",
            "media_type": "text/markdown",
            "preview_content": "\n".join(artifact_lines),
        },
        "steps": [
            {"label": "Resolving inventory query", "detail": goal, "status": "done", "kind": "thinking"},
            {"label": "Querying workspace inventory", "detail": f"{len(items)} live matches", "status": "done", "kind": "connector"},
        ],
    }
