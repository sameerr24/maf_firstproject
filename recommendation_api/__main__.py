"""`python -m recommendation_api` starts the API with uvicorn (REC_API_HOST / REC_API_PORT)."""

import uvicorn

from recommendation_api.settings import ApiSettings

if __name__ == "__main__":
    settings = ApiSettings.from_env()
    uvicorn.run("recommendation_api.main:app", host=settings.host, port=settings.port)
