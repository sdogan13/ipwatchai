"""
Pydantic Models for API Request/Response
"""
from datetime import datetime, date
from typing import List, Optional, Dict, Any
from uuid import UUID
from enum import Enum

from pydantic import BaseModel, EmailStr, Field, validator


# ==========================================
# Enums
# ==========================================

class PlanType(str, Enum):
    FREE = "free"
    STARTER = "starter"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


class UserRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class AlertSeverity(str, Enum):
    CRITICAL = "critical"
    VERY_HIGH = "very_high"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AlertStatus(str, Enum):
    NEW = "new"
    SEEN = "seen"
    ACKNOWLEDGED = "acknowledged"
    DISPUTED = "disputed"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class AlertFrequency(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"


class DeadlineStatus(str, Enum):
    PRE_PUBLICATION = "pre_publication"
    ACTIVE_CRITICAL = "active_critical"
    ACTIVE_URGENT = "active_urgent"
    ACTIVE = "active"
    EXPIRED = "expired"
    REGISTERED = "registered"
    OPPOSED = "opposed"
    RESOLVED = "resolved"


class ApplicationStatus(str, Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"


class MarkType(str, Enum):
    WORD = "word"
    FIGURATIVE = "figurative"
    COMBINED = "combined"


class ApplicationType(str, Enum):
    REGISTRATION = "registration"
    APPEAL = "appeal"
    RENEWAL = "renewal"


class ReportType(str, Enum):
    WATCHLIST_SUMMARY = "watchlist_summary"
    ALERT_DIGEST = "alert_digest"
    RISK_ASSESSMENT = "risk_assessment"
    COMPETITOR_ANALYSIS = "competitor_analysis"
    PORTFOLIO_STATUS = "portfolio_status"
    CUSTOM = "custom"


class TrademarkStatus(str, Enum):
    APPLIED = "Applied"
    PUBLISHED = "Published"
    OPPOSED = "Opposed"
    REGISTERED = "Registered"
    REFUSED = "Refused"
    WITHDRAWN = "Withdrawn"
    TRANSFERRED = "Transferred"
    RENEWED = "Renewed"
    PARTIAL_REFUSAL = "Partial Refusal"
    EXPIRED = "Expired"
    UNKNOWN = "Unknown"


# ==========================================
# Organization Models
# ==========================================

class OrganizationBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    phone: Optional[str] = None
    address: Optional[str] = None


class OrganizationCreate(OrganizationBase):
    slug: str = Field(..., min_length=2, max_length=100, pattern=r'^[a-z0-9-]+$')
    email: Optional[EmailStr] = None


class OrganizationUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None


class OrganizationResponse(OrganizationBase):
    id: UUID
    slug: Optional[str] = None
    is_active: bool = True
    created_at: Optional[datetime] = None
    # Plan details resolved from subscription_plans join
    plan: Optional[PlanType] = None
    max_users: Optional[int] = None
    max_watchlist_items: Optional[int] = None
    max_monthly_searches: Optional[int] = None

    class Config:
        from_attributes = True


class OrganizationStats(BaseModel):
    """Organization dashboard statistics"""
    user_count: int
    active_watchlist_items: int
    new_alerts: int
    critical_alerts: int
    searches_this_month: int
    storage_used_mb: float


# ==========================================
# User Models
# ==========================================

class UserBase(BaseModel):
    email: EmailStr
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    phone: Optional[str] = None
    avatar_url: Optional[str] = None


class UserCreate(UserBase):
    password: str = Field(..., min_length=8)
    role: UserRole = UserRole.MEMBER


class UserUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    title: Optional[str] = None
    department: Optional[str] = None
    linkedin: Optional[str] = None
    avatar_url: Optional[str] = None
    password_hash: Optional[str] = None


class UserResponse(UserBase):
    id: UUID
    organization_id: UUID
    role: UserRole
    is_active: bool
    is_verified: bool = False
    is_superadmin: bool = False
    last_login_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class UserProfile(UserResponse):
    """Extended user profile with organization info"""
    organization: OrganizationResponse
    permissions: List[str]


# ==========================================
# Watchlist Models
# ==========================================

class WatchlistItemBase(BaseModel):
    brand_name: str = Field(..., min_length=1, max_length=500)
    nice_class_numbers: List[int] = Field(..., min_items=1)
    description: Optional[str] = None

    # Optional trademark details - user's own registered trademark
    # These help with self-conflict filtering (exclude user's own marks from alerts)
    application_no: Optional[str] = None  # User's own application number (saved as customer_application_no in DB)
    bulletin_no: Optional[str] = None  # User's trademark publication bulletin number (saved as customer_bulletin_no in DB)
    registration_no: Optional[str] = None
    application_date: Optional[date] = None
    
    # Monitoring settings
    similarity_threshold: float = Field(default=0.70, ge=0.0, le=1.0)
    monitor_text: bool = True
    monitor_visual: bool = True
    monitor_phonetic: bool = True
    
    # Alert settings
    alert_frequency: AlertFrequency = AlertFrequency.DAILY
    alert_email: bool = True
    alert_webhook: bool = False
    webhook_url: Optional[str] = None
    
    @validator('nice_class_numbers')
    def validate_nice_classes(cls, v):
        for c in v:
            if c < 1 or c > 45:
                raise ValueError(f"Nice class {c} must be between 1 and 45")
        return list(set(v))  # Remove duplicates
    
    @validator('webhook_url')
    def validate_webhook(cls, v, values):
        if values.get('alert_webhook') and not v:
            raise ValueError("Webhook URL required when webhook alerts enabled")
        return v


class WatchlistItemCreate(WatchlistItemBase):
    pass


class WatchlistItemUpdate(BaseModel):
    brand_name: Optional[str] = None
    nice_class_numbers: Optional[List[int]] = None
    description: Optional[str] = None
    application_no: Optional[str] = None  # Customer's own application number
    bulletin_no: Optional[str] = None  # Customer's bulletin number
    similarity_threshold: Optional[float] = None
    monitor_text: Optional[bool] = None
    monitor_visual: Optional[bool] = None
    monitor_phonetic: Optional[bool] = None
    alert_frequency: Optional[AlertFrequency] = None
    alert_email: Optional[bool] = None
    alert_webhook: Optional[bool] = None
    webhook_url: Optional[str] = None
    is_active: Optional[bool] = None


class WatchlistItemResponse(WatchlistItemBase):
    id: UUID
    organization_id: UUID
    user_id: Optional[UUID]
    is_active: bool
    last_scan_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    # Override to map DB column names to API field names
    application_no: Optional[str] = Field(None, validation_alias='customer_application_no')
    bulletin_no: Optional[str] = Field(None, validation_alias='customer_bulletin_no')
    registration_no: Optional[str] = Field(None, validation_alias='customer_registration_no')
    application_date: Optional[date] = Field(None, validation_alias='customer_registration_date')

    # Logo (logo_path is internal, excluded from JSON via model serialization)
    logo_path: Optional[str] = Field(None, exclude=True)
    has_logo: bool = False
    logo_url: Optional[str] = None

    # Computed fields
    new_alerts_count: Optional[int] = 0
    total_alerts_count: Optional[int] = 0
    conflict_summary: Optional[Dict[str, Any]] = None

    @validator('has_logo', pre=True, always=True)
    def compute_has_logo(cls, v, values):
        if v:
            return v
        return bool(values.get('logo_path'))

    @validator('logo_url', pre=True, always=True)
    def compute_logo_url(cls, v, values):
        if v:
            return v
        item_id = values.get('id')
        logo_path = values.get('logo_path')
        if item_id and logo_path:
            return f"/api/v1/watchlist/{item_id}/logo"
        return None

    class Config:
        from_attributes = True
        populate_by_name = True  # Allow both field name and alias


class WatchlistBulkImport(BaseModel):
    """Bulk import watchlist items from CSV"""
    items: List[WatchlistItemCreate]


class WatchlistBulkImportResult(BaseModel):
    total: int
    created: int
    failed: int
    errors: List[Dict[str, Any]]
    limit_reached: bool = False
    max_allowed: int = 0
    current_count: int = 0


class FileUploadWarning(BaseModel):
    """Warning about optional columns"""
    column: str
    message: str


class FileUploadSkippedItem(BaseModel):
    """Item skipped during import"""
    row: int
    brand_name: Optional[str] = None
    application_no: Optional[str] = None
    reason: str


class FileUploadErrorItem(BaseModel):
    """Item with error during import"""
    row: int
    brand_name: Optional[str] = None
    error: str


class FileUploadSummary(BaseModel):
    """Summary of file upload results"""
    total_rows: int
    added: int
    skipped: int
    errors: int


class FileUploadResult(BaseModel):
    """File upload result"""
    success: bool
    message: str
    summary: FileUploadSummary
    warnings: List[FileUploadWarning] = []
    skipped_items: List[FileUploadSkippedItem] = []
    error_items: List[FileUploadErrorItem] = []


class MissingColumnInfo(BaseModel):
    """Info about a missing mandatory column"""
    column: str
    variants: str
    reason: str


class RequiredColumnInfo(BaseModel):
    """Info about required columns"""
    name: str
    variants: str


class FileUploadExample(BaseModel):
    """Example format for file upload"""
    headers: List[str]
    rows: List[List[str]]


class MissingColumnsError(BaseModel):
    """Error detail for missing mandatory columns"""
    error: str = "missing_mandatory_columns"
    message: str
    missing_columns: List[MissingColumnInfo]
    found_columns: List[str]
    required_columns: List[RequiredColumnInfo]
    optional_columns: List[RequiredColumnInfo]
    example: FileUploadExample


class ColumnAutoMappings(BaseModel):
    """Auto-detected column mappings"""
    brand_name: Optional[str] = None
    application_no: Optional[str] = None
    nice_classes: Optional[str] = None
    bulletin_no: Optional[str] = None


class ColumnDetectionResponse(BaseModel):
    """Response for column detection endpoint"""
    columns: List[str]
    sample_data: List[Dict[str, Any]]
    auto_mappings: ColumnAutoMappings
    total_rows: int = 0
    required_fields: List[str] = ["brand_name"]
    optional_fields: List[str] = ["application_no", "nice_classes", "bulletin_no"]


class ColumnMapping(BaseModel):
    """User-provided column mappings"""
    brand_name: str
    application_no: str
    nice_classes: str
    bulletin_no: Optional[str] = None


# ==========================================
# Alert Models
# ==========================================

class ConflictingTrademark(BaseModel):
    """Details of the conflicting trademark"""
    id: Optional[UUID]
    name: str
    application_no: str
    status: TrademarkStatus
    classes: List[int]
    holder: Optional[str]
    holder_tpe_client_id: Optional[str] = None
    attorney_name: Optional[str] = None
    attorney_no: Optional[str] = None
    registration_no: Optional[str] = None
    image_path: Optional[str]
    application_date: Optional[date] = None
    has_extracted_goods: bool = False


class AlertScores(BaseModel):
    """Similarity scores breakdown"""
    total: float
    text_similarity: Optional[float]
    semantic_similarity: Optional[float]
    visual_similarity: Optional[float]
    translation_similarity: Optional[float] = None
    phonetic_match: bool = False


class AlertResponse(BaseModel):
    id: UUID
    organization_id: UUID
    watchlist_id: UUID

    # Watched item info
    watched_brand_name: Optional[str]
    watchlist_bulletin_no: Optional[str] = None  # User's portfolio bulletin
    watchlist_application_no: Optional[str] = None  # User's application number
    watchlist_classes: Optional[List[int]] = None  # User's Nice classes

    # Conflicting trademark
    conflicting: ConflictingTrademark
    conflict_bulletin_no: Optional[str] = None  # Conflicting trademark's bulletin

    # Class overlap info - which classes both trademarks share
    overlapping_classes: Optional[List[int]] = None

    # Scores
    scores: AlertScores

    # Alert metadata
    severity: AlertSeverity
    status: AlertStatus

    # Source
    source_type: Optional[str]
    source_reference: Optional[str]
    source_date: Optional[date]

    # Deadline (from conflicting trademark's bulletin_date + 2 months)
    appeal_deadline: Optional[date] = None
    conflict_bulletin_date: Optional[date] = None
    deadline_status: Optional[str] = None
    deadline_days_remaining: Optional[int] = None
    deadline_label: Optional[str] = None
    deadline_urgency: Optional[str] = None

    # Timeline
    detected_at: datetime
    seen_at: Optional[datetime]
    acknowledged_at: Optional[datetime]
    resolved_at: Optional[datetime]
    resolution_notes: Optional[str]

    class Config:
        from_attributes = True


class AlertUpdate(BaseModel):
    status: Optional[AlertStatus] = None
    resolution_notes: Optional[str] = None


class AlertAcknowledge(BaseModel):
    notes: Optional[str] = None


class AlertResolve(BaseModel):
    resolution_notes: str


class AlertDismiss(BaseModel):
    reason: str


class AlertDigest(BaseModel):
    """Summary of alerts for digest notifications"""
    period_start: datetime
    period_end: datetime
    total_alerts: int
    by_severity: Dict[str, int]
    by_watchlist_item: List[Dict[str, Any]]
    critical_alerts: List[AlertResponse]


# ==========================================
# Report Models
# ==========================================

class ReportRequest(BaseModel):
    report_type: ReportType
    title: Optional[str] = None
    description: Optional[str] = None
    
    # Filters
    watchlist_ids: Optional[List[UUID]] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    include_resolved: bool = False
    
    # Output
    file_format: str = Field(default="pdf", pattern=r'^(pdf|xlsx|csv)$')


class ReportResponse(BaseModel):
    id: UUID
    organization_id: UUID
    report_type: ReportType
    title: Optional[str]
    status: str
    file_path: Optional[str]
    file_format: str
    file_size_bytes: Optional[int]
    generated_at: Optional[datetime]
    created_at: datetime
    
    class Config:
        from_attributes = True


class ScheduledReportCreate(BaseModel):
    report_type: ReportType
    title: str
    schedule_cron: str = Field(..., pattern=r'^(\*|[0-9,\-\/]+)\s+(\*|[0-9,\-\/]+)\s+(\*|[0-9,\-\/]+)\s+(\*|[0-9,\-\/]+)\s+(\*|[0-9,\-\/]+)$')
    parameters: Optional[Dict[str, Any]] = None


# ==========================================
# Search & Analysis Models
# ==========================================

class RiskAnalysisRequest(BaseModel):
    """Request for trademark risk analysis"""
    name: str = Field(..., min_length=1)
    classes: Optional[List[int]] = None
    description: Optional[str] = None
    # Image handled separately via file upload


class RiskAnalysisResponse(BaseModel):
    """Response from risk analysis"""
    query: Dict[str, Any]
    auto_suggested_classes: List[Dict[str, Any]]
    final_risk_score: float
    top_candidates: List[Dict[str, Any]]
    source: str
    job_id: Optional[str] = None


class SearchHistoryResponse(BaseModel):
    """Search history entry"""
    id: UUID
    query_name: str
    query_classes: List[int]
    risk_score: float
    candidate_count: int
    searched_at: datetime


# ==========================================
# Activity & Audit Models
# ==========================================

class ActivityLogResponse(BaseModel):
    id: UUID
    user_id: Optional[UUID]
    user_email: Optional[str]
    action: str
    resource_type: Optional[str]
    resource_id: Optional[UUID]
    description: Optional[str]
    ip_address: Optional[str]
    created_at: datetime


# ==========================================
# Pagination & Common Models
# ==========================================

class PaginationParams(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class PaginatedResponse(BaseModel):
    items: List[Any]
    total: int
    page: int
    page_size: int
    total_pages: int


class SuccessResponse(BaseModel):
    success: bool = True
    message: str


class ErrorResponse(BaseModel):
    success: bool = False
    error: str
    detail: Optional[Any] = None


# ==========================================
# Dashboard Models
# ==========================================

class DashboardStats(BaseModel):
    """Main dashboard statistics"""
    watchlist_count: int
    active_watchlist: int
    total_alerts: int
    new_alerts: int
    critical_alerts: int
    alerts_this_week: int
    searches_this_month: int
    plan_usage: Dict[str, Any]


class AlertTrendData(BaseModel):
    """Alert trends over time"""
    date: date
    total: int
    critical: int
    high: int
    medium: int
    low: int


class WatchlistStatusSummary(BaseModel):
    """Summary of watchlist item status"""
    id: UUID
    brand_name: str
    nice_classes: List[int]
    alert_count: int
    latest_alert_severity: Optional[AlertSeverity]
    risk_level: str  # safe, warning, danger


# ==========================================
# Creative Suite - Name Generator
# ==========================================

class NameSuggestionRequest(BaseModel):
    """Request for AI-powered name suggestions"""
    query: str = Field(..., min_length=1, max_length=200, description="Original brand name or concept")
    nice_classes: List[int] = Field(default=[], description="Nice classes to check against")
    industry: str = Field(default="", max_length=200, description="Industry description for context")
    style: str = Field(default="modern", description="Naming style: modern, classic, playful, technical")
    language: str = Field(default="tr", description="Primary language preference: tr, en")
    avoid_names: List[str] = Field(default=[], description="Names to explicitly avoid")

    @validator("nice_classes", each_item=True)
    def validate_nice_classes(cls, v):
        if v < 1 or v > 99:
            raise ValueError("Nice class must be between 1 and 99")
        return v


class SafeNameResult(BaseModel):
    """A single validated name suggestion with risk assessment"""
    name: str
    risk_score: float                           # 0-100, from risk_engine
    text_similarity: float                      # vs closest existing trademark
    semantic_similarity: float
    phonetic_match: bool                        # double metaphone collision
    closest_match: Optional[str] = None         # name of closest existing trademark
    is_safe: bool                               # True if passes all thresholds
    translation_similarity: float = 0.0         # cross-language similarity (0-1)
    risk_level: str = "low"                     # critical/very_high/high/medium/low


class NameSuggestionResponse(BaseModel):
    """Response from the name suggestion endpoint"""
    safe_names: List[SafeNameResult]
    filtered_count: int                         # how many were generated but failed validation
    total_generated: int
    session_count: int                          # cumulative names in this session
    credits_remaining: Dict[str, Any]           # { session_limit, used, purchased }
    cached: bool = False                        # True if served from Redis cache


# ==========================================
# Creative Suite - Logo Studio
# ==========================================

class LogoGenerationRequest(BaseModel):
    """Request for AI-powered logo generation"""
    brand_name: str = Field(..., min_length=1, max_length=200, description="Text to render in the logo")
    description: str = Field(default="", max_length=500, description="Visual description / style guide")
    style: str = Field(default="modern", description="Logo style: modern, classic, minimal, bold, playful")
    nice_classes: List[int] = Field(default=[], description="Nice classes for targeted similarity search")
    color_preferences: str = Field(default="", max_length=200, description="Color preferences, e.g. 'blue and white'")

    @validator("nice_classes", each_item=True)
    def validate_nice_classes(cls, v):
        if v < 1 or v > 99:
            raise ValueError("Nice class must be between 1 and 99")
        return v


class LogoResult(BaseModel):
    """A single generated logo with visual similarity audit"""
    image_id: str                                       # UUID from generated_images table
    image_url: str                                      # URL to serve the generated image
    similarity_score: float                             # max similarity vs existing trademarks (0-100)
    closest_match_name: Optional[str] = None            # name of most similar existing trademark
    closest_match_image_url: Optional[str] = None       # image URL of the closest match
    is_safe: bool                                       # True if similarity < 70% threshold
    visual_breakdown: Optional[Dict[str, Any]] = None   # {"clip": 0.45, "dino": 0.38, "ocr": 0.0, ...}


class LogoGenerationResponse(BaseModel):
    """Response from the logo generation endpoint"""
    logos: List[LogoResult]
    credits_remaining: Dict[str, Any]                   # { monthly: N, purchased: N }
    generation_id: str                                  # UUID of the generation_logs entry


# ==========================================
# Creative Suite - Generation History
# ==========================================

class GenerationHistoryItem(BaseModel):
    """A single generation log entry"""
    id: str
    feature_type: str                                   # NAME or LOGO
    input_params: Optional[Dict[str, Any]] = None
    output_data: Optional[Dict[str, Any]] = None
    credits_used: int = 1
    created_at: datetime
    images: Optional[List[Dict[str, Any]]] = None       # For LOGO: generated image details


class GenerationHistoryResponse(BaseModel):
    """Paginated generation history"""
    items: List[GenerationHistoryItem]
    total: int
    page: int
    per_page: int
    total_pages: int


# ==========================================
# Trademark Application Models
# ==========================================

class TrademarkApplicationCreate(BaseModel):
    """Create a new trademark application"""
    application_type: ApplicationType = ApplicationType.REGISTRATION
    brand_name: str = Field(..., min_length=1, max_length=500)
    mark_type: MarkType = MarkType.WORD
    nice_class_numbers: List[int] = Field(default=[])
    goods_services_description: Optional[str] = None

    # Applicant info (optional at draft stage)
    applicant_full_name: Optional[str] = None
    applicant_id_no: Optional[str] = None
    applicant_id_type: Optional[str] = Field(default="tc_kimlik", pattern=r'^(tc_kimlik|vergi_no)$')
    applicant_address: Optional[str] = None
    applicant_phone: Optional[str] = None
    applicant_email: Optional[EmailStr] = None

    notes: Optional[str] = None

    # Context from search
    source_search_query: Optional[str] = None
    source_risk_score: Optional[float] = None

    @validator('nice_class_numbers', each_item=True)
    def validate_nice_classes(cls, v):
        if v < 1 or v > 45:
            raise ValueError(f"Nice class {v} must be between 1 and 45")
        return v


class TrademarkApplicationUpdate(BaseModel):
    """Update a draft application"""
    application_type: Optional[ApplicationType] = None
    brand_name: Optional[str] = None
    mark_type: Optional[MarkType] = None
    nice_class_numbers: Optional[List[int]] = None
    goods_services_description: Optional[str] = None

    applicant_full_name: Optional[str] = None
    applicant_id_no: Optional[str] = None
    applicant_id_type: Optional[str] = None
    applicant_address: Optional[str] = None
    applicant_phone: Optional[str] = None
    applicant_email: Optional[str] = None

    notes: Optional[str] = None


class TrademarkApplicationResponse(BaseModel):
    """Response model for a trademark application"""
    id: UUID
    organization_id: UUID
    user_id: UUID
    status: ApplicationStatus
    application_type: ApplicationType = ApplicationType.REGISTRATION
    brand_name: str
    mark_type: MarkType
    nice_class_numbers: List[int]
    goods_services_description: Optional[str] = None

    applicant_full_name: Optional[str] = None
    applicant_id_no: Optional[str] = None
    applicant_id_type: Optional[str] = None
    applicant_address: Optional[str] = None
    applicant_phone: Optional[str] = None
    applicant_email: Optional[str] = None

    notes: Optional[str] = None
    specialist_notes: Optional[str] = None
    rejection_reason: Optional[str] = None

    assigned_specialist_id: Optional[UUID] = None
    turkpatent_application_no: Optional[str] = None
    turkpatent_filing_date: Optional[date] = None

    source_search_query: Optional[str] = None
    source_risk_score: Optional[float] = None

    logo_path: Optional[str] = Field(None, exclude=True)
    has_logo: bool = False
    logo_url: Optional[str] = None

    created_at: datetime
    updated_at: datetime
    submitted_at: Optional[datetime] = None
    reviewed_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    @validator('has_logo', pre=True, always=True)
    def compute_has_logo(cls, v, values):
        if v:
            return v
        return bool(values.get('logo_path'))

    @validator('logo_url', pre=True, always=True)
    def compute_logo_url(cls, v, values):
        if v:
            return v
        app_id = values.get('id')
        logo_path = values.get('logo_path')
        if app_id and logo_path:
            return f"/api/v1/applications/{app_id}/logo"
        return None

    class Config:
        from_attributes = True
