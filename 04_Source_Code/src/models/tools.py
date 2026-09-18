"""Sub-agent tool wrapper domain models."""
from pydantic import BaseModel, Field


class RetrieveDocumentsInput(BaseModel):
    """Validated input for the retrieve_documents tool call made by PR-04."""
    query: str = Field(description="Search query based on the ticket's core problem.")
