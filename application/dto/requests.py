from pydantic import BaseModel

class OpcUaLoginRequest(BaseModel):
    user: str
    password: str
    url: str