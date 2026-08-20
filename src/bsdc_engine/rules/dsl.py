from typing import Optional
from pydantic import BaseModel


class DirectRuleDSL(BaseModel):
    source_file: str = ""
    source_column: str = ""


class ConstantRuleDSL(BaseModel):
    value: str = ""


class ConditionalRuleDSL(BaseModel):
    if_col: Optional[str] = None
    if_val: Optional[str] = None
    then_val: Optional[str] = None
    else_val: Optional[str] = None
    raw_condition: str = ""


class MatrixLookupRuleDSL(BaseModel):
    target_ref: str = ""
    source_file: str = ""
    source_column: str = ""
    raw_notes: str = ""


class NoMappingRuleDSL(BaseModel):
    pass


class UnparsedRuleDSL(BaseModel):
    raw_notes: str = ""


class JoinRuleModel(BaseModel):
    source_file: str = ""
    source_col: str = ""
    target_file: str = ""
    target_col: str = ""


class SectionRuleDSL(BaseModel):
    filter_condition: Optional[str] = None
    join_rule: Optional[JoinRuleModel] = None
    raw_notes: str = ""