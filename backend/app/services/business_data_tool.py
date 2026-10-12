from typing import List, Optional

from backend.app.schemas.business_data import (
    Customer,
    ContractAccount,
    ChargingEvent,
    BillingDocument,
    Invoice,
    InvoiceChain,
)
from backend.app.services.mock_sap_repository import (
    MockSAPRepository,
    mock_sap_repository,
)


class BusinessDataTool:
    """
    Business Data Tool boundary for agentic orchestration and capability agents.

    Architecture boundary:
        Agent / Orchestrator / API
            ↓
        BusinessDataTool (Unified Tool Interface)
            ↓
        Data Source Adapter (Currently: MockSAPRepository; Future: SAP API Adapter, CSV/JSON Adapter)

    Provides strictly structured business data without LLM reasoning, prompt construction,
    or natural-language explanations.
    """

    def __init__(self, repository: Optional[MockSAPRepository] = None) -> None:
        self.repository = repository or mock_sap_repository

    def get_customer(self, customer_id: str) -> Optional[Customer]:
        """Retrieve customer master record by ID."""
        return self.repository.get_customer(customer_id)

    def get_contract_account(self, account_id: str) -> Optional[ContractAccount]:
        """Retrieve contract account record by ID."""
        return self.repository.get_contract_account(account_id)

    def get_invoice(self, invoice_id: str) -> Optional[Invoice]:
        """Retrieve invoice record by ID."""
        return self.repository.get_invoice(invoice_id)

    def get_billing_document(self, billing_id: str) -> Optional[BillingDocument]:
        """Retrieve billing document record by ID."""
        return self.repository.get_billing_document(billing_id)

    def get_charging_events(
        self,
        account_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[ChargingEvent]:
        """Retrieve charging events for a contract account, optionally filtered by timestamp."""
        return self.repository.get_charging_events(account_id, start_date, end_date)

    def get_invoice_chain(self, invoice_id: str) -> Optional[InvoiceChain]:
        """
        Retrieve complete structured relational chain for an invoice:
        Invoice -> Billing Document -> Charging Events -> Contract Account -> Customer.
        """
        return self.repository.get_invoice_chain(invoice_id)


business_data_tool = BusinessDataTool()
