import base64
import io
import json
import traceback
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pypdf import PdfReader

from . import data, llm, pipeline

STATIC = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="WaitLess")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.on_event("startup")
def startup():
    llm.discover_models()


class Query(BaseModel):
    referral: str
    postcode: str
    max_miles: float = 40
    context: str = ""


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/meta")
def meta():
    return {"period": data.PERIOD, "providers": len(data.PROVIDERS), "models": llm.MODELS}


@app.post("/api/read-letter")
async def read_letter(file: UploadFile = File(...)):
    raw = await file.read()
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(413, "File too large (max 8 MB).")
    if file.content_type == "application/pdf":
        text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(raw)).pages)
        return {"text": text.strip(), "usage": None}
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(415, "Upload a photo or a PDF.")
    text, usage = llm.read_image(base64.b64encode(raw).decode(), file.content_type)
    return {"text": text, "usage": usage}


@app.post("/api/analyze")
def analyze(q: Query):
    def stream():
        try:
            for ev in pipeline.run(q.referral, q.postcode, q.max_miles, q.context):
                yield f"data: {json.dumps(ev)}\n\n"
        except Exception as e:
            traceback.print_exc()
            yield f"data: {json.dumps({'step': 'error', 'status': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
