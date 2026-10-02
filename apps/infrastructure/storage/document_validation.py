"""Document parser adapters; caller owns upload policy and error presentation."""


def validate_pdf_document(data: bytes) -> None:
    import fitz

    try:
        with fitz.open(stream=data, filetype="pdf") as document:
            if document.page_count < 1:
                raise ValueError("PDF has no pages")
    except Exception as error:
        raise ValueError("invalid PDF document") from error
