"""
CV Dataset Builder - PostgreSQL 저장소
이력·로그를 DB로 저장. .env 또는 환경 변수로 연결 정보 설정.
기존 jobs.json 있으면 첫 기동 시 한 번 마이그레이션.
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import psycopg2
from psycopg2.extras import RealDictCursor

# 연결: .env 또는 환경 변수 (PGHOST, PGUSER, PGPASSWORD, PGDATABASE, PGPORT)
# URL 말고 변수만 써도 됨.
JOBS_JSON = Path(__file__).resolve().parent / "data" / "jobs.json"


def _get_connection_params():
    host = os.environ.get("PGHOST", "localhost")
    port = os.environ.get("PGPORT", "5432")
    dbname = os.environ.get("PGDATABASE", "cv_dataset_builder")
    user = os.environ.get("PGUSER", "")
    password = os.environ.get("PGPASSWORD", "")
    if not user:
        raise RuntimeError(
            "PostgreSQL 연결 정보가 없습니다. "
            "프로젝트 루트에 .env 파일 만들고 PGHOST, PGUSER, PGPASSWORD, PGDATABASE, PGPORT 를 넣으세요."
        )
    return {
        "host": host,
        "port": port,
        "dbname": dbname,
        "user": user,
        "password": password,
    }


def _connect():
    params = _get_connection_params()
    if "dsn" in params:
        return psycopg2.connect(params["dsn"])
    return psycopg2.connect(**params)


def init_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id VARCHAR(32) PRIMARY KEY,
                query TEXT NOT NULL,
                request_limit INTEGER NOT NULL,
                out_dir TEXT NOT NULL,
                status VARCHAR(32) NOT NULL,
                count INTEGER,
                error TEXT,
                log TEXT,
                started_at TEXT,
                finished_at TEXT
            )
        """)
    conn.commit()


def migrate_from_json_if_needed() -> None:
    """DB가 비어 있고 jobs.json이 있으면 한 번만 이전."""
    conn = _connect()
    try:
        init_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM jobs")
            if cur.fetchone()[0] > 0:
                return
        if not JOBS_JSON.exists():
            return
        raw = json.loads(JOBS_JSON.read_text(encoding="utf-8"))
        with conn.cursor() as cur:
            for j in raw:
                cur.execute(
                    """
                    INSERT INTO jobs (id, query, request_limit, out_dir, status, count, error, log, started_at, finished_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        query = EXCLUDED.query,
                        request_limit = EXCLUDED.request_limit,
                        out_dir = EXCLUDED.out_dir,
                        status = EXCLUDED.status,
                        count = EXCLUDED.count,
                        error = EXCLUDED.error,
                        log = EXCLUDED.log,
                        started_at = EXCLUDED.started_at,
                        finished_at = EXCLUDED.finished_at
                    """,
                    (
                        j.get("id"),
                        j.get("query") or "",
                        j.get("limit") or 0,
                        j.get("out_dir") or "",
                        j.get("status") or "cancelled",
                        j.get("count"),
                        j.get("error"),
                        j.get("log"),
                        j.get("started_at"),
                        j.get("finished_at"),
                    ),
                )
        conn.commit()
    finally:
        conn.close()


def get_all_jobs() -> list[dict]:
    conn = _connect()
    try:
        init_schema(conn)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, query, request_limit AS limit, out_dir, status, count, error, log, started_at, finished_at FROM jobs"
            )
            rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def upsert_job(job: dict) -> None:
    """한 건 저장 (API용 필드만)."""
    row = (
        job.get("id"),
        job.get("query") or "",
        job.get("limit") or 0,
        job.get("out_dir") or "",
        job.get("status") or "running",
        job.get("count"),
        job.get("error"),
        job.get("log"),
        job.get("started_at"),
        job.get("finished_at"),
    )
    conn = _connect()
    try:
        init_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO jobs (id, query, request_limit, out_dir, status, count, error, log, started_at, finished_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    query = EXCLUDED.query,
                    request_limit = EXCLUDED.request_limit,
                    out_dir = EXCLUDED.out_dir,
                    status = EXCLUDED.status,
                    count = EXCLUDED.count,
                    error = EXCLUDED.error,
                    log = EXCLUDED.log,
                    started_at = EXCLUDED.started_at,
                    finished_at = EXCLUDED.finished_at
                """,
                row,
            )
        conn.commit()
    finally:
        conn.close()


def save_all_jobs(jobs_dict: dict, job_for_api_fn) -> None:
    """메모리 jobs 전체를 DB에 반영 (직렬화 가능한 필드만)."""
    conn = _connect()
    try:
        init_schema(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM jobs")
            for j in jobs_dict.values():
                api = job_for_api_fn(j)
                cur.execute(
                    """
                    INSERT INTO jobs (id, query, request_limit, out_dir, status, count, error, log, started_at, finished_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        api.get("id"),
                        api.get("query") or "",
                        api.get("limit") or 0,
                        api.get("out_dir") or "",
                        api.get("status") or "running",
                        api.get("count"),
                        api.get("error"),
                        api.get("log"),
                        api.get("started_at"),
                        api.get("finished_at"),
                    ),
                )
        conn.commit()
    finally:
        conn.close()


def delete_job(job_id: str) -> bool:
    """한 건 삭제. 있으면 True, 없으면 False."""
    conn = _connect()
    try:
        init_schema(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM jobs WHERE id = %s", (job_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def clear_all_jobs() -> None:
    conn = _connect()
    try:
        init_schema(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM jobs")
        conn.commit()
    finally:
        conn.close()
