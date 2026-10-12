from fastapi import APIRouter, Depends, HTTPException, status

from backend.app.models.user import User
from backend.app.schemas.business_data import Invoice, InvoiceChain
from backend.app.security.auth import get_current_user
from backend.app.services.business_data_tool import business_data_tool

router = APIRouter(prefix="/business-data", tags=["Business Data"])


@router.get("/invoices/{invoice_id}", response_model=Invoice)
def get_invoice(
    invoice_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Retrieve an individual invoice record by ID.
    Read-only, authenticated endpoint.
    """
    invoice = business_data_tool.get_invoice(invoice_id)
    if not invoice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invoice '{invoice_id}' not found.",
        )
    return invoice


@router.get("/invoices/{invoice_id}/chain", response_model=InvoiceChain)
def get_invoice_chain(
    invoice_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Retrieve the full relational chain for an invoice:
    Invoice -> Billing Document -> Charging Events -> Contract Account -> Customer.
    Read-only, authenticated endpoint returning structured data only.
    """
    chain = business_data_tool.get_invoice_chain(invoice_id)
    if not chain:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invoice '{invoice_id}' not found.",
        )
    return chain
