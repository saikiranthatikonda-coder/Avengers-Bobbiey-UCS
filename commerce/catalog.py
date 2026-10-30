"""Service catalogue. `available` is true only when an executor exists and runs
reliably today; planned services are listed honestly and cannot be purchased.
Prices are owner-editable (stored in the ledger's settings, per mode)."""

SERVICES = [
    {
        "id": "website.extract", "name": "Website Extraction API",
        "description": "Fetches a public web page and returns its title, description, language, headings, "
                       "link counts, social profiles, contact emails and Open Graph data.",
        "input_schema": {"type": "object", "required": ["url"], "additionalProperties": False,
                         "properties": {"url": {"type": "string", "format": "uri", "maxLength": 2000,
                                                "description": "public http(s) page"}}},
        "output_schema": {"type": "object", "properties": {
            "url": {"type": "string"}, "status_code": {"type": "integer"}, "title": {"type": "string"},
            "description": {"type": ["string", "null"]}, "language": {"type": ["string", "null"]},
            "headings": {"type": "array"}, "links": {"type": "object"}, "social_profiles": {"type": "array"},
            "emails": {"type": "array"}, "og": {"type": "object"}, "word_count": {"type": "integer"},
            "fetched_at": {"type": "number"}}},
        "price_paise": 1000, "auth": "order access token; agents may also use an API key",
        "limits": {"per_minute": 20, "max_page_bytes": 2_000_000, "timeout_s": 10}, "available": True,
    },
    {"id": "document.intel", "name": "Document Intelligence API",
     "description": "Summaries and structured fields from uploaded documents.", "available": False,
     "planned_reason": "needs a commercially licensed AI API key (a personal Claude subscription can't be resold)"},
    {"id": "company.intel", "name": "Company Intelligence API",
     "description": "Company profile, filings and key facts.", "available": False,
     "planned_reason": "needs a licensed company-data source (e.g. MCA filings provider)"},
    {"id": "startup.discovery", "name": "Startup Discovery API",
     "description": "Find startups by sector, stage and region.", "available": False,
     "planned_reason": "needs a licensed startup database"},
    {"id": "competitor.research", "name": "Competitor Research API",
     "description": "Competitor landscape for a company or product.", "available": False,
     "planned_reason": "builds on company intelligence + web extraction"},
    {"id": "market.intel", "name": "Market Intelligence API",
     "description": "Market size, trends and signals for a sector.", "available": False,
     "planned_reason": "needs licensed market data"},
    {"id": "business.analytics", "name": "Business Analytics API",
     "description": "Analytics on a customer's own uploaded business data.", "available": False,
     "planned_reason": "needs data-upload storage and a licensed AI API"},
]
PLANNED_FIELDS = ("input_schema", "output_schema", "price_paise", "auth", "limits")


def get(service_id: str) -> dict | None:
    return next((s for s in SERVICES if s["id"] == service_id), None)


def public(ledger, include_planned: bool = True) -> list[dict]:
    """What customers and agents see (prices from the owner's settings)."""
    prices = ledger.get_setting("prices", {}) if ledger else {}
    disabled = set(ledger.get_setting("disabled_services", [])) if ledger else set()
    out = []
    for s in SERVICES:
        live = s["available"] and s["id"] not in disabled
        if not live and not include_planned:
            continue
        row = {k: v for k, v in s.items() if k != "planned_reason"}
        row["available"] = live
        if live:
            row["price_paise"] = int(prices.get(s["id"], s["price_paise"]))
            row["price"] = f"₹{row['price_paise'] / 100:,.2f}"
        else:
            row["status"] = "planned" if not s["available"] else "paused by owner"
            for k in PLANNED_FIELDS:
                row.pop(k, None)
        out.append(row)
    return out


def validate(service: dict, inp) -> str | None:
    """Minimal JSON-schema check for the declared input (required keys, types, extras, length)."""
    sch = service.get("input_schema", {})
    if not isinstance(inp, dict):
        return "input must be a JSON object"
    for k in sch.get("required", []):
        if k not in inp:
            return f"missing required field '{k}'"
    props = sch.get("properties", {})
    if sch.get("additionalProperties") is False:
        extra = set(inp) - set(props)
        if extra:
            return "unknown field(s): " + ", ".join(sorted(extra))
    for k, spec in props.items():
        if k in inp:
            if spec.get("type") == "string" and not isinstance(inp[k], str):
                return f"'{k}' must be a string"
            if isinstance(inp[k], str) and len(inp[k]) > spec.get("maxLength", 10_000):
                return f"'{k}' is too long"
    return None
