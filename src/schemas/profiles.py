from datetime import date

from fastapi import UploadFile, Form, File, HTTPException, status
from pydantic import BaseModel, field_validator, HttpUrl, ValidationError

from validation import (
    validate_name,
    validate_image,
    validate_gender,
    validate_birth_date,
)


class ProfileBase(BaseModel):
    first_name: str
    last_name: str
    gender: str
    date_of_birth: date
    info: str


class ProfileRequestSchema(ProfileBase):
    avatar: UploadFile

    model_config = {"arbitrary_types_allowed": True}

    @field_validator("first_name", "last_name")
    @classmethod
    def validate_names(cls, value: str) -> str:
        try:
            validate_name(value)
        except ValueError as error:
            raise ValueError(str(error))
        return value.lower()

    @field_validator("gender")
    @classmethod
    def validate_gender_value(cls, value):
        try:
            validate_gender(value)
        except ValueError as error:
            raise ValueError(str(error))
        return value

    @field_validator("date_of_birth")
    @classmethod
    def validate_date_of_birth(cls, value):
        try:
            validate_birth_date(value)
        except ValueError as error:
            raise ValueError(str(error))
        return value

    @field_validator("info")
    @classmethod
    def validate_info(cls, value):
        if not value or not value.strip():
            raise ValueError("Info field cannot be empty or contain only spaces.")

        return value

    @field_validator("avatar")
    @classmethod
    def validate_avatar(cls, value):
        try:
            validate_image(value)
        except ValueError as error:
            raise ValueError(str(error))

        return value

    @classmethod
    def as_form(
        cls,
        first_name: str = Form(...),
        last_name: str = Form(...),
        gender: str = Form(...),
        date_of_birth: date = Form(...),
        info: str = Form(""),
        avatar: UploadFile = File(...),
    ) -> "ProfileRequestSchema":
        try:
            return cls(
                first_name=first_name,
                last_name=last_name,
                gender=gender,
                date_of_birth=date_of_birth,
                info=info,
                avatar=avatar,
            )
        except ValidationError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
            )


class ProfileResponseSchema(ProfileBase):
    id: int
    user_id: int
    avatar: HttpUrl

    model_config = {"from_attributes": True}
