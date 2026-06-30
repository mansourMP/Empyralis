from __future__ import annotations

import csv
import io
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from server_modules import google_drive_api
from server_modules import vault_store
from server_modules import vault_helpers


REQUIRED_PRODUCT_CATALOG_COLUMNS = (
    "sku",
    "product_name",
    "category",
    "price",
    "currency",
    "quantity_available",
)
OPTIONAL_PRODUCT_CATALOG_COLUMNS = (
    "variant",
    "brand",
    "shipping_eta_days",
    "last_updated_at",
    "aliases",
    "policy_notes",
)
READ_ONLY_CATALOG_ACTIONS = frozenset(
    {
        "catalog.lookup",
        "spreadsheet_read.product_catalog_lookup",
        "product_catalog.lookup",
    }
)


def _normalize_text(value: Any, *, default: str = "") -> str:
    token = str(value or "").strip()
    return token or default


def _normalize_optional_text(value: Any) -> Optional[str]:
    token = _normalize_text(value)
    return token or None


def _coerce_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _coerce_list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, list) else []


def _coerce_float(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a number.")
    try:
        return float(str(value).strip())
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be a number.") from error


def _coerce_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer.")
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be an integer.") from error


def _normalize_aliases(value: Any) -> List[str]:
    if isinstance(value, list):
        candidates = value
    else:
        candidates = re.split(r"[,|]", str(value or ""))
    aliases: List[str] = []
    for candidate in candidates:
        alias = _normalize_text(candidate)
        if alias:
            aliases.append(alias)
    return aliases


def normalize_product_catalog_row(row: Dict[str, Any]) -> Dict[str, Any]:
    payload = {str(key).strip(): value for key, value in row.items() if str(key or "").strip()}
    missing = [key for key in REQUIRED_PRODUCT_CATALOG_COLUMNS if _normalize_text(payload.get(key)) == ""]
    if missing:
        raise ValueError(f"Product catalog row is missing required columns: {', '.join(missing)}")

    normalized: Dict[str, Any] = {
        "sku": _normalize_text(payload.get("sku")),
        "product_name": _normalize_text(payload.get("product_name")),
        "category": _normalize_text(payload.get("category")),
        "price": _coerce_float(payload.get("price"), field="price"),
        "currency": _normalize_text(payload.get("currency")),
        "quantity_available": _coerce_int(payload.get("quantity_available"), field="quantity_available"),
    }
    for key in OPTIONAL_PRODUCT_CATALOG_COLUMNS:
        if key not in payload:
            continue
        if key == "shipping_eta_days":
            if _normalize_text(payload.get(key)) != "":
                normalized[key] = _coerce_int(payload.get(key), field=key)
        elif key == "aliases":
            normalized[key] = _normalize_aliases(payload.get(key))
        else:
            text = _normalize_optional_text(payload.get(key))
            if text is not None:
                normalized[key] = text
    return normalized


def parse_product_catalog_csv(csv_text: str) -> List[Dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(str(csv_text or "")))
    rows = [normalize_product_catalog_row(dict(row)) for row in reader]
    if not rows:
        raise ValueError("Product catalog CSV contains no rows.")
    return rows


_CATALOG_CACHE: Dict[str, Dict[str, Any]] = {}
_CATALOG_CACHE_TTL_SECONDS = 300


def _catalog_cache_key(workspace_id: str, deployed_agent_id: str, connector_id: Optional[str], spreadsheet_id: Optional[str]) -> str:
    return f"catalog:{workspace_id}:{deployed_agent_id}:{connector_id or 'none'}:{spreadsheet_id or 'none'}"


def _get_cached_catalog_rows(cache_key: str) -> Optional[Dict[str, Any]]:
    entry = _CATALOG_CACHE.get(cache_key)
    if not isinstance(entry, dict):
        return None
    if time.time() > entry.get("expires_at", 0):
        _CATALOG_CACHE.pop(cache_key, None)
        return None
    return entry


def _set_cached_catalog_rows(cache_key: str, rows: List[Dict[str, Any]]) -> None:
    _CATALOG_CACHE[cache_key] = {
        "rows": rows,
        "last_synced_at": time.time(),
        "expires_at": time.time() + _CATALOG_CACHE_TTL_SECONDS,
    }


def _resolve_connector_credentials(credential_id: str, workspace_id: str) -> Optional[Dict[str, Any]]:
    """Resolve a connector credential only when it belongs to this workspace."""
    try:
        return vault_helpers.resolve_vault_credential(
            vault_store.load_vault,
            vault_store._openssl_decrypt,
            credential_id,
            workspace_id=workspace_id,
        )
    except Exception:
        pass
    return None


def _source_candidates(metadata: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    live_data = _coerce_dict(metadata.get("live_data_connectors"))
    for key in ("product_catalog", "catalog", "inventory"):
        candidate = live_data.get(key)
        if isinstance(candidate, dict):
            yield candidate

    for key in ("product_catalog", "catalog", "inventory"):
        candidate = metadata.get(key)
        if isinstance(candidate, dict):
            yield candidate

    revenue_proof = _coerce_dict(metadata.get("revenue_proof"))
    demo_rows = revenue_proof.get("demo_catalog_rows")
    if isinstance(demo_rows, list):
        yield {
            "source_id": "shop_assistant_demo_catalog",
            "connector_type": "demo_catalog",
            "rows": demo_rows,
        }


def _source_matches_connector(source: Dict[str, Any], connector_id: Optional[str]) -> bool:
    if not connector_id:
        return True
    return connector_id in {
        _normalize_text(source.get("id")),
        _normalize_text(source.get("source_id")),
        _normalize_text(source.get("connector_id")),
    }


def resolve_deployed_agent_product_catalog(
    deployed_agent: Dict[str, Any],
    *,
    workspace_id: str,
    connector_id: Optional[str] = None,
) -> Dict[str, Any]:
    agent_id = _normalize_text(deployed_agent.get("id"))
    agent_workspace_id = _normalize_text(deployed_agent.get("owner_workspace_id"))
    requested_workspace_id = _normalize_text(workspace_id)
    if not agent_id:
        raise ValueError("Deployed agent id is required.")
    if agent_workspace_id != requested_workspace_id:
        raise PermissionError("Product catalog source is outside this workspace.")

    metadata = _coerce_dict(deployed_agent.get("metadata"))
    for source in _source_candidates(metadata):
        if not _source_matches_connector(source, connector_id):
            continue
        source_workspace_id = _normalize_optional_text(source.get("workspace_id"))
        if source_workspace_id and source_workspace_id != requested_workspace_id:
            raise PermissionError("Product catalog source is outside this workspace.")
        source_agent_id = _normalize_optional_text(source.get("deployed_agent_id") or source.get("agent_id"))
        if source_agent_id and source_agent_id != agent_id:
            raise PermissionError("Product catalog source is outside this deployed agent.")

        last_synced_at: Optional[float] = None
        if isinstance(source.get("rows"), list):
            rows = [normalize_product_catalog_row(_coerce_dict(item)) for item in _coerce_list(source.get("rows"))]
        elif _normalize_optional_text(source.get("csv_text")):
            rows = parse_product_catalog_csv(_normalize_text(source.get("csv_text")))
        elif _normalize_optional_text(source.get("spreadsheet_id")):
            spreadsheet_id = _normalize_text(source.get("spreadsheet_id"))
            source_connector_id = _normalize_optional_text(source.get("connector_id"))
            cache_key = _catalog_cache_key(requested_workspace_id, agent_id, source_connector_id, spreadsheet_id)
            cached = _get_cached_catalog_rows(cache_key)
            if cached is not None:
                rows = cached["rows"]
                last_synced_at = cached.get("last_synced_at")
            else:
                credentials: Optional[Dict[str, Any]] = None
                if source_connector_id:
                    credentials = _resolve_connector_credentials(source_connector_id, requested_workspace_id)
                if not credentials:
                    raise ValueError(f"Product catalog connector credentials are not available for {source_connector_id}.")
                raw_rows = google_drive_api.google_workspace_read_spreadsheet(
                    credentials,
                    spreadsheet_id,
                    range=_normalize_text(source.get("range"), default="Sheet1"),
                )
                rows = [normalize_product_catalog_row(_coerce_dict(item)) for item in raw_rows]
                _set_cached_catalog_rows(cache_key, rows)
                last_synced_at = time.time()
        else:
            raise ValueError("Product catalog source must provide rows, csv_text, or spreadsheet_id for this proof path.")
        if not rows:
            raise ValueError("Product catalog source contains no rows.")
        source_meta: Dict[str, Any] = {
            "source_id": _normalize_optional_text(source.get("source_id") or source.get("id")),
            "connector_id": _normalize_optional_text(source.get("connector_id")),
            "connector_type": _normalize_text(
                source.get("connector_type") or source.get("type"),
                default="spreadsheet",
            ),
            "workspace_id": requested_workspace_id,
            "deployed_agent_id": agent_id,
            "read_only": True,
        }
        if last_synced_at is not None:
            source_meta["last_synced_at"] = last_synced_at
        return {
            "rows": rows,
            "source": source_meta,
        }
    raise ValueError("No product catalog source is configured for this deployed agent.")


def _search_terms(value: Any) -> List[str]:
    return re.findall(r"[a-z0-9]+", str(value or "").lower())


def _row_search_blob(row: Dict[str, Any]) -> Tuple[str, List[str]]:
    fields = [
        row.get("sku"),
        row.get("product_name"),
        row.get("category"),
        row.get("variant"),
        row.get("brand"),
        row.get("policy_notes"),
        " ".join(_normalize_aliases(row.get("aliases"))),
    ]
    blob = " ".join(_normalize_text(field).lower() for field in fields if _normalize_text(field))
    return blob, _search_terms(blob)


def _score_row(row: Dict[str, Any], query_terms: List[str], query_text: str) -> int:
    blob, row_terms = _row_search_blob(row)
    score = 0
    sku = _normalize_text(row.get("sku")).lower()
    if sku and sku in query_text:
        score += 50
    product_name = _normalize_text(row.get("product_name")).lower()
    if product_name and product_name in query_text:
        score += 30
    for term in query_terms:
        if term in row_terms:
            score += 4
        elif len(term) >= 3 and term in blob:
            score += 2
    return score


def _format_price(row: Dict[str, Any]) -> str:
    price = row.get("price")
    currency = _normalize_text(row.get("currency"), default="USD")
    if isinstance(price, float) and price.is_integer():
        return f"{int(price)} {currency}"
    return f"{price} {currency}"


def _answer_from_matches(query: str, matches: List[Dict[str, Any]]) -> str:
    if not matches:
        return "No matching catalog row was found in the connected product catalog."
    item = matches[0]
    quantity = int(item.get("quantity_available") or 0)
    stock_phrase = f"{quantity} in stock" if quantity > 0 else "currently out of stock"
    variant = _normalize_optional_text(item.get("variant"))
    brand = _normalize_optional_text(item.get("brand"))
    product_name = _normalize_text(item.get("product_name"))
    brand_prefix = brand if brand and not product_name.lower().startswith(brand.lower()) else None
    descriptor = " ".join(part for part in (brand_prefix, product_name, variant) if _normalize_text(part))
    eta = item.get("shipping_eta_days")
    eta_phrase = f" Shipping ETA is {eta} day{'s' if eta != 1 else ''}." if eta is not None else ""
    return f"{descriptor} is {stock_phrase}. Price: {_format_price(item)}.{eta_phrase}"


def lookup_product_catalog(
    deployed_agent: Dict[str, Any],
    *,
    workspace_id: str,
    query: str,
    connector_id: Optional[str] = None,
    limit: int = 5,
) -> Dict[str, Any]:
    catalog = resolve_deployed_agent_product_catalog(
        deployed_agent,
        workspace_id=workspace_id,
        connector_id=connector_id,
    )
    query_text = _normalize_text(query).lower()
    if not query_text:
        raise ValueError("query is required for catalog lookup.")
    query_terms = _search_terms(query_text)
    scored: List[Tuple[int, Dict[str, Any]]] = []
    for row in catalog["rows"]:
        score = _score_row(row, query_terms, query_text)
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda item: (-item[0], _normalize_text(item[1].get("sku"))))
    matches = [row for _, row in scored[: max(1, min(int(limit or 5), 20))]]
    return {
        "ok": True,
        "action": "catalog.lookup",
        "query": query,
        "matches": matches,
        "answer": _answer_from_matches(query, matches),
        "source": catalog["source"],
        "read_only": True,
    }


def execute_read_only_catalog_action(
    deployed_agent: Dict[str, Any],
    *,
    workspace_id: str,
    action_id: str,
    arguments: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    action = _normalize_text(action_id)
    if action not in READ_ONLY_CATALOG_ACTIONS:
        raise PermissionError("Only read-only product catalog lookup actions are allowed.")
    payload = _coerce_dict(arguments)
    return lookup_product_catalog(
        deployed_agent,
        workspace_id=workspace_id,
        query=_normalize_text(payload.get("query")),
        connector_id=_normalize_optional_text(payload.get("connector_id")),
        limit=_coerce_int(payload.get("limit", 5), field="limit"),
    )
