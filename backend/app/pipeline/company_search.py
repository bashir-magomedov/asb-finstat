from ..config import settings
from ..openrouter import chat_json


async def search_companies(country: str, query: str) -> list[dict]:
    """AI call #1: typeahead — real companies in `country` matching `query`."""
    result = await chat_json(
        settings.model_company_search,
        [{"role": "user", "content": (
            f"You are a company-name autocomplete for {country}. The user has typed the partial "
            f'text: "{query}". Suggest up to 8 real, notable companies registered or headquartered '
            f"in {country} whose names begin with or contain that text, case-insensitive (for "
            'example, the partial text "sie" matches "Siemens AG"). '
            'Respond ONLY with a JSON array: [{"name": "...", "description": "<one short line>"}]. '
            "Return [] only if no company name contains the text."
        )}],
    )
    return result if isinstance(result, list) else []
