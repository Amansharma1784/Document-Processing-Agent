
from decimal import Decimal, InvalidOperation

from app.schemas.invoice_schema import InvoiceData
from app.schemas.invoice_validation import (
    InvoiceValidationResult,
    ValidationIssue,
)

TOLERANCE = Decimal("0.01")


def validate_invoice_data(
    invoice: InvoiceData,
) -> InvoiceValidationResult:
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []

    def add_error(field: str, code: str, message: str):
        errors.append(
            ValidationIssue(
                field=field,
                code=code,
                message=message,
                severity="error",
            )
        )

    def add_warning(field: str, code: str, message: str):
        warnings.append(
            ValidationIssue(
                field=field,
                code=code,
                message=message,
                severity="warning",
            )
        )

    # 1. Required fields
    required_fields = {
        "invoice_number": invoice.invoice_number,
        "vendor_name": invoice.vendor_name,
        "invoice_date": invoice.invoice_date,
        "currency": invoice.currency,
        "total": invoice.total,
    }

    for field, value in required_fields.items():
        if value is None or (
            isinstance(value, str) and not value.strip()
        ):
            add_error(
                field,
                "required_field_missing",
                f"{field} is required but was not extracted.",
            )

    # 2. Validate dates
    if (
        invoice.invoice_date is not None
        and invoice.due_date is not None
        and invoice.due_date < invoice.invoice_date
    ):
        add_error(
            "due_date",
            "due_date_before_invoice_date",
            "Due date is earlier than invoice date.",
        )

    # 3. Validate currency format
    if invoice.currency is not None:
        currency = invoice.currency.strip()

        if (
            len(currency) != 3
            or not currency.isascii()
            or not currency.isalpha()
            or currency != currency.upper()
        ):
            add_error(
                "currency",
                "invalid_currency_format",
                "Currency must be a three-letter uppercase code.",
            )

    # 4. Validate monetary values are non-negative
    monetary_fields = {
        "subtotal": invoice.subtotal,
        "taxes": invoice.taxes,
        "discounts": invoice.discounts,
        "total": invoice.total,
    }

    for field, value in monetary_fields.items():
        if value is not None and value < 0:
            add_error(
                field,
                "negative_amount",
                f"{field} cannot be negative.",
            )

    # 5. Validate line-item calculations
    calculated_line_amounts: list[Decimal] = []

    for index, item in enumerate(invoice.line_items):
        prefix = f"line_items[{index}]"

        if item.quantity is not None and item.quantity < 0:
            add_error(
                f"{prefix}.quantity",
                "negative_quantity",
                "Quantity cannot be negative.",
            )

        if item.unit_price is not None and item.unit_price < 0:
            add_error(
                f"{prefix}.unit_price",
                "negative_unit_price",
                "Unit price cannot be negative.",
            )

        if item.amount is not None and item.amount < 0:
            add_error(
                f"{prefix}.amount",
                "negative_line_amount",
                "Line-item amount cannot be negative.",
            )

        if (
            item.quantity is not None
            and item.unit_price is not None
            and item.amount is not None
        ):
            expected = item.quantity * item.unit_price

            if abs(expected - item.amount) > TOLERANCE:
                add_error(
                    f"{prefix}.amount",
                    "line_item_calculation_mismatch",
                    (
                        f"Quantity × unit price equals {expected}, "
                        f"but the extracted amount is {item.amount}."
                    ),
                )

        if item.amount is not None:
            calculated_line_amounts.append(item.amount)

        elif (
            item.quantity is None
            or item.unit_price is None
        ):
            add_warning(
                f"{prefix}",
                "incomplete_line_item",
                "Line item is missing quantity, unit price, or amount.",
            )

    # 6. Compare line-item amounts with subtotal
    if (
        invoice.subtotal is not None
        and invoice.line_items
        and len(calculated_line_amounts) == len(invoice.line_items)
    ):
        line_sum = sum(calculated_line_amounts, Decimal("0"))

        if abs(line_sum - invoice.subtotal) > TOLERANCE:
            add_error(
                "subtotal",
                "subtotal_mismatch",
                (
                    f"Line items sum to {line_sum}, "
                    f"but subtotal is {invoice.subtotal}."
                ),
            )

    elif invoice.line_items and invoice.subtotal is None:
        add_warning(
            "subtotal",
            "subtotal_not_available",
            "Cannot compare line items because subtotal is missing.",
        )

    # 7. Check total only when all required calculation inputs exist
    if (
        invoice.subtotal is not None
        and invoice.taxes is not None
        and invoice.discounts is not None
        and invoice.total is not None
    ):
        expected_total = (
            invoice.subtotal + invoice.taxes - invoice.discounts
        )

        if abs(expected_total - invoice.total) > TOLERANCE:
            add_error(
                "total",
                "invoice_total_mismatch",
                (
                    f"Expected total {expected_total} from "
                    "subtotal + taxes - discounts, "
                    f"but extracted total is {invoice.total}."
                ),
            )
    elif invoice.total is not None:
        add_warning(
            "total",
            "cannot_verify_total",
            (
                "Total could not be fully recalculated because "
                "subtotal, taxes, or discounts are missing."
            ),
        )

    return InvoiceValidationResult(
        is_valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )
