import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from backend.app.schemas.business_data import (
    Customer,
    ContractAccount,
    ChargingEvent,
    BillingDocument,
    Invoice,
    InvoiceChain,
)

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "mock_sap"


class MockSAPRepository:
    """
    Centralized read-only repository for mock SAP business data.
    
    Provides deterministic access to mock customers, contract accounts,
    charging events, billing documents, and invoices.
    
    Prevents scattered file reads across the codebase.
    Strictly returns structured data; no LLM reasoning, no prompt logic,
    and no generated explanations.
    """

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self.data_dir = data_dir or DATA_DIR
        self._customers: Dict[str, Customer] = {}
        self._contract_accounts: Dict[str, ContractAccount] = {}
        self._charging_events: Dict[str, ChargingEvent] = {}
        self._billing_documents: Dict[str, BillingDocument] = {}
        self._invoices: Dict[str, Invoice] = {}
        self.reload()

    def reload(self) -> None:
        """Loads and indexes mock data from JSON storage files."""
        self._customers = self._load_records("customers.json", Customer, "customer_id")
        self._contract_accounts = self._load_records("contract_accounts.json", ContractAccount, "account_id")
        self._charging_events = self._load_records("charging_events.json", ChargingEvent, "event_id")
        self._billing_documents = self._load_records("billing_documents.json", BillingDocument, "billing_id")
        self._invoices = self._load_records("invoices.json", Invoice, "invoice_id")
        logger.info(
            "Mock SAP repository loaded: %d customers, %d contract accounts, %d charging events, %d billing documents, %d invoices.",
            len(self._customers),
            len(self._contract_accounts),
            len(self._charging_events),
            len(self._billing_documents),
            len(self._invoices),
        )

    def _load_records(self, filename: str, model_cls, id_field: str) -> Dict[str, any]:
        filepath = self.data_dir / filename
        if not filepath.exists():
            logger.warning("Mock SAP file does not exist: %s", filepath)
            return {}
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
            records = {}
            for item in raw_data:
                obj = model_cls.model_validate(item)
                key = getattr(obj, id_field)
                records[key] = obj
            return records
        except Exception as e:
            logger.error("Failed to load mock data from %s: %e", filepath, e)
            return {}

    # -------------------------------------------------------------------------
    # CUSTOMERS
    # -------------------------------------------------------------------------

    def get_customer(self, customer_id: str) -> Optional[Customer]:
        return self._customers.get(customer_id)

    def list_customers(self) -> List[Customer]:
        return list(self._customers.values())

    # -------------------------------------------------------------------------
    # CONTRACT ACCOUNTS
    # -------------------------------------------------------------------------

    def get_contract_account(self, account_id: str) -> Optional[ContractAccount]:
        return self._contract_accounts.get(account_id)

    def list_contract_accounts(self, customer_id: Optional[str] = None) -> List[ContractAccount]:
        if customer_id:
            return [ca for ca in self._contract_accounts.values() if ca.customer_id == customer_id]
        return list(self._contract_accounts.values())

    # -------------------------------------------------------------------------
    # CHARGING EVENTS
    # -------------------------------------------------------------------------

    def get_charging_event(self, event_id: str) -> Optional[ChargingEvent]:
        return self._charging_events.get(event_id)

    def get_charging_events(
        self,
        account_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[ChargingEvent]:
        events = [e for e in self._charging_events.values() if e.account_id == account_id]
        if start_date:
            events = [e for e in events if e.timestamp >= start_date]
        if end_date:
            events = [e for e in events if e.timestamp <= end_date]
        return events

    # -------------------------------------------------------------------------
    # BILLING DOCUMENTS
    # -------------------------------------------------------------------------

    def get_billing_document(self, billing_id: str) -> Optional[BillingDocument]:
        return self._billing_documents.get(billing_id)

    def list_billing_documents(self, account_id: Optional[str] = None) -> List[BillingDocument]:
        if account_id:
            return [b for b in self._billing_documents.values() if b.account_id == account_id]
        return list(self._billing_documents.values())

    # -------------------------------------------------------------------------
    # INVOICES
    # -------------------------------------------------------------------------

    def get_invoice(self, invoice_id: str) -> Optional[Invoice]:
        return self._invoices.get(invoice_id)

    def list_invoices(self, account_id: Optional[str] = None) -> List[Invoice]:
        if account_id:
            return [inv for inv in self._invoices.values() if inv.account_id == account_id]
        return list(self._invoices.values())

    # -------------------------------------------------------------------------
    # INVESTIGATION / RELATIONSHIP RESOLUTION
    # -------------------------------------------------------------------------

    def get_invoice_chain(self, invoice_id: str) -> Optional[InvoiceChain]:
        """
        Resolves the full relational chain for a given invoice:
        Invoice -> Billing Document -> Charging Events -> Contract Account -> Customer.
        
        Returns structured InvoiceChain without natural-language explanation.
        Returns None if the requested invoice does not exist.
        """
        invoice = self.get_invoice(invoice_id)
        if not invoice:
            return None

        # Resolve billing document
        billing_doc = self.get_billing_document(invoice.billing_id) if invoice.billing_id else None

        # Resolve charging events (prefer explicitly referenced events from billing doc)
        charging_events: List[ChargingEvent] = []
        if billing_doc and billing_doc.charging_event_ids:
            for eid in billing_doc.charging_event_ids:
                evt = self.get_charging_event(eid)
                if evt:
                    charging_events.append(evt)
        else:
            charging_events = self.get_charging_events(invoice.account_id)

        # Resolve contract account
        contract_account = self.get_contract_account(invoice.account_id)

        # Resolve customer
        customer = None
        if contract_account:
            customer = self.get_customer(contract_account.customer_id)

        return InvoiceChain(
            invoice=invoice,
            billing_document=billing_doc,
            charging_events=charging_events,
            contract_account=contract_account,
            customer=customer,
        )


mock_sap_repository = MockSAPRepository()
