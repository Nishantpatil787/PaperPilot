import pymupdf as fitz
import base64
import io
from pathlib import Path
from PIL import Image

class PDFEditService:
    @staticmethod
    def watermark_pdf(input_path, output_path, text="CONFIDENTIAL", opacity=0.3, font_size=40, rotation=0, color=(0.7, 0.7, 0.7), overlay=True):
        doc = fitz.open(str(input_path))
        for page in doc:
            rect = page.rect
            p = fitz.Point(rect.x0 + rect.width * 0.15, rect.y0 + rect.height * 0.5)
            rot_90 = (round(rotation / 90) * 90) % 360
            page.insert_text(
                p,
                text,
                fontsize=font_size,
                rotate=rot_90,
                color=color,
                overlay=overlay
            )
        doc.save(str(output_path))
        doc.close()
        return output_path

    @staticmethod
    def sign_pdf(input_path, output_path, signature_data, page_num=1, x=100, y=100, width=160, height=60):
        doc = fitz.open(str(input_path))
        target_idx = max(0, min(len(doc) - 1, int(page_num) - 1))
        page = doc[target_idx]

        # Respect page origin (x0, y0) and clamp within page rect
        x0 = page.rect.x0 + float(x)
        y0 = page.rect.y0 + float(y)
        x1 = min(page.rect.x1, x0 + max(10.0, float(width)))
        y1 = min(page.rect.y1, y0 + max(10.0, float(height)))
        rect = fitz.Rect(x0, y0, x1, y1)

        # Check if signature_data is base64 data URI or raw file path
        if isinstance(signature_data, str) and signature_data.startswith("data:image"):
            header, encoded = signature_data.split(",", 1)
            img_bytes = base64.b64decode(encoded)
            page.insert_image(rect, stream=img_bytes, overlay=True)
        else:
            page.insert_image(rect, filename=str(signature_data), overlay=True)

        doc.save(str(output_path))
        doc.close()
        return output_path

    @staticmethod
    def edit_pdf(input_path, output_path, text_items=None, shapes=None):
        doc = fitz.open(str(input_path))
        text_items = text_items or []
        shapes = shapes or []

        for item in text_items:
            pg_idx = max(0, min(len(doc) - 1, int(item.get("page", 1)) - 1))
            page = doc[pg_idx]
            p = fitz.Point(page.rect.x0 + float(item.get("x", 50)), page.rect.y0 + float(item.get("y", 50)))
            page.insert_text(
                p,
                str(item.get("text", "")),
                fontsize=float(item.get("font_size", 12)),
                color=(0.09, 0.23, 0.31) # Navy text
            )

        for shape in shapes:
            pg_idx = max(0, min(len(doc) - 1, int(shape.get("page", 1)) - 1))
            page = doc[pg_idx]
            x0 = page.rect.x0 + float(shape.get("x", 50))
            y0 = page.rect.y0 + float(shape.get("y", 50))
            rect = fitz.Rect(
                x0,
                y0,
                min(page.rect.x1, x0 + float(shape.get("w", 100))),
                min(page.rect.y1, y0 + float(shape.get("h", 50)))
            )
            shape_type = shape.get("type", "rect")
            if shape_type == "highlight":
                page.add_highlight_annot(rect)
            else:
                page.draw_rect(rect, color=(0.09, 0.23, 0.31), width=1.5)

        doc.save(str(output_path))
        doc.close()
        return output_path

    @staticmethod
    def detect_form_fields(input_path):
        doc = fitz.open(str(input_path))
        fields = []
        for pg_idx, page in enumerate(doc):
            for widget in page.widgets():
                fields.append({
                    "page": pg_idx + 1,
                    "name": widget.field_name,
                    "type": widget.field_type_string,
                    "value": widget.field_value or ""
                })
        doc.close()
        return fields

    @staticmethod
    def fill_form_fields(input_path, output_path, field_values):
        doc = fitz.open(str(input_path))
        updated_count = 0
        for page in doc:
            for widget in page.widgets():
                if widget.field_name in field_values:
                    widget.field_value = str(field_values[widget.field_name])
                    widget.update()
                    updated_count += 1
        doc.save(str(output_path))
        doc.close()
        return updated_count, output_path
