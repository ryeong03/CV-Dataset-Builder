"""
CV Dataset Builder - 웹 대시보드
검색어 입력 → 수집 작업 실행 → 저장 위치·수집 개수 확인
이력·로그는 PostgreSQL에 저장. .env에서 PGHOST, PGUSER 등 로드.
"""
from pathlib import Path as _Path
_PROJECT_ROOT = _Path(__file__).resolve().parent.parent
from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

import concurrent.futures
import json
import os
import re
import subprocess
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

try:
    from dashboard import db
except ImportError:
    import db  # python dashboard/app.py 로 실행 시

# 프로젝트 루트 (dashboard의 상위)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
COLLECTOR_SCRIPT = PROJECT_ROOT / "tools" / "high_quality_image_collector.py"
STATIC_DIR = Path(__file__).resolve().parent / "static"

# 수집 작업: 메모리(dict) + SQLite 영속화 (process 등 런타임 필드는 메모리만)
jobs: dict[str, dict] = {}
executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)


def _load_jobs() -> None:
    """DB에서 수집 이력 불러오기 (앱 시작·재시작 시)."""
    global jobs
    try:
        db.migrate_from_json_if_needed()
        raw = db.get_all_jobs()
        for j in raw:
            if j.get("status") == "running":
                j["status"] = "cancelled"
                j["error"] = (j.get("error") or "") or "서버 재시작으로 중단됨"
            jobs[j["id"]] = j
    except Exception as e:
        print(f"[DB] 이력 로드 실패 (첫 저장 시에도 오류 날 수 있음): {e}")


def _save_jobs() -> None:
    """수집 이력 전체를 DB에 저장 (직렬화 가능한 필드만)."""
    db.save_all_jobs(jobs, _job_for_api)


def _set_job_log(job_id: str, stdout: str | None, stderr: str | None) -> None:
    """수집 스크립트 stdout/stderr를 job 로그로 저장 (성공/실패 모두)."""
    if job_id not in jobs:
        return
    out = (stdout or "").strip()
    err = (stderr or "").strip()
    log = (err + "\n\n--- stdout ---\n" + out) if out else err
    jobs[job_id]["log"] = log[-15000:] if len(log) > 15000 else log or ""


def _job_for_api(job: dict) -> dict:
    """JSON 직렬화 가능한 job 복사본 (process 등 비직렬화 필드 제외)."""
    safe_keys = ("id", "query", "limit", "out_dir", "status", "count", "error", "log", "started_at", "finished_at")
    return {k: job.get(k) for k in safe_keys if k in job}


_load_jobs()


class RunRequest(BaseModel):
    query: str = Field(..., min_length=1, description="검색어")
    limit: int = Field(20, ge=1, le=500, description="수집할 이미지 개수")
    out_dir: str = Field("data/naver_collected", description="저장 폴더 (프로젝트 기준)")


def run_collector(job_id: str, query: str, limit: int, out_dir: str) -> None:
    """백그라운드에서 수집 스크립트 실행 후 결과 반영. 중단 시 process.terminate()로 종료 가능."""
    proc = None
    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen(
            ["python", str(COLLECTOR_SCRIPT), query, "--limit", str(limit), "--out_dir", out_dir],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        jobs[job_id]["process"] = proc
        if jobs[job_id].get("cancel_requested"):
            proc.terminate()
            proc.wait(timeout=10)
            jobs[job_id]["status"] = "cancelled"
            jobs[job_id]["error"] = "사용자에 의해 중단됨"
            jobs[job_id]["finished_at"] = datetime.now().isoformat()
            _save_jobs()
            return
        try:
            stdout, stderr = proc.communicate(timeout=600)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["error"] = "수집 시간 초과 (10분)"
            jobs[job_id]["finished_at"] = datetime.now().isoformat()
            _save_jobs()
            return
        returncode = proc.returncode
    except Exception as e:
        if job_id in jobs:
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["error"] = str(e)
            jobs[job_id]["finished_at"] = datetime.now().isoformat()
            _save_jobs()
        return
    finally:
        if job_id in jobs and "process" in jobs[job_id]:
            del jobs[job_id]["process"]

    if jobs[job_id].get("cancel_requested") or returncode != 0:
        if jobs[job_id].get("cancel_requested"):
            jobs[job_id]["status"] = "cancelled"
            jobs[job_id]["error"] = "사용자에 의해 중단됨"
        else:
            jobs[job_id]["status"] = "failed"
            err = (stderr or "").strip()
            out = (stdout or "").strip()
            full = (err + "\n\n--- stdout ---\n" + out) if out else err
            jobs[job_id]["error"] = full[-30000:] if len(full) > 30000 else full or "Unknown error"
        _set_job_log(job_id, stdout, stderr)
        jobs[job_id]["finished_at"] = datetime.now().isoformat()
        _save_jobs()
        return

    # 스크립트 stdout에서 "총 N장 저장됨" 파싱 (한글 경로 등으로 glob이 0일 수 있음)
    count = 0
    if stdout:
        m = re.search(r"총\s*(\d+)\s*장\s*저장", stdout)
        if m:
            count = int(m.group(1))
    if count == 0:
        out_path = PROJECT_ROOT / out_dir
        if out_path.exists():
            count = len(list(out_path.glob("*.jpg")))
    jobs[job_id]["status"] = "done"
    jobs[job_id]["count"] = count
    jobs[job_id]["finished_at"] = datetime.now().isoformat()
    _set_job_log(job_id, stdout, stderr)
    _save_jobs()


app = FastAPI(title="CV Dataset Builder", description="이미지 수집 대시보드")


@app.exception_handler(Exception)
def json_exception_handler(request, exc):
    """모든 예외를 JSON으로 반환해 프론트에서 파싱 오류 나지 않게."""
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error", "error": str(exc)},
    )


@app.post("/api/run")
def api_run(req: RunRequest):
    """수집 작업 시작. job_id 반환. 작업마다 별도 폴더 사용 (폴더명은 job_id만 사용해 한글/인코딩 이슈 방지)."""
    job_id = str(uuid.uuid4())[:8]
    base = (req.out_dir or "data/naver_collected").rstrip("/")
    out_dir = f"{base}/{job_id}"
    jobs[job_id] = {
        "id": job_id,
        "query": req.query,
        "limit": req.limit,
        "out_dir": out_dir,
        "status": "running",
        "count": None,
        "error": None,
        "started_at": datetime.now().isoformat(),
        "finished_at": None,
        "cancel_requested": False,
    }
    executor.submit(run_collector, job_id, req.query, req.limit, out_dir)
    _save_jobs()
    return {"job_id": job_id}


@app.post("/api/jobs/{job_id}/cancel")
def api_job_cancel(job_id: str):
    """진행 중인 수집 작업 중단."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    job = jobs[job_id]
    if job["status"] != "running":
        return {"ok": True, "message": "이미 완료되었거나 중단된 작업입니다."}
    job["cancel_requested"] = True
    if "process" in job:
        try:
            job["process"].terminate()
        except Exception:
            pass
    return {"ok": True, "message": "중단 요청되었습니다."}


@app.delete("/api/jobs/{job_id}")
def api_job_delete(job_id: str):
    """수집 이력 한 건 삭제 (DB + 메모리). 저장된 이미지 파일은 삭제하지 않음."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    del jobs[job_id]
    db.delete_job(job_id)
    return {"ok": True, "message": "삭제되었습니다."}


@app.post("/api/jobs/clear")
def api_jobs_clear():
    """수집 이력 전체 삭제 (DB + 메모리)."""
    jobs.clear()
    db.clear_all_jobs()
    return {"ok": True, "message": "이력이 삭제되었습니다."}


@app.get("/api/jobs")
def api_jobs_list(page: int = 1, per_page: int = 10):
    """작업 목록 (최신순). 10개 단위 페이지네이션."""
    items = sorted(jobs.values(), key=lambda x: x["started_at"], reverse=True)
    total = len(items)
    start = (page - 1) * per_page
    end = start + per_page
    page_items = items[start:end]
    return {
        "jobs": [_job_for_api(j) for j in page_items],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@app.get("/api/jobs/{job_id}")
def api_job_detail(job_id: str):
    """작업 한 건 조회."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_for_api(jobs[job_id])


def _job_out_path(job_id: str) -> Path | None:
    """작업의 저장 폴더 Path. 없거나 프로젝트 밖이면 None."""
    if job_id not in jobs:
        return None
    out_dir = jobs[job_id].get("out_dir")
    if not out_dir:
        return None
    path = (PROJECT_ROOT / out_dir).resolve()
    if not path.is_dir() or not str(path).startswith(str(PROJECT_ROOT.resolve())):
        return None
    return path


@app.get("/api/jobs/{job_id}/images")
def api_job_images(job_id: str):
    """해당 작업으로 수집된 이미지 파일명 목록. 디스크 기준으로 반환해 서빙 시 경로 일치."""
    out_path = _job_out_path(job_id)
    if not out_path:
        raise HTTPException(status_code=404, detail="Job or folder not found")
    out_path = out_path.resolve()
    files = sorted(f.name for f in out_path.iterdir() if f.suffix.lower() == ".jpg" and f.is_file())
    if not files:
        try:
            for line in (out_path / "manifest.jsonl").read_text(encoding="utf-8").strip().splitlines():
                if line:
                    files.append(json.loads(line).get("file", ""))
            files = [f for f in files if f]
        except Exception:
            pass
    return {"job_id": job_id, "out_dir": jobs[job_id]["out_dir"], "files": files}


@app.get("/api/jobs/{job_id}/images/{filename:path}")
def api_serve_job_image(job_id: str, filename: str):
    """수집된 이미지 파일 하나 서빙 (path traversal 방지, 한글 파일명 지원)."""
    filename = filename.strip()
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    out_path = _job_out_path(job_id)
    if not out_path:
        raise HTTPException(status_code=404, detail="Job or folder not found")
    # 한글 등으로 인한 경로 이슈 방지: 실제 디렉터리 목록과 비교
    out_path = out_path.resolve()
    file_path = (out_path / filename).resolve()
    if not str(file_path).startswith(str(out_path)) or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(file_path), media_type="image/jpeg")


# 프론트: dashboard/static/ (index.html, css/style.css, js/app.js)
@app.get("/")
def dashboard():
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
