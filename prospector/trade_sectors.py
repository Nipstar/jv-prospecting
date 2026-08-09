"""Trade sector seed list — high-ticket, ad-likely UK local businesses.

Each entry: name, category, ticket_size_estimate, google_search_term.
Stored as a dict keyed by a short slug so it's easy to extend later and so
the wizard can multi-select by category.
"""
from __future__ import annotations

TRADE_SECTORS: dict[str, dict[str, str]] = {
    # --- Legal / medical (£1k-£30k+ per case) -------------------------------
    "personal_injury_solicitors": {
        "name": "Personal injury solicitors",
        "category": "Legal / medical",
        "ticket_size_estimate": "£1k-£30k+ per case",
        "google_search_term": "personal injury solicitors",
    },
    "medical_negligence_solicitors": {
        "name": "Medical negligence solicitors",
        "category": "Legal / medical",
        "ticket_size_estimate": "£1k-£30k+ per case",
        "google_search_term": "medical negligence solicitors",
    },
    "immigration_solicitors": {
        "name": "Immigration solicitors",
        "category": "Legal / medical",
        "ticket_size_estimate": "£1k-£30k+ per case",
        "google_search_term": "immigration solicitors",
    },
    "conveyancing_solicitors": {
        "name": "Conveyancing solicitors",
        "category": "Legal / medical",
        "ticket_size_estimate": "£1k-£30k+ per case",
        "google_search_term": "conveyancing solicitors",
    },
    "cosmetic_dentistry": {
        "name": "Cosmetic dentistry / implant clinics",
        "category": "Legal / medical",
        "ticket_size_estimate": "£1k-£30k+ per case",
        "google_search_term": "cosmetic dentistry implant clinic",
    },
    "aesthetic_clinics": {
        "name": "Aesthetic clinics (hair transplant, laser eye, injectables)",
        "category": "Legal / medical",
        "ticket_size_estimate": "£1k-£30k+ per case",
        "google_search_term": "aesthetic clinic hair transplant laser eye injectables",
    },
    "private_gp_diagnostics": {
        "name": "Private GP / diagnostics clinics",
        "category": "Legal / medical",
        "ticket_size_estimate": "£1k-£30k+ per case",
        "google_search_term": "private GP diagnostics clinic",
    },

    # --- Property (£1k-£40k+ per job/tenancy) -------------------------------
    "hmo_property_sourcing": {
        "name": "HMO / property investment sourcing companies",
        "category": "Property",
        "ticket_size_estimate": "£1k-£40k+ per job/tenancy",
        "google_search_term": "HMO property investment sourcing company",
    },
    "lettings_agencies": {
        "name": "Lettings agencies",
        "category": "Property",
        "ticket_size_estimate": "£1k-£40k+ per job/tenancy",
        "google_search_term": "lettings agency",
    },
    "estate_agents_independent": {
        "name": "Estate agents (independent, non-corporate)",
        "category": "Property",
        "ticket_size_estimate": "£1k-£40k+ per job/tenancy",
        "google_search_term": "independent estate agents",
    },
    "basement_cellar_conversions": {
        "name": "Basement / cellar conversions",
        "category": "Property",
        "ticket_size_estimate": "£1k-£40k+ per job/tenancy",
        "google_search_term": "basement cellar conversion company",
    },
    "barn_conversions": {
        "name": "Barn conversions",
        "category": "Property",
        "ticket_size_estimate": "£1k-£40k+ per job/tenancy",
        "google_search_term": "barn conversion company",
    },
    "architects": {
        "name": "Architects / architectural services",
        "category": "Property",
        "ticket_size_estimate": "£1k-£40k+ per job/tenancy",
        "google_search_term": "architects architectural services",
    },

    # --- Home improvement (£3k-£40k per job) --------------------------------
    "windows_installation": {
        "name": "Windows installation (aluminium/uPVC)",
        "category": "Home improvement",
        "ticket_size_estimate": "£3k-£40k per job",
        "google_search_term": "aluminium uPVC windows installation",
    },
    "kitchen_fitters": {
        "name": "Kitchen fitters / kitchen renovation",
        "category": "Home improvement",
        "ticket_size_estimate": "£3k-£40k per job",
        "google_search_term": "kitchen fitters kitchen renovation",
    },
    "bathroom_renovation": {
        "name": "Bathroom renovation",
        "category": "Home improvement",
        "ticket_size_estimate": "£3k-£40k per job",
        "google_search_term": "bathroom renovation company",
    },
    "driveways_paving": {
        "name": "Driveways / paving",
        "category": "Home improvement",
        "ticket_size_estimate": "£3k-£40k per job",
        "google_search_term": "driveways paving company",
    },
    "air_source_heat_pumps": {
        "name": "Air source heat pump installers",
        "category": "Home improvement",
        "ticket_size_estimate": "£3k-£40k per job",
        "google_search_term": "air source heat pump installers",
    },
    "air_conditioning": {
        "name": "Air conditioning installation",
        "category": "Home improvement",
        "ticket_size_estimate": "£3k-£40k per job",
        "google_search_term": "air conditioning installation",
    },
    "loft_conversions": {
        "name": "Loft/attic conversions",
        "category": "Home improvement",
        "ticket_size_estimate": "£3k-£40k per job",
        "google_search_term": "loft attic conversions",
    },
    "orangeries_conservatories": {
        "name": "Orangeries / conservatories",
        "category": "Home improvement",
        "ticket_size_estimate": "£3k-£40k per job",
        "google_search_term": "orangeries conservatories company",
    },
    "solar_roofing": {
        "name": "Solar and roofing installers",
        "category": "Home improvement",
        "ticket_size_estimate": "£3k-£40k per job",
        "google_search_term": "solar panel and roofing installers",
    },

    # --- Trade services (£300-£5k per job, high volume) ---------------------
    "plumbers": {
        "name": "Plumbers",
        "category": "Trade services",
        "ticket_size_estimate": "£300-£5k per job",
        "google_search_term": "plumbers",
    },
    "hvac_engineers": {
        "name": "HVAC engineers",
        "category": "Trade services",
        "ticket_size_estimate": "£300-£5k per job",
        "google_search_term": "HVAC engineers",
    },
    "electricians": {
        "name": "Electricians",
        "category": "Trade services",
        "ticket_size_estimate": "£300-£5k per job",
        "google_search_term": "electricians",
    },
    "facilities_management": {
        "name": "Facilities management companies",
        "category": "Trade services",
        "ticket_size_estimate": "£300-£5k per job",
        "google_search_term": "facilities management company",
    },

    # --- Other independents --------------------------------------------------
    "veterinary_practices": {
        "name": "Private veterinary practices (non-group)",
        "category": "Other independents",
        "ticket_size_estimate": "varies",
        "google_search_term": "private veterinary practice",
    },
    "dental_practices_independent": {
        "name": "Independent dental practices (general, non-cosmetic)",
        "category": "Other independents",
        "ticket_size_estimate": "varies",
        "google_search_term": "independent dental practice",
    },
}

# Categories in display order, for the wizard's grouped checklist.
CATEGORY_ORDER = [
    "Legal / medical",
    "Property",
    "Home improvement",
    "Trade services",
    "Other independents",
]


def sectors_by_category() -> dict[str, list[tuple[str, dict[str, str]]]]:
    """Group TRADE_SECTORS entries by category, in CATEGORY_ORDER."""
    grouped: dict[str, list[tuple[str, dict[str, str]]]] = {c: [] for c in CATEGORY_ORDER}
    for slug, info in TRADE_SECTORS.items():
        grouped.setdefault(info["category"], []).append((slug, info))
    return grouped
