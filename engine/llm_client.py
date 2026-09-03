"""
A provider-agnostic LLM client. Reads provider from environment
variables and returns a unified interface. Agent imports this
instead of any specific provider SDK.

Dependencies: os, groq, google.generativeai, python-dotenv
"""

import os
from dotenv import load_dotenv

# Load .env file from project root or current working environment
load_dotenv()

DEFAULT_MODELS = {
    "groq": "llama-3.1-70b-versatile",
    "gemini": "gemini-1.5-flash",
}


def _get_provider_config():
    provider = os.getenv("LLM_PROVIDER", "groq").lower().strip()
    model = os.getenv("LLM_MODEL", "").strip()
    if not model:
        model = DEFAULT_MODELS.get(provider, "llama-3.1-70b-versatile")
    return provider, model


def get_provider_info() -> dict:
    """
    Returns high-level provider metadata and whether the respective API key is set.
    Never exposes the raw API key.
    """
    provider, model = _get_provider_config()

    if provider == "groq":
        key = os.getenv("GROQ_API_KEY", "").strip()
        api_key_set = bool(key and key != "your_key_here")
    elif provider == "gemini":
        key = os.getenv("GEMINI_API_KEY", "").strip()
        api_key_set = bool(key and key != "your_key_here")
    else:
        api_key_set = False

    return {
        "provider": provider,
        "model": model,
        "api_key_set": api_key_set,
    }


def get_llm_response(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.1,
) -> str:
    """
    Routes system and user prompts to the configured LLM provider
    (Groq or Gemini) and returns the text response.
    """
    provider, model = _get_provider_config()

    if provider == "groq":
        groq_api_key = os.getenv("GROQ_API_KEY", "").strip()
        if not groq_api_key or groq_api_key == "your_key_here":
            raise ValueError(
                "GROQ_API_KEY is not set or is still the placeholder. "
                "Please set a valid GROQ_API_KEY in your .env file."
            )

        from groq import Groq

        client = Groq(api_key=groq_api_key)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
        )
        return response.choices[0].message.content

    elif provider == "gemini":
        gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not gemini_api_key or gemini_api_key == "your_key_here":
            raise ValueError(
                "GEMINI_API_KEY is not set or is still the placeholder. "
                "Please set a valid GEMINI_API_KEY in your .env file."
            )

        import google.generativeai as genai

        genai.configure(api_key=gemini_api_key)
        gemini_model = genai.GenerativeModel(model)
        prompt = f"{system_prompt}\n\n{user_prompt}" if system_prompt else user_prompt
        response = gemini_model.generate_content(prompt)
        return response.text

    else:
        raise ValueError(
            f"Unsupported LLM_PROVIDER '{provider}'. Supported providers are: 'groq', 'gemini'."
        )


if __name__ == "__main__":
    info = get_provider_info()
    print(f"Provider: {info['provider']}")
    print(f"Model: {info['model']}")
    print(f"API key set: {info['api_key_set']}")

    if not info['api_key_set']:
        print("ERROR: API key not set in .env")
    else:
        response = get_llm_response(
            system_prompt="You are a helpful assistant. Be concise.",
            user_prompt="Say 'LLM client working' and nothing else."
        )
        print(f"Test response: {response}")
