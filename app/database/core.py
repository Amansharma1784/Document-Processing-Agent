from pydantic_settings import BaseSettings, SettingsConfigDict

# DATABASE_URL="mysql+pymysql://root:@127.0.0.1:3306/practice_db"
class Settings(BaseSettings):
    DB_HOST: str = "127.0.0.1"
    DB_PORT: int = 3306
    DB_NAME: str = "document_processor"
    DB_USER: str = "root"
    DB_PASSWORD: str = ""
    GROQ_API_KEY:str
    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()
