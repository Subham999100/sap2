import re
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional
import pymupdf

class DocumentService:
    SAP_DOC_METADATA = {
        "pdfdownload.pdf": {
            "title": "SAP S/4HANA Subscription Order Management (SOM) Guide",
            "module": "SOM / BRIM",
            "product": "SAP S/4HANA BRIM"
        },
        "SAP CC.pdf": {
            "title": "SAP Convergent Charging (CC) Implementation Guide",
            "module": "CC / BRIM",
            "product": "SAP Convergent Charging"
        },
        "SAP CI1.pdf": {
            "title": "SAP Cloud Integration & Convergent Invoicing Guide (Part 1)",
            "module": "CI / BRIM",
            "product": "SAP Convergent Invoicing"
        },
        "SAP CI2.pdf": {
            "title": "SAP Cloud Integration & Convergent Invoicing Guide (Part 2)",
            "module": "CI / BRIM",
            "product": "SAP Convergent Invoicing"
        },
        "SAP CI3.pdf": {
            "title": "SAP Cloud Integration & Convergent Invoicing Guide (Part 3)",
            "module": "CI / BRIM",
            "product": "SAP Convergent Invoicing"
        },
        "SAP CI4.pdf": {
            "title": "SAP Cloud Integration & Convergent Invoicing Guide (Part 4)",
            "module": "CI / BRIM",
            "product": "SAP Convergent Invoicing"
        },
        "SAP FI-CA.pdf": {
            "title": "SAP Contract Accounts Receivable and Payable (FI-CA) Guide",
            "module": "FI-CA / BRIM",
            "product": "SAP FI-CA"
        },
        "2020_S4H_BRIM_SOM_Device_as_a_Service.pdf": {
            "title": "SAP S/4HANA BRIM SOM - Device as a Service (DaaS) Guide",
            "module": "SOM / BRIM",
            "product": "SAP S/4HANA BRIM"
        },
        "Authorizations-in-S4-HANA-and-Fiori.pdf": {
            "title": "Authorizations in SAP S/4HANA and SAP Fiori",
            "module": "Security & Authorizations",
            "product": "SAP S/4HANA / Fiori"
        },
        "Billing_and_Revenue_Innovation_Management.pdf": {
            "title": "SAP Billing and Revenue Innovation Management (BRIM) Overview",
            "module": "BRIM Overview",
            "product": "SAP BRIM"
        },
        "BR234_EN_SOM 2.pdf": {
            "title": "SAP BR234: Subscription Order Management in SAP S/4HANA BRIM",
            "module": "SOM / BRIM",
            "product": "SAP S/4HANA BRIM"
        },
        "BRIM Book -2.pdf": {
            "title": "SAP BRIM Comprehensive Architecture and Implementation Guide",
            "module": "BRIM Architecture",
            "product": "SAP BRIM"
        },
        "BRIM Q Bank.pdf": {
            "title": "SAP BRIM Certification and Technical Questions Bank",
            "module": "BRIM Q&A / Certification",
            "product": "SAP BRIM"
        },
        "BRIM SOM 1809 FSP01 E2E Overview Deep Dive HandOut.pdf": {
            "title": "SAP BRIM SOM 1809 FPS01 End-to-End Overview and Deep Dive",
            "module": "SOM / BRIM",
            "product": "SAP S/4HANA BRIM"
        },
        "BRIM_AC240_EN_Col16.pdf": {
            "title": "SAP AC240: Contract Accounts Receivable and Payable (FI-CA) Deep Dive",
            "module": "FI-CA / BRIM",
            "product": "SAP FI-CA"
        },
        "Budget billing.pdf": {
            "title": "SAP FI-CA / Utilities Budget Billing Process and Plan Configuration",
            "module": "FI-CA / Budget Billing",
            "product": "SAP FI-CA"
        },
        "E_Book_ABAP_RESTful_Programming_Model_2020_Rel_NW_wwwERPExamsCom.pdf": {
            "title": "ABAP RESTful Application Programming Model (RAP) Guide",
            "module": "ABAP RAP / Extension",
            "product": "SAP S/4HANA ABAP RAP"
        },
        "mg.pdf": {
            "title": "SAP BRIM Mediation and Integration Architecture Guide",
            "module": "Mediation / BRIM",
            "product": "SAP Convergent Mediation"
        },
        "RAR Contract Reversal Process.pdf": {
            "title": "SAP Revenue Accounting and Reporting (RAR) Contract Reversal Process",
            "module": "RAR / Revenue Accounting",
            "product": "SAP RAR"
        },
        "SAP BRIM + RAR Implementation Roadmap -2.pdf": {
            "title": "SAP BRIM and SAP RAR End-to-End Implementation Roadmap",
            "module": "BRIM + RAR Integration",
            "product": "SAP BRIM / RAR"
        },
        "SAP BRIM Press Book 2nd Edition - Expertsoft.pdf": {
            "title": "SAP Press: SAP Billing and Revenue Innovation Management (2nd Edition)",
            "module": "BRIM Architecture",
            "product": "SAP BRIM"
        },
        "SAP Revenue Recognition (RAR) Processing Flow, Exemptions, Reversals & System Integration.pdf": {
            "title": "SAP RAR Processing Flow, Exemptions, Reversals and System Integration",
            "module": "RAR / Revenue Accounting",
            "product": "SAP RAR"
        },
        "SAP-PRESS-Catalog-Shop.pdf": {
            "title": "SAP PRESS Reference Catalog and Publication Index",
            "module": "SAP Reference",
            "product": "SAP PRESS"
        },
        "T Codes of BRIM .pdf": {
            "title": "SAP BRIM Transaction Codes (T-Codes) Reference Guide",
            "module": "BRIM T-Codes",
            "product": "SAP BRIM"
        },
        "100  Ans.pdf": {
            "title": "SAP BRIM Architect 100 Question Answer Book",
            "module": "BRIM Architecture / Q&A",
            "product": "SAP BRIM"
        },
        "100 4 ans.pdf": {
            "title": "SAP BRIM Expert Interview Answers (100 Questions)",
            "module": "BRIM Q&A / Interview",
            "product": "SAP BRIM"
        },
        "100 Ans 2.pdf": {
            "title": "SAP BRIM Architect 100 Question Answer Book (Vol 2)",
            "module": "BRIM Architecture / Q&A",
            "product": "SAP BRIM"
        },
        "ah.pdf": {
            "title": "SAP Convergent Charging 4.1 Administration Guide & Application Help",
            "module": "CC / Administration",
            "product": "SAP Convergent Charging"
        },
        "BR234_EN_Col22_CO_A4.pdf": {
            "title": "SAP S/4HANA Service: Subscription Order Management (BR234 Col22)",
            "module": "SOM / BRIM",
            "product": "SAP S/4HANA BRIM"
        },
        "BR245_EN_Col23_CO_A4.pdf": {
            "title": "SAP Convergent Invoicing Course Outline & Guide (BR245 Col23)",
            "module": "CI / BRIM",
            "product": "SAP Convergent Invoicing"
        },
        "cg.pdf": {
            "title": "SAP Convergent Charging 5.0 Configuration Guide",
            "module": "CC / Configuration",
            "product": "SAP Convergent Charging"
        },
        "pdf 1.pdf": {
            "title": "SAP Convergent Charging 5.0 Application Help",
            "module": "CC / Application Help",
            "product": "SAP Convergent Charging"
        },
        "SAP BRIM AI Architect (1).pdf": {
            "title": "SAP BRIM AI Architect Reference Answers (Copy 1)",
            "module": "BRIM Architecture / Reference",
            "product": "SAP BRIM"
        },
        "SAP BRIM AI Architect.pdf": {
            "title": "SAP BRIM AI Architect Reference Answers",
            "module": "BRIM Architecture / Reference",
            "product": "SAP BRIM"
        },
        "SAP_BRIM_Architect_Knowledge.md": {
            "title": "SAP BRIM Architect-Level Knowledge Supplement",
            "module": "BRIM Architecture & Diagnostic",
            "product": "SAP BRIM"
        }
    }

    @staticmethod
    def get_document_info(filename: str) -> Dict[str, str]:
        if filename in DocumentService.SAP_DOC_METADATA:
            return DocumentService.SAP_DOC_METADATA[filename]
        clean_title = filename.replace(".pdf", "").replace(".md", "").replace("_", " ")
        return {
            "title": clean_title,
            "module": "General SAP",
            "product": "SAP ERP"
        }

    @staticmethod
    def compute_file_hash(file_path: Path) -> str:
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest()

    @staticmethod
    def clean_text(text: str) -> str:
        if not text:
            return ""
        # Remove NUL (0x00) bytes which are invalid in PostgreSQL text fields
        text = text.replace("\x00", "")
        # Filter private-use unicode glyphs and replacement chars
        text = re.sub(r"[\ue000-\uf8ff\ufffd]", " ", text)
        # Remove repetitive SAP Help Portal headers & footers
        text = re.sub(r"Warning\s+This document has been generated from SAP Help Portal[^\n]*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"Original content:\s*https?://help\.sap\.com[^\n]*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"Generated on:\s*\d{4}-\d{2}-\d{2}[^\n]*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"(?i)Page\s+\d+(\s+of\s+\d+)?", "", text)
        text = re.sub(r"(?i)SAP S/4HANA\s*\|\s*\d{4}[^\n]*", "", text)
        # Normalize newlines and excess whitespace
        text = re.sub(r"\r\n|\r", "\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def extract_pdf_pages(file_path: Path) -> List[Dict[str, Any]]:
        """
        Extracts text and section structure from each page using PyMuPDF.
        Tracks active section hierarchy across pages.
        """
        pages_data = []
        doc = pymupdf.open(str(file_path))
        active_section = "Overview"

        try:
            for page_idx in range(len(doc)):
                page = doc[page_idx]
                page_number = page_idx + 1
                raw_text = page.get_text("text") or ""
                cleaned_text = DocumentService.clean_text(raw_text)

                # Detect potential section headings
                headings = []
                lines = [l.strip() for l in cleaned_text.split("\n") if l.strip()]
                for line in lines:
                    # Match numbered sections (e.g. "1.1 Flexible Billing", "Chapter 2", "Section 3.4")
                    # or uppercase short topic titles
                    if (
                        re.match(r"^(?:(?:Section|Chapter)\s+\d+(?:\.\d+)*|\d+(?:\.\d+)+\s+[A-Z0-9].*|\d+\s+[A-Z][A-Za-z0-9\s\-_/:]{3,40})$", line)
                        and len(line) < 70
                    ):
                        headings.append(line)
                    elif len(line) < 45 and line.isupper() and not line.startswith("PAGE"):
                        headings.append(line.title())

                if headings:
                    active_section = headings[0]

                pages_data.append({
                    "page_number": page_number,
                    "text": cleaned_text,
                    "headings": headings,
                    "active_section": active_section
                })
        finally:
            doc.close()

        return pages_data

    @staticmethod
    def extract_markdown_sections(file_path: Path) -> List[Dict[str, Any]]:
        """
        Extracts structured sections from a Markdown file (.md).
        Parses headers (# and ##) into discrete pages/sections with active section tracking.
        """
        raw_text = file_path.read_text(encoding="utf-8")
        cleaned_text = DocumentService.clean_text(raw_text)
        lines = cleaned_text.splitlines()

        sections = []
        current_title = "Overview"
        current_lines = []
        page_idx = 1
        parent_section = ""

        for line in lines:
            h1_match = re.match(r"^#\s+(.+)$", line)
            h2_match = re.match(r"^##\s+(.+)$", line)

            if h1_match:
                if current_lines and any(l.strip() and not l.startswith("#") for l in current_lines):
                    content = "\n".join(current_lines).strip()
                    sections.append({
                        "page_number": page_idx,
                        "text": content,
                        "active_section": current_title,
                        "headings": [current_title]
                    })
                    page_idx += 1
                    current_lines = []
                parent_section = h1_match.group(1).strip()
                current_title = parent_section
                current_lines.append(line)
            elif h2_match:
                if current_lines and any(l.strip() and not l.startswith("#") for l in current_lines):
                    content = "\n".join(current_lines).strip()
                    sections.append({
                        "page_number": page_idx,
                        "text": content,
                        "active_section": current_title,
                        "headings": [current_title]
                    })
                    page_idx += 1
                    current_lines = []
                sub_title = h2_match.group(1).strip()
                current_title = f"{parent_section} - {sub_title}" if parent_section else sub_title
                current_lines.append(line)
            else:
                current_lines.append(line)

        if current_lines:
            content = "\n".join(current_lines).strip()
            if content:
                sections.append({
                    "page_number": page_idx,
                    "text": content,
                    "active_section": current_title,
                    "headings": [current_title]
                })

        return sections

    @staticmethod
    def extract_document_pages(file_path: Path) -> List[Dict[str, Any]]:
        """Unified document page extractor supporting both PDF and Markdown."""
        if file_path.suffix.lower() == ".md":
            return DocumentService.extract_markdown_sections(file_path)
        return DocumentService.extract_pdf_pages(file_path)

    @staticmethod
    def chunk_page_text(
        text: str,
        page_number: int,
        document_name: str,
        active_section: str,
        target_chunk_size: int = 1200,
        chunk_overlap: int = 200
    ) -> List[Dict[str, Any]]:
        """
        Structure-aware chunker designed for SAP enterprise documentation:
        - Targets 1,000-1,400 chars.
        - Preserves numbered procedures (1. 2. Step X), tables, and notes.
        - Splits on logical paragraph/procedure breaks rather than cutting sentences.
        """
        if not text or len(text.strip()) < 30:
            return []

        cleaned = text.strip()
        doc_info = DocumentService.get_document_info(document_name)

        # Detect structural features
        has_procedure = bool(re.search(r"(?:Step\s+\d+|\bProcedure\b|\bPrerequisites\b|\n\s*\d+\.\s+[A-Z])", cleaned, re.IGNORECASE))
        has_table = bool(re.search(r"(?:\||\bTable\b|\bField\b.*\bDescription\b)", cleaned, re.IGNORECASE))

        # If page text is within standard chunk size range, keep as a single unified chunk
        single_chunk_margin = min(50, int(target_chunk_size * 0.1))
        if len(cleaned) <= target_chunk_size + single_chunk_margin:
            return [{
                "chunk_index": 0,
                "page_number": page_number,
                "document_name": document_name,
                "section": active_section,
                "chunk_text": cleaned,
                "metadata": {
                    "document": document_name,
                    "title": doc_info["title"],
                    "module": doc_info["module"],
                    "product": doc_info["product"],
                    "page": page_number,
                    "section": active_section,
                    "char_length": len(cleaned),
                    "has_procedure": has_procedure,
                    "has_table": has_table
                }
            }]

        chunks = []
        start = 0
        text_length = len(cleaned)
        chunk_idx = 0
        min_boundary_offset = max(80, int(target_chunk_size * 0.4))

        while start < text_length:
            end = min(start + target_chunk_size, text_length)

            # If not at the end of the text, seek a clean logical boundary
            if end < text_length:
                # 1. Paragraph boundary
                p_break = cleaned.rfind("\n\n", start + min_boundary_offset, end)
                if p_break != -1:
                    end = p_break
                else:
                    # 2. Numbered step boundary (e.g. "\n1. ", "\n2. ")
                    step_match = None
                    for m in re.finditer(r"\n(?:\d+\.|\-|\*)\s+", cleaned[start + min_boundary_offset : end]):
                        step_match = start + min_boundary_offset + m.start()
                    if step_match:
                        end = step_match
                    else:
                        # 3. Sentence boundary (". ", ".\n")
                        sent_break = max(
                            cleaned.rfind(". ", start + min_boundary_offset, end),
                            cleaned.rfind(".\n", start + min_boundary_offset, end)
                        )
                        if sent_break != -1:
                            end = sent_break + 1
                        else:
                            # 4. Line break
                            line_break = cleaned.rfind("\n", start + min_boundary_offset, end)
                            if line_break != -1:
                                end = line_break

            chunk_content = cleaned[start:end].strip()
            if len(chunk_content) >= 30:
                chunks.append({
                    "chunk_index": chunk_idx,
                    "page_number": page_number,
                    "document_name": document_name,
                    "section": active_section,
                    "chunk_text": chunk_content,
                    "metadata": {
                        "document": document_name,
                        "title": doc_info["title"],
                        "module": doc_info["module"],
                        "product": doc_info["product"],
                        "page": page_number,
                        "section": active_section,
                        "char_length": len(chunk_content),
                        "has_procedure": has_procedure,
                        "has_table": has_table
                    }
                })
                chunk_idx += 1

            if end >= text_length:
                break
            start = max(end - chunk_overlap, start + 50)

        return chunks

document_service = DocumentService()

