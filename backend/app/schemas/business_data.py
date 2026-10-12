from typing import List, Optional
from pydantic import BaseModel, Field


class Customer(BaseModel):
    customer_id: str
    name: str
    status: str


class ContractAccount(BaseModel):
    account_id: str
    customer_id: str
    currency: str
    status: str


class ChargingEvent(BaseModel):
    event_id: str
    account_id: str
    service: str
    quantity: float
    amount: float
    timestamp: str


class BillingDocument(BaseModel):
    billing_id: str
    account_id: str
    period: str
    total_amount: float
    status: str
    charging_event_ids: List[str] = Field(default_factory=list)


class Invoice(BaseModel):
    invoice_id: str
    account_id: str
    billing_id: str
    total_amount: float
    currency: str
    status: str
    invoice_date: str
    due_date: str


class InvoiceChain(BaseModel):
    """
    Structured relationship representation for invoice investigation.
    Resolves: Invoice -> Billing Document -> Charging Events -> Contract Account -> Customer.
    Strictly structured data only; no natural-language generation or prompt logic.
    """
    invoice: Invoice
    billing_document: Optional[BillingDocument] = None
    charging_events: List[ChargingEvent] = Field(default_factory=list)
    contract_account: Optional[ContractAccount] = None
    customer: Optional[Customer] = None
