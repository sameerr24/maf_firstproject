from database.connection import make_engine


def test_engine_pool_is_capped_by_default():
    engine = make_engine("postgresql+psycopg://user:password@localhost/database")
    try:
        assert engine.pool.size() == 1
        assert engine.pool._max_overflow == 0
    finally:
        engine.dispose()


def test_engine_pool_size_can_be_overridden():
    engine = make_engine("postgresql+psycopg://user:password@localhost/database",
                         pool_size=4, max_overflow=1)
    try:
        assert engine.pool.size() == 4
        assert engine.pool._max_overflow == 1
    finally:
        engine.dispose()
