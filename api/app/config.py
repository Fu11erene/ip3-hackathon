from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_db: str = "appdb"
    postgres_host: str = "db"
    postgres_port: int = 5432

    secret_key: str = "dev-secret-change-me"
    access_token_expire_minutes: int = 60

    nexway_api_base_url: str = "https://example.invalid"
    nexway_api_token: str = "changeme"

    otp_code_length: int = 6
    otp_expire_minutes: int = 5
    otp_max_attempts: int = 5
    log_otp_code: bool = False

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
