from typing import List, Literal, Optional, Any, Dict
from pydantic import BaseModel, Field
from dataclasses import dataclass

class UnsureEntry(BaseModel):
    path: str = Field(description="File or folder path relative to repository root")
    is_dir: bool = Field(description="True if this entry represents a directory, False for files")
    reason: str = Field(description="Explanation of why this entry's impact is uncertain")
    needed_info: Literal['file_overview', 'file_metadata', 'raw_content'] = Field(
        description="Type of additional information needed to make a confident decision about impact"
    )

class AnalysisResult(BaseModel):
    sure: List[str] = Field(description="List of file/folder paths that are definitely impacted by the changes")
    unsure: List[UnsureEntry] = Field(description="List of entries where impact is uncertain and requires further analysis")

class RefinementDecision(BaseModel):
    path: str = Field(description="File or folder path being evaluated relative to repository root")
    related: bool = Field(description="True if impacted by changes, False if not")
    confidence: Literal['high', 'medium', 'low'] = Field(description="Confidence level in the decision")
    reasoning: str = Field(description="Clear explanation of the decision and supporting evidence")

class RefineStats(BaseModel):
    path: str = Field(description="File or folder path that was refined relative to repository root")
    decision: str = Field(description="Final decision made during refinement ('related' or 'not_related')")
    confidence: str = Field(description="Confidence level of the refinement decision")
    reasoning: str = Field(description="Explanation of why this decision was made")

class ImpactAnalysis(BaseModel):
    impact: Literal['direct', 'inderect'] = Field(description="Whether the file was directly or indirectly impacted by the commit")
    impact_description: str = Field(description="Detailed explanation of how the file is impacted")

class UpdateAssessment(BaseModel):
    needs_update: bool = Field(description="Whether the file needs to be updated")
    update_rationale: str = Field(description="Explanation of why updates are or aren't needed")

class FixRecommendation(BaseModel):
    recommended_actions: List[str] = Field(description="List of specific actions to take")

class DetailedImpactReport(BaseModel):
    path: str = Field(description="Path to the file being analyzed relative to repository root")
    related: bool = Field(description="Whether the file is related to the changes")
    confidence: Literal['high', 'medium', 'low'] = Field(description="Confidence level in the analysis")
    analysis: ImpactAnalysis = Field(description="Analysis of how the file is impacted")
    diagnosis: UpdateAssessment = Field(description="Assessment of whether updates are needed")
    recommendations: FixRecommendation = Field(description="Specific recommendations for fixes")

@dataclass
class ChatConfig:
    """Vendor-agnostic chat configuration"""
    system_instruction: str
    temperature: float = 0.0
    response_format: str = "json"
    response_schema: Optional[type] = None
    max_tokens: Optional[int] = None
    timeout: Optional[int] = None

@dataclass
class ChatMessage:
    """Vendor-agnostic message representation"""
    content: str
    role: str = "user"
    metadata: Optional[Dict[str, Any]] = None

@dataclass
class ChatResponse:
    """Vendor-agnostic response representation"""
    content: str
    parsed: Optional[Any] = None
    metadata: Optional[Dict[str, Any]] = None
    usage: Optional[Dict[str, Any]] = None
