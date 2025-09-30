import json
import anthropic
from typing import List, Any, Union
from core.abstractions import ModelProviderInterface, ChatSession
from core.schemas import ChatConfig, ChatMessage, ChatResponse
from core.errors import RetryableError, NonRetryableError

class AnthropicProvider(ModelProviderInterface):
    def __init__(self, model_name: str):
        self.model_name = model_name
    
    def create_client(self, api_key: str, **kwargs) -> Any:
        return anthropic.Anthropic(api_key=api_key, **kwargs)
    
    def create_chat(self, client: Any, config: ChatConfig) -> 'AnthropicChatSession':
        return AnthropicChatSession(client, self.model_name, config)
    
    def is_retryable_error(self, error: Exception) -> bool:
        return isinstance(error, (
            anthropic.RateLimitError,
            anthropic.APITimeoutError,
            anthropic.InternalServerError,
        ))
    
    def get_error_wait_time(self, error: Exception, attempt: int) -> float:
        if isinstance(error, anthropic.RateLimitError):
            retry_after = getattr(error, 'retry_after', None)
            if retry_after:
                return float(retry_after)
        return 2 ** attempt

class AnthropicChatSession(ChatSession):
    def __init__(self, client: Any, model: str, config: ChatConfig):
        self.client = client
        self.model = model
        self.config = config
        self._messages = []
        self._history = []
    
    def send_message(self, message: Union[str, ChatMessage]) -> ChatResponse:
        content = message if isinstance(message, str) else message.content
        
        # Add user message
        self._messages.append({"role": "user", "content": content})
        
        try:
            # Prepare request params
            request_params = {
                "model": self.model,
                "messages": self._messages,
                "system": self.config.system_instruction,
                "temperature": self.config.temperature,
            }
            
            if self.config.max_tokens:
                request_params["max_tokens"] = self.config.max_tokens
            
            # Make the API call
            response = self.client.messages.create(**request_params)
            
            assistant_content = response.content[0].text
            
            # Add assistant response to messages
            self._messages.append({"role": "assistant", "content": assistant_content})
            
            # Parse JSON if requested
            parsed = None
            if self.config.response_format == "json" and self.config.response_schema:
                try:
                    parsed_dict = json.loads(assistant_content)
                    parsed = self.config.response_schema(**parsed_dict)
                except (json.JSONDecodeError, TypeError) as e:
                    print(f"Warning: Failed to parse JSON response: {e}")
            
            # Store in history
            self._history.append(ChatMessage(content=content, role="user"))
            self._history.append(ChatMessage(content=assistant_content, role="assistant"))
            
            return ChatResponse(
                content=assistant_content,
                parsed=parsed,
                usage=response.usage.model_dump() if response.usage else None,
                metadata={'model': self.model}
            )
            
        except Exception as e:
            provider = AnthropicProvider(self.model)
            if provider.is_retryable_error(e):
                raise RetryableError(f"Anthropic API error: {e}") from e
            else:
                raise NonRetryableError(f"Anthropic API error: {e}") from e
    
    def get_conversation_history(self) -> List[ChatMessage]:
        return self._history.copy()
    
    def clear_history(self):
        self._history.clear()
        self._messages = []