from typing import Literal, Union
from langchain_groq import ChatGroq

from app.schemas.invoice_schema import InvoiceData
from app.schemas.resume_schema import ResumeData
from app.database.core import settings


# 1. Base LLM Configuration
llm = ChatGroq(
    model="openai/gpt-oss-120b",
    api_key=settings.GROQ_API_KEY,
    temperature=0,
    timeout=30,
    max_retries=2,
)

# 2. Prompts Setup
INVOICE_PROMPT = """
Extract invoice information from the document text below.

Rules:
- Use only information explicitly supported by the text.
- Do not guess missing values.
- Return null for missing scalar fields.
- Return an empty list when no line items can be identified.
- Preserve monetary amounts as represented in the document.
- Do not calculate missing amounts.
- Use ISO date interpretation for dates when unambiguous.
- Currency should be an ISO 4217 code when identifiable,
  such as USD, EUR, or INR.
- Distinguish subtotal, taxes, discounts, and final total.

DOCUMENT TEXT:
<document>
{extracted_text}
</document>
"""

RESUME_PROMPT = """
Extract professional resume and candidate information from the document text below.

Rules:
- Use only facts explicitly stated in the document text.
- Do not assume skills, experience, or qualifications that are not explicitly mentioned.
- Return null for missing scalar fields (e.g., email, phone, location).
- Return an empty list for missing array fields (e.g., skills, experience, education, certifications).
- Preserve exact technical terms, tool names, and job titles as stated.
- Format dates systematically (e.g., YYYY-MM or ISO style) when unambiguous.
- Keep work experience entries in chronological or listed order as presented.

DOCUMENT TEXT:
<document>
{extracted_text}
</document>
"""


# 3. Dynamic Extraction Function
def extract_document_fields(
    extracted_text: str,
    doc_type: Literal["invoice", "resume"]
) -> Union[InvoiceData, ResumeData]:
    """
    Extracts structured fields from raw text based on the document type.
    
    :param extracted_text: Raw string text from document OCR or parser.
    :param doc_type: Type of document ("invoice" or "resume").
    :return: Validated InvoiceData or ResumeData Pydantic object.
    """
    if not extracted_text.strip():
        raise ValueError("Document text is empty.")

    if doc_type == "invoice":
        prompt = INVOICE_PROMPT.format(extracted_text=extracted_text)
        extractor = llm.with_structured_output(InvoiceData, method="json_schema")
    elif doc_type == "resume":
        prompt = RESUME_PROMPT.format(extracted_text=extracted_text)
        extractor = llm.with_structured_output(ResumeData, method="json_schema")
    else:
        raise ValueError(f"Unsupported document type: '{doc_type}'. Expected 'invoice' or 'resume'.")

    return extractor.invoke(prompt)