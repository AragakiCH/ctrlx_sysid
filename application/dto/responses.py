from pydantic import BaseModel

class OpcUaStatusResponse(BaseModel):
    authenticated: bool
    connected: bool
    url: str | None = None
    buffer_size: int = 0
    has_identification: bool = False