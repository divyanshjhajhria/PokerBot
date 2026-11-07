from hypothesis import HealthCheck, settings

settings.register_profile(
    "engine",
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
settings.load_profile("engine")
