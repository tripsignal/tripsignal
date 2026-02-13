"""Application configuration from environment variables."""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator

def _clean_str(value: str | None) -> str | None:
    if value is None:
        return None
    # Replace non-breaking spaces with normal spaces
    return value.replace("\u00a0", " ")



class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # API Settings
    API_V1_PREFIX: str = "/api/v1"
    PROJECT_NAME: str = "TripSignal"
    DEBUG: bool = False
    DEV_API_TOKEN: str | None = None


    # Database Settings
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "tripsignal"
    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: int = 5432

    # Notifications / Email (MVP)
    ENABLE_EMAIL_NOTIFICATIONS: bool = False

    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587

    SMTP_USERNAME: str | None = Field(default=None, alias="SMTP_USERNAME")
    SMTP_PASSWORD: str | None = Field(default=None, alias="SMTP_PASSWORD")

    SMTP_USE_TLS: bool = True  # STARTTLS

    SMTP_FROM_EMAIL: str | None = Field(default=None, alias="SMTP_FROM_EMAIL")
    SMTP_FROM_NAME: str = Field(default="TripSignal", alias="SMTP_FROM_NAME")
    admin_email: str | None = None

    @field_validator(
    	"SMTP_USERNAME",
    	"SMTP_PASSWORD",
    	"SMTP_FROM_EMAIL",
    	"SMTP_FROM_NAME",
    	mode="before",
    )
    @classmethod
    def clean_smtp_strings(cls, v):
    	return _clean_str(v)



    @property
    def database_url(self) -> str:
        """Construct database URL from components."""
        return (
            f"postgresql+psycopg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )


settings = Settings()
