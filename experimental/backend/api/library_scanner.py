"""Library scanning API - Smart population and updates"""

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_current_admin_user
from ..database import get_db
from ..models.library_path import LibraryPath
from ..models.media import LibraryScanJob
from ..models.user import User

router = APIRouter(prefix="/api/library/scan", tags=["library-scanner"])


# Schemas
class ScanRequest(BaseModel):
    """Request to scan library"""

    path_id: Optional[int] = None  # null = scan all paths
    force_rescan: bool = False  # Re-scan even if recently scanned (for mass updates)


@router.post("/")
async def start_library_scan(
    request: ScanRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """
    Start library scan

    Scans one or all library paths for media files.
    Returns immediately with job ID - scan runs in background.

    """

    # Validate path if specified
    if request.path_id:
        result = await db.execute(
            select(LibraryPath).where(LibraryPath.id == request.path_id)
        )
        path = result.scalar_one_or_none()
        if not path:
            raise HTTPException(status_code=404, detail="Library path not found")
        if not path.enabled:
            raise HTTPException(status_code=400, detail="Library path is disabled")

    # Cancel any existing pending or running jobs
    result = await db.execute(
        select(LibraryScanJob).where(LibraryScanJob.status.in_(["pending", "running"]))
    )
    existing_jobs = result.scalars().all()

    for existing_job in existing_jobs:
        existing_job.cancelled = True

        # If it's pending, it hasn't started yet, so we can fail it immediately
        # If it's running, we flag it as cancelled and the worker will pick it up
        if existing_job.status == "pending":
            existing_job.status = "failed"
            existing_job.error_message = "Cancelled by newer scan request"
            existing_job.completed_at = datetime.now(timezone.utc)

    if existing_jobs:
        await db.commit()

    # Create scan job
    job = LibraryScanJob(
        library_path_id=request.path_id,
        status="pending",
        triggered_by_user_id=current_user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Queue background task using new scanner coordinator
    async def run_scan_job():
        """Background task to run the scan"""
        from ..database import AsyncSessionLocal
        from ..services.library import create_scanner_coordinator
        from ..services.settings_manager import SettingsManager
        from ..services.tmdb_service import TMDBService

        async with AsyncSessionLocal() as scan_db:
            try:
                # Update job status
                result = await scan_db.execute(
                    select(LibraryScanJob).where(LibraryScanJob.id == job.id)
                )
                scan_job = result.scalar_one_or_none()
                if not scan_job:
                    return

                scan_job.status = "running"
                scan_job.started_at = datetime.now(timezone.utc)
                await scan_db.commit()

                # Initialize scanner coordinator
                settings = SettingsManager(scan_db)
                tmdb_key = await settings.get("tmdb_api_key")
                tmdb = TMDBService(tmdb_key) if tmdb_key else None
                coordinator = await create_scanner_coordinator(scan_db, tmdb)

                # Get library path(s)
                if scan_job.library_path_id:
                    result = await scan_db.execute(
                        select(LibraryPath).where(
                            LibraryPath.id == scan_job.library_path_id
                        )
                    )
                    paths = [result.scalar_one()]
                else:
                    result = await scan_db.execute(
                        select(LibraryPath).where(LibraryPath.enabled == True)
                    )
                    paths = result.scalars().all()

                # Run scans
                total_new = 0
                total_updated = 0
                for library_path in paths:
                    scan_result = await coordinator.run_scan(
                        library_path, scan_job, request.force_rescan
                    )
                    if isinstance(scan_result, dict):
                        total_new += scan_result.get("new", 0)
                        total_updated += scan_result.get("updated", 0)

                # Mark completed
                scan_job.status = "completed"
                scan_job.completed_at = datetime.now(timezone.utc)
                scan_job.new_media_added = total_new
                scan_job.existing_media_updated = total_updated
                await scan_db.commit()

            except Exception as e:
                # Mark failed
                if scan_job:
                    scan_job.status = "failed"
                    scan_job.error_message = str(e)
                    scan_job.completed_at = datetime.now(timezone.utc)
                    await scan_db.commit()

    background_tasks.add_task(run_scan_job)

    return job.to_dict()


@router.get("/jobs")
async def list_scan_jobs(
    status: Optional[str] = Query(None, pattern="^(pending|running|completed|failed)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """
    List scan jobs

    """

    query = select(LibraryScanJob).order_by(LibraryScanJob.created_at.desc())

    if status:
        query = query.where(LibraryScanJob.status == status)

    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    jobs = result.scalars().all()

    return [job.to_dict() for job in jobs]


@router.get("/jobs/{job_id}")
async def get_scan_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """
    Get scan job status

    Poll this endpoint to track scan progress

    """

    result = await db.execute(select(LibraryScanJob).where(LibraryScanJob.id == job_id))
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Scan job not found")

    return job.to_dict()


@router.post("/jobs/{job_id}/cancel")
async def cancel_scan_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """
    Cancel running scan job

    """

    result = await db.execute(select(LibraryScanJob).where(LibraryScanJob.id == job_id))
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Scan job not found")

    if job.status not in ["pending", "running"]:
        raise HTTPException(
            status_code=400, detail="Can only cancel pending or running jobs"
        )

    # Set cancelled flag
    job.cancelled = True
    if job.status == "pending":
        job.status = "failed"
        job.error_message = "Cancelled by user"
        job.completed_at = datetime.now(timezone.utc)
    # If running, the background task will check the flag and cancel itself
    await db.commit()

    return {"message": "Scan job cancelled"}


@router.get("/status")
async def get_scanner_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """Get scanner system status"""
    from ..services.library import create_scanner_coordinator
    from ..services.settings_manager import SettingsManager
    from ..services.tmdb_service import TMDBService

    settings = SettingsManager(db)
    tmdb_key = await settings.get("tmdb_api_key")
    tmdb = TMDBService(tmdb_key) if tmdb_key else None

    coordinator = await create_scanner_coordinator(db, tmdb)
    status = await coordinator.get_scanner_status()

    return status
