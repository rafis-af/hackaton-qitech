from constants import find_example_secrets

EXAMPLE_SETTINGS = {
    "INTERNAL_TOKEN": "default_token",
    "RECEITA_API_INTERNAL_TOKEN": "default_token",
    "DATABASE_URL": "postgresql+psycopg2://bootcamp:bootcamp@db:5432/bootcamp",
}


class TestExampleSecrets:
    def test_development_environments_accept_example_values(self):
        assert find_example_secrets("local", EXAMPLE_SETTINGS) == []
        assert find_example_secrets("test", EXAMPLE_SETTINGS) == []

    def test_any_other_environment_refuses_them(self):
        assert find_example_secrets("production", EXAMPLE_SETTINGS) == [
            "INTERNAL_TOKEN",
            "RECEITA_API_INTERNAL_TOKEN",
            "DATABASE_URL",
        ]

    def test_own_values_are_accepted_anywhere(self):
        own_settings = {
            "INTERNAL_TOKEN": "um-token-gerado-de-verdade",
            "RECEITA_API_INTERNAL_TOKEN": "outro-token-de-verdade",
            "DATABASE_URL": "postgresql+psycopg2://antifraude:senha-forte@db.interno:5432/antifraude",
        }

        assert find_example_secrets("production", own_settings) == []
