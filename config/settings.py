"""
Configuration Management
Centralized settings using Pydantic BaseSettings
"""
import os
import shutil
from pathlib import Path
from typing import List, Optional
from pydantic_settings import BaseSettings
from pydantic import Field, validator
from functools import lru_cache


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BULLETINS_ROOT = PROJECT_ROOT / "bulletins" / "Marka"
DEFAULT_UPLOAD_DIR = PROJECT_ROOT / "uploads"
DEFAULT_REPORT_DIR = DEFAULT_UPLOAD_DIR / "reports"
DEFAULT_LOG_DIR = PROJECT_ROOT / "logs"
DEFAULT_LOGO_OUTPUT_DIR = DEFAULT_UPLOAD_DIR / "generated" / "logos"
DEFAULT_WINDOWS_7Z_PATH = Path(
    os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles", r"C:\Program Files")
) / "7-Zip" / "7z.exe"


def _resolve_project_directory(value: str) -> str:
    """Resolve relative directory settings from the project root."""
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return str(path.resolve())


def _resolve_project_path_or_command(value: str) -> str:
    """Resolve project-relative paths, or normalize executable names via PATH lookup."""
    text = os.fspath(value).strip()
    if not text:
        return text

    path = Path(text).expanduser()
    if path.is_absolute():
        return str(path.resolve())

    if any(sep in text for sep in (os.sep, "/", "\\")):
        return str((PROJECT_ROOT / path).resolve())

    resolved = shutil.which(text)
    if resolved:
        return str(Path(resolved).resolve())

    return text


def _default_seven_zip_path() -> str:
    """Prefer PATH discovery first, then a conventional Windows install path."""
    for executable in ("7zz", "7z"):
        resolved = shutil.which(executable)
        if resolved:
            return str(Path(resolved).resolve())

    if DEFAULT_WINDOWS_7Z_PATH.exists():
        return str(DEFAULT_WINDOWS_7Z_PATH.resolve())

    return "7z"


DEFAULT_SEVEN_ZIP_PATH = _default_seven_zip_path()


class DatabaseSettings(BaseSettings):
    """PostgreSQL Database Configuration"""
    host: str = Field(default="127.0.0.1", alias="DB_HOST")
    port: int = Field(default=5432, alias="DB_PORT")
    name: str = Field(default="trademark_db", alias="DB_NAME")
    user: str = Field(default="turk_patent", alias="DB_USER")
    password: str = Field(alias="DB_PASSWORD")

    # Connection Pool
    pool_min_size: int = Field(default=5, alias="DB_POOL_MIN")
    pool_max_size: int = Field(default=20, alias="DB_POOL_MAX")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"
        populate_by_name = True

    @property
    def url(self) -> str:
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"

    @property
    def async_url(self) -> str:
        return f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"


class RedisSettings(BaseSettings):
    """Redis Configuration"""
    host: str = Field(default="localhost", alias="REDIS_HOST")
    port: int = Field(default=6379, alias="REDIS_PORT")
    password: Optional[str] = Field(default=None, alias="REDIS_PASSWORD")

    # Database numbers
    cache_db: int = Field(default=0, alias="REDIS_CACHE_DB")
    queue_db: int = Field(default=2, alias="REDIS_QUEUE_DB")
    session_db: int = Field(default=3, alias="REDIS_SESSION_DB")

    # Cache settings
    embedding_cache_ttl: int = Field(default=86400, alias="EMBEDDING_CACHE_TTL")  # 24 hours
    session_ttl: int = Field(default=604800, alias="SESSION_TTL")  # 7 days

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"
        populate_by_name = True

    def get_url(self, db: int = 0) -> str:
        if self.password:
            return f"redis://:{self.password}@{self.host}:{self.port}/{db}"
        return f"redis://{self.host}:{self.port}/{db}"


class AuthSettings(BaseSettings):
    """Authentication Configuration"""
    secret_key: str = Field(alias="AUTH_SECRET_KEY")
    algorithm: str = Field(default="HS256", alias="AUTH_ALGORITHM")

    # Token expiry
    access_token_expire_minutes: int = Field(default=30, alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    refresh_token_expire_days: int = Field(default=7, alias="REFRESH_TOKEN_EXPIRE_DAYS")

    # Password requirements
    password_min_length: int = Field(default=8, alias="PASSWORD_MIN_LENGTH")

    # Rate limiting
    login_rate_limit: int = Field(default=5, alias="LOGIN_RATE_LIMIT")  # per minute
    api_rate_limit: int = Field(default=60, alias="API_RATE_LIMIT")  # per minute

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"
        populate_by_name = True

    @validator("secret_key")
    def validate_secret_key(cls, v):
        if not v or len(v) < 32:
            raise ValueError(
                "FATAL: AUTH_SECRET_KEY must be at least 32 characters. "
                "Set a strong random secret in your .env file."
            )
        weak_defaults = {
            "your-super-secret-key-change-in-production",
            "changeme", "secret", "password", "test",
        }
        if v.lower() in weak_defaults:
            raise ValueError(
                "FATAL: AUTH_SECRET_KEY is set to a known weak default. "
                "Generate a strong random key: python -c \"import secrets; print(secrets.token_urlsafe(64))\""
            )
        return v


class AISettings(BaseSettings):
    """AI Model Configuration"""
    device: str = Field(default="cuda", alias="AI_DEVICE")

    # Batch sizes
    clip_batch_size: int = Field(default=64, alias="CLIP_BATCH_SIZE")
    dino_batch_size: int = Field(default=32, alias="DINO_BATCH_SIZE")
    text_batch_size: int = Field(default=256, alias="TEXT_BATCH_SIZE")

    # Precision
    use_fp16: bool = Field(default=True, alias="USE_FP16")
    use_tf32: bool = Field(default=True, alias="USE_TF32")

    # Model names
    clip_model: str = Field(default="ViT-B-32", alias="CLIP_MODEL")
    clip_pretrained: str = Field(default="laion2b_s34b_b79k", alias="CLIP_PRETRAINED")
    dino_model: str = Field(default="dinov2_vitb14", alias="DINO_MODEL")

    # Translation
    translation_backend: str = Field(default="madlad", alias="TRANSLATION_BACKEND")
    pipeline_translation_backend: str = Field(default="madlad", alias="PIPELINE_TRANSLATION_BACKEND")
    offline_translation_backend: str = Field(default="madlad", alias="OFFLINE_TRANSLATION_BACKEND")
    translation_model: str = Field(default="facebook/nllb-200-distilled-600M", alias="TRANSLATION_MODEL")
    madlad_translation_model: str = Field(default="google/madlad400-3b-mt", alias="MADLAD_TRANSLATION_MODEL")
    madlad_translate_batch_size: int = Field(default=16, alias="MADLAD_TRANSLATE_BATCH_SIZE")
    translation_device: str = Field(default="auto", alias="TRANSLATION_DEVICE")  # "auto", "cuda", "cpu"

    # OCR
    ocr_languages: list = Field(default=["en", "tr"], alias="OCR_LANGUAGES")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"
        populate_by_name = True


class MonitoringSettings(BaseSettings):
    """Watchlist Monitoring Configuration"""
    # Scanning
    scan_batch_size: int = Field(default=1000, alias="SCAN_BATCH_SIZE")
    default_similarity_threshold: float = Field(default=0.70, alias="DEFAULT_SIMILARITY_THRESHOLD")

    # Alerts
    critical_threshold: float = Field(default=0.90, alias="CRITICAL_THRESHOLD")
    high_threshold: float = Field(default=0.75, alias="HIGH_THRESHOLD")
    medium_threshold: float = Field(default=0.60, alias="MEDIUM_THRESHOLD")

    # Notifications
    digest_send_hour: int = Field(default=9, alias="DIGEST_SEND_HOUR")  # 9 AM
    digest_send_day: int = Field(default=1, alias="DIGEST_SEND_DAY")  # Monday

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"
        populate_by_name = True


class EmailSettings(BaseSettings):
    """Email Configuration"""
    smtp_host: str = Field(default="smtp.gmail.com", alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_user: str = Field(default="", alias="SMTP_USER")
    smtp_password: str = Field(default="", alias="SMTP_PASSWORD")
    smtp_tls: bool = Field(default=True, alias="SMTP_TLS")

    from_email: str = Field(default="noreply@ipwatchai.com", alias="FROM_EMAIL")
    from_name: str = Field(default="Trademark Risk System", alias="FROM_NAME")

    # Templates
    template_dir: str = Field(default="templates/email", alias="EMAIL_TEMPLATE_DIR")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"
        populate_by_name = True


class PathSettings(BaseSettings):
    """File Path Configuration"""
    data_root: str = Field(default=str(DEFAULT_BULLETINS_ROOT), alias="DATA_ROOT")
    upload_dir: str = Field(default=str(DEFAULT_UPLOAD_DIR), alias="UPLOAD_DIR")
    report_dir: str = Field(default=str(DEFAULT_REPORT_DIR), alias="REPORT_DIR")
    log_dir: str = Field(default=str(DEFAULT_LOG_DIR), alias="LOG_DIR")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"
        populate_by_name = True

    @validator("upload_dir", "report_dir", "log_dir", pre=True)
    def resolve_runtime_dir(cls, v):
        return _resolve_project_directory(v)

    @validator("data_root", pre=True)
    def resolve_data_root(cls, v):
        return _resolve_project_directory(v)


class CreativeSettings(BaseSettings):
    """Creative Suite Configuration (Name Generator + Logo Studio)"""
    # DeepSeek API (primary text provider for search risk reports)
    deepseek_api_key: str = Field(default="", alias="CREATIVE_DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(default="https://api.deepseek.com", alias="CREATIVE_DEEPSEEK_BASE_URL")
    deepseek_text_model: str = Field(default="deepseek-v4-pro", alias="CREATIVE_DEEPSEEK_TEXT_MODEL")
    deepseek_timeout: int = Field(default=120, alias="CREATIVE_DEEPSEEK_TIMEOUT")
    deepseek_max_retries: int = Field(default=2, alias="CREATIVE_DEEPSEEK_MAX_RETRIES")

    # Qwen API (primary text and multimodal provider for search risk reports)
    qwen_api_key: str = Field(default="", alias="CREATIVE_QWEN_API_KEY")
    qwen_base_url: str = Field(default="https://dashscope-intl.aliyuncs.com/compatible-mode/v1", alias="CREATIVE_QWEN_BASE_URL")
    qwen_text_model: str = Field(default="qwen-max", alias="CREATIVE_QWEN_TEXT_MODEL")
    qwen_class_model: str = Field(default="qwen-flash", alias="CREATIVE_QWEN_CLASS_MODEL")
    qwen_vl_model: str = Field(default="qwen3-vl-plus", alias="CREATIVE_QWEN_VL_MODEL")
    qwen_timeout: int = Field(default=120, alias="CREATIVE_QWEN_TIMEOUT")
    qwen_max_retries: int = Field(default=2, alias="CREATIVE_QWEN_MAX_RETRIES")

    # OpenAI API (primary image provider for Logo Studio)
    openai_api_key: str = Field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""), alias="CREATIVE_OPENAI_API_KEY")
    openai_image_model: str = Field(default="gpt-image-2", alias="CREATIVE_OPENAI_IMAGE_MODEL")
    openai_image_size: str = Field(default="1024x1024", alias="CREATIVE_OPENAI_IMAGE_SIZE")
    openai_image_quality: str = Field(default="high", alias="CREATIVE_OPENAI_IMAGE_QUALITY")
    openai_image_revision_quality: str = Field(default="high", alias="CREATIVE_OPENAI_IMAGE_REVISION_QUALITY")
    openai_image_background: str = Field(default="auto", alias="CREATIVE_OPENAI_IMAGE_BACKGROUND")
    openai_image_output_format: str = Field(default="png", alias="CREATIVE_OPENAI_IMAGE_OUTPUT_FORMAT")
    openai_timeout: int = Field(default=360, alias="CREATIVE_OPENAI_TIMEOUT")
    openai_max_retries: int = Field(default=2, alias="CREATIVE_OPENAI_MAX_RETRIES")

    # Gemini API
    google_api_key: str = Field(default="", alias="CREATIVE_GOOGLE_API_KEY")
    gemini_text_model: str = Field(default="gemini-2.5-pro", alias="CREATIVE_GEMINI_TEXT_MODEL")
    gemini_class_fallback_model: str = Field(default="gemini-2.5-flash-lite", alias="CREATIVE_GEMINI_CLASS_FALLBACK_MODEL")
    gemini_image_model: str = Field(default="gemini-3-pro-image-preview", alias="CREATIVE_GEMINI_IMAGE_MODEL")
    gemini_timeout: int = Field(default=30, alias="CREATIVE_GEMINI_TIMEOUT")
    gemini_max_retries: int = Field(default=2, alias="CREATIVE_GEMINI_MAX_RETRIES")

    # Name Generator
    name_batch_size: int = Field(default=10, alias="CREATIVE_NAME_BATCH_SIZE")
    name_similarity_threshold: float = Field(default=0.50, alias="CREATIVE_NAME_SIMILARITY_THRESHOLD")
    name_phonetic_check: bool = Field(default=True, alias="CREATIVE_NAME_PHONETIC_CHECK")

    # Logo Studio
    logo_images_per_run: int = Field(default=4, alias="CREATIVE_LOGO_IMAGES_PER_RUN")
    logo_revision_images_per_run: int = Field(default=1, alias="CREATIVE_LOGO_REVISION_IMAGES_PER_RUN")
    logo_similarity_threshold: float = Field(default=0.70, alias="CREATIVE_LOGO_SIMILARITY_THRESHOLD")
    logo_output_dir: str = Field(default=str(DEFAULT_LOGO_OUTPUT_DIR), alias="CREATIVE_LOGO_OUTPUT_DIR")

    # Redis
    generation_cache_db: int = Field(default=4, alias="CREATIVE_GENERATION_CACHE_DB")
    generation_cache_ttl: int = Field(default=3600, alias="CREATIVE_GENERATION_CACHE_TTL")

    class Config:
        env_prefix = "CREATIVE_"
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"
        populate_by_name = True

    @validator("logo_output_dir", pre=True)
    def resolve_logo_dir(cls, v):
        return _resolve_project_directory(v)


class PipelineSettings(BaseSettings):
    """Data Pipeline Configuration (collection → extraction → metadata → ingest)"""
    # Paths
    bulletins_root: str = Field(
        default=str(DEFAULT_BULLETINS_ROOT),
        alias="PIPELINE_BULLETINS_ROOT"
    )

    # data_collection.py
    turkpatent_url: str = Field(
        default="https://www.turkpatent.gov.tr/bultenler",
        alias="PIPELINE_TURKPATENT_URL"
    )
    categories: List[str] = Field(default=["Marka"], alias="PIPELINE_CATEGORIES")
    headless_browser: bool = Field(default=True, alias="PIPELINE_HEADLESS_BROWSER")
    download_timeout: int = Field(default=300, alias="PIPELINE_DOWNLOAD_TIMEOUT")
    incremental_lookback: int = Field(default=5, alias="PIPELINE_INCREMENTAL_LOOKBACK")
    recent_window_days: int = Field(default=60, alias="PIPELINE_RECENT_WINDOW_DAYS")
    min_gazette_issue_number: int = Field(default=300, alias="PIPELINE_MIN_GAZETTE_ISSUE_NUMBER")
    enable_ui_scrape: bool = Field(default=True, alias="PIPELINE_ENABLE_UI_SCRAPE")
    scrape_max_scroll_seconds: int = Field(default=0, alias="PIPELINE_SCRAPE_MAX_SCROLL_SECONDS")
    scrape_limit: int = Field(default=0, alias="PIPELINE_SCRAPE_LIMIT")

    # zip.py
    seven_zip_path: str = Field(
        default=DEFAULT_SEVEN_ZIP_PATH,
        alias="PIPELINE_SEVEN_ZIP_PATH"
    )
    max_cd_archives: int = Field(default=0, alias="PIPELINE_MAX_CD_ARCHIVES")
    skip_existing: bool = Field(default=True, alias="PIPELINE_SKIP_EXISTING")
    clean_after_extract: bool = Field(default=True, alias="PIPELINE_CLEAN_AFTER_EXTRACT")

    # metadata.py
    skip_if_metadata_exists: bool = Field(default=True, alias="PIPELINE_SKIP_IF_METADATA_EXISTS")
    canary_failure_threshold: float = Field(default=0.05, alias="PIPELINE_CANARY_FAILURE_THRESHOLD")

    # ai.py (embedding generation)
    embedding_batch_size: int = Field(default=64, alias="PIPELINE_EMBEDDING_BATCH_SIZE")
    skip_if_embeddings_exist: bool = Field(default=True, alias="PIPELINE_SKIP_IF_EMBEDDINGS_EXIST")
    generate_clip: bool = Field(default=True, alias="PIPELINE_GENERATE_CLIP")
    generate_dinov2: bool = Field(default=True, alias="PIPELINE_GENERATE_DINOV2")
    generate_text: bool = Field(default=True, alias="PIPELINE_GENERATE_TEXT")
    generate_color_histogram: bool = Field(default=True, alias="PIPELINE_GENERATE_COLOR_HISTOGRAM")
    generate_ocr: bool = Field(default=True, alias="PIPELINE_GENERATE_OCR")

    # Scheduling
    collection_schedule_day: str = Field(default="monday", alias="PIPELINE_COLLECTION_SCHEDULE_DAY")
    collection_schedule_hour: int = Field(default=3, alias="PIPELINE_COLLECTION_SCHEDULE_HOUR")
    pipeline_schedule_hour: int = Field(default=5, alias="PIPELINE_PIPELINE_SCHEDULE_HOUR")

    class Config:
        env_prefix = "PIPELINE_"
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"
        populate_by_name = True

    @validator("bulletins_root", pre=True)
    def resolve_bulletins_root(cls, v):
        return _resolve_project_directory(v)

    @validator("seven_zip_path", pre=True)
    def resolve_seven_zip_path(cls, v):
        if v is None or (isinstance(v, str) and not v.strip()):
            return DEFAULT_SEVEN_ZIP_PATH
        return _resolve_project_path_or_command(v)


class IyzicoSettings(BaseSettings):
    """iyzico Payment Gateway Configuration"""
    api_key: str = Field(default="", alias="IYZICO_API_KEY")
    secret_key: str = Field(default="", alias="IYZICO_SECRET_KEY")
    base_url: str = Field(default="https://sandbox-api.iyzipay.com", alias="IYZICO_BASE_URL")
    callback_url: str = Field(default="http://localhost:8000/api/v1/payments/callback", alias="IYZICO_CALLBACK_URL")
    webhook_url: str = Field(default="http://localhost:8000/api/v1/payments/webhook", alias="IYZICO_WEBHOOK_URL")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"
        populate_by_name = True


class StripeSettings(BaseSettings):
    """Stripe Payment Gateway Configuration"""
    secret_key: str = Field(default="", alias="STRIPE_SECRET_KEY")
    webhook_secret: str = Field(default="", alias="STRIPE_WEBHOOK_SECRET")
    success_url: str = Field(
        default="http://localhost:8000/dashboard?payment=success&session_id={CHECKOUT_SESSION_ID}",
        alias="STRIPE_SUCCESS_URL",
    )
    cancel_url: str = Field(default="http://localhost:8000/checkout?error=payment_cancelled", alias="STRIPE_CANCEL_URL")
    automatic_tax: bool = Field(default=True, alias="STRIPE_AUTOMATIC_TAX")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"
        populate_by_name = True


class BillingSettings(BaseSettings):
    """Regional billing catalog configuration"""
    region_catalog_json: str = Field(default="", alias="BILLING_REGION_CATALOG_JSON")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"
        populate_by_name = True


class Settings(BaseSettings):
    """Main Settings - Aggregates all settings"""

    # Application
    app_name: str = Field(default="Trademark Risk Assessment System", alias="APP_NAME")
    app_version: str = Field(default="3.0.0", alias="APP_VERSION")
    debug: bool = Field(default=False, alias="DEBUG")
    environment: str = Field(default="development", alias="ENVIRONMENT")

    # Feature flags
    use_unified_scoring: bool = Field(default=True, alias="USE_UNIFIED_SCORING")

    # Server
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")
    workers: int = Field(default=4, alias="WORKERS")

    # CORS
    cors_origins: List[str] = Field(default=["http://localhost:3000", "http://localhost:8080"], alias="CORS_ORIGINS")

    # Superadmin
    superadmin_email: Optional[str] = Field(default=None, alias="SUPERADMIN_EMAIL")

    # Sub-settings
    database: DatabaseSettings = DatabaseSettings()
    redis: RedisSettings = RedisSettings()
    auth: AuthSettings = AuthSettings()
    ai: AISettings = AISettings()
    monitoring: MonitoringSettings = MonitoringSettings()
    email: EmailSettings = EmailSettings()
    paths: PathSettings = PathSettings()
    creative: CreativeSettings = CreativeSettings()
    pipeline: PipelineSettings = PipelineSettings()
    iyzico: IyzicoSettings = IyzicoSettings()
    stripe: StripeSettings = StripeSettings()
    billing: BillingSettings = BillingSettings()

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"
        populate_by_name = True


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()


# Convenience access
settings = get_settings()
