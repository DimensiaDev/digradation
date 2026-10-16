from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.auth import MAX_USERNAME_LENGTH, NAME_SURNAME_MAX_LENGTH, AccountRole
from app.models.settings import MAX_PROMPT_EXTENSION_LENGTH
from app.schemas import PasswordField


class AdminUserFilters(BaseModel):
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    role: AccountRole | None = None
    is_expired: bool | None = None


class AdminCreateUserSchema(BaseModel):
    username: str = Field(max_length=MAX_USERNAME_LENGTH)
    password: str
    first_name: str | None = Field(None, max_length=NAME_SURNAME_MAX_LENGTH)
    last_name: str | None = Field(None, max_length=NAME_SURNAME_MAX_LENGTH)
    expires_at: datetime | None = None
    role: AccountRole = AccountRole.USER


class AdminEditUserSchema(BaseModel):
    username: str | None = Field(None, max_length=MAX_USERNAME_LENGTH)
    first_name: str | None = Field(None, max_length=NAME_SURNAME_MAX_LENGTH)
    last_name: str | None = Field(None, max_length=NAME_SURNAME_MAX_LENGTH)
    expires_at: datetime | None = None
    role: AccountRole | None = None

    @field_validator("username", "role")
    @classmethod
    def reject_none(cls, value):
        if value is None:
            raise ValueError("Field cannot be None")
        return value


class AdminSettingsResponse(BaseModel):
    """The instance-wide settings, as stored.

    `last_update_at` is null until something has been saved: "never set" and "set
    to the empty extension" are different states and only the second one has a
    timestamp.
    """

    prompt_extension: str
    last_update_by: int | None
    last_update_at: datetime | None


class AdminSettingsUpdate(BaseModel):
    """A partial settings update.

    Every field is optional so a later option can be added to this payload without
    touching the ones already here, and the three states a field can be in are all
    meaningful:

    - **absent** — leave the stored value alone;
    - **`null`** — a 422, because it would be indistinguishable from absent while
      meaning the opposite;
    - **a value** — set it.

    The reset is the empty string rather than null: "no extension" is a perfectly
    good value, so `prompt_extension` never needs null.
    `model_fields_set` is what tells the route which fields the caller actually sent.

    `extra="forbid"` is load-bearing, not tidiness. The most likely mistake in this
    payload is misspelling a key or sending an option that does not exist, and with
    extras allowed that arrives silently: nothing is set, the audit fields are
    still stamped, and the caller gets a 200 that changed nothing. Rejecting them
    turns a silent no-op into a 422 naming the offending key.
    """

    model_config = ConfigDict(extra="forbid")

    prompt_extension: str | None = Field(None, max_length=MAX_PROMPT_EXTENSION_LENGTH)

    @field_validator("prompt_extension")
    @classmethod
    def reject_none(cls, value):
        if value is None:
            raise ValueError("Field cannot be None")
        return value


class AdminSetPasswordSchema(BaseModel):
    password: PasswordField
