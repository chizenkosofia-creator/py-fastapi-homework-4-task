from datetime import date
from fastapi import (
    APIRouter,
    Depends,
    Form,
    File,
    UploadFile,
    HTTPException,
    Request,
    status
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from database.models.accounts import UserModel, UserProfileModel, GenderEnum
from schemas.profiles import ProfileResponseSchema
from config.dependencies import get_settings, get_jwt_auth_manager, get_s3_storage_client
from config.settings import BaseAppSettings
from security.interfaces import JWTAuthManagerInterface
from storages import S3StorageInterface
from exceptions.security import TokenExpiredError, InvalidTokenError
from exceptions.storage import StorageUploadError
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
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(...),
    gender: str = Form(...),
    date_of_birth: date = Form(...),
    info: str = Form(...),
    avatar: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    settings: BaseAppSettings = Depends(get_settings),
    jwt_manager: JWTAuthManagerInterface = Depends(get_jwt_auth_manager),
    s3_client: S3StorageInterface = Depends(get_s3_storage_client),
):
    # 1. Токен валідація
    token = get_token(request)
    try:
        payload = jwt_manager.decode_access_token(token)
        current_user_id = int(payload.get("sub"))
        user_group = payload.get("group")
    except (TokenExpiredError, InvalidTokenError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired."
        )

    # 2. Перевірка прав доступу (права редагування)
    if current_user_id != user_id and user_group != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to edit this profile."
        )

    # 3. Перевірка існування та активності користувача
    stmt = select(UserModel).where(UserModel.id == user_id)
    result = await db.execute(stmt)
    target_user = result.scalar_one_or_none()

    if not target_user or not target_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or not active."
        )

    # 4. Перевірка чи профіль вже існує
    stmt_profile = select(UserProfileModel).where(UserProfileModel.user_id == user_id)
    res_profile = await db.execute(stmt_profile)
    existing_profile = res_profile.scalar_one_or_none()

    if existing_profile:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User already has a profile."
        )

    # Валідація вхідних даних поля
    validate_name(first_name)
    validate_name(last_name)
    validate_gender(gender)
    validate_birth_date(date_of_birth)
    if not info or not info.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Info field cannot be empty or contain only spaces."
        )
    validate_image(avatar)

    # 5. Завантаження аватарки в S3
    try:
        file_extension = avatar.filename.split(".")[-1]
        object_name = f"avatars/{user_id}_avatar.{file_extension}"
        avatar_url = await s3_client.upload_file(
            file=avatar.file,
            object_name=object_name,
            content_type=avatar.content_type
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload avatar. Please try again later."
        )

    # 6. Створення профілю в БД
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
