from fastapi import FastAPI

from constants import check_variables
from errors import register_error_handlers
from errors.base_error import error_verification
from middlewares import (
    register_internal_token_middleware,
    register_request_context_middleware,
    register_request_logger_middleware,
    register_session_manager_middleware,
)
from resources import HealthCheckResource, PixTransactionResource
from utils.logger import setup_logging


def create_app() -> FastAPI:
    application = FastAPI(
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    register_session_manager_middleware(application)
    register_internal_token_middleware(application)
    register_request_logger_middleware(application)
    register_request_context_middleware(application)

    health_check_resource = HealthCheckResource()
    pix_transaction_resource = PixTransactionResource()

    application.add_api_route("/", health_check_resource.on_get_home, methods=["GET"])
    application.add_api_route(
        "/health_check",
        health_check_resource.on_get_health_check,
        methods=["GET"]
    )

    application.add_api_route(
        "/pix/transaction",
        pix_transaction_resource.on_post,
        methods=["POST"],
    )
    application.add_api_route(
        "/pix/transaction/{pix_id}",
        pix_transaction_resource.on_get_by_id,
        methods=["GET"],
    )
    application.add_api_route(
        "/pix/transaction/{pix_id}/two-factor",
        pix_transaction_resource.on_post_two_factor,
        methods=["POST"],
    )

    register_error_handlers(application)

    return application


def main() -> FastAPI:
    check_variables()
    error_verification()
    setup_logging()

    return create_app()


app = main()
