
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class InvoiceLineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None


class InvoiceData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_number: str | None = None
    vendor_name: str | None = None

    invoice_date: date | None = None
    due_date: date | None = None
    currency: str | None = None

    subtotal: Decimal | None = None
    taxes: Decimal | None = None
    discounts: Decimal | None = None
    total: Decimal | None = None

    line_items: list[InvoiceLineItem] = Field(
        default_factory=list
    )
