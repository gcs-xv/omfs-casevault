from typing import Any
from pydantic import BaseModel, Field

class ParseRequest(BaseModel): soap:str=Field(min_length=1,max_length=100_000)
class SaveRequest(BaseModel):
    patient:dict[str,Any]; visit:dict[str,Any]; soap:dict[str,Any]; procedures:list[str]=[]; diagnoses:list[str]=[]; residents:list[str]=[]; dpjp:dict[str,Any]|None=None; episode_id:str|None=None

