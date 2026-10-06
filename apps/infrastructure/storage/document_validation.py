"""Document parser adapters; caller owns upload policy and error presentation."""


def validate_pdf_document(data: bytes, *, max_pages: int | None = None) -> None:
    import fitz

    try:
        with fitz.open(stream=data, filetype="pdf") as document:
            if max_pages is not None and (document.needs_pass or document.page_count > max_pages):
                raise ValueError("PDF reading limit")
            if document.page_count < 1:
                raise ValueError("PDF has no pages")
    except Exception as error:
        raise ValueError("invalid PDF document") from error
