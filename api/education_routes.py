"""Education routes for landing-page study content and progress."""

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from auth.authentication import CurrentUser, get_current_user
from models.schemas import (
    EducationCatalogResponse,
    EducationFlashcardDeckDetail,
    EducationModerationItem,
    EducationModerationUpdate,
    EducationProgressItem,
    EducationProgressResponse,
    EducationProgressSyncRequest,
    EducationProgressUpdate,
    EducationQuizSectionDetail,
)


education_router = APIRouter(prefix="/education", tags=["Education"])


@education_router.get("/catalog", response_model=EducationCatalogResponse)
async def get_education_catalog():
    """Return public education catalog metadata."""
    from services.education_service import get_education_catalog_data

    return await get_education_catalog_data()


@education_router.get("/flashcards/{deck_id}", response_model=EducationFlashcardDeckDetail)
async def get_flashcard_deck(deck_id: str):
    """Return one flashcard deck."""
    from services.education_service import get_flashcard_deck_data

    return await get_flashcard_deck_data(deck_id=deck_id)


@education_router.get("/quizzes/{section_id}", response_model=EducationQuizSectionDetail)
async def get_quiz_section(section_id: str):
    """Return one quiz section."""
    from services.education_service import get_quiz_section_data

    return await get_quiz_section_data(section_id=section_id)


@education_router.get("/assets/{file_name}")
async def get_education_asset(file_name: str):
    """Serve an education asset from the repo-owned education directory."""
    from services.education_service import resolve_education_asset_path

    asset_path = resolve_education_asset_path(file_name=file_name)
    media_type = None
    if asset_path.suffix.lower() == ".pdf":
        media_type = "application/pdf"
    elif asset_path.suffix.lower() == ".mp4":
        media_type = "video/mp4"
    elif asset_path.suffix.lower() == ".png":
        media_type = "image/png"
    return FileResponse(path=asset_path, filename=asset_path.name, media_type=media_type)


@education_router.get("/progress", response_model=EducationProgressResponse)
async def get_education_progress(current_user: CurrentUser = Depends(get_current_user)):
    """Return stored progress for the authenticated user."""
    from services.education_service import get_education_progress_data

    return await get_education_progress_data(current_user=current_user)


@education_router.put("/progress", response_model=EducationProgressItem)
async def put_education_progress(
    data: EducationProgressUpdate,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Upsert progress for one education item."""
    from services.education_service import upsert_education_progress_data

    return await upsert_education_progress_data(data=data, current_user=current_user)


@education_router.post("/progress/sync", response_model=EducationProgressResponse)
async def sync_education_progress(
    data: EducationProgressSyncRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Merge browser-local progress into the authenticated account."""
    from services.education_service import sync_education_progress_data

    return await sync_education_progress_data(data=data, current_user=current_user)


@education_router.put("/moderation", response_model=EducationModerationItem)
async def put_education_moderation(
    data: EducationModerationUpdate,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Persist tester moderation for one flashcard or quiz question."""
    from services.education_service import upsert_education_moderation_data

    return await upsert_education_moderation_data(data=data, current_user=current_user)
