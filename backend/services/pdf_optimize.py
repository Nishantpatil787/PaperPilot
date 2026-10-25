import pymupdf as fitz
import os
import io
import shutil
import difflib
from pathlib import Path
from PIL import Image

class PDFOptimizeService:
    @staticmethod
    def compress_pdf(input_path, output_path, level="medium"):
        orig_size = os.path.getsize(str(input_path))
        doc = fitz.open(str(input_path))

        level = (level or "medium").lower()
        if level == "extreme":
            # Downsample high-res embedded raster images
            try:
                for page in doc:
                    img_list = page.get_images(full=True)
                    for img_info in img_list:
                        xref = img_info[0]
                        base_image = doc.extract_image(xref)
                        if base_image and "image" in base_image:
                            raw_bytes = base_image["image"]
                            try:
                                pil_img = Image.open(io.BytesIO(raw_bytes))
                                # Downsample if width or height exceeds 1024
                                if pil_img.width > 1024 or pil_img.height > 1024:
                                    pil_img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                                buf = io.BytesIO()
                                pil_img.convert("RGB").save(buf, format="JPEG", quality=50, optimize=True)
                                doc.update_stream(xref, buf.getvalue())
                            except Exception:
                                pass
            except Exception:
                pass
            doc.save(str(output_path), garbage=4, deflate=True, clean=True, deflate_images=True, deflate_fonts=True)
        elif level == "low":
            # Fast deflation without heavy stream rewriting
            doc.save(str(output_path), garbage=2, deflate=True)
        else:
            # Medium / balanced optimization
            doc.save(str(output_path), garbage=3, deflate=True, clean=True, deflate_images=True)

        doc.close()

        new_size = os.path.getsize(str(output_path))
        # Guard: never deliver an output larger than the original input
        if new_size > orig_size:
            shutil.copy2(str(input_path), str(output_path))
            new_size = orig_size
            reduction = 0.0
        else:
            reduction = max(0.0, round(((orig_size - new_size) / orig_size) * 100, 1))

        return {
            "output_path": str(output_path),
            "original_size": orig_size,
            "compressed_size": new_size,
            "reduction_percent": reduction,
            "level": level
        }

    @staticmethod
    def repair_pdf(input_path, output_path):
        try:
            doc = fitz.open(str(input_path))
            doc.save(str(output_path), garbage=4, clean=True, deflate=True)
            page_count = len(doc)
            doc.close()
            return True, output_path, f"PDF successfully repaired ({page_count} pages recovered)."
        except Exception as e:
            return False, None, f"Unable to salvage document: {str(e)}"

    @staticmethod
    def compare_pdfs(pdf1_path, pdf2_path):
        doc1 = fitz.open(str(pdf1_path))
        doc2 = fitz.open(str(pdf2_path))

        text1 = [page.get_text() for page in doc1]
        text2 = [page.get_text() for page in doc2]

        full_text1 = "\n".join(text1)
        full_text2 = "\n".join(text2)

        matcher = difflib.SequenceMatcher(None, full_text1, full_text2)
        similarity = round(matcher.ratio() * 100, 1)

        diff = list(difflib.unified_diff(
            full_text1.splitlines(),
            full_text2.splitlines(),
            fromfile="Document 1",
            tofile="Document 2",
            lineterm=""
        ))

        doc1.close()
        doc2.close()

        return {
            "similarity_percent": similarity,
            "diff_lines": diff[:100],  # Return first 100 diff lines
            "doc1_pages": len(text1),
            "doc2_pages": len(text2)
        }
