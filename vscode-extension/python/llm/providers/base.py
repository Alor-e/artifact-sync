from core.abstractions import ModelProviderInterface, ModelProvider
from llm.providers.gemini import GeminiProvider
from llm.providers.openai import OpenAIProvider
from llm.providers.anthropic import AnthropicProvider

class ProviderFactory:
    """Factory for creating model providers"""
    
    _providers = {
        ModelProvider.GEMINI: GeminiProvider,
        ModelProvider.OPENAI: OpenAIProvider,
        ModelProvider.ANTHROPIC: AnthropicProvider,
    }
    
    @classmethod
    def create_provider(cls, provider_type: ModelProvider, model_name: str) -> ModelProviderInterface:
        if provider_type not in cls._providers:
            raise ValueError(f"Unsupported provider: {provider_type}")
        
        return cls._providers[provider_type](model_name)
    
    @classmethod
    def register_provider(cls, provider_type: ModelProvider, provider_class: type):
        """Register a new provider implementation"""
        cls._providers[provider_type] = provider_class