from fastapi import APIRouter, Depends, status, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from config import get_jwt_auth_manager, get_s3_storage_client
from exceptions import BaseSecurityError, BaseS3Error
from schemas.profiles import ProfileResponseSchema, ProfileRequestSchema

from database import get_db, UserModel, UserGroupEnum, UserProfileModel
from security.http import get_token
from security.interfaces import JWTAuthManagerInterface
from storages import S3StorageInterface

router = APIRouter()


@router.post(
    path="/users/{user_id}/profile/",
    status_code=201,
    response_model=ProfileResponseSchema,
)
async def create_user_profile(
    user_id: int,
    profile_data: ProfileRequestSchema = Depends(ProfileRequestSchema.as_form),
    token: str = Depends(get_token),
    jwt_manager: JWTAuthManagerInterface = Depends(get_jwt_auth_manager),
    s3_client: S3StorageInterface = Depends(get_s3_storage_client),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a profile for a user.

    Only the user themselves or an admin can create a profile for a given user_id.
    The avatar is uploaded to S3-compatible storage, and the profile is stored in the database.

    Args:
        user_id (int): The ID of the user for whom the profile is being created.
        profile_data (ProfileCreateSchema): The parsed and validated multipart/form-data payload.
        token (str): The bearer access token extracted from the Authorization header.
        jwt_manager (JWTAuthManagerInterface): The JWT authentication manager.
        s3_client (S3StorageInterface): The S3-compatible storage client.
        db (AsyncSession): The asynchronous database session.

    Returns:
        ProfileResponseSchema: The newly created profile.

    Raises:
        HTTPException:
            - 401 Unauthorized if the token is invalid/expired or the requesting user
              does not exist or is not active.
            - 403 Forbidden if the requesting user tries to create a profile for someone else
              without admin rights.
            - 400 Bad Request if the target user already has a profile.
            - 500 Internal Server Error if the avatar upload fails.
    """
    try:
        decoded_token = jwt_manager.decode_access_token(token)
    except BaseSecurityError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(error),
        )

    requesting_user_id = decoded_token.get("user_id")

    stmt = (
        select(UserModel)
        .options(joinedload(UserModel.group))
        .where(UserModel.id == requesting_user_id)
    )
    result = await db.execute(stmt)
    requesting_user = result.scalar()

    if not requesting_user or not requesting_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or not active.",
        )

    if requesting_user.id != user_id and not requesting_user.has_group(
        UserGroupEnum.ADMIN
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to edit this profile.",
        )

    stmt_profile = select(UserProfileModel).where(UserProfileModel.user_id == user_id)
    result = await db.execute(stmt_profile)
    existing_profile = result.scalar()

    if existing_profile:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User already has a profile.",
        )

    avatar_key = f"avatars/{user_id}_avatar.jpg"
    avatar_bytes = await profile_data.avatar.read()

    try:
        await s3_client.upload_file(file_name=avatar_key, file_data=avatar_bytes)
    except BaseS3Error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload avatar. Please try again later.",
        )

    avatar_url = await s3_client.get_file_url(avatar_key)

    new_profile = UserProfileModel(
        first_name=profile_data.first_name,
        last_name=profile_data.last_name,
        avatar=avatar_key,
        gender=profile_data.gender,
        date_of_birth=profile_data.date_of_birth,
        info=profile_data.info,
        user_id=user_id,
    )

    db.add(new_profile)
    await db.commit()
    await db.refresh(new_profile)

    return ProfileResponseSchema(
        id=new_profile.id,
        user_id=new_profile.user_id,
        first_name=new_profile.first_name,
        last_name=new_profile.last_name,
        gender=new_profile.gender,
        date_of_birth=new_profile.date_of_birth,
        info=new_profile.info,
        avatar=avatar_url,
    )
