from google import genai
from google.genai import types

class GenAIService:
    """
    A service to interact with the Google GenAI API.
    """

    def __init__(self, api_key: str):
        """
        Initializes the GenAIService with the given API key.

        Args:
            api_key: The API key for the Google GenAI API.
        """
        self.client = genai.Client(api_key=api_key)

    def generate_text(self, prompt: str) -> str:
        """
        Generates text using the Gemini 2.5 Flash model.

        Args:
            prompt: The text prompt to generate text from.

        Returns:
            The generated text.
        """
        response = self.client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        return response.text
