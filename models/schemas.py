"""
Pydantic Models for API Request/Response
"""
from datetime import datetime, date
from typing import List, Optional, Dict, Any, Literal
from uuid import UUID
from enum import Enum

from pydantic import BaseModel, EmailStr, Field, validator, root_validator


def _is_custom_watchlist_logo_path(logo_path: Optional[str]) -> bool:
    if not logo_path:
        return False
    normalized = str(logo_path).replace("\\", "/").strip("/").lower()
    return "/watchlist_logos/" in f"/{normalized}/"


# ==========================================
# Enums
# ==========================================

class PlanType(str, Enum):
    FREE = "free"
    STARTER = "starter"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


class UserRole(str, Enum):
    ADMIN = "admin"
    USER = "user"
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
    role: UserRole = UserRole.USER


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
    # DB stores threshold as 'alert_threshold'; schema exposes it as 'similarity_threshold'
    similarity_threshold: float = Field(default=0.70, validation_alias='alert_threshold', ge=0.0, le=1.0)
    application_no: Optional[str] = Field(None, validation_alias='customer_application_no')
    bulletin_no: Optional[str] = Field(None, validation_alias='customer_bulletin_no')
    registration_no: Optional[str] = Field(None, validation_alias='customer_registration_no')
    application_date: Optional[date] = Field(None, validation_alias='customer_registration_date')
    monitor_text: bool = Field(default=True, validation_alias='monitor_similar_names')
    monitor_visual: bool = Field(default=True, validation_alias='monitor_similar_logos')

    # Logo (logo_path is internal, excluded from JSON via model serialization)
    logo_path: Optional[str] = Field(None, exclude=True)
    has_logo: bool = False
    logo_url: Optional[str] = None
    has_custom_logo: bool = False
    custom_logo_url: Optional[str] = None

    # Computed fields
    new_alerts_count: Optional[int] = 0
    total_alerts_count: Optional[int] = 0
    conflict_summary: Optional[Dict[str, Any]] = None
    true_application_date: Optional[date] = None
    trademark_image_path: Optional[str] = None  # image_path from trademarks table (for /api/trademark-image/ endpoint)
    trademark_status: Optional[str] = None       # final_status from trademarks table
    needs_renewal: bool = False        # expiry already passed (days_until_expiry <= 0)
    renewal_approaching: bool = False  # expiry within 12 months (0 < days <= 365)
    expiry_date: Optional[date] = None
    days_until_expiry: Optional[int] = None

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

    @validator('has_custom_logo', pre=True, always=True)
    def compute_has_custom_logo(cls, v, values):
        if v:
            return v
        return _is_custom_watchlist_logo_path(values.get('logo_path'))

    @validator('custom_logo_url', pre=True, always=True)
    def compute_custom_logo_url(cls, v, values):
        if v:
            return v
        item_id = values.get('id')
        if item_id and values.get('has_custom_logo'):
            return f"/api/v1/watchlist/{item_id}/logo"
        return None

    @root_validator(pre=True)
    def compute_renewal(cls, values):
        from datetime import timedelta
        today = datetime.now().date()

        def _set_from_expiry(expiry):
            values['expiry_date'] = expiry
            days = (expiry - today).days
            values['days_until_expiry'] = days
            values['needs_renewal'] = days <= 0
            values['renewal_approaching'] = 0 < days <= 365

        app_date = values.get('true_application_date')
        if app_date:
            try:
                ten_yr = app_date.replace(year=app_date.year + 10)
            except ValueError:
                ten_yr = app_date + timedelta(days=3652)
            expiry = ten_yr + timedelta(days=183)  # 6-month renewal grace window
            _set_from_expiry(expiry)
        else:
            # Fallback: derive from application number year
            app_no = values.get('customer_application_no') or values.get('application_no')
            if app_no and isinstance(app_no, str) and len(app_no) >= 4:
                try:
                    year_str = app_no[:4]
                    if year_str.isdigit():
                        # +10y +6m approximated as July 1st of the 10th anniversary year
                        approx_expiry = date(int(year_str) + 10, 7, 1)
                        _set_from_expiry(approx_expiry)
                except Exception:
                    pass
        return values

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
    skipped: int = 0
    errors: List[Dict[str, Any]]
    limit_reached: bool = False
    max_allowed: int = 0
    current_count: int = 0
    queued_scans: int = 0


class PortfolioPreviewRequest(BaseModel):
    holder_id: Optional[str] = None
    attorney_no: Optional[str] = None


class PortfolioPreviewResponse(BaseModel):
    total_items: int
    duplicate_count: int
    can_add: int


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
    queued_scans: int = 0


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
    status: Optional[str]  # DB stores Turkish values; keep as str for display
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
    text_idf_score: Optional[float] = None
    path_a_score: Optional[float] = None
    path_b_score: Optional[float] = None
    scoring_path_source: Optional[str] = None
    decision_reason: Optional[str] = None
    textual_breakdown: Optional[Dict[str, Any]] = None
    visual_breakdown: Optional[Dict[str, Any]] = None


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


class SearchRiskReportCandidateInput(BaseModel):
    """Visible search result sent for advisory LLM risk reporting."""
    name: str = Field(..., min_length=1, max_length=300)
    application_no: Optional[str] = Field(None, max_length=80)
    status: Optional[str] = Field(None, max_length=120)
    status_code: Optional[str] = Field(None, max_length=80)
    nice_classes: List[int] = Field(default_factory=list)
    owner: Optional[str] = Field(None, max_length=300)
    attorney: Optional[str] = Field(None, max_length=300)
    image_url: Optional[str] = Field(None, max_length=500)
    deterministic_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    text_similarity: Optional[float] = Field(None, ge=0.0, le=1.0)
    visual_similarity: Optional[float] = Field(None, ge=0.0, le=1.0)
    phonetic_similarity: Optional[float] = Field(None, ge=0.0, le=1.0)
    translation_similarity: Optional[float] = Field(None, ge=0.0, le=1.0)
    scores: Optional[Dict[str, Any]] = None

    @validator("nice_classes", pre=True, always=True)
    def validate_nice_classes(cls, value):
        if not value:
            return []
        cleaned = []
        for item in value:
            try:
                class_no = int(item)
            except (TypeError, ValueError):
                continue
            if 1 <= class_no <= 45 and class_no not in cleaned:
                cleaned.append(class_no)
        return cleaned


class SearchRiskReportRequest(BaseModel):
    """Request to generate an advisory LLM risk report for visible results."""
    query: str = Field(default="", max_length=300)
    selected_classes: List[int] = Field(default_factory=list)
    language: Literal["tr", "en", "ar"] = "tr"
    image_used: bool = False
    results: List[SearchRiskReportCandidateInput] = Field(..., min_length=1, max_length=20)

    @validator("selected_classes", pre=True, always=True)
    def validate_selected_classes(cls, value):
        if not value:
            return []
        cleaned = []
        for item in value:
            try:
                class_no = int(item)
            except (TypeError, ValueError):
                continue
            if 1 <= class_no <= 45 and class_no not in cleaned:
                cleaned.append(class_no)
        return cleaned


class SearchRiskReportCandidate(BaseModel):
    """Advisory LLM risk assessment for one visible result."""
    input_index: int = Field(..., ge=1, le=20)
    name: str
    application_no: Optional[str] = None
    image_url: Optional[str] = Field(None, max_length=500)
    llm_risk_score: float = Field(..., ge=0.0, le=100.0)
    risk_level: Literal["critical", "high", "medium", "low"]
    reasons: List[str] = Field(default_factory=list)
    key_factors: List[str] = Field(default_factory=list)
    uncertainty: Literal["low", "medium", "high"] = "medium"


class SearchRiskReportResponse(BaseModel):
    """Validated advisory risk report returned to the dashboard."""
    query: str
    selected_classes: List[int]
    image_used: bool
    summary: str
    overall_risk_score: float = Field(..., ge=0.0, le=100.0)
    highest_risk_application_no: Optional[str] = None
    results: List[SearchRiskReportCandidate]
    model: str
    generated_at: datetime
    report_usage: Optional[Dict[str, Any]] = None
    report_id: Optional[str] = None
    report_download_url: Optional[str] = None
    claim_token: Optional[str] = None
    claim_expires_at: Optional[datetime] = None
    is_pending: bool = False
    credits_remaining: Optional[Dict[str, Any]] = None


class SearchRiskReportClaimRequest(BaseModel):
    """Request to attach a landing-page pending risk report to the logged-in user."""
    claim_token: str = Field(..., min_length=24, max_length=256)


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
    active_deadline_count: int = 0
    pre_publication_count: int = 0
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
# Education Models
# ==========================================

class EducationStats(BaseModel):
    """Aggregate counts for the education landing tab."""
    pdf_count: int
    flashcard_deck_count: int
    flashcard_card_count: int
    quiz_section_count: int
    question_count: int


class EducationPdfItem(BaseModel):
    """A downloadable PDF resource."""
    id: str
    title: str
    file_name: str
    file_size_bytes: int
    language: Optional[str] = None
    download_url: str


class EducationFlashcardDeckSummary(BaseModel):
    """Flashcard deck metadata."""
    id: str
    title: str
    card_count: int


class EducationFlashcardCard(BaseModel):
    """Single flashcard."""
    id: str
    front: str
    back: str
    category_title: Optional[str] = None


class EducationFlashcardDeckDetail(EducationFlashcardDeckSummary):
    """Full flashcard deck payload."""
    cards: List[EducationFlashcardCard]


class EducationQuizSectionSummary(BaseModel):
    """Quiz section metadata."""
    id: str
    title: str
    question_count: int


class EducationQuizOption(BaseModel):
    """Answer option for a quiz question."""
    id: str
    text: str
    short_feedback: Optional[str] = None


class EducationQuizQuestion(BaseModel):
    """Single quiz question."""
    id: str
    legacy_id: Optional[str] = None
    prompt: str
    options: List[EducationQuizOption]
    correct_option_id: Optional[str] = None
    explanation: Optional[str] = None
    summary: Optional[str] = None
    category_title: Optional[str] = None


class EducationQuizSectionDetail(EducationQuizSectionSummary):
    """Full quiz section payload."""
    questions: List[EducationQuizQuestion]


class EducationCategorySummary(BaseModel):
    """Category summary that ties together flashcards and quizzes."""
    id: str
    title: str
    flashcard_deck_id: Optional[str] = None
    flashcard_card_count: int = 0
    quiz_section_id: Optional[str] = None
    question_count: int = 0


class EducationCatalogResponse(BaseModel):
    """Public education landing data."""
    stats: EducationStats
    categories: List[EducationCategorySummary] = Field(default_factory=list)
    pdfs: List[EducationPdfItem]
    flashcard_decks: List[EducationFlashcardDeckSummary]
    quiz_sections: List[EducationQuizSectionSummary]


class EducationProgressItem(BaseModel):
    """Stored per-user progress for one education item."""
    item_type: str = Field(..., pattern=r"^(pdf|flashcard|quiz)$")
    item_key: str = Field(..., min_length=1, max_length=255)
    status: str = Field(default="not_started", pattern=r"^(not_started|in_progress|completed)$")
    percent_complete: int = Field(default=0, ge=0, le=100)
    progress_data: Dict[str, Any] = Field(default_factory=dict)
    completed_at: Optional[datetime] = None
    last_interacted_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class EducationProgressResponse(BaseModel):
    """Progress payload returned to the landing page."""
    items: List[EducationProgressItem]


class EducationProgressUpdate(BaseModel):
    """Upsert request for one education progress entry."""
    item_type: str = Field(..., pattern=r"^(pdf|flashcard|quiz)$")
    item_key: str = Field(..., min_length=1, max_length=255)
    status: str = Field(default="in_progress", pattern=r"^(not_started|in_progress|completed)$")
    percent_complete: int = Field(default=0, ge=0, le=100)
    progress_data: Dict[str, Any] = Field(default_factory=dict)


class EducationProgressSyncRequest(BaseModel):
    """Batch sync request used to merge local browser progress into the account."""
    items: List[EducationProgressUpdate] = Field(default_factory=list)


class EducationModerationUpdate(BaseModel):
    """Tester moderation update for one flashcard or quiz question."""
    item_type: str = Field(..., pattern=r"^(flashcard|quiz_question)$")
    item_id: str = Field(..., min_length=1, max_length=255)
    category_title: Optional[str] = Field(default=None, min_length=1, max_length=100)
    explanation: Optional[str] = None
    summary: Optional[str] = None
    deleted: Optional[bool] = None


class EducationModerationItem(BaseModel):
    """Stored tester moderation state for one education item."""
    item_type: str = Field(..., pattern=r"^(flashcard|quiz_question)$")
    item_id: str = Field(..., min_length=1, max_length=255)
    category_title: Optional[str] = None
    explanation: Optional[str] = None
    summary: Optional[str] = None
    deleted: bool = False


# ==========================================
# Creative Suite - Name Generator
# ==========================================

class NameSuggestionRequest(BaseModel):
    """Request for AI-powered name suggestions"""
    query: str = Field(..., min_length=1, max_length=200, description="Original brand name or concept")
    nice_classes: List[int] = Field(..., min_length=1, description="Nice classes to check against")
    industry: str = Field(..., min_length=1, max_length=200, description="Industry description for context")
    style: Literal["modern", "classic", "playful", "technical"] = Field(..., description="Naming style")
    language: Literal["mixed", "tr", "en", "de", "it", "fr", "ar", "ku", "fa", "zh", "ru"] = Field(..., description="Name suggestion language preference")
    avoid_names: List[str] = Field(default=[], description="Names to explicitly avoid")

    @validator("query", "industry")
    def validate_required_text(cls, v):
        if not str(v or "").strip():
            raise ValueError("Field is required")
        return str(v).strip()

    @validator("nice_classes", each_item=True)
    def validate_nice_classes(cls, v):
        if v < 1 or v > 45:
            raise ValueError("Nice class must be between 1 and 45")
        return v


class SafeNameResult(BaseModel):
    """A single validated name suggestion with risk assessment"""
    name: str
    risk_score: float                           # 0-100 effective displayed risk score
    llm_risk_score: Optional[float] = None      # score-only AI Studio risk-report score (0-100)
    risk_source: Optional[str] = None           # risk_report_llm/hard_block
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
    style: Optional[Literal["modern", "classic", "bold", "playful"]] = Field(default=None, description="Logo style. When omitted on first-gen, the server fans out one candidate per canonical style. Revisions read the parent's style automatically.")
    nice_classes: List[int] = Field(default=[], description="Nice classes for targeted similarity search")
    color_preferences: str = Field(default="", max_length=200, description="Color preferences, e.g. 'blue and white'")
    project_id: Optional[str] = Field(default=None, description="Existing Logo Studio project/thread id for revisions")
    parent_image_id: Optional[str] = Field(default=None, description="Selected logo image id to revise")
    revision_prompt: str = Field(default="", max_length=800, description="Natural-language revision request")

    @validator("nice_classes", each_item=True)
    def validate_nice_classes(cls, v):
        if v < 1 or v > 45:
            raise ValueError("Nice class must be between 1 and 45")
        return v


class LogoResult(BaseModel):
    """A single generated logo with visual similarity audit"""
    image_id: str                                       # UUID from generated_images table
    image_url: str                                      # URL to serve the generated image
    similarity_score: float                             # backward-compatible overall risk vs existing trademarks (0-100)
    llm_risk_score: Optional[float] = None              # score-only AI Studio risk-report score (0-100)
    risk_source: Optional[str] = None                   # risk_report_llm
    closest_match_name: Optional[str] = None            # name of most similar existing trademark
    closest_match_image_url: Optional[str] = None       # image URL of the closest match
    is_safe: bool                                       # True if similarity < 70% threshold
    project_id: Optional[str] = None
    parent_image_id: Optional[str] = None
    variant_index: Optional[int] = None
    generation_kind: str = "INITIAL"
    revision_prompt: Optional[str] = None
    style: Optional[str] = None                         # canonical style this candidate represents
    audit_status: str = "completed"                     # pending/running/completed/failed
    audit_error: Optional[str] = None
    audited_at: Optional[datetime] = None


class LogoGenerationResponse(BaseModel):
    """Response from the logo generation endpoint"""
    logos: List[LogoResult]
    credits_remaining: Dict[str, Any]                   # { monthly: N, purchased: N }
    generation_id: str                                  # UUID of the generation_logs entry
    project_id: Optional[str] = None


class LogoProjectSelectRequest(BaseModel):
    """Mark an audited safe logo candidate as the selected project option."""
    image_id: str = Field(..., min_length=1)


class LogoProjectResponse(BaseModel):
    """Logo Studio project/thread with its generated candidates."""
    id: str
    org_id: str
    user_id: str
    brand_name: str
    description: str = ""
    style: str = "modern"
    nice_classes: List[int] = []
    color_preferences: str = ""
    selected_image_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    logos: List[LogoResult] = []


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
    """Create a new application (polymorphic across registries).

    Field name retained for backwards compatibility; covers trademark,
    design, patent, and cografi applications via registry_kind.
    `brand_name` is the generic primary display title (brand name for
    TM, design title for design, invention title for patent, GI name
    for cografi). `classification_codes` is the multi-registry
    classification list (NICE for TM, Locarno for design, IPC for
    patent, empty for cografi). `details` is a registry-specific
    extras JSON blob.
    """
    registry_kind: str = Field(default="trademark", pattern=r'^(trademark|design|patent|cografi)$')
    application_type: ApplicationType = ApplicationType.REGISTRATION
    brand_name: str = Field(..., min_length=1, max_length=500)
    mark_type: MarkType = MarkType.WORD
    nice_class_numbers: List[int] = Field(default=[])
    classification_codes: List[str] = Field(default=[])
    details: Dict[str, Any] = Field(default_factory=dict)
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

    # Opposition-specific fields (used when application_type == 'appeal')
    opposition_target_app_no: Optional[str] = None
    opposition_target_brand: Optional[str] = None
    opposition_target_holder: Optional[str] = None
    opposition_target_bulletin_no: Optional[str] = None
    opposition_target_bulletin_date: Optional[date] = None
    opposition_target_classes: List[int] = Field(default=[])
    opposition_grounds: Optional[str] = None

    @validator('nice_class_numbers', each_item=True)
    def validate_nice_classes(cls, v):
        if v < 1 or v > 45:
            raise ValueError(f"Nice class {v} must be between 1 and 45")
        return v


class TrademarkApplicationUpdate(BaseModel):
    """Update a draft application (polymorphic).

    `registry_kind` is immutable after creation, so it's deliberately
    omitted from Update.
    """
    application_type: Optional[ApplicationType] = None
    brand_name: Optional[str] = None
    mark_type: Optional[MarkType] = None
    nice_class_numbers: Optional[List[int]] = None
    classification_codes: Optional[List[str]] = None
    details: Optional[Dict[str, Any]] = None
    goods_services_description: Optional[str] = None

    applicant_full_name: Optional[str] = None
    applicant_id_no: Optional[str] = None
    applicant_id_type: Optional[str] = None
    applicant_address: Optional[str] = None
    applicant_phone: Optional[str] = None
    applicant_email: Optional[str] = None

    notes: Optional[str] = None

    # Opposition-specific fields
    opposition_target_app_no: Optional[str] = None
    opposition_target_brand: Optional[str] = None
    opposition_target_holder: Optional[str] = None
    opposition_target_bulletin_no: Optional[str] = None
    opposition_target_bulletin_date: Optional[date] = None
    opposition_target_classes: Optional[List[int]] = None
    opposition_grounds: Optional[str] = None


class TrademarkApplicationResponse(BaseModel):
    """Response model for an application (polymorphic across registries)."""
    id: UUID
    organization_id: UUID
    user_id: UUID
    status: ApplicationStatus
    registry_kind: str = "trademark"
    application_type: ApplicationType = ApplicationType.REGISTRATION
    brand_name: Optional[str] = None
    mark_type: MarkType
    nice_class_numbers: List[int]
    classification_codes: List[str] = Field(default=[])
    details: Dict[str, Any] = Field(default_factory=dict)
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

    # Opposition-specific fields
    opposition_target_app_no: Optional[str] = None
    opposition_target_brand: Optional[str] = None
    opposition_target_holder: Optional[str] = None
    opposition_target_bulletin_no: Optional[str] = None
    opposition_target_bulletin_date: Optional[date] = None
    opposition_target_classes: List[int] = Field(default=[])
    opposition_grounds: Optional[str] = None

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
