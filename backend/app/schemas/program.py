from datetime import datetime

from pydantic import BaseModel, Field

from app.agentic.models import ProgramDraft, ProgramStatus


class ChatProgramSchema(BaseModel):
    """State of the programme being built in a chat."""

    status: ProgramStatus = Field(
        description=(
            "draft while the agent is still collecting the source data, "
            "pending_approval once a draft is saved, approved after confirmation"
        )
    )
    draft: ProgramDraft | None = Field(default=None, description="The saved draft; null while nothing is saved")
    updated_at: datetime | None = Field(default=None, description="When the draft was saved or last revised")
