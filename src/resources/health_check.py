import os

from fastapi import Response, status
from fastapi.responses import JSONResponse

from constants import SERVICE_NAME


class HealthCheckResource:
    def on_get_home(self) -> JSONResponse:
        home = {"service": SERVICE_NAME, "id": str(os.getpid())}

        return JSONResponse(content=home, status_code=status.HTTP_200_OK)

    def on_get_health_check(self) -> Response:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
