import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.schemas.business_data import (
    Customer,
    ContractAccount,
    ChargingEvent,
    BillingDocument,
    Invoice,
    InvoiceChain,
)
from backend.app.services.business_data_tool import business_data_tool
from backend.app.services.mock_sap_repository import mock_sap_repository

client = TestClient(app)


@pytest.fixture
def auth_headers():
    client.post(
        "/api/auth/register",
        json={"email": "business_tester@sap.com", "name": "Business Tester", "password": "Password123!"},
    )
    login_resp = client.post(
        "/api/auth/login",
        json={"email": "business_tester@sap.com", "password": "Password123!"},
    ).json()
    return {"Authorization": f"Bearer {login_resp['access_token']}"}


def test_customer_retrieval():
    """1: Existing customer can be retrieved."""
    cust = business_data_tool.get_customer("CUST-1001")
    assert cust is not None
    assert isinstance(cust, Customer)
    assert cust.customer_id == "CUST-1001"
    assert cust.name == "Acme Telecom"
    assert cust.status == "ACTIVE"


def test_contract_account_retrieval():
    """2: Existing contract account can be retrieved."""
    account = business_data_tool.get_contract_account("CA-2001")
    assert account is not None
    assert isinstance(account, ContractAccount)
    assert account.account_id == "CA-2001"
    assert account.customer_id == "CUST-1001"
    assert account.currency == "USD"
    assert account.status == "ACTIVE"


def test_invoice_retrieval():
    """3: Invoice INV-1042 can be retrieved."""
    inv = business_data_tool.get_invoice("INV-1042")
    assert inv is not None
    assert isinstance(inv, Invoice)
    assert inv.invoice_id == "INV-1042"
    assert inv.account_id == "CA-2001"
    assert inv.billing_id == "BILL-3001"
    assert inv.total_amount == 125.00
    assert inv.currency == "USD"
    assert inv.status == "POSTED"


def test_billing_document_retrieval():
    """4: BILL-3001 can be retrieved."""
    bill = business_data_tool.get_billing_document("BILL-3001")
    assert bill is not None
    assert isinstance(bill, BillingDocument)
    assert bill.billing_id == "BILL-3001"
    assert bill.account_id == "CA-2001"
    assert bill.total_amount == 100.00
    assert bill.status == "POSTED"


def test_charging_events_for_account():
    """5: Charging events for CA-2001 resolve correctly."""
    events = business_data_tool.get_charging_events("CA-2001")
    assert len(events) == 3
    event_ids = {e.event_id for e in events}
    assert event_ids == {"EVT-001", "EVT-002", "EVT-003"}
    for e in events:
        assert isinstance(e, ChargingEvent)
        assert e.account_id == "CA-2001"


def test_charging_events_total_100():
    """6: EVT-001 + EVT-002 + EVT-003 total $100."""
    events = business_data_tool.get_charging_events("CA-2001")
    total = sum(e.amount for e in events)
    assert total == 100.00
    event_map = {e.event_id: e.amount for e in events}
    assert event_map["EVT-001"] == 40.00
    assert event_map["EVT-002"] == 35.00
    assert event_map["EVT-003"] == 25.00


def test_billing_document_references_charging_events():
    """7: BILL-3001 references the expected charging events."""
    bill = business_data_tool.get_billing_document("BILL-3001")
    assert bill is not None
    assert bill.charging_event_ids == ["EVT-001", "EVT-002", "EVT-003"]
    # Check that referenced events exist and sum to billing doc total
    referenced_events = [business_data_tool.repository.get_charging_event(eid) for eid in bill.charging_event_ids]
    assert all(re is not None for re in referenced_events)
    assert sum(re.amount for re in referenced_events) == bill.total_amount == 100.00


def test_invoice_references_billing_document():
    """8: INV-1042 references BILL-3001."""
    inv = business_data_tool.get_invoice("INV-1042")
    assert inv is not None
    assert inv.billing_id == "BILL-3001"
    bill = business_data_tool.get_billing_document(inv.billing_id)
    assert bill is not None
    assert bill.billing_id == "BILL-3001"


def test_invoice_chain_resolution():
    """9: get_invoice_chain('INV-1042') resolves the complete relationship."""
    chain = business_data_tool.get_invoice_chain("INV-1042")
    assert chain is not None
    assert isinstance(chain, InvoiceChain)

    # 1. Invoice
    assert chain.invoice.invoice_id == "INV-1042"
    assert chain.invoice.total_amount == 125.00
    assert chain.invoice.account_id == "CA-2001"

    # 2. Billing Document
    assert chain.billing_document is not None
    assert chain.billing_document.billing_id == "BILL-3001"
    assert chain.billing_document.total_amount == 100.00

    # 3. Charging Events
    assert len(chain.charging_events) == 3
    assert {e.event_id for e in chain.charging_events} == {"EVT-001", "EVT-002", "EVT-003"}
    assert sum(e.amount for e in chain.charging_events) == 100.00

    # 4. Contract Account
    assert chain.contract_account is not None
    assert chain.contract_account.account_id == "CA-2001"
    assert chain.contract_account.currency == "USD"

    # 5. Customer
    assert chain.customer is not None
    assert chain.customer.customer_id == "CUST-1001"
    assert chain.customer.name == "Acme Telecom"


def test_missing_ids_return_not_found():
    """10: Missing IDs return the project's appropriate not-found behavior (None)."""
    assert business_data_tool.get_customer("NONEXISTENT") is None
    assert business_data_tool.get_contract_account("NONEXISTENT") is None
    assert business_data_tool.get_invoice("NONEXISTENT") is None
    assert business_data_tool.get_billing_document("NONEXISTENT") is None
    assert business_data_tool.get_invoice_chain("NONEXISTENT") is None


def test_no_natural_language_explanation_in_data_layer():
    """11: No natural-language explanation is generated by the data layer."""
    chain = business_data_tool.get_invoice_chain("INV-1042")
    assert chain is not None

    dumped = chain.model_dump()
    # Ensure there are only structured keys
    expected_top_keys = {"invoice", "billing_document", "charging_events", "contract_account", "customer"}
    assert set(dumped.keys()) == expected_top_keys

    # Ensure no reasoning, prompt, or explanation text exists in the data
    disallowed_substrings = [
        "discrepancy",
        "investigation",
        "because",
        "due to",
        "difference of",
        "explanation",
        "reason",
    ]
    dumped_str = str(dumped).lower()
    for phrase in disallowed_substrings:
        assert phrase not in dumped_str, f"Data layer leaked natural language phrase: {phrase}"


def test_api_endpoints_for_business_data(auth_headers):
    """12: Read-only authenticated API endpoints function correctly."""
    # Successful invoice fetch
    resp = client.get("/api/business-data/invoices/INV-1042", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["invoice_id"] == "INV-1042"
    assert data["total_amount"] == 125.00

    # Successful chain fetch
    resp_chain = client.get("/api/business-data/invoices/INV-1042/chain", headers=auth_headers)
    assert resp_chain.status_code == 200
    chain_data = resp_chain.json()
    assert chain_data["invoice"]["invoice_id"] == "INV-1042"
    assert chain_data["billing_document"]["billing_id"] == "BILL-3001"
    assert len(chain_data["charging_events"]) == 3
    assert chain_data["customer"]["name"] == "Acme Telecom"

    # 404 for missing invoice
    resp_404 = client.get("/api/business-data/invoices/INV-9999", headers=auth_headers)
    assert resp_404.status_code == 404

    # 401 for unauthenticated request
    resp_unauth = client.get("/api/business-data/invoices/INV-1042")
    assert resp_unauth.status_code == 401
