import functools
import json
import math
import os

from jsonschema import RefResolver, ValidationError, validate, validators
from jsonschema.exceptions import best_match

from constants import SCHEMA_PATH
from errors import InvalidSchema


class SchemaCache:
    schemas = {}
    strict_validators = {}

    @staticmethod
    def get_schema(schema_file_name: str) -> dict:
        schema_path = os.path.join(SCHEMA_PATH, schema_file_name)

        if schema_path in SchemaCache.schemas:
            return SchemaCache.schemas[schema_path]

        if not os.path.isfile(schema_path):
            raise Exception(
                f"Nao encontrei o schema '{schema_file_name}' em {SCHEMA_PATH}. "
                + "Ou o arquivo nao existe, ou o nome escrito na rota esta diferente."
            )

        with open(schema_path, "r", encoding="utf-8") as arquivo:
            schema = json.loads(arquivo.read())

        SchemaCache.schemas[schema_path] = schema

        return schema


class SchemaHandler:
    @staticmethod
    def validate(schema_file_name: str):
        def decorator_validate(func):
            @functools.wraps(func)
            def wrapper_validate(*args, **kwargs):
                if "payload" not in kwargs:
                    raise Exception(
                        f"A rota '{func.__name__}' foi decorada com o SchemaHandler, mas nao "
                        + "tem um parametro chamado 'payload'. E de la que sai o corpo a validar."
                    )

                schema = SchemaCache.get_schema(schema_file_name)
                resolver = RefResolver(f"file://{SCHEMA_PATH}/", None)

                refuse_what_json_schema_cannot_see(kwargs["payload"])

                error = best_match(strict_validator(schema)(schema, resolver=resolver).iter_errors(kwargs["payload"]))
                if error is not None:
                    raise InvalidSchema(describe_schema_error(error))

                return func(*args, **kwargs)

            return wrapper_validate

        return decorator_validate

    @staticmethod
    def validate_query_params(schema_file_name: str):
        def decorator_validate(func):
            @functools.wraps(func)
            def wrapper_validate(*args, **kwargs):
                request = kwargs.get("request")
                if request is None:
                    raise Exception(
                        f"A rota '{func.__name__}' foi decorada com o validate_query_params, mas "
                        + "nao tem um parametro chamado 'request'. E de la que sai a query string."
                    )

                schema = SchemaCache.get_schema(schema_file_name)
                resolver = RefResolver(f"file://{SCHEMA_PATH}/", None)
                query_params = query_params_to_dict(request.query_params, schema)

                try:
                    validate(query_params, schema, resolver=resolver)
                except ValidationError as error:
                    raise InvalidSchema(describe_schema_error(error))

                return func(*args, **kwargs)

            return wrapper_validate

        return decorator_validate


def is_strict_integer(checker, instance) -> bool:
    return isinstance(instance, int) and not isinstance(instance, bool)


def strict_validator(schema: dict):
    base = validators.validator_for(schema)

    if base not in SchemaCache.strict_validators:
        type_checker = base.TYPE_CHECKER.redefine("integer", is_strict_integer)
        SchemaCache.strict_validators[base] = validators.extend(base, type_checker=type_checker)

    return SchemaCache.strict_validators[base]


def query_params_to_dict(query_params, schema: dict) -> dict:
    properties = schema.get("properties", {})
    parsed_params = {}

    for param_name in query_params.keys():
        declared = properties.get(param_name, {})

        if declared.get("type") == "array":
            parsed_params[param_name] = query_params.getlist(param_name)
        else:
            parsed_params[param_name] = query_params[param_name]

    return parsed_params


def refuse_what_json_schema_cannot_see(value, location: list = None) -> None:
    pending = [(value, location or [])]

    while pending:
        current, path = pending.pop()

        if isinstance(current, dict):
            for key in current:
                if has_unsafe_character(key):
                    raise InvalidSchema(f"field names with control characters or invalid unicode are not allowed in {'.'.join(path) or 'body'}")
            pending.extend((item, path + [str(key)]) for key, item in current.items())
        elif isinstance(current, list):
            pending.extend((item, path + [str(index)]) for index, item in enumerate(current))
        elif isinstance(current, float) and not math.isfinite(current):
            raise InvalidSchema(f"{current} is not a valid JSON number in {'.'.join(path)}")
        elif isinstance(current, str) and has_unsafe_character(current):
            raise InvalidSchema(f"control characters or invalid unicode are not allowed in {'.'.join(path)}")


def has_unsafe_character(text: str) -> bool:
    for character in text:
        code = ord(character)

        if code < 32 or code == 127 or 0xD800 <= code <= 0xDFFF:
            return True

    return False


def describe_schema_error(error: ValidationError) -> str:
    location = []
    for part in error.absolute_path:
        location.append(str(part))

    if location:
        return f"{error.message} in {'.'.join(location)}"

    return error.message
