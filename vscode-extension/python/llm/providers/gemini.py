from google import genai
from google.genai import types
from google.api_core import exceptions as google_exceptions
from typing import List, Any, Union
from core.abstractions import ModelProviderInterface, ChatSession
from core.schemas import ChatConfig, ChatMessage, ChatResponse
from core.errors import RetryableError, NonRetryableError

class GeminiProvider(ModelProviderInterface):
    def __init__(self, model_name: str):
        self.model_name = model_name
        
    def create_client(self, api_key: str, **kwargs) -> Any:
        return genai.Client(api_key=api_key, **kwargs)
    
    def create_chat(self, client: Any, config: ChatConfig) -> 'GeminiChatSession':
        # Gemini-specific config
        gemini_config = types.GenerateContentConfig(
            system_instruction=config.system_instruction,
            temperature=config.temperature,
            response_mime_type="application/json" if config.response_format == "json" else None,
            response_schema=config.response_schema
        )
        
        # Create the chat with retry logic
        return GeminiChatSession(client, self.model_name, gemini_config)
    
    def is_retryable_error(self, error: Exception) -> bool:
        return isinstance(error, (
            google_exceptions.ResourceExhausted,
            google_exceptions.ServiceUnavailable,
            google_exceptions.InternalServerError
        ))
    
    def get_error_wait_time(self, error: Exception, attempt: int) -> float:
        return 2 ** attempt  # Exponential backoff

class GeminiChatSession(ChatSession):
    def __init__(self, client: Any, model: str, config: Any):
        self.client = client
        self.model = model
        self.config = config
        self._chat = None
        self._history = []
    
    def _ensure_chat_created(self):
        if self._chat is None:
            self._chat = self.client.chats.create(model=self.model, config=self.config)
    
    def send_message(self, message: Union[str, ChatMessage]) -> ChatResponse:
        self._ensure_chat_created()
        
        content = message if isinstance(message, str) else message.content
        
        try:
            response = self._chat.send_message(content)
            
            # Store in history
            self._history.append(ChatMessage(content=content, role="user"))
            self._history.append(ChatMessage(content=response.text, role="assistant"))
            
            return ChatResponse(
                content=response.text,
                parsed=getattr(response, 'parsed', None),
                metadata={'model': self.model}
            )
        except Exception as e:
            # Convert to our error types
            provider = GeminiProvider(self.model)
            if provider.is_retryable_error(e):
                raise RetryableError(f"Gemini API error: {e}") from e
            else:
                raise NonRetryableError(f"Gemini API error: {e}") from e
    
    def get_conversation_history(self) -> List[ChatMessage]:
        return self._history.copy()
    
    def clear_history(self):
        self._history.clear()
        self._chat = None