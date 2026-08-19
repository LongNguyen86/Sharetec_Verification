from typing import Optional, Union, Literal, Dict, Any
from pydantic import BaseModel, TypeAdapter

class JoinRuleModel(BaseModel):
    """Data model for Join operations between two tables"""
    source_file: str
    source_col: str
    target_file: str
    target_col: str


class SectionRuleDSL(BaseModel):
    """DSL schema for Section-level rules (Filters and Joins)"""
    action: Literal["SECTION_RULE"] = "SECTION_RULE"
    filter_condition: Optional[str] = None
    join_rule: Optional[Union[JoinRuleModel, Dict[str, Any], str]] = None
    raw_notes: Optional[str] = None


class ConditionalRuleDSL(BaseModel):
    """DSL schema for IF-THEN-ELSE conditional rules"""
    action: Literal["CONDITIONAL"] = "CONDITIONAL"
    if_col: str
    if_val: str
    then_val: str
    else_val: str
    raw_condition: Optional[str] = None


class DirectRuleDSL(BaseModel):
    """DSL schema for direct 1-to-1 column mapping"""
    action: Literal["DIRECT"] = "DIRECT"
    source_file: str
    source_column: str


class ConstantRuleDSL(BaseModel):
    """DSL schema for static constant values"""
    action: Literal["CONSTANT"] = "CONSTANT"
    value: str


class MatrixLookupRuleDSL(BaseModel):
    """DSL schema for matrix or table lookups"""
    action: Literal["MATRIX_LOOKUP"] = "MATRIX_LOOKUP"
    target_ref: str
    source_file: str
    source_column: str
    raw_notes: Optional[str] = None


class NoMappingRuleDSL(BaseModel):
    """DSL schema for empty/unmapped fields"""
    action: Literal["NONE"] = "NONE"
    reason: str = "UNMAPPED_FIELD"


class UnparsedRuleDSL(BaseModel):
    """DSL schema for complex notes needing review"""
    action: Literal["UNPARSED"] = "UNPARSED"
    raw_notes: Optional[str] = None


# Discriminated TypeAdapter for generic validation and parsing
RuleDSLUnion = Union[
    SectionRuleDSL,
    ConditionalRuleDSL,
    DirectRuleDSL,
    ConstantRuleDSL,
    MatrixLookupRuleDSL,
    NoMappingRuleDSL,
    UnparsedRuleDSL,
]

RuleDSLAdapter = TypeAdapter(RuleDSLUnion)