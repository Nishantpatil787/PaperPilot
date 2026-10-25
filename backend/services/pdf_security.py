import pymupdf as fitz

class PDFSecurityService:
    @staticmethod
    def protect_pdf(input_path, output_path, user_password, owner_password=None, allow_print=True, allow_copy=True):
        doc = fitz.open(str(input_path))
        owner_pwd = owner_password or user_password

        perm = 0
        if allow_print:
            perm |= fitz.PDF_PERM_PRINT
        if allow_copy:
            perm |= fitz.PDF_PERM_COPY

        doc.save(
            str(output_path),
            encryption=fitz.PDF_ENCRYPT_AES_256,
            user_pw=user_password,
            owner_pw=owner_pwd,
            permissions=perm
        )
        doc.close()
        return output_path

    @staticmethod
    def unlock_pdf(input_path, output_path, password):
        doc = fitz.open(str(input_path))
        if doc.is_encrypted:
            authenticated = doc.authenticate(password)
            if not authenticated:
                doc.close()
                raise ValueError("Incorrect password provided for encrypted PDF.")
        doc.save(str(output_path))
        doc.close()
        return output_path

    @staticmethod
    def redact_pdf(input_path, output_path, search_terms):
        doc = fitz.open(str(input_path))
        total_redactions = 0

        for page in doc:
            for term in search_terms:
                if not term.strip():
                    continue
                rects = page.search_for(term.strip())
                for r in rects:
                    page.add_redact_annot(r, fill=(0, 0, 0))
                    total_redactions += 1
            page.apply_redactions()

        doc.save(str(output_path))
        doc.close()
        return total_redactions, output_path
