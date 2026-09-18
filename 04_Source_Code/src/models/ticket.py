"""Ticket input domain model."""
from typing import Optional
from pydantic import BaseModel


class TicketInput(BaseModel):
    ticket_id: str
    channel: str
    subject: str = ""
    body: str
    received_at: str
    customer_id: str
    customer_name: str
    customer_tier: str
    customer_region: str
    language_fluency: Optional[str] = None  # fluent | non_fluent; if set, PR-02 is skipped
    urgency: Optional[str] = None           # high | medium | low; if set, PR-03 is skipped
