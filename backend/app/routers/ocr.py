from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..db import get_session
from ..errors import BusinessError
from ..ocr import service
from ..ocr.providers import PROVIDERS, available_providers
from ..schemas import ReasonIn

router = APIRouter(prefix="/api/ocr", tags=["AI đọc phiếu"])

MAX_UPLOAD = 25 * 1024 * 1024


@router.get("/providers")
def providers():
    return {"configured": available_providers(),
            "all": {k: {"default_model": v.default_model, "key_env": v.key_env} for k, v in PROVIDERS.items()}}


@router.post("/jobs", status_code=201)
async def upload(file: UploadFile = File(...), provider: Optional[str] = Form(None), model: Optional[str] = Form(None),
                 partner_id: Optional[int] = Form(None), session: Session = Depends(get_session)):
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise BusinessError("Ảnh quá lớn (tối đa 25 MB)")
    job = service.process_image(session, data, file.filename or "phieu.jpg", provider=provider, model=model,
                                partner_id=partner_id)
    return service.job_dict(job)


@router.get("/jobs")
def list_jobs(status: Optional[str] = None, session: Session = Depends(get_session)):
    return [service.job_dict(j) for j in service.list_jobs(session, status)]


@router.get("/jobs/{job_id}")
def get_job(job_id: int, session: Session = Depends(get_session)):
    return service.job_dict(service.get_job(session, job_id))


@router.get("/jobs/{job_id}/image")
def job_image(job_id: int, session: Session = Depends(get_session)):
    job = service.get_job(session, job_id)
    if not job.image_path:
        raise BusinessError("Lượt đọc không có ảnh")
    return FileResponse(job.image_path)


@router.post("/jobs/{job_id}/apply")
def apply(job_id: int, payload: Optional[dict] = Body(None), session: Session = Depends(get_session)):
    return service.job_dict(service.apply_job(session, job_id, payload))


@router.post("/jobs/{job_id}/reject")
def reject(job_id: int, body: ReasonIn, session: Session = Depends(get_session)):
    return service.job_dict(service.reject_job(session, job_id, body.reason))
