from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_api_key: str = ""
    jwt_secret: str = ""

    # SMTP (optional — falls back to console sender when unset)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    # Frontend base URL (used to build invite links in emails)
    app_base_url: str = "http://localhost:5173"

    # LangSmith tracing (optional — Phase 4c). When api_key is set,
    # DeepAgentsAdapter wires LangGraph's tracing callbacks so runs show
    # up in LangSmith. Project defaults to "agent-platform".
    langsmith_api_key: str = ""
    langsmith_project: str = "agent-platform"
    langsmith_endpoint: str = "https://api.smith.langchain.com"


settings = Settings()