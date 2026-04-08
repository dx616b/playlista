from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://playlista:playlista@postgres:5432/playlista"
    redis_url: str = "redis://redis:6379/0"
    music_root: str = "/music"
    music_roots: str | None = None
    export_music_root: str | None = None
    analysis_version: str = "v1"
    scan_presets: str | None = None
    essentia_tf_model_path: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()


def get_scan_presets() -> list[str]:
    values: list[str] = []
    if settings.music_roots:
        values.extend([p.strip() for p in settings.music_roots.split(",") if p.strip()])
    if settings.scan_presets:
        values.extend([p.strip() for p in settings.scan_presets.split(",") if p.strip()])
    if settings.music_root:
        values.append(settings.music_root)
    # keep order, remove duplicates
    return list(dict.fromkeys(values))


def get_default_music_root() -> str:
    presets = get_scan_presets()
    return presets[0] if presets else settings.music_root
