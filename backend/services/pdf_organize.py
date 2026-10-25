import pymupdf as fitz  # PyMuPDF
from pathlib import Path

class PDFOrganizeService:
    @staticmethod
    def merge_pdfs(input_paths, output_path):
        result_doc = fitz.open()
        for path in input_paths:
            src_doc = fitz.open(str(path))
            result_doc.insert_pdf(src_doc)
            src_doc.close()
        result_doc.save(str(output_path))
        result_doc.close()
        return output_path

    @staticmethod
    def split_pdf(input_path, output_dir, split_mode="all", ranges=None):
        doc = fitz.open(str(input_path))
        generated_files = []
        total_pages = len(doc)

        if split_mode == "all":
            for page_num in range(total_pages):
                new_doc = fitz.open()
                new_doc.insert_pdf(doc, from_page=page_num, to_page=page_num)
                out_path = Path(output_dir) / f"page_{page_num + 1}.pdf"
                new_doc.save(str(out_path))
                new_doc.close()
                generated_files.append(str(out_path))
        elif split_mode == "ranges" and ranges:
            range_list = [r.strip() for r in ranges.split(",") if r.strip()]
            for idx, r in enumerate(range_list):
                if "-" in r:
                    parts = r.split("-")
                    start = int(parts[0])
                    end = int(parts[1])
                    start_idx = max(0, start - 1)
                    end_idx = min(total_pages - 1, end - 1)
                else:
                    pg = int(r)
                    start_idx = end_idx = max(0, min(total_pages - 1, pg - 1))

                if start_idx <= end_idx:
                    new_doc = fitz.open()
                    new_doc.insert_pdf(doc, from_page=start_idx, to_page=end_idx)
                    out_path = Path(output_dir) / f"split_range_{idx + 1}_{r.replace(' ', '')}.pdf"
                    new_doc.save(str(out_path))
                    new_doc.close()
                    generated_files.append(str(out_path))

        doc.close()
        return generated_files

    @staticmethod
    def rotate_pdf(input_path, output_path, rotation=90, pages="all"):
        doc = fitz.open(str(input_path))
        total_pages = len(doc)
        target_pages = range(total_pages) if pages == "all" else [int(p.strip()) - 1 for p in pages.split(",") if p.strip()]

        for p_idx in target_pages:
            if 0 <= p_idx < total_pages:
                page = doc[p_idx]
                page.set_rotation((page.rotation + int(rotation)) % 360)

        doc.save(str(output_path))
        doc.close()
        return output_path

    @staticmethod
    def organize_pdf(input_path, output_path, page_order, rotations=None):
        doc = fitz.open(str(input_path))
        new_doc = fitz.open()
        rotations = rotations or {}
        total_pages = len(doc)

        for new_idx, orig_page_num in enumerate(page_order):
            if 0 <= orig_page_num < total_pages:
                new_doc.insert_pdf(doc, from_page=orig_page_num, to_page=orig_page_num)
                if str(orig_page_num) in rotations:
                    rot = rotations[str(orig_page_num)]
                    new_doc[-1].set_rotation(int(rot) % 360)

        new_doc.save(str(output_path))
        new_doc.close()
        doc.close()
        return output_path

    @staticmethod
    def add_page_numbers(input_path, output_path, position="bottom-right", start_num=1, font_size=10, format_pattern="{n}"):
        doc = fitz.open(str(input_path))
        total = len(doc)
        for idx, page in enumerate(doc):
            curr_num = start_num + idx
            text = format_pattern.replace("{n}", str(curr_num)).replace("{total}", str(total))
            rect = page.rect

            if position == "bottom-right":
                p = fitz.Point(rect.width - 60, rect.height - 30)
            elif position == "bottom-center":
                p = fitz.Point(rect.width / 2 - 15, rect.height - 30)
            elif position == "bottom-left":
                p = fitz.Point(40, rect.height - 30)
            elif position == "top-center":
                p = fitz.Point(rect.width / 2 - 15, 40)
            elif position == "top-right":
                p = fitz.Point(rect.width - 60, 40)
            else:
                p = fitz.Point(rect.width - 60, rect.height - 30)

            page.insert_text(p, text, fontsize=font_size, color=(0.2, 0.2, 0.2))

        doc.save(str(output_path))
        doc.close()
        return output_path

    @staticmethod
    def crop_pdf(input_path, output_path, top_pct=5, bottom_pct=5, left_pct=5, right_pct=5):
        doc = fitz.open(str(input_path))
        for page in doc:
            rect = page.rect
            crop_rect = fitz.Rect(
                rect.x0 + (rect.width * (left_pct / 100.0)),
                rect.y0 + (rect.height * (top_pct / 100.0)),
                rect.x1 - (rect.width * (right_pct / 100.0)),
                rect.y1 - (rect.height * (bottom_pct / 100.0))
            )
            page.set_cropbox(crop_rect)

        doc.save(str(output_path))
        doc.close()
        return output_path
