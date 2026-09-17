from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "postgresql+psycopg://railscope:railscope@localhost:5432/railscope")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    data_dir: Path = Path(os.getenv("DATA_DIR", "../data"))
    default_same_direction_headway_s: int = 180
    default_opposite_direction_headway_s: int = 0


settings = Settings()
