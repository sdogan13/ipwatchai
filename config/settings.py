"""
Configuration Management
Centralized settings using Pydantic BaseSettings
"""
import os
from typing import List, Optional
from pydantic_settings import BaseSettings
from pydantic import Field, validator
from functools import lru_cache


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
    host: str = Field(default="localhost", env="REDIS_HOST")
    port: int = Field(default=6379, env="REDIS_PORT")
    password: Optional[str] = Field(default=None, env="REDIS_PASSWORD")
    
    # Database numbers
    cache_db: int = Field(default=0, env="REDIS_CACHE_DB")
    queue_db: int = Field(default=2, env="REDIS_QUEUE_DB")
    session_db: int = Field(default=3, env="REDIS_SESSION_DB")
    
    # Cache settings
    embedding_cache_ttl: int = Field(default=86400, env="EMBEDDING_CACHE_TTL")  # 24 hours
    session_ttl: int = Field(default=604800, env="SESSION_TTL")  # 7 days
    
    def get_url(self, db: int = 0) -> str:
        if self.password:
            return f"redis://:{self.password}@{self.host}:{self.port}/{db}"
        return f"redis://{self.host}:{self.port}/{db}"


class AuthSettings(BaseSettings):
    """Authentication Configuration"""
    secret_key: str = Field(default="your-super-secret-key-change-in-production", env="AUTH_SECRET_KEY")
    algorithm: str = Field(default="HS256", env="AUTH_ALGORITHM")

    # Token expiry
    access_token_expire_minutes: int = Field(default=30, env="ACCESS_TOKEN_EXPIRE_MINUTES")
    refresh_token_expire_days: int = Field(default=7, env="REFRESH_TOKEN_EXPIRE_DAYS")

    # Password requirements
    password_min_length: int = Field(default=8, env="PASSWORD_MIN_LENGTH")

    # Rate limiting
    login_rate_limit: int = Field(default=5, env="LOGIN_RATE_LIMIT")  # per minute
    api_rate_limit: int = Field(default=100, env="API_RATE_LIMIT")  # per minute

    @validator("secret_key")
    def validate_secret_key(cls, v):
        if v == "your-super-secret-key-change-in-production":
            if os.getenv("ENVIRONMENT", "development") == "production":
                raise ValueError(
                    "FATAL: You must set a unique AUTH_SECRET_KEY in production. "
                    "The default secret key is not allowed when ENVIRONMENT=production."
                )
        return v


class AISettings(BaseSettings):
    """AI Model Configuration"""
    device: str = Field(default="cuda", env="AI_DEVICE")

    # Batch sizes
    clip_batch_size: int = Field(default=64, env="CLIP_BATCH_SIZE")
    dino_batch_size: int = Field(default=32, env="DINO_BATCH_SIZE")
    text_batch_size: int = Field(default=256, env="TEXT_BATCH_SIZE")

    # Precision
    use_fp16: bool = Field(default=True, env="USE_FP16")
    use_tf32: bool = Field(default=True, env="USE_TF32")

    # Model names
    clip_model: str = Field(default="ViT-B-32", env="CLIP_MODEL")
    clip_pretrained: str = Field(default="laion2b_s34b_b79k", env="CLIP_PRETRAINED")
    text_model: str = Field(default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", env="TEXT_MODEL")
    dino_model: str = Field(default="dinov2_vitb14", env="DINO_MODEL")

    # Translation
    translation_model: str = Field(default="facebook/nllb-200-distilled-600M", env="TRANSLATION_MODEL")
    translation_device: str = Field(default="auto", env="TRANSLATION_DEVICE")  # "auto", "cuda", "cpu"

    # OCR
    ocr_languages: list = Field(default=["en", "tr", "ar", "de", "fr"], env="OCR_LANGUAGES")


class MonitoringSettings(BaseSettings):
    """Watchlist Monitoring Configuration"""
    # Scanning
    scan_batch_size: int = Field(default=1000, env="SCAN_BATCH_SIZE")
    default_similarity_threshold: float = Field(default=0.70, env="DEFAULT_SIMILARITY_THRESHOLD")
    
    # Alerts
    critical_threshold: float = Field(default=0.90, env="CRITICAL_THRESHOLD")
    high_threshold: float = Field(default=0.75, env="HIGH_THRESHOLD")
    medium_threshold: float = Field(default=0.60, env="MEDIUM_THRESHOLD")
    
    # Notifications
    digest_send_hour: int = Field(default=9, env="DIGEST_SEND_HOUR")  # 9 AM
    digest_send_day: int = Field(default=1, env="DIGEST_SEND_DAY")  # Monday


class EmailSettings(BaseSettings):
    """Email Configuration"""
    smtp_host: str = Field(default="smtp.gmail.com", env="SMTP_HOST")
    smtp_port: int = Field(default=587, env="SMTP_PORT")
    smtp_user: str = Field(default="", env="SMTP_USER")
    smtp_password: str = Field(default="", env="SMTP_PASSWORD")
    smtp_tls: bool = Field(default=True, env="SMTP_TLS")
    
    from_email: str = Field(default="noreply@trademark-system.com", env="FROM_EMAIL")
    from_name: str = Field(default="Trademark Risk System", env="FROM_NAME")
    
    # Templates
    template_dir: str = Field(default="templates/email", env="EMAIL_TEMPLATE_DIR")


class PathSettings(BaseSettings):
    """File Path Configuration"""
    data_root: str = Field(default=r"C:\Users\701693\turk_patent\bulletins\Marka", env="DATA_ROOT")
    upload_dir: str = Field(default="uploads", env="UPLOAD_DIR")
    report_dir: str = Field(default="reports", env="REPORT_DIR")
    log_dir: str = Field(default="logs", env="LOG_DIR")
    
    @validator("data_root", "upload_dir", "report_dir", "log_dir", pre=True)
    def ensure_dir_exists(cls, v):
        os.makedirs(v, exist_ok=True)
        return v


class CreativeSettings(BaseSettings):
    """Creative Suite Configuration (Name Generator + Logo Studio)"""
    # Gemini API
    google_api_key: str = Field(default="", env="CREATIVE_GOOGLE_API_KEY")
    gemini_text_model: str = Field(default="gemini-2.5-pro", env="CREATIVE_GEMINI_TEXT_MODEL")
    gemini_image_model: str = Field(default="gemini-3-pro-image-preview", env="CREATIVE_GEMINI_IMAGE_MODEL")
    gemini_timeout: int = Field(default=30, env="CREATIVE_GEMINI_TIMEOUT")
    gemini_max_retries: int = Field(default=2, env="CREATIVE_GEMINI_MAX_RETRIES")

    # Name Generator
    name_batch_size: int = Field(default=25, env="CREATIVE_NAME_BATCH_SIZE")
    name_similarity_threshold: float = Field(default=0.50, env="CREATIVE_NAME_SIMILARITY_THRESHOLD")
    name_phonetic_check: bool = Field(default=True, env="CREATIVE_NAME_PHONETIC_CHECK")

    # Logo Studio
    logo_images_per_run: int = Field(default=4, env="CREATIVE_LOGO_IMAGES_PER_RUN")
    logo_similarity_threshold: float = Field(default=0.65, env="CREATIVE_LOGO_SIMILARITY_THRESHOLD")
    logo_output_dir: str = Field(default="uploads/generated/logos", env="CREATIVE_LOGO_OUTPUT_DIR")

    # Redis
    generation_cache_db: int = Field(default=4, env="CREATIVE_GENERATION_CACHE_DB")
    generation_cache_ttl: int = Field(default=3600, env="CREATIVE_GENERATION_CACHE_TTL")

    class Config:
        env_prefix = "CREATIVE_"
        extra = "ignore"

    @validator("logo_output_dir", pre=True)
    def ensure_logo_dir_exists(cls, v):
        os.makedirs(v, exist_ok=True)
        return v


class PipelineSettings(BaseSettings):
    """Data Pipeline Configuration (collection → extraction → metadata → ingest)"""
    # Paths
    bulletins_root: str = Field(
        default=r"C:\Users\701693\turk_patent\bulletins\Marka",
        env="PIPELINE_BULLETINS_ROOT"
    )

    # data_collection.py
    turkpatent_url: str = Field(
        default="https://www.turkpatent.gov.tr/bultenler",
        env="PIPELINE_TURKPATENT_URL"
    )
    categories: List[str] = Field(default=["Marka"], env="PIPELINE_CATEGORIES")
    headless_browser: bool = Field(default=True, env="PIPELINE_HEADLESS_BROWSER")
    download_timeout: int = Field(default=300, env="PIPELINE_DOWNLOAD_TIMEOUT")

    # zip.py
    seven_zip_path: str = Field(
        default=r"C:\Program Files\7-Zip\7z.exe",
        env="PIPELINE_SEVEN_ZIP_PATH"
    )
    max_cd_archives: int = Field(default=0, env="PIPELINE_MAX_CD_ARCHIVES")
    skip_existing: bool = Field(default=True, env="PIPELINE_SKIP_EXISTING")
    clean_after_extract: bool = Field(default=True, env="PIPELINE_CLEAN_AFTER_EXTRACT")

    # metadata.py
    skip_if_metadata_exists: bool = Field(default=True, env="PIPELINE_SKIP_IF_METADATA_EXISTS")
    canary_failure_threshold: float = Field(default=0.05, env="PIPELINE_CANARY_FAILURE_THRESHOLD")

    # ai.py (embedding generation)
    embedding_batch_size: int = Field(default=64, env="PIPELINE_EMBEDDING_BATCH_SIZE")
    skip_if_embeddings_exist: bool = Field(default=True, env="PIPELINE_SKIP_IF_EMBEDDINGS_EXIST")
    generate_clip: bool = Field(default=True, env="PIPELINE_GENERATE_CLIP")
    generate_dinov2: bool = Field(default=True, env="PIPELINE_GENERATE_DINOV2")
    generate_text: bool = Field(default=True, env="PIPELINE_GENERATE_TEXT")
    generate_color_histogram: bool = Field(default=True, env="PIPELINE_GENERATE_COLOR_HISTOGRAM")
    generate_ocr: bool = Field(default=True, env="PIPELINE_GENERATE_OCR")

    # Scheduling
    collection_schedule_day: str = Field(default="monday", env="PIPELINE_COLLECTION_SCHEDULE_DAY")
    collection_schedule_hour: int = Field(default=3, env="PIPELINE_COLLECTION_SCHEDULE_HOUR")
    pipeline_schedule_hour: int = Field(default=5, env="PIPELINE_PIPELINE_SCHEDULE_HOUR")

    class Config:
        env_prefix = "PIPELINE_"
        extra = "ignore"


class Settings(BaseSettings):
    """Main Settings - Aggregates all settings"""

    # Application
    app_name: str = Field(default="Trademark Risk Assessment System", env="APP_NAME")
    app_version: str = Field(default="3.0.0", env="APP_VERSION")
    debug: bool = Field(default=False, env="DEBUG")
    environment: str = Field(default="development", env="ENVIRONMENT")

    # Server
    host: str = Field(default="0.0.0.0", env="HOST")
    port: int = Field(default=8000, env="PORT")
    workers: int = Field(default=4, env="WORKERS")

    # CORS
    cors_origins: List[str] = Field(default=["http://localhost:3000", "http://localhost:8080"], env="CORS_ORIGINS")

    # Superadmin
    superadmin_email: Optional[str] = Field(default=None, env="SUPERADMIN_EMAIL")

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

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"  # Allow extra env vars from .env file


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()


# Convenience access
settings = get_settings()
