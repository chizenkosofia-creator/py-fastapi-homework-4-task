from datetime import date
from sqlalchemy.orm import selectinload
import inspect
from fastapi import status
from starlette.requests import Request
from fastapi import (
    APIRouter,
    Depends,
    Form,
    File,
    UploadFile,
    HTTPException,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from database.models.accounts import UserModel, UserProfileModel, GenderEnum
from schemas.profiles import ProfileResponseSchema
from config.dependencies import get_jwt_auth_manager, get_s3_storage_client
from security.interfaces import JWTAuthManagerInterface
from storages import S3StorageInterface
from exceptions.security import TokenExpiredError, InvalidTokenError
from validation import (
    validate_name,
    validate_image,
    validate_gender,
    validate_birth_date
)

router = APIRouter(prefix="/users", tags=["profiles"])


def get_token(request: Request) -> str:
    authorization: str = request.headers.get("Authorization")

    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header is missing"
        )

    scheme, _, token = authorization.partition(" ")

    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format. Expected 'Bearer <token>'"
        )

    return token


@router.post(
    "/{user_id}/profile/",
    response_model=ProfileResponseSchema,
    status_code=status.HTTP_201_CREATED
)
async def create_profile(
    user_id: int,
    token: str = Depends(get_token),
    first_name: str = Form(...),
    last_name: str = Form(...),
    gender: str = Form(...),
    date_of_birth: date = Form(...),
    info: str | None = Form(None),
    avatar: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    jwt_manager: JWTAuthManagerInterface = Depends(get_jwt_auth_manager),
    s3_client: S3StorageInterface = Depends(get_s3_storage_client),
):
    try:
        payload = jwt_manager.decode_access_token(token)
    except (TokenExpiredError, InvalidTokenError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired."
        )
    current_user_id = int(payload.get("user_id"))
    stmt = select(UserModel).where(UserModel.id == current_user_id).options(selectinload(UserModel.group))
    result = await db.execute(stmt)
    current_user = result.scalar_one_or_none()
    is_admin = current_user and current_user.group and current_user.group.name.lower() == "admin"
    if current_user_id != user_id and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to edit this profile."
        )

    stmt = select(UserModel).where(UserModel.id == user_id)
    result = await db.execute(stmt)
    target_user = result.scalar_one_or_none()

    if not target_user or not target_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or not active."
        )

    stmt_profile = select(UserProfileModel).where(UserProfileModel.user_id == user_id)
    res_profile = await db.execute(stmt_profile)
    existing_profile = res_profile.scalar_one_or_none()

    if existing_profile:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="User already has a profile."
        )

    try:
        validate_name(first_name)
        validate_name(last_name)
        validate_gender(gender)
        validate_birth_date(date_of_birth)
        if not info or not info.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Info field cannot be empty or contain only spaces."
            )
        validate_image(avatar)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e)
        )

    try:
        file_ext = avatar.filename.split(".")[-1] if avatar.filename and "." in avatar.filename else "jpg"
        object_name = f"avatars/{user_id}_avatar.{file_ext}"
        file_bytes = await avatar.read()

        res = s3_client.upload_file(
            file_data=file_bytes,
            file_name=object_name,
        )
        avatar_url = f"http://minio-theater/{object_name}"
    except HTTPException:
        raise
    except Exception as e:
        import logging
        logging.error(f"S3 upload error: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload avatar. Please try again later."
        )

    profile = UserProfileModel(
        user_id=user_id,
        first_name=first_name,
        last_name=last_name,
        gender=GenderEnum(gender.lower()),
        date_of_birth=date_of_birth,
        info=info,
        avatar=avatar_url
    )

    db.add(profile)
    await db.commit()
    await db.refresh(profile)

    return profile
