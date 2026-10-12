import re
from typing import Tuple, Optional, List, Dict


SAP_CORE_MODULES = {
    "fi", "co", "fico", "mm", "sd", "pp", "qm", "pm", "ps", "hcm", "hr",
    "basis", "abap", "fiori", "s/4hana", "s4hana", "s4", "ecc", "btp",
    "netweaver", "successfactors", "ariba", "concur", "hybris", "c4c",
    "bw", "bi", "crm", "srm", "ewm", "tm", "grc", "mdg", "solman",
    "brim", "cc", "ci", "som", "fica", "fi-ca", "bit", "cit",
    "rar", "odi", "daas", "pob"
}


SAP_KEYWORDS = {
    "sap", "hana", "s/4", "s4", "fiori", "abap", "bapi", "idoc", "ale",
    "rfc", "odata", "spro", "img", "t-code", "tcode", "transaction code",
    "transport request", "movement type", "company code", "controlling area",
    "purchasing organization", "plant", "storage location", "material master",
    "vendor master", "customer master", "bill of materials", "bom", "routing",
    "work center", "mrp", "goods receipt", "goods issue",
    "3-way match", "three way match", "purchase order", "sales order",
    "delivery document", "billing document", "general ledger",
    "accounts payable", "accounts receivable", "asset accounting",
    "cost center", "profit center", "internal order", "posting key",
    "chart of accounts", "reconciliation account", "sapgui", "sap gui",
    "netweaver", "alv", "smartforms", "adobe forms", "enhancement point",
    "user exit", "badi", "luw", "cds view", "amdp", "core data services",
    "rap", "cap", "btp", "open sql", "hana cloud", "solution manager",
    "service marketplace", "sap notes", "oss note", "su01", "pfcg",
    "sm50", "sm21", "st22", "st03n", "se11", "se16n", "se38", "se80",
    "me21n", "va01", "fb01", "fb50", "fb60", "migo", "miro", "vl01n",
    "vf01", "co01",
    # SAP BRIM Revenue & Billing Domain Keywords
    "provider contract", "billing plan", "chargeable item", "billable item",
    "consumption item", "chargeable event", "chargeable service", "price plan",
    "pricing plan", "price table", "charge plan", "charging plan",
    "allowance plan", "allowance logic", "allowance",
    "tier table", "mapping table", "range table", "core tool",
    "convergent charging", "convergent invoicing", "subscription order management",
    "contract accounts receivable and payable", "bit class", "cit class",
    "billable item class", "consumption item class", "partner settlement",
    "revenue sharing", "rating logic", "rating process", "invoicing document",
    "billable item management", "usage-based rating", "usage-based billing",
    "usage metering", "consumption metering", "charge calculation",
    # Subledger, clearing, and financial reconciliation terms
    "clearing", "cleared", "clear document", "dunning", "payment lot",
    "payment allocation", "open item", "open items", "open receivable",
    "subledger", "subledger document", "contract account", "order distribution",
    "order distribution infrastructure", "revenue accounting", "revenue accounting and reporting",
    "performance obligation", "clarification list", "residual item", "partial payment",
    "on-account payment", "unallocated payment", "charging contract"
}


GENERAL_UNRELATED_PATTERNS = [
    r"\b(cricket|football|soccer|basketball|baseball|tennis|olympics|ipl)\b",
    r"\b(recipe|cook|baking|cake|pizza|pasta|dinner|lunch|breakfast|salad)\b",
    r"\b(birthday|anniversary|wedding|congratulation|love letter|poem|poetry|song)\b",
    r"\b(elon musk|donald trump|joe biden|taylor swift|movie|actor|actress|cinema)\b",
    r"\b(python|javascript|typescript|c\+\+|java|golang|rust|ruby|php)\b",
    r"\b(weather|horoscope|zodiac|astrology|travel guide|hotel booking|flight ticket)\b",
]


class SAPClassifier:
    """Strict SAP-domain classifier with comprehensive SAP BRIM coverage and multi-turn context support."""

    @staticmethod
    def _contains_term(text: str, term: str) -> bool:
        """
        Match SAP terms safely.

        Examples:
        'cap' matches 'CAP'
        'cap' does NOT match 'capital'
        'fi' matches 'FI'
        'fi' does NOT match 'finance'
        """
        escaped = re.escape(term.lower())

        # Terms containing non-word characters need a slightly
        # more flexible boundary.
        if re.search(r"\W", term):
            pattern = rf"(?<!\w){escaped}(?!\w)"
        else:
            pattern = rf"(?<![a-z0-9]){escaped}(?![a-z0-9])"

        return bool(re.search(pattern, text, re.IGNORECASE))

    @classmethod
    def classify(cls, query: str, chat_history: Optional[List[Dict[str, str]]] = None) -> Tuple[bool, str]:
        """
        Returns:
            (True, 'sap_domain_verified') for SAP-related queries
            (False, refusal message) for non-SAP queries
        """

        text = query.strip().lower()

        if not text:
            return False, "Query cannot be empty."

        # Explicit SAP mention always qualifies.
        if cls._contains_term(text, "sap"):
            return True, "sap_domain_verified"

        # Explicitly check for known unrelated non-SAP questions first if no explicit 'sap'
        for pattern in GENERAL_UNRELATED_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                # Only accept if it ALSO explicitly contains a strong SAP/BRIM keyword
                has_strong_sap = any(
                    cls._contains_term(text, kw)
                    for kw in (
                        "convergent charging", "convergent invoicing", "subscription order management",
                        "provider contract", "price plan", "charge plan", "billable item", "s/4hana", "abap", "bapi"
                    )
                )
                if not has_strong_sap:
                    return False, "I can only help with SAP and SAP-related topics."

        # Match SAP keywords using whole-term matching.
        has_sap_keyword = any(
            cls._contains_term(text, keyword)
            for keyword in SAP_KEYWORDS
            if keyword != "sap"
        )

        # Match SAP module names safely.
        has_sap_module = any(
            cls._contains_term(text, module)
            for module in SAP_CORE_MODULES
        )

        # SAP transaction-code pattern including FI-CA & BRIM monitoring codes.
        has_tcode_pattern = bool(
            re.search(
                r"\b(?:me21n|va01|fb01|fb50|fb60|migo|miro|vl01n|vf01|"
                r"co01|su01|pfcg|sm50|sm21|st22|st03n|se11|se16n|se38|se80|"
                r"fpl9|fpe[1-3]|fp04|fpy1|fpva|fpvb|fpo1|fp06|fp07|fp08|fp40|"
                r"fkkbix[a-z0-9_]*|dfkk[a-z0-9_]*)\b",
                text,
                re.IGNORECASE
            )
        )

        # Common SAP table names.
        common_tables = {
            "mara", "marc", "mard", "bseg", "bkpf",
            "ekko", "ekpo", "vbak", "vbap",
            "kna1", "lfa1", "t001", "dfkkbip", "dfkkop"
        }

        has_sap_table = any(
            cls._contains_term(text, table)
            for table in common_tables
        )

        if (
            has_sap_keyword
            or has_sap_module
            or has_tcode_pattern
            or has_sap_table
        ):
            return True, "sap_domain_verified"

        # Multi-turn conversational context check:
        # If this is an ongoing conversation with established SAP context,
        # allow valid follow-up questions referencing prior turn concepts/entities.
        if chat_history:
            recent_context = " ".join((turn.get("content") or "") for turn in chat_history[-3:]).lower()
            context_has_sap = any(cls._contains_term(recent_context, kw) for kw in (
                "sap", "brim", "som", "cc", "ci", "fica", "fi-ca", "rar", "fpl9", "fp04",
                "invoice", "contract", "billing", "clearing", "receivable"
            ))
            if context_has_sap:
                is_conversational_followup = bool(re.search(
                    r"\b(?:what about|how about|why did|can we|could this|is that|does that|"
                    r"how to|what is the next step|explain step|what causes that|why was|"
                    r"that issue|the previous|that document|that invoice|that account|it|this|reverse)\b",
                    text,
                    re.IGNORECASE
                ))
                if is_conversational_followup:
                    return True, "sap_domain_verified"

        # Everything without SAP context is rejected.
        return False, "I can only help with SAP and SAP-related topics."


sap_classifier = SAPClassifier()