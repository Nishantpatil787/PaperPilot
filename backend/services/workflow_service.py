from utils.file_manager import register_job
import os
import uuid
import json
import shutil
from pathlib import Path
from database import get_db
from services.pdf_optimize import PDFOptimizeService
from services.pdf_organize import PDFOrganizeService
from services.pdf_edit import PDFEditService
from services.pdf_security import PDFSecurityService
from services.pdf_convert import PDFConvertService
from services.pdf_intelligence import PDFIntelligenceService

STORAGE_DIR = Path(__file__).resolve().parent.parent / "storage"
WORKFLOW_RUNS_DIR = STORAGE_DIR / "workflow_runs"
WORKFLOW_RUNS_DIR.mkdir(parents=True, exist_ok=True)

class WorkflowService:
    SUPPORTED_STEPS = {
        "compress": {"name": "Compress PDF", "input_ext": [".pdf"], "output_ext": ".pdf"},
        "rotate": {"name": "Rotate PDF", "input_ext": [".pdf"], "output_ext": ".pdf"},
        "watermark": {"name": "Watermark PDF", "input_ext": [".pdf"], "output_ext": ".pdf"},
        "protect": {"name": "Protect PDF", "input_ext": [".pdf"], "output_ext": ".pdf"},
        "pdf_to_word": {"name": "Convert to Word (DOCX)", "input_ext": [".pdf"], "output_ext": ".docx"},
        "pdf_to_excel": {"name": "Convert to Excel (XLSX)", "input_ext": [".pdf"], "output_ext": ".xlsx"},
        "pdf_to_powerpoint": {"name": "Convert to PowerPoint (PPTX)", "input_ext": [".pdf"], "output_ext": ".pptx"},
        "pdf_to_text": {"name": "Extract Text (TXT)", "input_ext": [".pdf"], "output_ext": ".txt"},
        "pdf_to_jpg": {"name": "Convert to Images (ZIP)", "input_ext": [".pdf"], "output_ext": ".zip"},
        "summarize": {"name": "AI Summarize", "input_ext": [".pdf"], "output_ext": ".txt"}
    }

    @classmethod
    def get_supported_steps(cls):
        return cls.SUPPORTED_STEPS

    @classmethod
    def validate_workflow_steps(cls, steps):
        if not steps or not isinstance(steps, list):
            raise ValueError("Steps must be a non-empty list of actions.")
        
        current_ext = ".pdf"
        for idx, step in enumerate(steps):
            step_type = step.get("type")
            if step_type not in cls.SUPPORTED_STEPS:
                raise ValueError(f"Unknown step type '{step_type}' at position {idx + 1}.")
            
            meta = cls.SUPPORTED_STEPS[step_type]
            if current_ext not in meta["input_ext"]:
                raise ValueError(
                    f"Incompatible step '{meta['name']}' at position {idx + 1}. "
                    f"It requires {meta['input_ext']} input, but previous step produced '{current_ext}'."
                )
            current_ext = meta["output_ext"]
        return True

    @staticmethod
    def create_workflow(user_id, name, description="", steps=None, team_id=None):
        if not name or not name.strip():
            raise ValueError("Workflow name is required.")
        steps = steps or []
        WorkflowService.validate_workflow_steps(steps)

        wf_id = f"wf_{uuid.uuid4().hex[:12]}"
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO workflows (id, user_id, team_id, name, description, steps_json)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (wf_id, user_id, team_id, name.strip(), description.strip(), json.dumps(steps)))
        conn.commit()
        conn.close()

        return WorkflowService.get_workflow(wf_id, user_id)

    @staticmethod
    def list_workflows(user_id, team_id=None):
        conn = get_db()
        cursor = conn.cursor()
        if team_id:
            cursor.execute("""
                SELECT id, user_id, team_id, name, description, steps_json, created_at, updated_at
                FROM workflows WHERE team_id = ? OR user_id = ?
                ORDER BY updated_at DESC
            """, (team_id, user_id))
        else:
            cursor.execute("""
                SELECT id, user_id, team_id, name, description, steps_json, created_at, updated_at
                FROM workflows WHERE user_id = ?
                ORDER BY updated_at DESC
            """, (user_id,))
        rows = cursor.fetchall()
        conn.close()

        workflows = []
        for r in rows:
            workflows.append({
                "id": r["id"],
                "user_id": r["user_id"],
                "team_id": r["team_id"],
                "name": r["name"],
                "description": r["description"],
                "steps": json.loads(r["steps_json"]),
                "created_at": str(r["created_at"]),
                "updated_at": str(r["updated_at"])
            })
        return workflows

    @staticmethod
    def get_workflow(workflow_id, user_id=None):
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, user_id, team_id, name, description, steps_json, created_at, updated_at
            FROM workflows WHERE id = ?
        """, (workflow_id,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "team_id": row["team_id"],
            "name": row["name"],
            "description": row["description"],
            "steps": json.loads(row["steps_json"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"])
        }

    @staticmethod
    def update_workflow(workflow_id, user_id, name, description="", steps=None):
        steps = steps or []
        WorkflowService.validate_workflow_steps(steps)

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM workflows WHERE id = ?", (workflow_id,))
        wf = cursor.fetchone()
        if not wf:
            conn.close()
            raise ValueError("Workflow not found.")
        if wf["user_id"] != user_id:
            conn.close()
            raise PermissionError("Unauthorized to update this workflow.")

        cursor.execute("""
            UPDATE workflows
            SET name = ?, description = ?, steps_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (name.strip(), description.strip(), json.dumps(steps), workflow_id))
        conn.commit()
        conn.close()

        return WorkflowService.get_workflow(workflow_id, user_id)

    @staticmethod
    def delete_workflow(workflow_id, user_id):
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM workflows WHERE id = ?", (workflow_id,))
        wf = cursor.fetchone()
        if not wf:
            conn.close()
            raise ValueError("Workflow not found.")
        if wf["user_id"] != user_id:
            conn.close()
            raise PermissionError("Unauthorized to delete this workflow.")

        cursor.execute("DELETE FROM workflows WHERE id = ?", (workflow_id,))
        conn.commit()
        conn.close()
        return True

    @staticmethod
    def execute_workflow(user_id, input_file_path, original_filename, workflow_id=None, custom_steps=None):
        exec_id = f"exec_{uuid.uuid4().hex[:12]}"
        run_dir = WORKFLOW_RUNS_DIR / exec_id
        run_dir.mkdir(parents=True, exist_ok=True)

        steps = custom_steps
        wf_name = "Custom Workflow"
        if workflow_id:
            wf = WorkflowService.get_workflow(workflow_id, user_id)
            if not wf:
                raise ValueError("Workflow not found.")
            steps = wf["steps"]
            wf_name = wf["name"]

        WorkflowService.validate_workflow_steps(steps)

        # Initialize execution record
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO workflow_executions (id, workflow_id, user_id, status, input_filename, total_steps, steps_completed, log_json)
            VALUES (?, ?, ?, 'running', ?, ?, 0, '[]')
        """, (exec_id, workflow_id, user_id, original_filename, len(steps)))
        conn.commit()
        conn.close()

        # Copy original input to run_dir
        curr_ext = Path(original_filename).suffix.lower()
        curr_file = run_dir / f"step_0_input{curr_ext}"
        shutil.copy2(input_file_path, curr_file)

        step_logs = []
        completed_count = 0

        try:
            for idx, step in enumerate(steps):
                step_type = step.get("type")
                params = step.get("params", {})
                step_meta = WorkflowService.SUPPORTED_STEPS.get(step_type, {})
                step_name = step_meta.get("name", step_type)
                out_ext = step_meta.get("output_ext", ".pdf")
                step_out = run_dir / f"step_{idx + 1}_{step_type}{out_ext}"

                step_log_entry = {
                    "step_index": idx + 1,
                    "type": step_type,
                    "name": step_name,
                    "status": "processing"
                }

                if step_type == "compress":
                    level = params.get("level", "medium")
                    PDFOptimizeService.compress_pdf(curr_file, step_out, level=level)
                elif step_type == "rotate":
                    rot = int(params.get("rotation", 90))
                    pgs = params.get("pages", "all")
                    PDFOrganizeService.rotate_pdf(curr_file, step_out, rotation=rot, pages=pgs)
                elif step_type == "watermark":
                    text = params.get("text", "CONFIDENTIAL")
                    opacity = float(params.get("opacity", 0.3))
                    font_size = float(params.get("font_size", 40))
                    PDFEditService.watermark_pdf(curr_file, step_out, text=text, opacity=opacity, font_size=font_size)
                elif step_type == "protect":
                    pwd = params.get("password", "PaperPilot123")
                    PDFSecurityService.protect_pdf(curr_file, step_out, user_password=pwd)
                elif step_type == "pdf_to_word":
                    PDFConvertService.pdf_to_word(curr_file, step_out)
                elif step_type == "pdf_to_excel":
                    PDFConvertService.pdf_to_excel(curr_file, step_out)
                elif step_type == "pdf_to_powerpoint":
                    PDFConvertService.pdf_to_powerpoint(curr_file, step_out)
                elif step_type == "pdf_to_text":
                    PDFConvertService.pdf_to_text(curr_file, step_out)
                elif step_type == "pdf_to_jpg":
                    PDFConvertService.pdf_to_jpg(curr_file, step_out)
                elif step_type == "summarize":
                    res = PDFIntelligenceService.summarize_pdf(curr_file)
                    with open(step_out, "w", encoding="utf-8") as f:
                        f.write(f"=== SUMMARY ===\n{res.get('summary', '')}\n\n")
                        f.write("=== KEY POINTS ===\n")
                        for kp in res.get("key_points", []):
                            f.write(f"• {kp}\n")
                        f.write("\n=== ACTION ITEMS ===\n")
                        for ai in res.get("action_items", []):
                            f.write(f"- {ai}\n")
                else:
                    raise ValueError(f"Unsupported step: {step_type}")

                step_log_entry["status"] = "completed"
                step_logs.append(step_log_entry)
                completed_count += 1
                curr_file = step_out

            final_ext = Path(curr_file).suffix
            base_stem = Path(original_filename).stem
            final_filename = f"{base_stem}_workflow{final_ext}"
            final_output_path = run_dir / final_filename
            shutil.copy2(curr_file, final_output_path)

            register_job(
                job_id=exec_id,
                operation=f"workflow_{wf_name}",
                output_path=final_output_path,
                output_filename=final_filename
            )

            download_url = f"/api/workflows/executions/{exec_id}/download"

            # Update DB execution status
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE workflow_executions
                SET status = 'completed', output_filename = ?, download_url = ?, steps_completed = ?, log_json = ?
                WHERE id = ?
            """, (final_filename, download_url, completed_count, json.dumps(step_logs), exec_id))
            conn.commit()
            conn.close()

            return {
                "execution_id": exec_id,
                "status": "completed",
                "workflow_name": wf_name,
                "total_steps": len(steps),
                "steps_completed": completed_count,
                "output_filename": final_filename,
                "download_url": download_url,
                "output_path": str(final_output_path),
                "logs": step_logs
            }

        except Exception as e:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE workflow_executions
                SET status = 'failed', steps_completed = ?, log_json = ?
                WHERE id = ?
            """, (completed_count, json.dumps(step_logs + [{"error": str(e)}]), exec_id))
            conn.commit()
            conn.close()
            raise e

    @staticmethod
    def get_execution(exec_id, user_id=None):
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, workflow_id, user_id, status, input_filename, output_filename, download_url, steps_completed, total_steps, log_json, created_at
            FROM workflow_executions WHERE id = ?
        """, (exec_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        return {
            "id": row["id"],
            "workflow_id": row["workflow_id"],
            "user_id": row["user_id"],
            "status": row["status"],
            "input_filename": row["input_filename"],
            "output_filename": row["output_filename"],
            "download_url": row["download_url"],
            "steps_completed": row["steps_completed"],
            "total_steps": row["total_steps"],
            "logs": json.loads(row["log_json"] or "[]"),
            "created_at": str(row["created_at"])
        }

    @staticmethod
    def list_executions(user_id, limit=20):
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, workflow_id, user_id, status, input_filename, output_filename, download_url, steps_completed, total_steps, created_at
            FROM workflow_executions WHERE user_id = ?
            ORDER BY created_at DESC LIMIT ?
        """, (user_id, limit))
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]
