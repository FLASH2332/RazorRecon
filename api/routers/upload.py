from fastapi import APIRouter, UploadFile, File, HTTPException
from engine.tools.ingestion import ingest_payments, ingest_settlements, ingest_bank
import shutil, os, tempfile

router = APIRouter()

@router.post("/{session_id}/upload/payments")
async def upload_payments(session_id: str, file: UploadFile = File(...)):
    if not file.filename.endswith(".csv"):
        raise HTTPException(400, "Only CSV files accepted for payments")
    
    with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name
    
    try:
        result = ingest_payments(session_id, tmp_path)
        return {"session_id": session_id, **result}
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        os.unlink(tmp_path)

@router.post("/{session_id}/upload/settlements")
async def upload_settlements(session_id: str, file: UploadFile = File(...)):
    if not file.filename.endswith(".csv"):
        raise HTTPException(400, "Only CSV files accepted for settlements")
    
    with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name
    
    try:
        result = ingest_settlements(session_id, tmp_path)
        return {"session_id": session_id, **result}
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        os.unlink(tmp_path)

@router.post("/{session_id}/upload/bank-statement")
async def upload_bank_statement(session_id: str, file: UploadFile = File(...)):
    suffix = ".pdf" if file.filename.endswith(".pdf") else ".csv"
    
    if suffix not in [".pdf", ".csv"]:
        raise HTTPException(400, "Only CSV or PDF files accepted for bank statement")
    
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name
    
    try:
        if suffix == ".pdf":
            from engine.pdf_parser import parse_bank_pdf
            csv_path = tmp_path.replace(".pdf", "_parsed.csv")
            parse_result = parse_bank_pdf(tmp_path, csv_path)
            if not parse_result["success"]:
                raise HTTPException(422, f"PDF parse failed: {parse_result['reason']}")
            result = ingest_bank(session_id, csv_path)
            os.unlink(csv_path)
        else:
            result = ingest_bank(session_id, tmp_path)
        
        return {"session_id": session_id, **result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        os.unlink(tmp_path)
