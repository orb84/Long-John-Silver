"""
Resilient LLM response parsing and extraction utilities for LJS.

Provides safe extraction for text content, tool calls, and structured JSON
payloads from varied LLM completion response formats (e.g., direct litellm
ModelResponse objects or parsed nested dictionaries).
"""

from __future__ import annotations

import json
import re
from typing import Any


class LLMResponseParser:
    """Stateless utility class for parsing and extracting data from LLM responses.

    Groups helper methods that clean up raw LLM outputs, isolate markdown code
    fences, and safely handle both object-style and dictionary-style payloads
    returned by diverse cloud or local model endpoints.
    """

    @staticmethod
    def safe_extract_content(response: Any) -> str:
        """Extract text content from an LLM response safely.

        Handles both object-style (Pydantic/litellm) and dictionary-style
        responses to prevent AttributeErrors or KeyErrors.

        Args:
            response: The raw completion response from the LLM.

        Returns:
            The extracted and stripped content string, or an empty string.
        """
        if response is None:
            return ''

        # Object-style response check (Pydantic model or ModelResponse)
        if hasattr(response, 'choices') and response.choices:
            choice = response.choices[0]
            if hasattr(choice, 'message') and choice.message:
                msg = choice.message
                if hasattr(msg, 'content') and msg.content is not None:
                    return str(msg.content).strip()
                if isinstance(msg, dict) and msg.get('content') is not None:
                    return str(msg.get('content')).strip()

        # Dictionary-style response check
        if isinstance(response, dict):
            choices = response.get('choices')
            if choices and isinstance(choices, list) and len(choices) > 0:
                choice = choices[0]
                if isinstance(choice, dict):
                    msg = choice.get('message')
                    if isinstance(msg, dict) and msg.get('content') is not None:
                        return str(msg.get('content')).strip()

        # Fallback to string representation if simple format
        if isinstance(response, str):
            return response.strip()

        return ''

    @staticmethod
    def safe_extract_tool_calls(response: Any) -> list[Any]:
        """Extract tool calls from an LLM response safely.

        Handles both object-style (Pydantic/litellm) and dictionary-style
        responses.

        Args:
            response: The raw completion response from the LLM.

        Returns:
            A list of tool calls, or an empty list.
        """
        if response is None:
            return []

        # Object-style response check
        if hasattr(response, 'choices') and response.choices:
            choice = response.choices[0]
            if hasattr(choice, 'message') and choice.message:
                msg = choice.message
                if hasattr(msg, 'tool_calls') and msg.tool_calls is not None:
                    return list(msg.tool_calls)
                if isinstance(msg, dict) and msg.get('tool_calls') is not None:
                    return list(msg.get('tool_calls'))

        # Dictionary-style response check
        if isinstance(response, dict):
            choices = response.get('choices')
            if choices and isinstance(choices, list) and len(choices) > 0:
                choice = choices[0]
                if isinstance(choice, dict):
                    msg = choice.get('message')
                    if isinstance(msg, dict) and msg.get('tool_calls') is not None:
                        return list(msg.get('tool_calls'))

        return []

    @staticmethod
    def extract_json_resilient(raw_text: str) -> dict[str, Any]:
        """Isolate and parse a JSON object from raw LLM output text.

        Cleans up markdown code blocks (e.g. ```json ... ```), isolates the
        bounding curly braces, and parses the extracted string.

        Args:
            raw_text: Raw string containing a JSON payload.

        Returns:
            The parsed dictionary.

        Raises:
            json.JSONDecodeError: If no valid JSON payload could be extracted.
        """
        if not raw_text:
            raise json.JSONDecodeError('Empty raw text passed to parser', raw_text, 0)

        text = raw_text.strip()

        # 1. Strip markdown fences if present
        if '```json' in text:
            text = text.split('```json', 1)[1]
            if '```' in text:
                text = text.split('```', 1)[0]
        elif '```' in text:
            text = text.split('```', 1)[1]
            if '```' in text:
                text = text.split('```', 1)[0]

        text = text.strip()

        # 2. If it is still not a clean JSON, search for the outer-most curly braces
        if not (text.startswith('{') and text.endswith('}')):
            match = re.search(r'(\{.*\})', text, re.DOTALL)
            if match:
                text = match.group(1).strip()

        # 3. Attempt direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError as err:
            # Let's try fixing single quotes to double quotes in case LLM got it wrong
            try:
                # Simple replacement for single-quoted JSON keys/strings
                fixed_text = text.replace("'", '"')
                return json.loads(fixed_text)
            except json.JSONDecodeError:
                # Raise the original error if fallback also fails
                raise err
