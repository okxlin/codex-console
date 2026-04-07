from pathlib import Path


def test_create_app_initializes_database_before_reading_settings():
    source = Path("src/web/app.py").read_text(encoding="utf-8")

    create_app_start = source.index("def create_app()")
    lifespan_start = source.index("@asynccontextmanager", create_app_start)
    create_app_prefix = source[create_app_start:lifespan_start]

    init_call = create_app_prefix.index("initialize_database()")
    settings_call = create_app_prefix.index("settings = get_settings()")

    assert init_call < settings_call, "create_app() 必须先初始化数据库，再首次读取设置"
