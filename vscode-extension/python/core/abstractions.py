import asyncio
from concurrent.futures import ThreadPoolExecutor
from abc import ABC, abstractmethod
from typing import List, Any, Union
from enum import Enum
from core.schemas import ChatConfig, ChatMessage, ChatResponse

class ModelProvider(Enum):
    GEMINI = "GEMINI"
    OPENAI = "OPENAI"
    ANTHROPIC = "ANTHROPIC"

class ModelProviderInterface(ABC):
    """Abstract interface for all model providers"""
    
    @abstractmethod
    def create_client(self, api_key: str, **kwargs) -> Any:
        """Create provider-specific client"""
        pass
    
    @abstractmethod
    def create_chat(self, client: Any, config: ChatConfig) -> 'ChatSession':
        """Create a new chat session"""
        pass
    
    @abstractmethod
    def is_retryable_error(self, error: Exception) -> bool:
        """Determine if an error should trigger a retry"""
        pass
    
    @abstractmethod
    def get_error_wait_time(self, error: Exception, attempt: int) -> float:
        """Calculate wait time for retry based on error and attempt"""
        pass

class ChatSession(ABC):
    """Abstract chat session interface"""
    
    @abstractmethod
    def send_message(self, message: Union[str, ChatMessage]) -> ChatResponse:
        """Send a message and get response"""
        pass
    
    @abstractmethod
    def get_conversation_history(self) -> List[ChatMessage]:
        """Get conversation history"""
        pass
    
    @abstractmethod
    def clear_history(self):
        """Clear conversation history"""
        pass

class AsyncChatSession:
    """Wrapper to make synchronous chat sessions work with async/await"""
    
    def __init__(self, chat_session: ChatSession, executor: ThreadPoolExecutor):
        self.chat_session = chat_session
        self.executor = executor
    
    async def send_message_async(self, message: Union[str, ChatMessage]) -> ChatResponse:
        """Send message asynchronously"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self.executor, 
            self.chat_session.send_message, 
            message
        )