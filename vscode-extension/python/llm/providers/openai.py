import json
from typing import List, Any, Union

import openai
from openai.lib._parsing._responses import type_to_response_format_param
from pydantic import ValidationError
from core.abstractions import ModelProviderInterface, ChatSession
from core.schemas import ChatConfig, ChatMessage, ChatResponse, DetailedImpactReport
from core.errors import RetryableError, NonRetryableError
from utils.reporting_normalizer import coerce_detailed_report_payload, normalize_keys

class OpenAIProvider(ModelProviderInterface):
    def __init__(self, model_name: str):
        self.model_name = model_name
    
    def create_client(self, api_key: str, **kwargs) -> Any:
        return openai.OpenAI(api_key=api_key, **kwargs)
    
    def create_chat(self, client: Any, config: ChatConfig) -> 'OpenAIChatSession':
        return OpenAIChatSession(client, self.model_name, config)
    
    def is_retryable_error(self, error: Exception) -> bool:
        return isinstance(error, (
            openai.RateLimitError,
            openai.APITimeoutError,
            openai.InternalServerError,
        ))
    
    def get_error_wait_time(self, error: Exception, attempt: int) -> float:
        if isinstance(error, openai.RateLimitError):
            # Check if retry-after header is present
            retry_after = getattr(error, 'retry_after', None)
            if retry_after:
                return float(retry_after)
        return 2 ** attempt

class OpenAIChatSession(ChatSession):
    def __init__(self, client: Any, model: str, config: ChatConfig):
        self.client = client
        self.model = model
        self.config = config
        self._messages = [{"role": "system", "content": config.system_instruction}]
        self._history = []
        self._use_responses_api = self._should_use_responses_api(model)

    @staticmethod
    def _should_use_responses_api(model: str) -> bool:
        """Determine whether to route requests through the Responses API."""
        if not model:
            return False
        normalized = model.lower()
        return normalized.startswith("gpt-5")
    
    def send_message(self, message: Union[str, ChatMessage]) -> ChatResponse:
        content = message if isinstance(message, str) else message.content
        
        # Add user message
        self._messages.append({"role": "user", "content": content})
        
        try:
            if self._use_responses_api:
                response = self._send_via_responses_api()
                assistant_content = self._extract_response_text(response)
                usage = response.usage.model_dump() if response.usage else None
            else:
                response = self._send_via_chat_completions()
                assistant_content = response.choices[0].message.content
                usage = response.usage.model_dump() if response.usage else None

            # Add assistant response to messages
            self._messages.append({"role": "assistant", "content": assistant_content})

            # Parse JSON if requested
            parsed = None
            if self.config.response_format == "json" and self.config.response_schema:
                try:
                    parsed_dict = json.loads(assistant_content)
                    normalized_dict = normalize_keys(parsed_dict)
                    parsed = self.config.response_schema(**normalized_dict)
                except (json.JSONDecodeError, TypeError) as e:
                    print(f"Warning: Failed to parse JSON response: {e}")
                except ValidationError as e:
                    parsed = self._handle_validation_error(normalized_dict, assistant_content, e)

            # Store in history
            self._history.append(ChatMessage(content=content, role="user"))
            self._history.append(ChatMessage(content=assistant_content, role="assistant"))

            return ChatResponse(
                content=assistant_content,
                parsed=parsed,
                usage=usage,
                metadata={'model': self.model}
            )

        except Exception as e:
            provider = OpenAIProvider(self.model)
            if provider.is_retryable_error(e):
                raise RetryableError(f"OpenAI API error: {e}") from e
            else:
                raise NonRetryableError(f"OpenAI API error: {e}") from e

    def _send_via_chat_completions(self):
        request_params = {
            "model": self.model,
            "messages": self._messages,
            "temperature": self.config.temperature,
        }

        if self.config.response_format == "json":
            request_params["response_format"] = {"type": "json_object"}

        if self.config.max_tokens:
            request_params["max_tokens"] = self.config.max_tokens

        return self.client.chat.completions.create(**request_params)

    def _send_via_responses_api(self):
        request_params = {
            "model": self.model,
            "input": self._format_messages_for_responses(),
        }

        if self.config.max_tokens:
            request_params["max_output_tokens"] = self.config.max_tokens

        if self.config.response_format == "json":
            if self.config.response_schema:
                raw_format = type_to_response_format_param(self.config.response_schema)
                response_format = self._normalize_json_schema_format(raw_format)
            else:
                response_format = {
                    "type": "json_schema",
                    "name": "json_object",
                    "schema": {"type": "object"}
                }

            request_params["text"] = {"format": response_format}

        return self.client.responses.create(**request_params)

    def _format_messages_for_responses(self):
        formatted = []
        for message in self._messages:
            role = message["role"]
            content_type = "output_text" if role == "assistant" else "input_text"
            formatted.append({
                "role": role,
                "content": [
                    {
                        "type": content_type,
                        "text": message["content"],
                    }
                ],
            })
        return formatted

    def _extract_response_text(self, response: Any) -> str:
        if hasattr(response, "output_text") and response.output_text:
            return response.output_text

        texts: List[str] = []
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", None) == "message":
                for content in getattr(item, "content", []) or []:
                    content_type = getattr(content, "type", None)
                    if content_type in ("output_text", "text"):
                        texts.append(getattr(content, "text", ""))
        return "".join(texts)

    def _normalize_json_schema_format(self, format_payload: Any) -> Any:
        """Convert older json_schema format payloads to the Responses API structure."""
        if not isinstance(format_payload, dict):
            return format_payload

        if format_payload.get("type") != "json_schema":
            return format_payload

        schema_section = format_payload.get("json_schema")
        if not isinstance(schema_section, dict):
            return {
                "type": "json_schema",
                "name": self.config.response_schema.__name__ if self.config.response_schema else "json_object",
                "schema": {}
            }

        name = schema_section.get("name") or (self.config.response_schema.__name__ if self.config.response_schema else "json_object")
        schema = schema_section.get("schema") or {}
        strict = schema_section.get("strict")
        description = schema_section.get("description")

        normalized = {
            "type": "json_schema",
            "name": name,
            "schema": schema,
        }

        if strict is not None:
            normalized["strict"] = strict
        if description:
            normalized["description"] = description

        return normalized

    def _handle_validation_error(self, normalized_dict: Any, raw_text: str, error: ValidationError) -> Any:
        schema = self.config.response_schema
        if schema is DetailedImpactReport:
            fallback = coerce_detailed_report_payload(normalized_dict, path=normalized_dict.get('path'))
            if fallback is None:
                fallback = coerce_detailed_report_payload(raw_text, path=normalized_dict.get('path'))
            if fallback is not None:
                try:
                    return DetailedImpactReport(**fallback)
                except ValidationError as exc:
                    print(f"Warning: Failed to coerce payload after validation error: {exc}")
        print(f"Warning: Failed to validate JSON response against schema: {error}")
        return None
    
    def get_conversation_history(self) -> List[ChatMessage]:
        return self._history.copy()
    
    def clear_history(self):
        self._history.clear()
        self._messages = [{"role": "system", "content": self.config.system_instruction}]
